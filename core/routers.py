import json
import ast
import os
import sys
import platform
import asyncio
import logging
import time
from datetime import datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException, Response, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

from core.archive import ArchiveSecurityError, extract_zip_safely
from core.settings import SettingsManager, settings
from core.dependencies import get_settings_manager, get_module_manager, get_llm_bridge, require_debug_mode, get_research_manager
from core.module_manager import ModuleManager
from core.flow_manager import flow_manager
from core.llm import LLMBridge
from core.debug import debug_logger
from core.research_manager import ResearchManager
from core.paths import data_path, state_path

logger = logging.getLogger(__name__)


router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

def format_reasoning_content(content):
    """Extracts the actual content from a raw LLM response dictionary string."""
    if isinstance(content, str) and content.strip().startswith("{") and "'choices':" in content:
        try:
            # Attempt to parse stringified dict
            data = ast.literal_eval(content)
            if isinstance(data, dict):
                # Check for OpenAI format
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    if "content" in message:
                        return message["content"]
        except (ValueError, SyntaxError, KeyError, IndexError) as _exc:
            # ValueError/SyntaxError: ast.literal_eval failed to parse
            # KeyError/IndexError: Missing expected keys in parsed structure
            logger.debug("ignored exception: %s", _exc)
    return content


templates.env.filters["format_reasoning"] = format_reasoning_content

def _legacy_hardware_id_path() -> str:
    """The nested path some builds used (``<state dir>/data/hardware_id.txt``).

    Ownership claims are keyed by the persisted hardware id, so a build that discovers this
    file adopts it instead of generating a new id and orphaning the user's marketplace items.
    """
    return state_path("data", "hardware_id.txt")


def _adopt_legacy_hardware_id(id_file: str, legacy_file: str) -> None:
    import os

    if id_file == legacy_file or os.path.exists(id_file) or not os.path.exists(legacy_file):
        return
    try:
        with open(legacy_file, "r", encoding="utf-8") as f:
            persisted = f.read()
        if len(persisted.strip()) < 16:
            return  # hygiene: allow corrupt legacy id is not adopted
        os.makedirs(os.path.dirname(id_file) or ".", exist_ok=True)
        tmp = id_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(persisted)
        os.replace(tmp, id_file)
        logger.info("Adopted hardware id from legacy path %s", legacy_file)
    except OSError as exc:
        logger.warning("Could not adopt legacy hardware id: %s", exc)


def get_hardware_id():
    import uuid
    import hashlib
    import subprocess
    import os

    id_file = data_path("hardware_id.txt")
    _adopt_legacy_hardware_id(id_file, _legacy_hardware_id_path())

    # Return persisted ID so hardware changes never alter it
    if os.path.exists(id_file):
        try:
            with open(id_file, "r", encoding="utf-8") as f:
                persisted = f.read().strip()
            if persisted and len(persisted) >= 16:
                return persisted
        except Exception as _exc:  # hygiene: allow persisted id fallback
            logger.debug("ignored exception: %s", _exc)

    def get_cmd(cmd):
        try: return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=5.0).decode().strip()  # hygiene: allow wmic hardware probe
        except Exception: return ""  # hygiene: allow probe error fallback

    try:
        cpu_id = get_cmd("wmic cpu get ProcessorId")
        cpu_name = get_cmd("wmic cpu get Name")
        cpu_mhz = get_cmd("wmic cpu get MaxClockSpeed")
        board_sn = get_cmd("wmic baseboard get SerialNumber")
        board_mfr = get_cmd("wmic baseboard get Manufacturer")
        board_product = get_cmd("wmic baseboard get Product")
        ram = get_cmd("wmic ComputerSystem get TotalPhysicalMemory")
        disk_sn = get_cmd("wmic diskdrive where index=0 get SerialNumber")
        os_name = f"{platform.system()} {platform.release()} {platform.version()}"

        probes = [cpu_id, cpu_name, cpu_mhz, board_sn, board_mfr, board_product, ram, disk_sn]
        probe_text = "".join(probes).strip()
        combined = f"{'|'.join(probes)}|{os_name}"
        if not probe_text:
            # wmic is absent on current Windows and Linux; without a per-host signal every
            # machine with the same OS version would receive the same hardware id.
            combined = f"{combined}|{uuid.getnode()}"
        useful = combined.replace("|", "").replace(" ", "").strip()
        if len(useful) < 10:
            raise ValueError("Specs too short")

        hardware_id = hashlib.sha256(combined.encode()).hexdigest()[:24].upper()
    except Exception:
        try:
            node = uuid.getnode()
            hardware_id = hashlib.sha256(str(node).encode()).hexdigest()[:24].upper()
        except Exception:
            hardware_id = "UNKNOWN"

    # Persist so future calls never recompute
    try:
        os.makedirs(os.path.dirname(id_file) or ".", exist_ok=True)
        with open(id_file, "w", encoding="utf-8") as f:
            f.write(hardware_id)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)

    return hardware_id


def _decoder_key_path() -> str:
    """Where the decoder key lives; every reader and writer must use this one location."""
    return data_path("decoder.key")


def _dashboard_paths() -> dict:
    """The state files the dashboard counts, at the locations the app actually writes them."""
    return {
        "memory": data_path("memory.sqlite3"),
        "sessions": state_path("chat_sessions.json"),
        "knowledge_base": data_path("knowledge_base.sqlite3"),
    }


def get_decoder() -> bytes:
    """
    Load or generate the local decoder key (32 random bytes).
    Stored in the data directory as decoder.key, a 64-char hex string.
    This key NEVER leaves the machine and is the basis for all ownership claims.
    If lost, the user permanently loses the ability to manage their marketplace items.
    """
    import secrets, os
    key_file = _decoder_key_path()
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                hex_key = f.read().strip()
            if len(hex_key) == 64:
                return bytes.fromhex(hex_key)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)
    # Generate new random key
    key = secrets.token_bytes(32)
    try:
        os.makedirs(data_path(), exist_ok=True)
        # Write atomically: temp file + rename
        tmp = key_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(key.hex())
        os.replace(tmp, key_file)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)
    return key


def make_claim(item_id: str) -> str:
    """
    Compute a cryptographic ownership claim for a marketplace item.
    Only reproducible by a machine holding the same decoder key.
    Uses a context prefix to prevent cross-purpose token reuse.
    Returns a 32-char hex string.
    """
    import hmac as _hmac, hashlib
    key = get_decoder()
    return _hmac.new(key, f"marketplace-claim:{item_id}".encode(), hashlib.sha256).hexdigest()[:32]


def get_public_handle() -> str:
    """
    A stable, opaque public identifier for this user, shown in the marketplace as the uploader.
    Same across all items from the same machine/decoder key, but reveals nothing about the user.
    Derived from the decoder key so it follows the key when imported to a new machine.
    Returns a 12-char uppercase hex string, e.g. 'A3F8C1D920BE'.
    """
    import hmac as _hmac, hashlib
    key = get_decoder()
    return _hmac.new(key, b"marketplace-user-handle", hashlib.sha256).hexdigest()[:12].upper()


def make_vote_id(item_id: str) -> str:
    """
    Derive a stable anonymous vote identity for an item.
    Different context prefix than make_claim prevents cross-use.
    Returns a 16-char hex string.
    """
    import hmac as _hmac, hashlib
    key = get_decoder()
    return _hmac.new(key, f"marketplace-vote:{item_id}".encode(), hashlib.sha256).hexdigest()[:16]


REPORT_THRESHOLD = 3  # reports needed to show warning badge


def make_report_id() -> str:
    """
    Stable anonymous reporter identity — same machine always produces the same ID.
    Prevents double-reporting without storing PII.
    Different context prefix from vote/claim prevents cross-use.
    """
    import hmac as _hmac, hashlib
    key = get_decoder()
    return _hmac.new(key, b"marketplace-reporter", hashlib.sha256).hexdigest()[:16]


def _load_blocklist() -> list:
    import os
    path = data_path("blocklist.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_blocklist(handles: list) -> None:
    import os
    path = data_path("blocklist.json")
    os.makedirs(data_path(), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(handles, f, indent=2)
    os.replace(tmp, path)


MARKETPLACE_MAX_FILE_MB = 10  # max upload size in megabytes


_PROFILE_PATH = data_path("marketplace_profile.json")


def _load_profile() -> dict:
    """Return local marketplace profile dict (username, description)."""
    try:
        with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_profile_field(key: str, value: str) -> None:
    """Atomically update a single field in the local marketplace profile."""
    os.makedirs(data_path(), exist_ok=True)
    profile = _load_profile()
    profile[key] = value
    tmp = _PROFILE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    os.replace(tmp, _PROFILE_PATH)


def _get_marketplace_username() -> str:
    return _load_profile().get("username", "")


def _get_marketplace_description() -> str:
    return _load_profile().get("description", "")


# ── Marketplace notifications ──────────────────────────────────────────────────

_NOTIFS_PATH = data_path("marketplace_notifications.json")


def _load_notifs() -> list:
    try:
        with open(_NOTIFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_notifs(notifs: list) -> None:
    os.makedirs(data_path(), exist_ok=True)
    tmp = _NOTIFS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(notifs, f, indent=2)
    os.replace(tmp, _NOTIFS_PATH)


def _add_notif(notif_type: str, item_id: str, item_name: str,
               from_handle: str, from_username: str, text: str) -> None:
    """Append a notification to the local notifications list (capped at 200)."""
    import uuid as _uuid
    notifs = _load_notifs()
    notifs.insert(0, {
        "id": str(_uuid.uuid4())[:8],
        "type": notif_type,
        "item_id": item_id,
        "item_name": item_name,
        "from_handle": from_handle,
        "from_username": from_username,
        "text": text[:200],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "read": False,
    })
    _save_notifs(notifs[:200])


def _is_legacy_owner(item: dict) -> bool:
    """
    Backward-compat check for items uploaded before the decoder system.
    Covers items with uploader_id = hardware_id, old public_user_id, or 'local_user'.
    """
    uid = item.get("uploader_id", "")
    if uid == "local_user":
        return True
    try:
        hw = get_hardware_id()
        if uid == hw:
            return True
        # Also cover the brief window when get_public_user_id() was used
        import hashlib
        pub = hashlib.sha256(f"neurocore-marketplace-v1:{hw}".encode()).hexdigest()[:20].upper()
        if uid == pub:
            return True
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)
    return False


def is_item_owner(item: dict) -> bool:
    """
    Full ownership check: claim-based (new) with legacy fallback (old).
    """
    if item.get("claim") and item["claim"] == make_claim(item["id"]):
        return True
    return _is_legacy_owner(item)


# Centralized definition of config keys that should be hidden from the generic JSON editor
HIDDEN_CONFIG_KEYS = {
    'memory': ['save_default_confidence', 'save_confidence_threshold', 'recall_limit', 'recall_min_score', 'consolidation_threshold', 'auto_consolidation_hours', 'arbiter_model', 'arbiter_prompt', 'similarity_threshold', 'belief_ttl_days', 'recall_access_weight'],
    'llm_module': ['temperature', 'max_tokens'],
    'chat': ['auto_rename_turns', 'auto_compact_tokens', 'compact_keep_last'],
    'agent_loop': ['max_iterations', 'max_tokens', 'temperature', 'max_llm_retries', 'retry_delay', 'timeout', 'tool_error_strategy', 'include_plan_in_context', 'include_memory_context', 'include_knowledge_context', 'include_reasoning_context', 'compact_threshold', 'compact_keep_last'],
    'telegram': ['bot_token', 'chat_id'],
    'messaging_bridge': [
        'telegram_bot_token', 'telegram_chat_id',
        'discord_bot_token', 'discord_channel_id',
        'signal_api_url', 'signal_phone_number',
        'whatsapp_api_url', 'whatsapp_api_key', 'whatsapp_instance', 'whatsapp_phone_number',
    ],
    'email_bridge': [
        'imap_host', 'imap_port', 'imap_use_ssl', 'imap_username', 'imap_password',
        'imap_folder', 'imap_filter_sender', 'poll_interval', 'mark_as_read',
        'smtp_host', 'smtp_port', 'smtp_use_tls', 'smtp_username', 'smtp_password',
        'smtp_from_address', 'reply_to_address',
    ],
}

# --- System & Navigation ---

@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    return templates.TemplateResponse(request, "index.html", {
        "modules": module_manager.get_all_modules(),
        "active_module": "dashboard",
        "settings": settings_man.settings,
        "hide_module_list": True,
        "hide_status_panel": True,
        "full_width_content": True
    })

# --- Dashboard ---

@router.get("/dashboard/gui", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {})

@router.get("/dashboard/stats", response_class=HTMLResponse)
async def get_dashboard_stats(request: Request):
    import json
    from pathlib import Path
    
    base_dir = Path(__file__).resolve().parent.parent
    state_files = _dashboard_paths()

    # Memory count
    memory_count = 0
    memory_path = Path(state_files["memory"])
    if memory_path.exists():
        try:
            import sqlite3
            with sqlite3.connect(str(memory_path)) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0")
                memory_count = cursor.fetchone()[0]
        except (sqlite3.Error, OSError) as e:
            # sqlite3.Error: Database connection or query failed
            # OSError: File system issues (permissions, disk full, etc.)
            logger.warning(f"[Dashboard Stats] Could not read memory count: {e}")

    
    # Chat sessions count
    sessions_count = 0
    sessions_path = Path(state_files["sessions"])
    if sessions_path.exists():
        try:
            with open(sessions_path, 'r', encoding="utf-8") as f:
                sessions = json.load(f)
            sessions_count = len(sessions)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            # JSONDecodeError: Corrupted JSON file
            # OSError: File read permissions or I/O issues
            # KeyError: Unexpected structure in sessions data
            logger.warning(f"[Dashboard Stats] Could not read sessions count: {e}")

    
    # Knowledge base docs count
    docs_count = 0
    kb_path = Path(state_files["knowledge_base"])
    if kb_path.exists():
        try:
            import sqlite3
            with sqlite3.connect(str(kb_path)) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM documents")
                docs_count = cursor.fetchone()[0]
        except (sqlite3.Error, OSError) as e:
            # sqlite3.Error: Database connection or query failed
            # OSError: File system issues
            logger.warning(f"[Dashboard Stats] Warning: Could not read knowledge base count: {e}")

    
    # Tools count
    tools_count = 0
    tools_path = base_dir / "modules" / "tools" / "library"
    logger.debug(f"Checking tools_path: {tools_path}, exists: {tools_path.exists()}")
    if tools_path.exists():
        try:
            files = list(tools_path.iterdir())
            py_files = [f for f in files if f.suffix == '.py' and not f.name.startswith('_') and f.name != '__init__.py']
            tools_count = len(py_files)
            logger.info(f"Found {tools_count} tools: {[f.name for f in py_files]}")
        except (OSError, PermissionError) as e:
            # OSError: Directory iteration failed
            # PermissionError: Insufficient permissions to read directory
            logger.warning(f"Error counting tools: {e}")
    else:
        logger.warning(f"Tools path does not exist: {tools_path}")


    
    html = f"""
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="group bg-slate-800/50 rounded-xl border border-slate-700/50 p-5 hover:border-blue-500/30 transition-all duration-300 hover:shadow-lg hover:shadow-blue-500/5">
            <div class="flex items-center justify-between mb-3">
                <div class="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center">
                    <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
                </div>
                <span class="text-xs text-slate-500">Memory</span>
            </div>
            <div class="text-3xl font-bold text-white">{memory_count}</div>
            <div class="text-xs text-slate-500 mt-1">Stored Memories</div>
        </div>
        <div class="group bg-slate-800/50 rounded-xl border border-slate-700/50 p-5 hover:border-emerald-500/30 transition-all duration-300 hover:shadow-lg hover:shadow-emerald-500/5">
            <div class="flex items-center justify-between mb-3">
                <div class="w-10 h-10 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                    <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                </div>
                <span class="text-xs text-slate-500">Chat</span>
            </div>
            <div class="text-3xl font-bold text-white">{sessions_count}</div>
            <div class="text-xs text-slate-500 mt-1">Active Sessions</div>
        </div>
        <div class="group bg-slate-800/50 rounded-xl border border-slate-700/50 p-5 hover:border-purple-500/30 transition-all duration-300 hover:shadow-lg hover:shadow-purple-500/5">
            <div class="flex items-center justify-between mb-3">
                <div class="w-10 h-10 rounded-lg bg-purple-500/20 flex items-center justify-center">
                    <svg class="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path></svg>
                </div>
                <span class="text-xs text-slate-500">Knowledge</span>
            </div>
            <div class="text-3xl font-bold text-white">{docs_count}</div>
            <div class="text-xs text-slate-500 mt-1">Documents Indexed</div>
        </div>
        <div class="group bg-slate-800/50 rounded-xl border border-slate-700/50 p-5 hover:border-orange-500/30 transition-all duration-300 hover:shadow-lg hover:shadow-orange-500/5">
            <div class="flex items-center justify-between mb-3">
                <div class="w-10 h-10 rounded-lg bg-orange-500/20 flex items-center justify-center">
                    <svg class="w-5 h-5 text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
                </div>
                <span class="text-xs text-slate-500">Tools</span>
            </div>
            <div class="text-3xl font-bold text-white">{tools_count}</div>
            <div class="text-xs text-slate-500 mt-1">Available Tools</div>
        </div>
    </div>
    """
    return html

@router.get("/dashboard/recent-sessions", response_class=HTMLResponse)
async def get_recent_sessions(request: Request):
    import json
    import os
    import html
    from datetime import datetime
    
    sessions_path = _dashboard_paths()["sessions"]
    if not os.path.exists(sessions_path):
        return '<p class="text-slate-500 text-sm italic">No sessions yet</p>'
    
    try:
        with open(sessions_path, 'r', encoding="utf-8") as f:
            sessions = json.load(f)
        
        sorted_sessions = sorted(sessions.values(), key=lambda x: x.get('updated_at', ''), reverse=True)
        recent = sorted_sessions[:5]
        
        if not recent:
            return '<p class="text-slate-500 text-sm italic">No sessions yet</p>'
        
        html_content = ""
        for s in recent:
            updated = s.get('updated_at', '')
            try:
                dt = datetime.fromisoformat(updated.replace('Z', '+00:00'))
                time_ago = dt.strftime('%b %d, %H:%M')
            except (ValueError, TypeError, AttributeError):
                # ValueError: Invalid date format in updated string
                # TypeError: updated is not a string
                # AttributeError: replace/strftime called on wrong type
                time_ago = updated[:10] if len(updated) >= 10 else 'Unknown'
            
            # Escape user-supplied session name to prevent XSS
            session_name = html.escape(s.get('name', 'Untitled'))
            
            html_content += f"""
            <button onclick="htmx.ajax('GET', '/chat/gui?session_id={s['id']}', '#module-content')" class="w-full flex items-center justify-between p-3 rounded-lg hover:bg-slate-700/50 border border-transparent hover:border-slate-600/50 transition-all group text-left">
                <div class="flex items-center gap-3 min-w-0">
                    <div class="w-8 h-8 rounded-lg bg-slate-700 flex items-center justify-center flex-shrink-0">
                        <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                    </div>
                    <span class="text-sm text-slate-300 truncate">{session_name}</span>
                </div>
                <span class="text-xs text-slate-500 flex-shrink-0">{time_ago}</span>
            </button>
            """
        return html_content
    except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
        # JSONDecodeError: Corrupted sessions file
        # OSError: File system issues
        # KeyError/ValueError: Data structure issues
        logger.warning(f"[Recent Sessions] Warning: Could not load recent sessions: {e}")
        return f'<p class="text-slate-500 text-sm italic">Error loading sessions</p>'


@router.get("/navbar", response_class=HTMLResponse)
async def get_navbar(request: Request, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    return templates.TemplateResponse(request, "navbar.html", {"modules": module_manager.get_all_modules(), "settings": settings_man.settings})

@router.get("/llm-status", response_class=HTMLResponse)
async def get_llm_status(request: Request, llm: LLMBridge = Depends(get_llm_bridge)):
    api_status_check = await llm.get_models()
    api_online = "error" not in api_status_check
    return f"""
    <div class="flex items-center space-x-2">
        <div class="w-2 h-2 rounded-full {"bg-emerald-500 animate-pulse" if api_online else "bg-red-500"}"></div>
        <span class="text-xs {"text-slate-400" if api_online else "text-red-400"}">LLM Status: {"Online" if api_online else "Offline"}</span>
    </div>
    """

# --- Module Management ---

@router.get("/modules/list", response_class=HTMLResponse)
async def list_modules(request: Request, module_manager: ModuleManager = Depends(get_module_manager)):
    return templates.TemplateResponse(request, "module_list.html", {"modules": module_manager.get_all_modules()})

@router.get("/modules/{module_id}/details", response_class=HTMLResponse)
async def get_module_details(request: Request, module_id: str, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    module = module_manager.modules.get(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    
    config_display = module.get('config', {}).copy()
    keys_to_hide = HIDDEN_CONFIG_KEYS.get(module_id, [])
    for key in keys_to_hide:
        config_display.pop(key, None)
        
    formatted_config = json.dumps(config_display, indent=4)
    
    # Check if there are any keys left to display
    has_visible_config = len(config_display) > 0
    
    return templates.TemplateResponse(request, "module_details.html", {
        "module": module, 
        "formatted_config": formatted_config,
        "has_visible_config": has_visible_config,
        "settings": settings_man.settings
    })

@router.get("/modules/{module_id}/default-prompt")
async def get_default_prompt(module_id: str, module_manager: ModuleManager = Depends(get_module_manager)):
    """Returns the default prompt for reflection or planner modules as plain text."""
    from fastapi import Response
    
    module = module_manager.modules.get(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    
    config = module.get('config', {})
    
    if module_id == 'reflection':
        default_prompt = config.get('default_reflection_prompt', '')
    elif module_id == 'planner':
        default_prompt = config.get('default_planner_prompt', '')
    else:
        raise HTTPException(status_code=400, detail="Module does not support default prompts")
    
    # Return as plain text to preserve actual newlines (not escaped \n)
    return Response(content=default_prompt, media_type="text/plain")


@router.post("/modules/{module_id}/config")
async def save_module_config(request: Request, module_id: str, module_manager: ModuleManager = Depends(get_module_manager)):
    try:
        form_data = await request.form()
        
        # Handle system_prompt module specially
        if module_id == "system_prompt":
            new_config = {}
            if "system_prompt" in form_data:
                new_config["system_prompt"] = form_data["system_prompt"]
            if "enabled_tools" in form_data:
                try:
                    new_config["enabled_tools"] = json.loads(form_data["enabled_tools"])
                except json.JSONDecodeError:
                    new_config["enabled_tools"] = []
        # Handle reflection module with custom prompt
        elif module_id == "reflection":
            new_config = {}
            current_module = module_manager.modules.get(module_id)
            current_config = current_module.get('config', {}) if current_module else {}
            
            # Copy existing config to preserve defaults
            new_config.update(current_config)
            
            # Update reflection_prompt (custom prompt)
            if "reflection_prompt" in form_data:
                new_config['reflection_prompt'] = form_data["reflection_prompt"]
            
            # Update inject_improvement setting
            new_config['inject_improvement'] = form_data.get("inject_improvement") == "true"
            
        # Handle planner module with custom prompt
        elif module_id == "planner":
            new_config = {}
            current_module = module_manager.modules.get(module_id)
            current_config = current_module.get('config', {}) if current_module else {}
            
            # Copy existing config to preserve defaults
            new_config.update(current_config)
            
            # Update planner_prompt (custom prompt)
            if "planner_prompt" in form_data:
                new_config['planner_prompt'] = form_data["planner_prompt"]
            
            # Update max_steps
            if "max_steps" in form_data:
                try:
                    new_config['max_steps'] = int(form_data["max_steps"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            # Update enabled setting
            new_config['enabled'] = form_data.get("enabled") == "true"
            
            # Update reasoning book integration settings
            new_config['log_to_reasoning_book'] = form_data.get("log_to_reasoning_book") == "true"
            new_config['include_reasoning_context'] = form_data.get("include_reasoning_context") == "true"
        
        # Handle agent_loop module with individual form fields
        elif module_id == "agent_loop":
            new_config = {}
            current_module = module_manager.modules.get(module_id)
            current_config = current_module.get('config', {}) if current_module else {}
            
            # Copy existing config to preserve defaults
            new_config.update(current_config)
            
            # Update loop control settings
            if "max_iterations" in form_data:
                try:
                    new_config['max_iterations'] = int(form_data["max_iterations"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            if "max_tokens" in form_data:
                try:
                    new_config['max_tokens'] = int(form_data["max_tokens"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            if "temperature" in form_data:
                try:
                    new_config['temperature'] = float(form_data["temperature"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            # Update retry & timeout settings
            if "max_llm_retries" in form_data:
                try:
                    new_config['max_llm_retries'] = int(form_data["max_llm_retries"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            if "retry_delay" in form_data:
                try:
                    new_config['retry_delay'] = float(form_data["retry_delay"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            if "timeout" in form_data:
                try:
                    new_config['timeout'] = int(form_data["timeout"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            # Update context inclusion settings (checkboxes)
            new_config['include_plan_in_context'] = form_data.get("include_plan_in_context") == "true"
            new_config['include_memory_context'] = form_data.get("include_memory_context") == "true"
            new_config['include_knowledge_context'] = form_data.get("include_knowledge_context") == "true"
            new_config['include_reasoning_context'] = form_data.get("include_reasoning_context") == "true"
            
            # Update tool error strategy
            if "tool_error_strategy" in form_data:
                new_config['tool_error_strategy'] = form_data["tool_error_strategy"]

            # Context compaction settings
            if "compact_threshold" in form_data:
                try:
                    new_config['compact_threshold'] = int(form_data["compact_threshold"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)

            if "compact_keep_last" in form_data:
                try:
                    new_config['compact_keep_last'] = int(form_data["compact_keep_last"])
                except ValueError as _exc:
                    logger.debug("ignored exception: %s", _exc)

        elif module_id == "email_bridge":
            current_module = module_manager.modules.get(module_id)
            current_config = current_module.get('config', {}) if current_module else {}
            new_config = current_config.copy()
            section = form_data.get('_section', '')

            for str_key in ('imap_host', 'imap_username', 'imap_password', 'imap_folder',
                            'imap_filter_sender', 'smtp_host', 'smtp_username', 'smtp_password',
                            'smtp_from_address', 'reply_to_address'):
                if str_key in form_data:
                    new_config[str_key] = form_data[str_key]
            for int_key in ('imap_port', 'smtp_port', 'poll_interval'):
                if int_key in form_data:
                    try: new_config[int_key] = int(form_data[int_key])
                    except ValueError as _exc: logger.debug("ignored exception: %s", _exc)
            # Booleans: only update for the section that was submitted
            if section in ('imap', ''):
                new_config['imap_use_ssl'] = form_data.get('imap_use_ssl') == 'true'
                new_config['mark_as_read'] = form_data.get('mark_as_read') == 'true'
            if section in ('smtp', ''):
                new_config['smtp_use_tls'] = form_data.get('smtp_use_tls') == 'true'

        else:

            # Standard JSON config
            if "config_json" not in form_data:
                return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Missing config_json field"}})})
            
            new_config = json.loads(form_data["config_json"])
        
        # Preserve hidden keys by merging from existing config
        keys_to_preserve = HIDDEN_CONFIG_KEYS.get(module_id, [])
        current_module = module_manager.modules.get(module_id)
        
        if current_module:
            current_config = current_module.get('config', {})
            for key in keys_to_preserve:
                if key in current_config:
                    new_config[key] = current_config[key]
                    
        module_manager.update_module_config(module_id, new_config)
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Configuration saved"}})})
    except json.JSONDecodeError:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid JSON format"}})})
    except (KeyError, TypeError, ValueError) as e:
        # KeyError: Missing expected form fields
        # TypeError: Invalid type operations during config processing
        # ValueError: Invalid values in form data
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Configuration error: {e}"}})})



@router.post("/modules/reorder")
async def reorder_modules(request: Request, order: str = Form(...), module_manager: ModuleManager = Depends(get_module_manager)):
    module_ids = order.split(',')
    try:
        module_manager.reorder_modules(module_ids)
        # Trigger a refresh of the navbar to reflect the new order
        return Response(status_code=200, headers={"HX-Trigger": "modulesChanged"})
    except (ValueError, KeyError, TypeError) as e:
        # ValueError: Invalid module ID format
        # KeyError: Module not found during reordering
        # TypeError: Invalid operations on module data
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to reorder: {e}"}})})


@router.post("/modules/{module_id}/{action}")
async def toggle_module(request: Request, module_id: str, action: str, module_manager: ModuleManager = Depends(get_module_manager)):
    if action == "enable":
        module = module_manager.enable_module(module_id)
    elif action == "disable":
        module = module_manager.disable_module(module_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    
    formatted_config = json.dumps(module.get('config', {}), indent=4)
    return templates.TemplateResponse(
        request, "module_details.html", {"module": module, "formatted_config": formatted_config}, headers={"HX-Trigger": json.dumps({"modulesChanged": None, "showMessage": {"level": "success", "message": f"Module {action}d"}})}
    )


# --- AI Flow ---

@router.get("/ai-flow", response_class=HTMLResponse)
async def ai_flow_page(request: Request, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    active_flow_ids = settings_man.get("active_ai_flows", [])
    return templates.TemplateResponse(request, "ai_flow.html", {
        "modules": module_manager.get_all_modules(),
        "flows": flow_manager.list_flows(),
        "active_flow_ids": active_flow_ids,
        "settings": settings_man.settings
    })

@router.get("/ai-flow/{flow_id}", response_class=JSONResponse)
async def get_flow_data(flow_id: str):
    flow = flow_manager.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow

@router.get("/ai-flow/{flow_id}/validate", response_class=JSONResponse)
async def validate_flow(flow_id: str, request: Request):
    """Validates a flow for potential issues before execution."""
    flow = flow_manager.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    
    from core.flow_runner import FlowRunner
    module_manager = request.app.state.module_manager
    
    try:
        runner = FlowRunner(flow_id, flow_override=flow)
        validation_result = runner.validate(module_manager)
        return validation_result
    except (ValueError, KeyError, TypeError) as e:
        # ValueError: Flow not found or invalid flow structure
        # KeyError: Missing required flow data
        # TypeError: Invalid data types in flow structure
        return {"valid": False, "issues": [], "warnings": [], "error": f"Validation error: {e}"}


@router.get("/ai-flow/{flow_id}/simulate", response_class=JSONResponse)
async def simulate_flow_route(flow_id: str, request: Request):
    """Deterministic pre-flight analysis: structure, external effects and a cost envelope."""
    flow = flow_manager.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    from core.flow_simulation import node_type_catalogue, simulate_flow

    try:
        modules = getattr(request.app.state.module_manager, "modules", None)
        known = node_type_catalogue(modules)
    except (AttributeError, TypeError):
        known = set()
    return simulate_flow(flow, known_node_types=known or None)


@router.post("/ai-flow/save")
async def save_ai_flow(request: Request, name: str = Form(...), nodes: str = Form(...), connections: str = Form(...), bridges: str = Form("[]"), flow_id: str = Form(None)):
    if not flow_id:
        flow_id = None
    
    try:
        flow_manager.save_flow(name=name, nodes=json.loads(nodes), connections=json.loads(connections), bridges=json.loads(bridges), flow_id=flow_id, validate=True)
    except json.JSONDecodeError:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid JSON in flow data"}})})
    except (TypeError, ValueError) as exc:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Invalid flow: {exc}"}})})
    
    return templates.TemplateResponse(request, "ai_flow_list.html", {
        "flows": flow_manager.list_flows(),
        "active_flow_ids": settings.get("active_ai_flows", [])
    }, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Flow saved successfully"}})})

@router.post("/ai-flow/{flow_id}/rename", response_class=HTMLResponse)
async def rename_flow(request: Request, flow_id: str, name: str = Form(...), settings_man: SettingsManager = Depends(get_settings_manager)):
    flow_manager.rename_flow(flow_id, name)
    return templates.TemplateResponse(request, "ai_flow_list.html", {
        "flows": flow_manager.list_flows(),
        "active_flow_ids": settings_man.get("active_ai_flows", [])
    })

@router.post("/ai-flow/{flow_id}/set-active", response_class=HTMLResponse)
async def set_active_flow(request: Request, flow_id: str, action: str = Form("toggle"), settings_man: SettingsManager = Depends(get_settings_manager)):
    # Create a copy to avoid mutating the internal settings list
    active_flows = list(settings_man.get("active_ai_flows", []))
    
    if action == "activate":
        if flow_id not in active_flows:
            active_flows.append(flow_id)
    elif action == "deactivate":
        if flow_id in active_flows:
            active_flows.remove(flow_id)
    else:
        if flow_id in active_flows:
            active_flows.remove(flow_id)
        else:
            active_flows.append(flow_id)
    
    settings_man.save_settings({"active_ai_flows": active_flows})
    
    # Auto-start the flow if it has a Repeater node
    flow = flow_manager.get_flow(flow_id)
    if flow:
        background_node_types = ["repeater_node"]
        nodes = flow.get("nodes", [])
        connections = flow.get("connections", [])
        
        # Find repeater nodes that have incoming connections
        start_nodes = []
        for n in nodes:
            if n.get("nodeTypeId") in background_node_types:
                # Check if this node has any incoming connections
                has_incoming = any(c.get("to") == n.get("id") for c in connections)
                if has_incoming:
                    start_nodes.append(n)
        
        if start_nodes:
            from core.flow_runner import FlowRunner
            from fastapi import BackgroundTasks
            
            node = start_nodes[0]
            logger.info(f"[System] Auto-starting flow '{flow.get('name')}' from {node['nodeTypeId']} '{node['id']}'.")
            
            async def run_flow():
                runner = FlowRunner(flow_id)
                await runner.run({"_repeat_count": 1}, start_node_id=node['id'])
            
            if hasattr(request.app.state, 'background_tasks'):
                task = asyncio.create_task(run_flow())
                # Add callback to remove task from set when done to prevent memory leak
                task.add_done_callback(request.app.state.background_tasks.discard)
                request.app.state.background_tasks.add(task)
    
    return templates.TemplateResponse(request, "ai_flow_list.html", {
        "flows": flow_manager.list_flows(),
        "active_flow_ids": active_flows
    })

@router.post("/ai-flow/stop-active", response_class=HTMLResponse)
async def stop_active_flow(request: Request, settings_man: SettingsManager = Depends(get_settings_manager)):
    """Stops all active flows by clearing the active flows list and canceling tasks."""
    if hasattr(request.app.state, 'background_tasks'):
        stopped_count = 0
        tasks_to_cancel = []
        for task in list(request.app.state.background_tasks):
            if not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
                stopped_count += 1
        
        # Wait for tasks to actually be cancelled
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        
        logger.info(f"[System] Cancelled {stopped_count} background tasks")
    
    settings_man.save_settings({"active_ai_flows": []})
    return templates.TemplateResponse(request, "ai_flow_list.html", {
        "flows": flow_manager.list_flows(),
        "active_flow_ids": []
    }, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "info", "message": "All active flows stopped"}})})


@router.post("/ai-flow/{flow_id}/delete", response_class=HTMLResponse)
async def delete_flow(request: Request, flow_id: str, settings_man: SettingsManager = Depends(get_settings_manager)):
    # Create a copy to avoid mutating the internal settings list
    active_flows = list(settings_man.get("active_ai_flows", []))
    if flow_id in active_flows:
        active_flows.remove(flow_id)
        settings_man.save_settings({"active_ai_flows": active_flows})
    flow_manager.delete_flow(flow_id)
    return templates.TemplateResponse(request, "ai_flow_list.html", {
        "flows": flow_manager.list_flows(),
        "active_flow_ids": active_flows
    })

@router.get("/ai-flow/{flow_id}/versions", response_class=JSONResponse)
async def get_flow_versions(flow_id: str):
    """Returns the version history for a flow (metadata only, newest first)."""
    if not flow_manager.get_flow(flow_id):
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow_manager.get_versions(flow_id)


@router.get("/ai-flow/{flow_id}/versions/partial", response_class=HTMLResponse)
async def get_flow_versions_partial(request: Request, flow_id: str):
    """Returns the version history panel HTML for HTMX swap."""
    flow = flow_manager.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    versions = flow_manager.get_versions(flow_id)
    return templates.TemplateResponse(request, "ai_flow_versions.html", {
        "flow_id": flow_id,
        "flow_name": flow.get("name", ""),
        "versions": versions,
    })


@router.post("/ai-flow/{flow_id}/rollback/{version}", response_class=HTMLResponse)
async def rollback_flow_version(request: Request, flow_id: str, version: int, settings_man: SettingsManager = Depends(get_settings_manager)):
    """Restores a flow to the specified version snapshot."""
    restored = flow_manager.rollback_version(flow_id, version)
    if restored is None:
        return Response(status_code=404, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Version not found"}})})
    return templates.TemplateResponse(request, "ai_flow_list.html", {
        "flows": flow_manager.list_flows(),
        "active_flow_ids": settings_man.get("active_ai_flows", []),
    }, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": f"Flow restored to version {version}"}})})


@router.post("/ai-flow/make-default")
async def make_active_flow_default(request: Request):
    """Overwrites the default flow with the currently active flow."""
    if flow_manager.make_active_flow_default():
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Active flow saved as Default"}})})
    return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "No active flow found"}})})

@router.post("/ai-flow/{flow_id}/run-node/{node_id}")
async def run_flow_node(flow_id: str, node_id: str, request: Request, background_tasks: BackgroundTasks):
    """Manually triggers a specific node in a flow."""
    
    flow_override = None
    try:
        if "application/json" in request.headers.get("content-type", ""):
            flow_override = await request.json()
    except (json.JSONDecodeError, KeyError, TypeError) as _exc:
        # JSONDecodeError: Invalid JSON in request body
        # KeyError/TypeError: Unexpected JSON structure
        logger.debug("ignored exception: %s", _exc)


    async def _run():
        from core.flow_runner import FlowRunner

        try:
            runner = FlowRunner(flow_id, flow_override=flow_override)
            # Inject some default data so nodes that expect input don't fail immediately
            payload = {
                "trigger": True,
                "timestamp": datetime.utcnow().isoformat() + 'Z',
                "manual": True
            }
            await runner.run(payload, start_node_id=node_id)
        except (RuntimeError, ValueError, TypeError, KeyError) as e:
            # RuntimeError: Flow execution failed at runtime
            # ValueError: Invalid flow ID or node ID
            # TypeError/KeyError: Data structure issues during execution
            logger.warning(f"[Manual Trigger] Flow execution failed: {e}")


    background_tasks.add_task(_run)
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Node triggered"}})})

# --- Settings ---


# --- Marketplace ---

@router.get("/marketplace", response_class=HTMLResponse)
async def get_marketplace(request: Request, settings_man: SettingsManager = Depends(get_settings_manager), module_manager: ModuleManager = Depends(get_module_manager)):
    import os
    # 1. Local inventory
    modules = module_manager.get_all_modules()
    tools = {}
    tools_path = os.path.join("modules", "tools", "tools.json")
    if os.path.exists(tools_path):
         try:
             with open(tools_path, "r", encoding="utf-8") as f:
                 tools = json.load(f)
         except Exception as _exc: logger.debug("ignored exception: %s", _exc)

    skills = {}
    skills_path = os.path.join("modules", "skills", "data", "skills_metadata.json")
    if os.path.exists(skills_path):
         try:
             with open(skills_path, "r", encoding="utf-8") as f:
                 skills = json.load(f)
         except Exception as _exc: logger.debug("ignored exception: %s", _exc)

    # 2. Community Catalog
    catalog = []
    catalog_path = data_path("marketplace", "catalog.json")
    if os.path.exists(catalog_path):
         try:
             with open(catalog_path, "r", encoding="utf-8") as f:
                 catalog = json.load(f)
         except Exception as _exc: logger.debug("ignored exception: %s", _exc)

    # Load local download history for "new version" badges
    dl_history_path = data_path("download_history.json")
    dl_history = {}
    try:
        if os.path.exists(dl_history_path):
            with open(dl_history_path, "r", encoding="utf-8") as f:
                dl_history = json.load(f)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)

    # Filter blocked uploaders
    blocklist = _load_blocklist()
    my_handle = get_public_handle()
    if blocklist:
        catalog = [i for i in catalog if i.get("uploader_handle", "") not in blocklist]

    # Pre-compute ownership, vote state, and report state (Jinja2 can't call Python functions)
    owned_ids = {item["id"] for item in catalog if is_item_owner(item)}
    my_votes = {
        item["id"]: item.get("voters", {}).get(make_vote_id(item["id"]))
        for item in catalog
    }
    my_reporter_id = make_report_id()
    my_reported_ids = {
        item["id"] for item in catalog
        if any(r.get("reporter_id") == my_reporter_id for r in item.get("reports", []))
    }

    # Top stats
    top_downloaded = sorted(catalog, key=lambda i: i.get("download_count", 0), reverse=True)[:5]
    top_upvoted = sorted(catalog, key=lambda i: i.get("upvotes", 0), reverse=True)[:5]

    # Top uploaders: sum downloads per handle
    uploader_stats: dict = {}
    for item in catalog:
        h = item.get("uploader_handle", "")
        if not h:
            continue
        if h not in uploader_stats:
            uploader_stats[h] = {"handle": h, "downloads": 0, "items": 0}
        uploader_stats[h]["downloads"] += item.get("download_count", 0)
        uploader_stats[h]["items"] += 1
    top_uploaders = sorted(uploader_stats.values(), key=lambda x: x["downloads"], reverse=True)[:5]

    # System prompt config for Library tab
    sp_module = module_manager.get_module("system_prompt") or {}
    sp_config = sp_module.get("config", {})
    system_prompts_data = {
        "active": sp_config.get("system_prompt", ""),
        "enabled": sp_module.get("enabled", False),
    }

    # Flows for Library tab: the store in memory is the one that was validated and saved.
    flows_data = flow_manager.get_all_flows_dict()

    return templates.TemplateResponse(request, "marketplace.html", {
        "request": request,
        "modules": modules,
        "tools": tools,
        "skills": skills,
        "flows": flows_data,
        "system_prompts_data": system_prompts_data,
        "catalog": catalog,
        "owned_ids": owned_ids,
        "my_votes": my_votes,
        "my_reported_ids": my_reported_ids,
        "dl_history": dl_history,
        "blocklist": blocklist,
        "my_handle": my_handle,
        "my_username": _get_marketplace_username(),
        "my_upload_count": sum(1 for i in catalog if i.get("uploader_handle") == my_handle),
        "my_total_dl": sum(i.get("download_count", 0) for i in catalog if i.get("uploader_handle") == my_handle),
        "my_total_up": sum(i.get("upvotes", 0) for i in catalog if i.get("uploader_handle") == my_handle),
        "top_downloaded": top_downloaded,
        "top_upvoted": top_upvoted,
        "top_uploaders": top_uploaders,
        "report_threshold": REPORT_THRESHOLD,
        "max_file_mb": MARKETPLACE_MAX_FILE_MB,
        "active_module": "marketplace",
        "settings": settings_man.settings,
    })

@router.post("/marketplace/upload")
async def upload_marketplace_item(
    name: str = Form(...),
    description: str = Form(""),
    type: str = Form(...), # 'module', 'tool', 'skill'
    file: UploadFile = File(...),
    image: UploadFile = File(None)
):
    import uuid
    import shutil
    import os
    
    upload_dir = data_path("marketplace", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    item_id = str(uuid.uuid4())
    
    # Save Main File
    ext = os.path.splitext(file.filename)[1]
    save_filename = f"{item_id}{ext}"
    save_path = os.path.join(upload_dir, save_filename)
    content = await file.read()
    if len(content) > MARKETPLACE_MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {MARKETPLACE_MAX_FILE_MB} MB.")
    with open(save_path, "wb") as f:
        f.write(content)

    # Save Image Banner Optional
    image_filename = None
    if image and image.filename:
        img_ext = os.path.splitext(image.filename)[1]
        image_filename = f"{item_id}_banner{img_ext}"
        img_save_path = os.path.join(upload_dir, image_filename)
        with open(img_save_path, "wb") as f:
             shutil.copyfileobj(image.file, f)
        
    catalog_path = data_path("marketplace", "catalog.json")
    catalog = []
    if os.path.exists(catalog_path):
         try:
             with open(catalog_path, "r", encoding="utf-8") as f:
                 catalog = json.load(f)
         except Exception as _exc: logger.debug("ignored exception: %s", _exc)
             
    entry = {
        "id": item_id,
        "name": name,
        "description": description,
        "type": type.capitalize(),
        "filename": file.filename,
        "save_filename": save_filename,
        "image_filename": image_filename,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "uploader_handle": get_public_handle(),
        "uploader_username": _get_marketplace_username(),
        "uploader_description": _get_marketplace_description(),
        "version": 1,
    }
    # Compute claim after entry is built so item_id is available
    entry["claim"] = make_claim(entry["id"])
    catalog.append(entry)
    
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)
        
    return RedirectResponse(url="/marketplace", status_code=303)

@router.get("/marketplace/image/{item_id}")
async def get_marketplace_image(item_id: str):
    import os
    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
         raise HTTPException(status_code=404, detail="Catalog not found")
        
    try:
         with open(catalog_path, "r", encoding="utf-8") as f:
             catalog = json.load(f)
    except Exception:
         raise HTTPException(status_code=500, detail="Failed to read catalog")
        
    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item or not item.get("image_filename"):
         raise HTTPException(status_code=404, detail="Image not configured")
        
    file_path = data_path("marketplace", "uploads", item["image_filename"])
    if not os.path.exists(file_path):
         raise HTTPException(status_code=404, detail="Image missing on disk")
        
    return FileResponse(file_path)

@router.get("/marketplace/download/{item_id}")
async def download_marketplace_item(item_id: str):
    import os
    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
         raise HTTPException(status_code=404, detail="Catalog not found")
        
    try:
         with open(catalog_path, "r", encoding="utf-8") as f:
             catalog = json.load(f)
    except Exception:
         raise HTTPException(status_code=500, detail="Failed to read catalog")
        
    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
         raise HTTPException(status_code=404, detail="Item not found")
        
    file_path = data_path("marketplace", "uploads", item["save_filename"])
    if not os.path.exists(file_path):
         raise HTTPException(status_code=404, detail="File missing on disk")
        
    # Increment download count in catalog
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog_mutable = json.load(f)
        for ci in catalog_mutable:
            if ci["id"] == item_id:
                ci["download_count"] = ci.get("download_count", 0) + 1
                break
        tmp = catalog_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(catalog_mutable, f, indent=4)
        os.replace(tmp, catalog_path)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)

    # Record which version this user downloaded
    dl_history_path = data_path("download_history.json")
    try:
        dl_history = {}
        if os.path.exists(dl_history_path):
            with open(dl_history_path, "r", encoding="utf-8") as f:
                dl_history = json.load(f)
        dl_history[item_id] = item.get("version", 1)
        tmp = dl_history_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dl_history, f, indent=2)
        os.replace(tmp, dl_history_path)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)

    return FileResponse(file_path, filename=item["filename"])

@router.get("/marketplace/item/{item_id}", response_class=HTMLResponse)
async def marketplace_item_detail(request: Request, item_id: str, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    import os, zipfile
    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
        raise HTTPException(status_code=404, detail="Catalog not found")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    file_path = data_path("marketplace", "uploads", item["save_filename"])
    ext = os.path.splitext(item["save_filename"])[1].lower()
    is_owner = is_item_owner(item)
    user_vote = item.get("voters", {}).get(make_vote_id(item_id))
    reports = item.get("reports", [])
    report_count = len(reports)
    comments = item.get("comments", [])
    is_flagged = report_count >= REPORT_THRESHOLD
    already_reported = any(r.get("reporter_id") == make_report_id() for r in reports)
    is_blocked = item.get("uploader_handle", "") in _load_blocklist()

    # New version badge for non-owners who have downloaded before
    dl_history_path = data_path("download_history.json")
    dl_history = {}
    try:
        if os.path.exists(dl_history_path):
            with open(dl_history_path, "r", encoding="utf-8") as f:
                dl_history = json.load(f)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)
    downloaded_version = dl_history.get(item_id)
    item_version = item.get("version", 1)
    new_version_available = (
        not is_owner
        and downloaded_version is not None
        and item_version > downloaded_version
    )

    # Build file tree for zips; single content for flat files
    file_tree = []   # list of {"path": str, "name": str, "is_dir": bool}
    initial_content = ""
    initial_file = ""
    is_zip = ext == ".zip"

    if is_zip and os.path.exists(file_path):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                names = sorted(zf.namelist())
                for name in names:
                    file_tree.append({
                        "path": name,
                        "name": name,
                        "is_dir": name.endswith("/"),
                        "depth": name.rstrip("/").count("/"),
                    })
                # Load first non-directory file as default view
                first_file = next((n for n in names if not n.endswith("/")), None)
                if first_file:
                    initial_file = first_file
                    try:
                        with zf.open(first_file) as fh:  # hygiene: allow zip member binary stream
                            initial_content = fh.read(256 * 1024).decode("utf-8", errors="replace")
                    except Exception:  # hygiene: allow fallback for unreadable zip member
                        initial_content = ""
        except Exception as e:
            initial_content = f"Could not open zip: {e}"
    elif os.path.exists(file_path):
        initial_file = item.get("filename", item["save_filename"])
        try:
            if ext in (".md", ".txt", ".json", ".py", ".yaml", ".yml", ".toml", ".cfg", ".ini"):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    initial_content = f.read()
            else:
                initial_content = f"Binary file ({ext}) — download to view."
        except Exception as e:
            initial_content = f"Could not read file: {e}"

    return templates.TemplateResponse(request, "marketplace_item.html", {
        "item": item,
        "is_owner": is_owner,
        "user_vote": user_vote,
        "reports": reports,
        "report_count": report_count,
        "is_flagged": is_flagged,
        "already_reported": already_reported,
        "is_blocked": is_blocked,
        "report_threshold": REPORT_THRESHOLD,
        "new_version_available": new_version_available,
        "downloaded_version": downloaded_version,
        "item_version": item_version,
        "is_zip": is_zip,
        "file_tree": file_tree,
        "initial_content": initial_content,
        "initial_file": initial_file,
        "comments": comments,
        "changelog": item.get("changelog", []),
        "my_handle": get_public_handle(),
        "modules": module_manager.get_all_modules(),
        "settings": settings_man.settings,
    })

@router.get("/marketplace/item/{item_id}/file")
async def marketplace_item_file(item_id: str, path: str = ""):
    import os, zipfile
    catalog_path = data_path("marketplace", "catalog.json")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    file_path = data_path("marketplace", "uploads", item["save_filename"])
    ext = os.path.splitext(item["save_filename"])[1].lower()

    if ext == ".zip":
        if not path:
            raise HTTPException(status_code=400, detail="path required for zip items")
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                with zf.open(path) as fh:  # hygiene: allow zip member binary stream
                    content = fh.read(256 * 1024).decode("utf-8", errors="replace")
            return JSONResponse(content={"status": "success", "content": content, "path": path})
        except KeyError:
            raise HTTPException(status_code=404, detail="File not found in zip")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return JSONResponse(content={"status": "success", "content": content, "path": item.get("filename", "")})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@router.get("/marketplace/preview/{item_id}")
async def preview_marketplace_item(item_id: str):
    import os, json
    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path): raise HTTPException(status_code=404, detail="Catalog not found")
    try:
         with open(catalog_path, "r", encoding="utf-8") as f: catalog = json.load(f)
    except Exception: raise HTTPException(status_code=500, detail="Failed to read catalog")
    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item: raise HTTPException(status_code=404, detail="Item not found")
    file_path = data_path("marketplace", "uploads", item["save_filename"])
    if not os.path.exists(file_path): raise HTTPException(status_code=404, detail="File missing on disk")
    content = ""
    try:
         ext = os.path.splitext(item["save_filename"])[1].lower()
         if ext in ['.md', '.txt', '.json', '.py']:
             with open(file_path, "r", encoding="utf-8") as f: content = f.read()
         else: content = f"Binary file ({ext}). Preview not available."
    except Exception as e: content = f"Could not read preview: {str(e)}"
    return JSONResponse(content={"status": "success", "content": content})

@router.post("/marketplace/update/{item_id}")
async def update_marketplace_item(
    item_id: str,
    description: str = Form(None),
    update_notes: str = Form(None),
    file: UploadFile = File(None),
    image: UploadFile = File(None),
):
    """Owner publishes a new version. Bumps version counter, optionally replaces file/image/description."""
    import os, shutil
    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
        raise HTTPException(status_code=404, detail="Catalog not found")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not is_item_owner(item):
        raise HTTPException(status_code=403, detail="Not authorized")

    upload_dir = data_path("marketplace", "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    # Replace file if provided
    if file and file.filename:
        old_path = os.path.join(upload_dir, item["save_filename"])
        # Archive old version alongside the new one (keep for rollback reference)
        old_version = item.get("version", 1)
        archive_name = f"{item_id}_v{old_version}_{item['save_filename']}"
        try:
            if os.path.exists(old_path):
                shutil.copy2(old_path, os.path.join(upload_dir, archive_name))
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)
        content = await file.read()
        with open(old_path, "wb") as f_out:
            f_out.write(content)
        item["filename"] = file.filename
        # Update content hash for skills
        import hashlib
        item["content_hash"] = hashlib.md5(content).hexdigest()

    # Replace image if provided
    if image and image.filename:
        img_content = await image.read()
        img_ext = os.path.splitext(image.filename)[1].lower() or ".jpg"
        img_filename = f"{item_id}{img_ext}"
        with open(os.path.join(upload_dir, img_filename), "wb") as f_out:
            f_out.write(img_content)
        item["image_filename"] = img_filename

    # Update description if provided
    if description is not None:
        item["description"] = description

    # Bump version
    new_version = item.get("version", 1) + 1
    item["version"] = new_version
    item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Append to changelog
    if update_notes and update_notes.strip():
        entry = {
            "version": new_version,
            "notes": update_notes.strip()[:500],
            "timestamp": item["updated_at"],
        }
        item.setdefault("changelog", []).insert(0, entry)

    tmp = catalog_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)
    os.replace(tmp, catalog_path)

    return JSONResponse(content={"status": "success", "version": item["version"]})


@router.post("/marketplace/delete/{item_id}")
async def delete_marketplace_item(item_id: str):
    import os
    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
         raise HTTPException(status_code=404, detail="Catalog not found")
        
    try:
         with open(catalog_path, "r", encoding="utf-8") as f:
             catalog = json.load(f)
    except Exception:
         raise HTTPException(status_code=500, detail="Failed to read catalog")
        
    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
         raise HTTPException(status_code=404, detail="Item not found")
        
    if not is_item_owner(item):
        raise HTTPException(status_code=403, detail="You can only delete items that you uploaded")
        
    # Remove files
    upload_dir = data_path("marketplace", "uploads")
    if item.get("save_filename"):
         fpath = os.path.join(upload_dir, item["save_filename"])
         if os.path.exists(fpath): os.remove(fpath)
            
    if item.get("image_filename"):
         fpath = os.path.join(upload_dir, item["image_filename"])
         if os.path.exists(fpath): os.remove(fpath)
            
    # Update Catalog
    catalog = [i for i in catalog if i["id"] != item_id]
    with open(catalog_path, "w", encoding="utf-8") as f:
         json.dump(catalog, f, indent=4)

    from fastapi import Response
    return Response(status_code=200)

@router.post("/marketplace/report/{item_id}")
async def report_marketplace_item(item_id: str, reason: str = Form(...)):
    """Add a user report with description to a marketplace item."""
    import os
    if not reason or not reason.strip():
        raise HTTPException(status_code=400, detail="Reason is required")

    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
        raise HTTPException(status_code=404, detail="Catalog not found")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if is_item_owner(item):
        raise HTTPException(status_code=403, detail="You cannot report your own item")

    reporter_id = make_report_id()
    reports = item.setdefault("reports", [])

    if any(r.get("reporter_id") == reporter_id for r in reports):
        raise HTTPException(status_code=409, detail="You have already reported this item")

    reports.append({
        "reporter_id": reporter_id,
        "reporter_handle": get_public_handle(),
        "reason": reason.strip()[:500],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })

    tmp = catalog_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)
    os.replace(tmp, catalog_path)

    report_count = len(reports)
    return JSONResponse(content={
        "status": "success",
        "report_count": report_count,
        "flagged": report_count >= REPORT_THRESHOLD,
    })


@router.post("/marketplace/block")
async def block_uploader(handle: str = Form(...)):
    if not handle:
        raise HTTPException(status_code=400, detail="Handle required")
    if handle == get_public_handle():
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    blocklist = _load_blocklist()
    if handle not in blocklist:
        blocklist.append(handle)
        _save_blocklist(blocklist)
    return JSONResponse(content={"status": "success", "blocked": handle})


@router.post("/marketplace/unblock")
async def unblock_uploader(handle: str = Form(...)):
    if not handle:
        raise HTTPException(status_code=400, detail="Handle required")
    blocklist = _load_blocklist()
    if handle in blocklist:
        blocklist.remove(handle)
        _save_blocklist(blocklist)
    return JSONResponse(content={"status": "success", "unblocked": handle})


@router.get("/marketplace/uploader/{handle}", response_class=HTMLResponse)
async def marketplace_uploader_profile(
    request: Request, handle: str,
    preview: bool = False,
    module_manager: ModuleManager = Depends(get_module_manager),
    settings_man: SettingsManager = Depends(get_settings_manager),
):
    """Public uploader profile: all their items, stats, and reports."""
    import os
    catalog_path = data_path("marketplace", "catalog.json")
    catalog = []
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)

    their_items = [i for i in catalog if i.get("uploader_handle") == handle]

    # Aggregate stats
    total_downloads = sum(i.get("download_count", 0) for i in their_items)
    total_upvotes   = sum(i.get("upvotes", 0) for i in their_items)
    total_downvotes = sum(i.get("downvotes", 0) for i in their_items)
    all_reports = [
        {**r, "item_name": i.get("name", i["id"]), "item_id": i["id"]}
        for i in their_items
        for r in i.get("reports", [])
    ]
    total_reports = len(all_reports)

    is_actual_me = handle == get_public_handle()
    is_me = is_actual_me and not preview
    is_blocked = handle in _load_blocklist()
    owned_ids = {i["id"] for i in their_items if is_item_owner(i)}

    dl_history = {}
    dl_path = data_path("download_history.json")
    if os.path.exists(dl_path):
        try:
            with open(dl_path, "r", encoding="utf-8") as f:
                dl_history = json.load(f)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)

    # Get uploader's display username:
    # - For own handle: use local profile file (authoritative), even in preview mode
    # - For others: use username stored in their most recent item
    if is_actual_me:
        uploader_username = _get_marketplace_username()
        uploader_description = _get_marketplace_description()
    else:
        uploader_username = next((i.get("uploader_username", "") for i in reversed(their_items) if i.get("uploader_username")), "")
        uploader_description = next((i.get("uploader_description", "") for i in reversed(their_items) if i.get("uploader_description")), "")
    return templates.TemplateResponse(request, "marketplace_uploader.html", {
        "handle": handle,
        "uploader_username": uploader_username,
        "uploader_description": uploader_description,
        "items": their_items,
        "total_downloads": total_downloads,
        "total_upvotes": total_upvotes,
        "total_downvotes": total_downvotes,
        "all_reports": all_reports,
        "total_reports": total_reports,
        "report_threshold": REPORT_THRESHOLD,
        "is_me": is_me,
        "is_preview": preview,
        "is_blocked": is_blocked,
        "owned_ids": owned_ids,
        "dl_history": dl_history,
        "settings": settings_man.settings,
    })


@router.post("/marketplace/vote/{item_id}/{direction}")
async def vote_marketplace_item(item_id: str, direction: str):
    import os
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Direction must be 'up' or 'down'")

    catalog_path = data_path("marketplace", "catalog.json")
    if not os.path.exists(catalog_path):
        raise HTTPException(status_code=404, detail="Catalog not found")

    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    voter_id = make_vote_id(item_id)

    # Initialise vote fields if missing
    if "voters" not in item:
        item["voters"] = {}
    if "upvotes" not in item:
        item["upvotes"] = 0
    if "downvotes" not in item:
        item["downvotes"] = 0

    previous = item["voters"].get(voter_id)

    if previous == direction:
        # Same direction → remove vote (toggle off)
        del item["voters"][voter_id]
        if direction == "up":
            item["upvotes"] = max(0, item["upvotes"] - 1)
        else:
            item["downvotes"] = max(0, item["downvotes"] - 1)
        user_vote = None
    else:
        # Remove previous vote if any
        if previous == "up":
            item["upvotes"] = max(0, item["upvotes"] - 1)
        elif previous == "down":
            item["downvotes"] = max(0, item["downvotes"] - 1)
        # Apply new vote
        item["voters"][voter_id] = direction
        if direction == "up":
            item["upvotes"] += 1
        else:
            item["downvotes"] += 1
        user_vote = direction

    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)

    return JSONResponse(content={
        "status": "success",
        "upvotes": item["upvotes"],
        "downvotes": item["downvotes"],
        "user_vote": user_vote
    })


@router.post("/marketplace/item/{item_id}/comment")
async def add_marketplace_comment(item_id: str, text: str = Form(...)):
    """Append a comment to a marketplace item. Comments persist through owner updates."""
    import os, uuid as _uuid
    text = text.strip()
    if not text:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Comment text is required"})
    if len(text) > 1000:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Comment too long (max 1000 chars)"})

    catalog_path = data_path("marketplace", "catalog.json")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    comment = {
        "id": str(_uuid.uuid4())[:8],
        "author_handle": get_public_handle(),
        "author_username": _get_marketplace_username(),
        "text": text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    item.setdefault("comments", []).append(comment)

    tmp = catalog_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)
    os.replace(tmp, catalog_path)

    my_handle = get_public_handle()
    author_handle = comment["author_handle"]

    # Notify if someone else commented on my item
    if item.get("uploader_handle") == my_handle and author_handle != my_handle:
        _add_notif("comment", item_id, item.get("name", item_id),
                   author_handle, comment["author_username"], text)

    # Notify if comment @mentions my handle (and I'm not the commenter)
    import re as _re
    if author_handle != my_handle and _re.search(rf"@{_re.escape(my_handle)}", text, _re.IGNORECASE):
        _add_notif("mention", item_id, item.get("name", item_id),
                   author_handle, comment["author_username"], text)

    return JSONResponse(content={"status": "success", "comment": comment})


@router.delete("/marketplace/item/{item_id}/comment/{comment_id}")
async def delete_marketplace_comment(item_id: str, comment_id: str):
    """Delete a comment if it belongs to the current user."""
    catalog_path = data_path("marketplace", "catalog.json")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    my_handle = get_public_handle()
    comments = item.get("comments", [])
    target = next((c for c in comments if c["id"] == comment_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Comment not found")
    if target["author_handle"] != my_handle:
        raise HTTPException(status_code=403, detail="Cannot delete another user's comment")

    item["comments"] = [c for c in comments if c["id"] != comment_id]
    tmp = catalog_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)
    os.replace(tmp, catalog_path)
    return JSONResponse(content={"status": "success"})


@router.post("/marketplace/import/{item_id}")
async def import_marketplace_item_to_neurocore(item_id: str, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    """Import a marketplace item directly into NeuroCore (no file saved to user's disk)."""
    import os, zipfile, shutil, re

    catalog_path = data_path("marketplace", "catalog.json")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read catalog")

    item = next((i for i in catalog if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    file_path = data_path("marketplace", "uploads", item["save_filename"])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    item_type = item.get("type", "").lower()
    ext = os.path.splitext(file_path)[1].lower()
    result_message = ""

    if item_type == "skill":
        skills_data_dir = os.path.join("modules", "skills", "data")
        skills_meta_path = os.path.join(skills_data_dir, "skills_metadata.json")
        os.makedirs(skills_data_dir, exist_ok=True)

        md_content = None
        if ext in (".md", ".txt"):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                md_content = f.read()
        elif ext == ".zip":
            with zipfile.ZipFile(file_path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".md") and not name.startswith("__"):
                        md_content = zf.read(name).decode("utf-8", errors="replace")
                        break

        if not md_content:
            return JSONResponse(status_code=400, content={"status": "error", "detail": "No .md file found in the skill package"})

        skill_id = re.sub(r'[^a-zA-Z0-9\s]', '', item["name"]).lower().strip().replace(' ', '_') or item["id"]
        with open(os.path.join(skills_data_dir, f"{skill_id}.md"), "w", encoding="utf-8") as f:
            f.write(md_content)

        skills_meta: dict = {}
        try:
            if os.path.exists(skills_meta_path):
                with open(skills_meta_path, "r", encoding="utf-8") as f:
                    skills_meta = json.load(f)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)
        skills_meta[skill_id] = {
            "name": item["name"],
            "description": item.get("description", ""),
            "category": "marketplace",
            "tags": ["marketplace", "imported"],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        tmp = skills_meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(skills_meta, f, indent=4)
        os.replace(tmp, skills_meta_path)
        result_message = f"Skill '{item['name']}' imported to your skills library."

    elif item_type == "flow":
        flow_data = None
        if ext == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    flow_data = json.load(f)
            except Exception as e:
                return JSONResponse(status_code=400, content={"status": "error", "detail": f"Invalid JSON: {e}"})
        elif ext == ".zip":
            with zipfile.ZipFile(file_path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".json") and not name.startswith("__"):
                        try:
                            flow_data = json.loads(zf.read(name).decode("utf-8"))
                            break
                        except Exception as _exc:
                            logger.debug("Skipping unreadable flow in zip: %s", _exc)
                            continue

        if not flow_data or not isinstance(flow_data, dict):
            return JSONResponse(status_code=400, content={"status": "error", "detail": "No valid JSON flow file found"})

        import uuid as _uuid
        multiple = all(isinstance(v, dict) and "nodes" in v for v in flow_data.values())
        if multiple:
            entries = list(flow_data.items())
        elif "nodes" in flow_data or "name" in flow_data:
            flow_name = flow_data.get("name", item["name"])
            entries = [(re.sub(r'[^a-zA-Z0-9_]', '_', flow_name.lower()), flow_data)]
        else:
            return JSONResponse(status_code=400, content={"status": "error", "detail": "Unrecognized flow format"})

        # Validate every flow before adding any, so a bad package changes nothing.
        pending = []
        for fid, fdata in entries:
            # A name of only punctuation sanitizes to an empty id, which the store rejects.
            target_id = str(fid).strip() or f"imported_{_uuid.uuid4().hex[:8]}"
            if target_id in flow_manager.flows:
                target_id = f"{target_id}_{_uuid.uuid4().hex[:6]}"
            errors = flow_manager.validate_flow(target_id, fdata)
            if errors:
                return JSONResponse(status_code=400, content={
                    "status": "error", "detail": f"Invalid flow '{fid}': " + "; ".join(errors)})
            pending.append((target_id, fdata))

        imported_ids = [flow_manager.add_flow(target_id, fdata)["id"] for target_id, fdata in pending]
        if multiple:
            result_message = f"Imported {len(imported_ids)} flow(s) from '{item['name']}'."
        else:
            saved_name = flow_manager.get_flow(imported_ids[0])["name"]
            result_message = f"Flow '{saved_name}' imported to your flows."

        # Auto-activate the first imported flow if none are currently active
        active_flows = list(settings_man.get("active_ai_flows", []))
        if not active_flows and imported_ids:
            settings_man.save_settings({"active_ai_flows": [imported_ids[0]]})
            result_message += " Set as active flow."


    elif item_type == "module":
        if ext != ".zip":
            return JSONResponse(status_code=400, content={"status": "error", "detail": "Module must be a .zip file"})
        module_name = re.sub(r'[^a-zA-Z0-9_]', '_', item["name"].lower()).strip('_') or item["id"]
        module_target = os.path.join("modules", module_name)
        # Untrusted package: a zip-slip, absolute-path or symlink member must never write
        # outside modules/<module_name>/. See core/archive.py.
        try:
            extract_zip_safely(file_path, module_target, strip_root=True)
        except ArchiveSecurityError as exc:
            return JSONResponse(status_code=400, content={
                "status": "error", "detail": f"Unsafe module package: {exc}"})
        result_message = f"Module '{item['name']}' extracted to modules/{module_name}/. Reload modules in Settings to activate."

    elif item_type == "tool":
        import filelock as _filelock
        tools_json   = os.path.join("modules", "tools", "tools.json")
        library_dir  = os.path.join("modules", "tools", "library")
        lock_file    = tools_json + ".lock"
        os.makedirs(library_dir, exist_ok=True)

        with open(file_path, "rb") as f:
            raw = f.read()

        _lock = _filelock.FileLock(lock_file)
        with _lock:
            existing = {}
            if os.path.exists(tools_json):
                try:
                    with open(tools_json, "r", encoding="utf-8") as f:
                        existing = json.loads(f.read())
                except Exception:
                    existing = {}

            if ext == ".json":
                tool_data = json.loads(raw.decode("utf-8"))
                if isinstance(tool_data, list):
                    tool_data = tool_data[0] if tool_data else {}
                t_name = re.sub(r'[^a-zA-Z0-9_]', '_', (tool_data.get("name") or item["name"]).strip()).strip("_") or item["id"]
                existing[t_name] = {
                    "definition": {
                        "type": "function",
                        "function": {
                            "name": t_name,
                            "description": tool_data.get("description", item.get("description", "")),
                            "parameters": tool_data.get("parameters", {"type": "object", "properties": {}})
                        }
                    },
                    "enabled": tool_data.get("enabled", True)
                }
                code = tool_data.get("code", "")
                with open(os.path.join(library_dir, f"{t_name}.py"), "w", encoding="utf-8") as f:
                    f.write(code)
            elif ext == ".py":
                t_name = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(os.path.basename(file_path))[0]).strip("_") or item["id"]
                if t_name not in existing:
                    existing[t_name] = {
                        "definition": {
                            "type": "function",
                            "function": {
                                "name": t_name,
                                "description": item.get("description", "Imported tool"),
                                "parameters": {"type": "object", "properties": {}}
                            }
                        },
                        "enabled": True
                    }
                with open(os.path.join(library_dir, f"{t_name}.py"), "w", encoding="utf-8") as f:
                    f.write(raw.decode("utf-8"))
            else:
                return JSONResponse(status_code=400, content={"status": "error", "detail": "Tool must be a .json or .py file"})

            with open(tools_json, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=4)

        result_message = f"Tool '{t_name}' registered and ready in Tools."

    elif item_type == "system prompt":
        prompts_dir = data_path("marketplace", "imported_prompts")
        os.makedirs(prompts_dir, exist_ok=True)
        prompt_name = re.sub(r'[^a-zA-Z0-9\s_-]', '', item["name"]).strip()
        shutil.copy2(file_path, os.path.join(prompts_dir, f"{prompt_name}.md"))
        result_message = f"System prompt '{item['name']}' saved. Go to Settings → System Prompt to apply it."

    else:
        return JSONResponse(status_code=400, content={"status": "error", "detail": f"Unknown type: {item_type}"})

    # Record import: increment download_count + update download_history
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            cat_m = json.load(f)
        for ci in cat_m:
            if ci["id"] == item_id:
                ci["download_count"] = ci.get("download_count", 0) + 1
                break
        tmp = catalog_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cat_m, f, indent=4)
        os.replace(tmp, catalog_path)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)

    dl_history_path = data_path("download_history.json")
    try:
        dl: dict = {}
        if os.path.exists(dl_history_path):
            with open(dl_history_path, "r", encoding="utf-8") as f:
                dl = json.load(f)
        dl[item_id] = item.get("version", 1)
        tmp = dl_history_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dl, f, indent=2)
        os.replace(tmp, dl_history_path)
    except Exception as _exc:
        logger.debug("ignored exception: %s", _exc)

    return JSONResponse(content={"status": "success", "message": result_message})


@router.post("/marketplace/profile/username")
async def set_marketplace_username(username: str = Form(...)):
    """Set the user's display name shown alongside their handle."""
    import re
    username = username.strip()
    if len(username) > 30:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Username too long (max 30 chars)"})
    if username and not re.match(r'^[\w\s\-\.]+$', username):
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Only letters, numbers, spaces, hyphens, and periods allowed"})
    _save_profile_field("username", username)
    return JSONResponse(content={"status": "success", "username": username})


@router.post("/marketplace/profile/description")
async def set_marketplace_description(description: str = Form(...)):
    """Set the user's short bio shown on their uploader profile."""
    description = description.strip()
    if len(description) > 200:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Description too long (max 200 chars)"})
    _save_profile_field("description", description)
    return JSONResponse(content={"status": "success", "description": description})


@router.get("/marketplace/handles")
async def get_marketplace_handles():
    """Return all distinct uploader handles + display names from the catalog."""
    catalog_path = data_path("marketplace", "catalog.json")
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        return JSONResponse(content={"status": "success", "handles": []})
    seen = {}
    for item in catalog:
        h = item.get("uploader_handle", "")
        if h and h not in seen:
            seen[h] = item.get("uploader_username", "")
    handles = [{"handle": h, "username": u} for h, u in seen.items()]
    return JSONResponse(content={"status": "success", "handles": handles})


@router.get("/marketplace/notifications")
async def get_marketplace_notifications():
    """Return all local marketplace notifications, newest first."""
    return JSONResponse(content={"status": "success", "notifications": _load_notifs()})


@router.post("/marketplace/notifications/read")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    notifs = _load_notifs()
    for n in notifs:
        n["read"] = True
    _save_notifs(notifs)
    return JSONResponse(content={"status": "success"})


@router.post("/marketplace/notifications/read/{notif_id}")
async def mark_notification_read(notif_id: str):
    """Mark a single notification as read."""
    notifs = _load_notifs()
    for n in notifs:
        if n["id"] == notif_id:
            n["read"] = True
    _save_notifs(notifs)
    return JSONResponse(content={"status": "success"})


@router.delete("/marketplace/notifications")
async def clear_all_notifications():
    """Delete all notifications."""
    _save_notifs([])
    return JSONResponse(content={"status": "success"})


@router.post("/settings/decoder/import")
async def import_decoder_key(key: str = Form(...)):
    """
    Import a decoder key from another machine.
    The key must be a 64-char hex string (32 bytes).
    This overwrites the current decoder — all current ownership claims become unverifiable.
    """
    import os, re
    key = key.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{64}", key):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid key format. Must be 64 hex characters."})
    key_file = _decoder_key_path()
    try:
        os.makedirs(data_path(), exist_ok=True)
        tmp = key_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(key.lower())
        os.replace(tmp, key_file)
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    return JSONResponse(content={"status": "success", "message": "Decoder key imported. Restart may be needed."})


@router.post("/settings/skills/delete/{skill_id}")
async def delete_skill(skill_id: str):
    import os
    skills_path = os.path.join("modules", "skills", "data", "skills_metadata.json")
    if not os.path.exists(skills_path):
         raise HTTPException(status_code=404, detail="Skills Metadata not found")
        
    try:
         with open(skills_path, "r", encoding="utf-8") as f:
             skills = json.load(f)
    except Exception:
         raise HTTPException(status_code=500, detail="Failed to read skills metadata")
        
    if skill_id not in skills:
         raise HTTPException(status_code=404, detail="Skill not found")
        
    # Delete from JSON
    del skills[skill_id]
    with open(skills_path, "w", encoding="utf-8") as f:
         json.dump(skills, f, indent=4)
         
    # Delete MD file
    content_path = os.path.join("modules", "skills", "data", f"{skill_id}.md")
    if os.path.exists(content_path):
         os.remove(content_path)
         
    return JSONResponse(content={"status": "success", "message": "Skill deleted"})

@router.post("/settings/skills/save/{skill_id}")
async def save_skill(skill_id: str, instructions: str = Form(...)):
    import os
    content_path = os.path.join("modules", "skills", "data", f"{skill_id}.md")
    try:
         with open(content_path, "w", encoding="utf-8") as f:
             f.write(instructions)
    except Exception:
         raise HTTPException(status_code=500, detail="Failed to save skill content")
    return JSONResponse(content={"status": "success", "message": "Skill content saved"})

@router.post("/settings/skills/create")
async def create_skill(
    name: str = Form(...),
    description: str = Form(...),
    category: str = Form("general"),
    instructions: str = Form("")
):
    import os, re
    from datetime import datetime
    skill_id = re.sub(r'[^a-zA-Z0-9\s]', '', name).lower().replace(' ', '_')
    if not skill_id: raise HTTPException(status_code=400, detail="Invalid skill name")
    skills_path = os.path.join("modules", "skills", "data", "skills_metadata.json")
    try:
         if os.path.exists(skills_path):
              with open(skills_path, "r", encoding="utf-8") as f: skills = json.load(f)
         else: skills = {}
    except Exception: skills = {}
    if skill_id in skills: raise HTTPException(status_code=400, detail="Skill already exists")
    skills[skill_id] = {
         "name": name, "description": description, "category": category,
         "tags": [], "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat()
    }
    try:
         with open(skills_path, "w", encoding="utf-8") as f: json.dump(skills, f, indent=4)
         content_path = os.path.join("modules", "skills", "data", f"{skill_id}.md")
         with open(content_path, "w", encoding="utf-8") as f: f.write(instructions)
    except Exception: raise HTTPException(status_code=500, detail="Failed to create skill assets")
    return JSONResponse(content={"status": "success", "message": "Skill created", "id": skill_id})

@router.post("/settings/skills/upload_to_marketplace/{skill_id}")
async def upload_skill_to_marketplace(skill_id: str):
    import os, json, uuid, shutil
    from datetime import datetime
    skills_path = os.path.join("modules", "skills", "data", "skills_metadata.json")
    content_path = os.path.join("modules", "skills", "data", f"{skill_id}.md")
    if not os.path.exists(skills_path) or not os.path.exists(content_path): raise HTTPException(status_code=404, detail="Skill assets not found")
    import hashlib
    try:
         with open(skills_path, "r", encoding="utf-8") as f: skills = json.load(f)
    except Exception: raise HTTPException(status_code=500, detail="Failed to read skills metadata")
    skill = skills.get(skill_id)
    if not skill: raise HTTPException(status_code=404, detail="Skill not found")
    
    with open(content_path, "r", encoding="utf-8") as f: content_str = f.read()
    content_hash = hashlib.md5(content_str.encode("utf-8")).hexdigest()

    catalog_path = data_path("marketplace", "catalog.json")
    upload_dir = data_path("marketplace", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    try:
         if os.path.exists(catalog_path):
              with open(catalog_path, "r", encoding="utf-8") as f: catalog = json.load(f)
         else: catalog = []
    except Exception: catalog = []
    
    # Deduplicate: Check if already exists in catalog
    existing_item = next((i for i in catalog if i.get("type") == "skill" and i.get("filename") == f"{skill_id}.md"), None)
    
    item_id = existing_item["id"] if existing_item else str(uuid.uuid4())
    shutil.copy(content_path, os.path.join(upload_dir, f"{item_id}.md"))
    
    if existing_item:
         # Update existing
         existing_item["name"] = skill.get("name", skill_id)
         existing_item["description"] = skill.get("description", "")
         existing_item["content_hash"] = content_hash
         existing_item["uploaded_at"] = datetime.now().isoformat()
    else:
         # Append new
         catalog.append({
              "id": item_id, "name": skill.get("name", skill_id), "description": skill.get("description", ""),
              "type": "skill", "filename": f"{skill_id}.md", "save_filename": f"{item_id}.md", 
              "uploaded_at": datetime.now().isoformat(),
              "content_hash": content_hash,
              "claim": make_claim(item_id),
              "uploader_handle": get_public_handle(),
              "version": 1,
         })
         
    with open(catalog_path, "w", encoding="utf-8") as f: json.dump(catalog, f, indent=4)
    return JSONResponse(content={"status": "success", "message": "Uploaded to Marketplace", "id": item_id})

@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request, settings_man: SettingsManager = Depends(get_settings_manager), module_manager: ModuleManager = Depends(get_module_manager)):
    import os
    system_info = {
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "processor": platform.processor() or "Unknown"
    }
    
    skills = {}
    skills_path = os.path.join("modules", "skills", "data", "skills_metadata.json")
    import hashlib
    if os.path.exists(skills_path):
         try:
             with open(skills_path, "r", encoding="utf-8") as f:
                 skills = json.load(f)
             for skill_id, skill_data in skills.items():
                 content_path = os.path.join("modules", "skills", "data", f"{skill_id}.md")
                 if os.path.exists(content_path):
                      with open(content_path, "r", encoding="utf-8") as f_content:
                          instructions = f_content.read()
                          skill_data["instructions"] = instructions
                          skill_data["content_hash"] = hashlib.md5(instructions.encode("utf-8")).hexdigest()
         except Exception as _exc: logger.debug("ignored exception: %s", _exc)
         
    catalog = []
    catalog_path = data_path("marketplace", "catalog.json")
    if os.path.exists(catalog_path):
         try:
              with open(catalog_path, "r", encoding="utf-8") as f: catalog = json.load(f)
         except Exception as _exc: logger.debug("ignored exception: %s", _exc)

    return templates.TemplateResponse(request, "settings.html", {
        "settings": settings_man.settings, "modules": module_manager.get_all_modules(),
        "system_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "system_info": system_info,
        "hardware_id": get_hardware_id(),
        "decoder_key": get_decoder().hex().upper(),
        "public_handle": get_public_handle(),
        "skills": skills,
        "marketplace_catalog": catalog
    })

@router.get("/system-time", response_class=HTMLResponse)
async def get_system_time(request: Request):
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

@router.get("/settings/modules-nav", response_class=HTMLResponse)
async def get_settings_modules_nav(request: Request, module_manager: ModuleManager = Depends(get_module_manager)):
    return templates.TemplateResponse(request, "settings_modules_nav.html", {"modules": module_manager.get_all_modules()})

@router.get("/footer", response_class=HTMLResponse)
async def get_footer(request: Request, settings_man: SettingsManager = Depends(get_settings_manager)):
    return templates.TemplateResponse(request, "footer.html", {"settings": settings_man.settings})

@router.get("/settings/export/config")
async def export_config(settings_man: SettingsManager = Depends(get_settings_manager)):
    """Downloads the current settings.json file, without plaintext secrets by default.

    Secrets are dropped rather than masked so re-importing the file never overwrites a real key
    with a placeholder. Set NEUROCORE_EXPORT_SECRETS=1 to include them (e.g. for a full backup).
    """
    from core.security import export_includes_secrets, strip_secrets
    content = settings_man.settings if export_includes_secrets() else strip_secrets(settings_man.settings)
    return JSONResponse(
        content=content,
        headers={"Content-Disposition": 'attachment; filename="neurocore_settings.json"'}
    )

@router.post("/settings/import/config")
async def import_config(request: Request, file: UploadFile = File(...), settings_man: SettingsManager = Depends(get_settings_manager)):
    """Imports settings from a JSON file."""
    try:
        content = await file.read()
        new_settings = json.loads(content)
        if not isinstance(new_settings, dict):
             return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid format: Root must be a dictionary"}})})
        
        settings_man.save_settings(new_settings)
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"settingsChanged": None, "showMessage": {"level": "success", "message": "Configuration imported successfully"}})})
    except json.JSONDecodeError:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid JSON file"}})})
    except (OSError, PermissionError, TypeError, KeyError, ValueError) as e:
        # OSError/PermissionError: File system issues during import
        # TypeError/KeyError: Invalid settings structure
        # ValueError: Invalid settings values (from _validate_settings)
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Import failed: {e}"}})})


@router.get("/settings/export/flows")
async def export_flows():
    """Downloads the current ai_flows.json file."""
    # Use thread-safe method to get flows dict with lock held
    data = flow_manager.get_all_flows_dict()
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": 'attachment; filename="ai_flows_backup.json"'}
    )

@router.post("/settings/import/flows")
async def import_flows(request: Request, file: UploadFile = File(...)):
    """Imports flows from a JSON file."""
    try:
        content = await file.read()
        flows_data = json.loads(content)
        if not isinstance(flows_data, dict):
             return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid format: Root must be a dictionary"}})})
        
        result = flow_manager.import_flows(flows_data)
        if not result.get("success"):
            errors = "; ".join(str(item) for item in result.get("errors", []))[:300]
            return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Import failed: {errors}"}})})
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Flows imported successfully"}})})
    except json.JSONDecodeError:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid JSON file"}})})
    except (OSError, PermissionError, TypeError, KeyError, ValueError) as e:
        # OSError/PermissionError: File system issues during import
        # TypeError/KeyError: Invalid flow data structure
        # ValueError: Invalid flow ID or configuration values
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Import failed: {e}"}})})


@router.post("/settings/reset")
async def reset_settings(request: Request, confirm: str = None,
                         settings_man: SettingsManager = Depends(get_settings_manager)):
    """Resets settings to defaults."""
    from core.confirmations import require_confirmation
    from core.settings import DEFAULT_SETTINGS

    require_confirmation(confirm, "reset all settings", "resetting settings")
    # Preserve the file path but overwrite content
    settings_man.save_settings(DEFAULT_SETTINGS)
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({"settingsChanged": None, "showMessage": {"level": "success", "message": "Settings reset to defaults"}})})

@router.post("/settings/save")
async def save_settings_route(request: Request, settings_man: SettingsManager = Depends(get_settings_manager)):
    form_data = await request.form()
    updates = {}
    
    # Handle text fields (update only if present)
    text_fields = ["llm_api_url", "llm_api_key", "embedding_api_url", "default_model", "embedding_model"]
    for field in text_fields:
        if field in form_data:
            updates[field] = form_data[field]
    
    # Handle numeric fields
    for field in ["temperature", "max_tokens"]:
        if field in form_data:
            try:
                updates[field] = float(form_data[field])
            except (ValueError, TypeError) as _exc:
                logger.debug("ignored exception: %s", _exc)
            
    if "request_timeout" in form_data:
        try:
            updates["request_timeout"] = float(form_data["request_timeout"])
        except (ValueError, TypeError) as _exc:
            logger.debug("ignored exception: %s", _exc)
            
    if "max_node_loops" in form_data:
        try:
            updates["max_node_loops"] = int(form_data["max_node_loops"])
        except (ValueError, TypeError) as _exc:
            logger.debug("ignored exception: %s", _exc)
            
    # Handle debug_mode checkbox (only if the form intended to submit it)
    if "save_debug_mode" in form_data:
        updates["debug_mode"] = form_data.get("debug_mode") == "on"
    
    if "save_ui_wide_mode" in form_data:
        updates["ui_wide_mode"] = form_data.get("ui_wide_mode") == "on"
        
    if "save_ui_show_footer" in form_data:
        updates["ui_show_footer"] = form_data.get("ui_show_footer") == "on"

    # Catch validation errors and return 400 with helpful message
    try:
        settings_man.save_settings(updates)
    except ValueError as e:
        return Response(
            status_code=400, 
            headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Invalid settings: {str(e)}"}})}
        )
    
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({"settingsChanged": None, "showMessage": {"level": "success", "message": "Settings saved successfully"}})})

# --- Debug ---

@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request, settings_man: SettingsManager = Depends(get_settings_manager), module_manager: ModuleManager = Depends(get_module_manager)):
    if not settings_man.get("debug_mode"):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "debug.html", {"settings": settings_man.settings, "modules": module_manager.get_all_modules()})

@router.get("/debug/logs", response_class=HTMLResponse)
async def get_debug_logs(request: Request, _: bool = Depends(require_debug_mode)):
    return templates.TemplateResponse(request, "debug_logs.html", {"logs": debug_logger.get_logs()})

@router.get("/debug/events", response_class=JSONResponse)
async def get_debug_events(request: Request, since: float = 0, _: bool = Depends(require_debug_mode)):
    return debug_logger.get_recent_logs(since)

@router.get("/debug/agent-summary", response_class=JSONResponse)
async def get_agent_summary(request: Request, since: float = None, limit: int = 5, _: bool = Depends(require_debug_mode)):
    """Get a summary of the last agent session trace events.
    
    Query params:
        since: Optional time window in seconds (e.g., 300 = last 5 minutes, 900 = last 15 minutes)
        limit: Number of recent events to include (default: 5)
    """
    from core.session_manager import session_manager
    
    # Convert relative time (seconds) to absolute timestamp
    since_ts = None
    if since is not None:
        since_ts = time.time() - since
    
    summary = session_manager.get_trace_summary(limit=limit, since=since_ts)
    return JSONResponse(content=summary)

@router.get("/debug/summary", response_class=HTMLResponse)
async def get_debug_summary(request: Request, since: float = None, limit: int = 5, _: bool = Depends(require_debug_mode)):
    """Get the agent summary panel for the debug page.

    Query params:
        since: Optional time window in seconds (e.g., 300 = last 5 minutes, 900 = last 15 minutes)
        limit: Number of recent events to include (default: 5)
    """
    from core.session_manager import session_manager
    from core.observability import get_token_stats

    # Convert relative time (seconds) to absolute timestamp
    since_ts = None
    if since is not None:
        since_ts = time.time() - since

    summary = session_manager.get_trace_summary(limit=limit, since=since_ts)
    return templates.TemplateResponse(
        request, "debug_summary.html",
        {"summary": summary, "stats": get_token_stats()},
    )

@router.get("/debug/token-stats", response_class=JSONResponse)
async def get_token_stats_json(request: Request, _: bool = Depends(require_debug_mode)):
    """Return LLM token usage counters (total + per-model) as JSON."""
    from core.observability import get_token_stats
    return JSONResponse(content=get_token_stats())

@router.get("/debug/token-stats/partial", response_class=HTMLResponse)
async def get_token_stats_partial(request: Request, _: bool = Depends(require_debug_mode)):
    """Return the token stats panel as an HTML partial for HTMX polling."""
    from core.observability import get_token_stats
    return templates.TemplateResponse(
        request, "debug_token_stats.html", {"stats": get_token_stats()}
    )

@router.post("/debug/clear")
async def clear_debug_logs(request: Request, _: bool = Depends(require_debug_mode)):
    debug_logger.clear()
    return templates.TemplateResponse(request, "debug_logs.html", {"logs": []})

# --- Goals ---

@router.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request, module_manager: ModuleManager = Depends(get_module_manager), settings_man: SettingsManager = Depends(get_settings_manager)):
    return templates.TemplateResponse(request, "goals.html", {
        "settings": settings_man.settings,
        "modules": module_manager.get_all_modules(),
    })


@router.get("/modules/{module_id}/default-config")
async def get_default_config(module_id: str, module_manager: ModuleManager = Depends(get_module_manager)):
    """Returns the default configuration for modules that support loading defaults (e.g., agent_loop)."""
    from pathlib import Path
    
    module = module_manager.modules.get(module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    
    # Only support agent_loop for now
    if module_id != 'agent_loop':
        raise HTTPException(status_code=400, detail="Module does not support loading default config")
    
    # Read the module.json file to get default config
    module_dir = Path(__file__).resolve().parent.parent / "modules" / module_id
    module_json_path = module_dir / "module.json"
    
    if not module_json_path.exists():
        raise HTTPException(status_code=404, detail="Module configuration file not found")
    
    try:
        with open(module_json_path, 'r', encoding="utf-8") as f:
            module_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        raise HTTPException(status_code=500, detail="Failed to read module configuration")
    
    # Extract default values from config schema
    config_schema = module_data.get('config', {})
    default_config = {}

    for key, value in config_schema.items():
        if isinstance(value, dict) and 'value' in value:
            default_config[key] = value['value']
        else:
            # For flat config values (like in chat module)
            default_config[key] = value

    return JSONResponse(content=default_config)


# --- Research ---

@router.get("/research/sources")
async def research_source_list():
    """Allowlisted literature sources and their licence terms."""
    from core.research_gateway import sources

    return {"sources": sources()}


@router.post("/research/fetch")
async def research_source_fetch(request: Request):
    """Search one allowlisted source; provenance is returned with every item."""
    from core.research_gateway import SourcePolicyError, SourceUnavailableError, search

    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        limit = int(body.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    try:
        return await search(
            str(body.get("source", "")), str(body.get("query", "")), limit,
            persist=bool(body.get("persist", False)),
        )
    except SourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/research", response_class=HTMLResponse)
async def get_research(request: Request, settings_man: SettingsManager = Depends(get_settings_manager), module_manager: ModuleManager = Depends(get_module_manager)):
    modules = module_manager.get_all_modules()
    return templates.TemplateResponse(request, "research.html", {
        "modules": modules,
        "settings": settings_man.settings,
    })


@router.get("/research/stats", response_class=HTMLResponse)
async def get_research_stats(request: Request, rm: ResearchManager = Depends(get_research_manager)):
    stats = rm.get_stats()
    return templates.TemplateResponse(request, "research_stats.html", {"stats": stats})


# --- Hypotheses ---

@router.get("/research/hypotheses", response_class=HTMLResponse)
async def list_hypotheses(request: Request, status: str = "", domain: str = "", rm: ResearchManager = Depends(get_research_manager)):
    hypotheses = rm.get_hypotheses(status=status or None, domain=domain or None)
    return templates.TemplateResponse(request, "research_hypotheses.html", {"hypotheses": hypotheses})


@router.get("/research/hypotheses/{hypothesis_id}/edit", response_class=HTMLResponse)
async def edit_hypothesis_form(request: Request, hypothesis_id: str, rm: ResearchManager = Depends(get_research_manager)):
    h = rm.get_hypothesis(hypothesis_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hypothesis not found")
    return templates.TemplateResponse(request, "research_hypothesis_edit.html", {"h": h})


@router.post("/research/hypotheses", response_class=HTMLResponse)
async def create_hypothesis(
    request: Request,
    title: str = Form(...),
    statement: str = Form(...),
    hypothesis_type: str = Form("correlation"),
    domain: str = Form("general"),
    independent_variable: str = Form(""),
    dependent_variable: str = Form(""),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    rm.create_hypothesis({
        "title": title,
        "statement": statement,
        "hypothesis_type": hypothesis_type,
        "domain": domain,
        "independent_variable": independent_variable or None,
        "dependent_variable": dependent_variable or None,
        "notes": notes or None,
    })
    hypotheses = rm.get_hypotheses()
    return templates.TemplateResponse(request, "research_hypotheses.html", {"hypotheses": hypotheses})


@router.put("/research/hypotheses/{hypothesis_id}", response_class=HTMLResponse)
async def update_hypothesis(
    request: Request,
    hypothesis_id: str,
    title: str = Form(...),
    statement: str = Form(...),
    hypothesis_type: str = Form("correlation"),
    status: str = Form("proposed"),
    domain: str = Form("general"),
    independent_variable: str = Form(""),
    dependent_variable: str = Form(""),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    rm.update_hypothesis(hypothesis_id, {
        "title": title,
        "statement": statement,
        "hypothesis_type": hypothesis_type,
        "status": status,
        "domain": domain,
        "independent_variable": independent_variable or None,
        "dependent_variable": dependent_variable or None,
        "notes": notes or None,
    })
    hypotheses = rm.get_hypotheses()
    return templates.TemplateResponse(request, "research_hypotheses.html", {"hypotheses": hypotheses})


@router.delete("/research/hypotheses/{hypothesis_id}")
async def delete_hypothesis(hypothesis_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_hypothesis(hypothesis_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Hypothesis not found")
    return Response(status_code=200)


# --- Articles ---

@router.get("/research/articles", response_class=HTMLResponse)
async def list_articles(request: Request, domain: str = "", article_type: str = "", q: str = "", rm: ResearchManager = Depends(get_research_manager)):
    articles = rm.get_articles(domain=domain or None, article_type=article_type or None)
    if q:
        q_lower = q.lower()
        articles = [
            a for a in articles
            if q_lower in a["title"].lower()
            or any(q_lower in author.lower() for author in (a.get("authors") or []))
        ]
    return templates.TemplateResponse(request, "research_articles.html", {"articles": articles})


@router.post("/research/articles", response_class=HTMLResponse)
async def create_article(
    request: Request,
    title: str = Form(...),
    authors: str = Form(""),
    year: str = Form(""),
    journal: str = Form(""),
    doi: str = Form(""),
    url: str = Form(""),
    abstract: str = Form(""),
    article_type: str = Form("research"),
    domain: str = Form("general"),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    authors_list = [a.strip() for a in authors.split(",") if a.strip()] if authors else []
    rm.create_article({
        "title": title,
        "authors": authors_list,
        "year": int(year) if year.strip() else None,
        "journal": journal or None,
        "doi": doi or None,
        "url": url or None,
        "abstract": abstract or None,
        "article_type": article_type,
        "domain": domain,
        "notes": notes or None,
    })
    articles = rm.get_articles()
    return templates.TemplateResponse(request, "research_articles.html", {"articles": articles})


@router.delete("/research/articles/{article_id}")
async def delete_article(article_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_article(article_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Article not found")
    return Response(status_code=200)


# --- Study Designs ---

@router.get("/research/study-designs", response_class=HTMLResponse)
async def list_study_designs(request: Request, hypothesis_id: str = "", status: str = "", rm: ResearchManager = Depends(get_research_manager)):
    studies = rm.get_study_designs(hypothesis_id=hypothesis_id or None, status=status or None)
    return templates.TemplateResponse(request, "research_studies.html", {"studies": studies})


@router.post("/research/study-designs", response_class=HTMLResponse)
async def create_study_design(
    request: Request,
    title: str = Form(...),
    study_type: str = Form(...),
    population: str = Form(...),
    sample_size: str = Form(""),
    sampling_method: str = Form("random"),
    design_type: str = Form(""),
    control_group: str = Form(""),
    randomization: str = Form(""),
    blinding: str = Form(""),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    rm.create_study_design({
        "title": title,
        "study_type": study_type,
        "population": population,
        "sample_size": int(sample_size) if sample_size.strip() else None,
        "sampling_method": sampling_method,
        "design_type": design_type or None,
        "control_group": control_group == "true",
        "randomization": randomization == "true",
        "blinding": blinding or None,
        "notes": notes or None,
    })
    studies = rm.get_study_designs()
    return templates.TemplateResponse(request, "research_studies.html", {"studies": studies})


@router.put("/research/study-designs/{study_id}")
async def update_study_design(study_id: str, status: str = Form(...), notes: str = Form(""), rm: ResearchManager = Depends(get_research_manager)):
    updated = rm.update_study_design(study_id, {"status": status, "notes": notes or None})
    if not updated:
        raise HTTPException(status_code=404, detail="Study design not found")
    return JSONResponse(content={"status": "success"})


@router.delete("/research/study-designs/{study_id}")
async def delete_study_design(study_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_study_design(study_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Study design not found")
    return Response(status_code=200)


# --- Findings ---

@router.get("/research/findings", response_class=HTMLResponse)
async def list_findings(request: Request, hypothesis_id: str = "", study_id: str = "", significance: str = "", rm: ResearchManager = Depends(get_research_manager)):
    findings = rm.get_findings(hypothesis_id=hypothesis_id or None, study_id=study_id or None)
    if significance:
        findings = [f for f in findings if f["significance"] == significance]
    return templates.TemplateResponse(request, "research_findings.html", {"findings": findings})


@router.post("/research/findings", response_class=HTMLResponse)
async def create_finding(
    request: Request,
    title: str = Form(...),
    summary: str = Form(...),
    conclusion: str = Form(...),
    statistical_test: str = Form(""),
    p_value: str = Form(""),
    effect_size: str = Form(""),
    significance: str = Form("not_significant"),
    status: str = Form("preliminary"),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    rm.create_finding({
        "title": title,
        "summary": summary,
        "conclusion": conclusion,
        "statistical_test": statistical_test or None,
        "p_value": float(p_value) if p_value.strip() else None,
        "effect_size": float(effect_size) if effect_size.strip() else None,
        "significance": significance,
        "status": status,
        "notes": notes or None,
    })
    findings = rm.get_findings()
    return templates.TemplateResponse(request, "research_findings.html", {"findings": findings})


@router.delete("/research/findings/{finding_id}")
async def delete_finding(finding_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_finding(finding_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Finding not found")
    return Response(status_code=200)


# --- Paper Writer ---

@router.post("/research/paper/draft")
async def draft_paper(
    title: str = Form(""),
    domain: str = Form("general"),
    section: str = Form("abstract"),
    context: str = Form(...),
    style: str = Form("academic"),
    llm: LLMBridge = Depends(get_llm_bridge),
    settings_man: SettingsManager = Depends(get_settings_manager),
):
    section_labels = {
        "abstract": "Abstract",
        "introduction": "Introduction",
        "literature_review": "Literature Review",
        "methodology": "Methodology",
        "results": "Results",
        "discussion": "Discussion",
        "conclusion": "Conclusion",
        "full_paper": "Full Paper",
    }
    style_instructions = {
        "academic": "Write in formal academic prose with precise language, third-person perspective, and passive voice where appropriate.",
        "technical": "Write in precise technical language with clear structure and exact terminology.",
        "concise": "Write concisely. Be brief and direct. Avoid padding.",
        "detailed": "Write in comprehensive detail, covering all aspects thoroughly.",
    }
    section_label = section_labels.get(section, section)
    style_instruction = style_instructions.get(style, "Write in academic style.")
    paper_title = f'Paper title: "{title}"\n' if title else ""
    prompt = (
        f"You are an expert academic writer in the field of {domain}.\n"
        f"{paper_title}"
        f"Write the {section_label} section for a research paper.\n\n"
        f"Context and key points provided by the researcher:\n{context}\n\n"
        f"{style_instruction}\n"
        f"Output only the {section_label} text itself, no meta-commentary."
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        response = await llm.chat(
            messages=messages,
            model=settings_man.get("default_model", ""),
            temperature=0.7,
            max_tokens=settings_man.get("max_tokens", 2048),
        )
        draft = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return JSONResponse(content={"draft": draft})
    except Exception as e:
        return JSONResponse(content={"error": str(e)})


# --- Experiments (Interventions / Metrics / Logs / Events / Analysis) ---

@router.get("/research/experiments", response_class=HTMLResponse)
async def get_experiments(request: Request, rm: ResearchManager = Depends(get_research_manager)):
    interventions = rm.get_interventions()
    return templates.TemplateResponse(request, "research_experiments.html", {"interventions": interventions})


@router.post("/research/interventions", response_class=HTMLResponse)
async def create_intervention(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    start_date: str = Form(...),
    end_date_projected: str = Form(""),
    end_date_actual: str = Form(""),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    rm.create_intervention({
        "name": name,
        "description": description or None,
        "start_date": start_date,
        "end_date_projected": end_date_projected or None,
        "end_date_actual": end_date_actual or None,
        "notes": notes or None,
    })
    return Response(status_code=200)


@router.put("/research/interventions/{intervention_id}")
async def update_intervention(
    intervention_id: str,
    end_date_actual: str = Form(""),
    name: str = Form(""),
    description: str = Form(""),
    start_date: str = Form(""),
    end_date_projected: str = Form(""),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    data = {}
    if end_date_actual:
        data["end_date_actual"] = end_date_actual
    if name:
        data["name"] = name
    if description:
        data["description"] = description
    if start_date:
        data["start_date"] = start_date
    if end_date_projected:
        data["end_date_projected"] = end_date_projected
    if notes:
        data["notes"] = notes
    updated = rm.update_intervention(intervention_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Intervention not found")
    return JSONResponse(content={"status": "success"})


@router.delete("/research/interventions/{intervention_id}")
async def delete_intervention(intervention_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_intervention(intervention_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Intervention not found")
    return Response(status_code=200)


@router.get("/research/interventions/{intervention_id}/export")
async def export_intervention_csv(intervention_id: str, rm: ResearchManager = Depends(get_research_manager)):
    iv = rm.get_intervention(intervention_id)
    if not iv:
        raise HTTPException(status_code=404, detail="Intervention not found")
    csv_data = rm.export_csv(intervention_id)
    filename = iv["name"].replace(" ", "_").lower() + "_data.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/research/metrics", response_class=HTMLResponse)
async def list_metrics_html(request: Request, rm: ResearchManager = Depends(get_research_manager)):
    metrics = rm.get_metrics()
    return templates.TemplateResponse(request, "research_metrics_list.html", {"metrics": metrics})


@router.get("/research/metrics/json")
async def list_metrics_json(rm: ResearchManager = Depends(get_research_manager)):
    return JSONResponse(content=rm.get_metrics())


@router.post("/research/metrics")
async def create_metric(
    name: str = Form(...),
    description: str = Form(""),
    unit: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    try:
        metric = rm.create_metric({"name": name, "description": description or None, "unit": unit or None})
        return JSONResponse(content=metric)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/research/metrics/{metric_id}")
async def delete_metric(metric_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_metric(metric_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Metric not found")
    return Response(status_code=200)


@router.post("/research/metric-logs")
async def log_metric(
    intervention_id: str = Form(...),
    metric_name: str = Form(...),
    log_date: str = Form(...),
    value: str = Form(...),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    try:
        entry = rm.log_metric({
            "intervention_id": intervention_id,
            "metric_name": metric_name,
            "log_date": log_date,
            "value": float(value),
            "notes": notes or None,
        })
        return JSONResponse(content=entry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/research/metric-logs/{intervention_id}", response_class=HTMLResponse)
async def get_metric_logs(request: Request, intervention_id: str, metric: str = "", rm: ResearchManager = Depends(get_research_manager)):
    logs = rm.get_metric_logs(intervention_id, metric_name=metric or None)
    return templates.TemplateResponse(request, "research_logs_list.html", {"logs": logs})


@router.delete("/research/metric-logs/{log_id}")
async def delete_metric_log(log_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_metric_log(log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return Response(status_code=200)


@router.post("/research/events")
async def log_event(
    intervention_id: str = Form(...),
    name: str = Form(...),
    event_datetime: str = Form(...),
    severity: str = Form("low"),
    notes: str = Form(""),
    rm: ResearchManager = Depends(get_research_manager),
):
    event = rm.log_event({
        "intervention_id": intervention_id,
        "name": name,
        "event_datetime": event_datetime,
        "severity": severity,
        "notes": notes or None,
    })
    return JSONResponse(content=event)


@router.get("/research/events/{intervention_id}", response_class=HTMLResponse)
async def get_events(request: Request, intervention_id: str, rm: ResearchManager = Depends(get_research_manager)):
    events = rm.get_events(intervention_id)
    return templates.TemplateResponse(request, "research_events_list.html", {"events": events})


@router.delete("/research/events/{event_id}")
async def delete_event(event_id: str, rm: ResearchManager = Depends(get_research_manager)):
    deleted = rm.delete_event(event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")
    return Response(status_code=200)


@router.post("/research/experiments/analyze")
async def analyze_experiment(
    intervention_id: str = Form(...),
    metric_name: str = Form(...),
    start_date: str = Form(...),
    baseline_days: int = Form(14),
    intervention_days: int = Form(14),
    alpha: float = Form(0.05),
    rm: ResearchManager = Depends(get_research_manager),
):
    from datetime import date, timedelta
    from core.statistics_engine import intervention_analysis

    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        return JSONResponse(content={"error": f"Invalid start date: {start_date}"})

    baseline_start = start - timedelta(days=baseline_days)
    intervention_end = start + timedelta(days=intervention_days)

    all_logs = rm.get_metric_logs(intervention_id, metric_name=metric_name)

    baseline_vals = [
        log["value"] for log in all_logs
        if baseline_start <= date.fromisoformat(log["log_date"]) < start
    ]
    intervention_vals = [
        log["value"] for log in all_logs
        if start <= date.fromisoformat(log["log_date"]) <= intervention_end
    ]

    if len(baseline_vals) < 2:
        return JSONResponse(content={"error": f"Not enough baseline data (found {len(baseline_vals)} points, need ≥ 2). Log data before the start date."})
    if len(intervention_vals) < 2:
        return JSONResponse(content={"error": f"Not enough intervention data (found {len(intervention_vals)} points, need ≥ 2). Log data after the start date."})

    try:
        result = intervention_analysis(baseline_vals, intervention_vals, alpha=alpha)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)})


# --- Ad-hoc Statistics ---

@router.post("/research/statistics/analyze")
async def analyze_statistics(
    type: str = Form(...),
    data_a: str = Form(...),
    data_b: str = Form(""),
    mu: float = Form(0.0),
    alpha: float = Form(0.05),
    confidence: float = Form(0.95),
):
    import core.statistics_engine as se

    try:
        a = se.parse_data(data_a)
    except ValueError as e:
        return JSONResponse(content={"error": f"Dataset A: {e}"})

    b = None
    if data_b.strip():
        try:
            b = se.parse_data(data_b)
        except ValueError as e:
            return JSONResponse(content={"error": f"Dataset B: {e}"})

    try:
        if type == "descriptive":
            result = se.descriptive_stats(a)
            result["test"] = "Descriptive Statistics"
        elif type == "one_sample_ttest":
            result = se.one_sample_ttest(a, mu=mu, alpha=alpha)
        elif type == "two_sample_ttest":
            if b is None:
                return JSONResponse(content={"error": "Dataset B is required."})
            result = se.two_sample_ttest(a, b, equal_var=False, alpha=alpha)
        elif type == "paired_ttest":
            if b is None:
                return JSONResponse(content={"error": "Dataset B is required."})
            result = se.paired_ttest(a, b, alpha=alpha)
        elif type == "mann_whitney":
            if b is None:
                return JSONResponse(content={"error": "Dataset B is required."})
            result = se.mann_whitney(a, b, alpha=alpha)
        elif type == "pearson":
            if b is None:
                return JSONResponse(content={"error": "Dataset B is required."})
            result = se.pearson_correlation(a, b, alpha=alpha)
        elif type == "spearman":
            if b is None:
                return JSONResponse(content={"error": "Dataset B is required."})
            result = se.spearman_correlation(a, b, alpha=alpha)
        elif type == "chi_square":
            result = se.chi_square_goodness(a, expected=b, alpha=alpha)
        elif type == "anova":
            if b is None:
                return JSONResponse(content={"error": "At least 2 groups required. Put group 2 in Dataset B."})
            result = se.one_way_anova(a, b, alpha=alpha)
        elif type == "confidence_interval":
            result = se.confidence_interval(a, confidence=confidence)
            result["test"] = "Confidence Interval"
        elif type == "intervention":
            if b is None:
                return JSONResponse(content={"error": "Dataset B (intervention) is required."})
            result = se.intervention_analysis(a, b, alpha=alpha)
        else:
            return JSONResponse(content={"error": f"Unknown test type: {type}"})

        return JSONResponse(content=result)
    except ValueError as e:
        return JSONResponse(content={"error": str(e)})
    except Exception as e:
        return JSONResponse(content={"error": f"Analysis failed: {e}"})
