import logging
import os
import json
import asyncio
import re
import time
import filelock
from fastapi import APIRouter, HTTPException, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from core.settings import settings
from modules.tools import approvals, calls, outbound
from modules.tools.tiers import Tier
from modules.tools.capabilities import (
    MANIFEST_VERSION,
    describe_supported,
    manifest_from_legacy,
    parse_requirements,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")
TOOLS_FILE = os.path.join(os.path.dirname(__file__), "tools.json")
LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "library")
TOOLS_LOCK_FILE = TOOLS_FILE + ".lock"

os.makedirs(LIBRARY_DIR, exist_ok=True)


def _sanitize_tool_name(name: str) -> str:
    """Sanitize tool name to prevent path traversal attacks.

    Removes any path separators, parent directory references, and other
    characters that could be used for path traversal.

    Args:
        name: The raw tool name from user input.

    Returns:
        Sanitized tool name safe for filesystem operations.

    Raises:
        ValueError: If the sanitized name is empty or invalid.
    """
    if not name:
        raise ValueError("Tool name cannot be empty")

    # Remove path separators and parent directory references
    sanitized = name.replace("/", "").replace("\\", "")
    sanitized = sanitized.replace("..", "")

    # Remove any characters that could be used for path traversal
    # Only allow alphanumeric, underscore, hyphen, and spaces
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in ("_", "-", " "))

    # Strip leading/trailing whitespace
    sanitized = sanitized.strip()

    if not sanitized:
        raise ValueError("Invalid tool name after sanitization")

    return sanitized


# L-5: providers reject function names outside ^[A-Za-z0-9_-]{1,64}$, so a
# tool saved with spaces breaks every request that offers it. New names are
# validated strictly; stored legacy names keep the lenient sanitizer so they
# can still be opened and renamed away.
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_tool_name(name: str) -> str:
    """Reject names no provider will accept as a function name."""
    cleaned = (name or "").strip()
    if not _TOOL_NAME_RE.match(cleaned):
        raise ValueError(
            "Why: tool names must match ^[A-Za-z0-9_-]{1,64}$ or providers "
            f"reject every request offering the tool (got {name!r}). "
            "Fix: rename with letters, digits, underscore or hyphen (max 64)."
        )
    return cleaned


def _library_path(name: str) -> str:
    """Resolve ``library/<name>.py`` case-insensitively (M-TOOL-3).

    Exact match wins; on case-sensitive filesystems fall back to a
    case-insensitive match so ``Calculator`` resolves ``calculator.py``
    and vice versa until the tree is renamed to canonical case.
    """
    exact = os.path.join(LIBRARY_DIR, f"{name}.py")
    if os.path.isfile(exact):
        return exact
    lowered = f"{name}.py".lower()
    try:
        for entry in os.listdir(LIBRARY_DIR):
            if entry.lower() == lowered and os.path.isfile(os.path.join(LIBRARY_DIR, entry)):
                return os.path.join(LIBRARY_DIR, entry)
    except OSError:
        pass
    return exact

def _quarantine_corrupt_tools() -> str:
    """Move a corrupt tools.json aside so it can never be overwritten silently."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    dest = f"{TOOLS_FILE}.corrupt.{stamp}"
    try:
        os.replace(TOOLS_FILE, dest)
    except OSError:
        dest = ""
    logger.error(
        "tools.json is corrupt; quarantined to %s. Refusing to load as empty "
        "(M-DATA-1). Restore the backup or delete the quarantine to start fresh.",
        dest or "(quarantine failed)",
    )
    return dest


def _load_tools_locked():
    """Load inside an already-held lock; quarantine and raise on corruption."""
    if os.path.exists(TOOLS_FILE):
        try:
            with open(TOOLS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            _quarantine_corrupt_tools()
            raise ValueError(f"tools.json is corrupt: {exc}") from exc
        return data if isinstance(data, dict) else {}
    return {}


def load_tools():
    """Load tools from JSON file with file locking for thread safety."""
    lock = filelock.FileLock(TOOLS_LOCK_FILE)
    with lock:
        try:
            return _load_tools_locked()
        except ValueError:
            # Reads stay available (empty); mutations via update_tools raise
            # so the next save cannot wipe the quarantined file silently.
            return {}


def _atomic_write_json(path: str, payload: dict) -> None:
    """Write JSON via temp file + atomic replace (M-DATA-1)."""
    import tempfile as _tempfile

    dir_path = os.path.dirname(path) or "."
    fd, tmp_path = _tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=4)
            tmp.flush()
            try:
                os.fsync(tmp.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def save_tools(tools):
    """Save tools to JSON file with file locking for thread safety."""
    lock = filelock.FileLock(TOOLS_LOCK_FILE)
    with lock:
        _atomic_write_json(TOOLS_FILE, tools)


def update_tools(mutator):
    """Read-modify-write under ONE lock so concurrent edits cannot lose updates.

    ``mutator`` receives the dict and returns the dict to persist (or None
    to persist the mutated input). Corrupt files quarantine and raise
    instead of loading as empty and wiping on save (M-DATA-1).
    """
    lock = filelock.FileLock(TOOLS_LOCK_FILE)
    with lock:
        tools = _load_tools_locked()
        result = mutator(tools)
        _atomic_write_json(TOOLS_FILE, result if isinstance(result, dict) else tools)
        return result if isinstance(result, dict) else tools

async def load_tools_async():
    """Async wrapper for load_tools to avoid blocking event loop."""
    return await asyncio.to_thread(load_tools)

async def save_tools_async(tools):
    """Async wrapper for save_tools to avoid blocking event loop."""
    return await asyncio.to_thread(save_tools, tools)

@router.get("", response_class=HTMLResponse)
async def tools_page(request: Request):
    """Loads the main dashboard with the Tool Library active."""
    module_manager = request.app.state.module_manager
    enabled_modules = [m for m in module_manager.get_all_modules() if m.get("enabled")]
    return templates.TemplateResponse(request, "index.html", {
        "modules": enabled_modules,
        "active_module": "tools",
        "full_width_content": True,
        "settings": settings.settings
    })

@router.get("/gui", response_class=HTMLResponse)
async def tools_gui(request: Request):
    """Returns the Tool Library GUI fragment."""
    tools = load_tools()
    return templates.TemplateResponse(request, "tools_gui.html", {"tools": tools})

@router.get("/list", response_class=HTMLResponse)
async def tools_list(request: Request):
    tools = load_tools()
    return templates.TemplateResponse(request, "tools_list.html", {"tools": tools})

@router.post("/save")
async def save_tool(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    parameters: str = Form(...),
    code: str = Form(...),
    original_name: str = Form("")
):
    try:
        # Validate JSON parameters
        params_json = json.loads(parameters)

        # Determine enabled state and handle renaming
        # L-5: the new name must be provider-safe; the stored original keeps
        # the lenient sanitizer so legacy names can still be renamed away.
        name = _validate_tool_name(name)
        # Sanitize tool name to prevent path traversal
        name = _sanitize_tool_name(name)

        if original_name:
            original_name = original_name.strip()
            # Sanitize original name as well for consistency
            original_name = _sanitize_tool_name(original_name)

        # M-TOOL-2: compare against the stored source so an edit that
        # changes code resets the risk rating instead of inheriting it.
        old_code = None
        try:
            old_path = _library_path(original_name or name)
            if os.path.exists(old_path):
                with open(old_path, "r", encoding="utf-8") as _f:
                    old_code = _f.read()
        except OSError:
            old_code = None

        class _RenameCollision(Exception):
            pass

        def _mutate(tools: dict) -> dict:
            # M-TOOL-2: renaming onto an existing tool must not silently
            # overwrite it.
            if original_name and original_name != name and name in tools:
                raise _RenameCollision(name)
            is_enabled = True
            existing = tools.get(original_name or name) or {}
            tier = existing.get("tier")
            tier_reason = str(existing.get("tier_reason") or "").strip()
            if tier not in {item.value for item in Tier} or not tier_reason:
                tier, tier_reason = Tier.HIGH.value, "new tool; review before lowering"
            elif existing and old_code is not None and code != old_code:
                # M-TOOL-2: a low-risk rating must not survive a code change.
                tier, tier_reason = Tier.HIGH.value, "code changed; pending re-review"

            if original_name and original_name != name:
                if original_name in tools:
                    is_enabled = tools[original_name].get("enabled", True)
                    del tools[original_name]
            elif name in tools:
                is_enabled = tools[name].get("enabled", True)

            # OpenAI Function Calling Format
            tools[name] = {
                "definition": {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": params_json
                    }
                },
                "enabled": is_enabled,
                "tier": tier,
                "tier_reason": tier_reason,
                "manifest_version": MANIFEST_VERSION,
                "requires": ["sandbox", "library_source"],
                "source": {"mode": "library", "entry": f"{name}.py"},
            }
            return tools

        # M-DATA-1: single-lock read-modify-write so concurrent saves
        # cannot lose updates; corrupt files quarantine and raise.
        try:
            update_tools(_mutate)
        except _RenameCollision as exc:
            return Response(status_code=409, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"A different tool named '{exc}' already exists; pick another name."}})})
        except ValueError as exc:
            return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Tool store corrupt, not saving: {exc}"}})})

        if original_name and original_name != name:
            old_code_path = _library_path(original_name)
            if os.path.exists(old_code_path):
                os.remove(old_code_path)

        # Save Python logic to standalone file
        with open(os.path.join(LIBRARY_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
            f.write(code)

        return await tools_list(request)
    except Exception as e:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to save tool: {str(e)}"}})})

@router.get("/calls/{call_id}")
async def get_tool_call(call_id: str):
    """One live or recently settled tool call (IDEA-1173)."""
    context = calls.default_registry().get(call_id)
    if context is None:
        raise HTTPException(status_code=404, detail=(
            f"Why: no call '{call_id}' is registered (it may have been evicted). "
            "Fix: list /tools/calls for the live calls."
        ))
    return JSONResponse(context.snapshot())


@router.get("/calls")
async def list_tool_calls():
    return JSONResponse({"calls": calls.default_registry().pending()})


@router.post("/calls/{call_id}/cancel")
async def cancel_tool_call(call_id: str):
    """Ask a running tool call to stop; the child is terminated by the sandbox."""
    context, outcome = calls.default_registry().request_cancel(call_id)
    if outcome == "unknown":
        raise HTTPException(status_code=404, detail=(
            f"Why: no call '{call_id}' is registered. "
            "Fix: list /tools/calls and use a live call id."
        ))
    if outcome == "already_settled":
        raise HTTPException(status_code=409, detail=(
            f"Why: call '{call_id}' already settled as '{context.status}'. "
            "Fix: nothing to cancel; read the call to see its result."
        ))
    return JSONResponse({"cancelled": True, "call": context.snapshot()})


@router.get("/capabilities")
async def tool_capabilities():
    """The declared and supported tool primitives, so manifest drift is visible (IDEA-1172)."""
    supported = describe_supported()
    drift = []
    for name, entry in (load_tools() or {}).items():
        requirements, error = parse_requirements(entry)
        if error:
            drift.append({"tool": name, "problem": "invalid_manifest", "detail": error})
            continue
        missing = sorted({req.name for req in requirements} - set(supported["supported"]))
        if missing:
            drift.append({"tool": name, "problem": "unsupported_primitives", "missing": missing})
    return JSONResponse({"supported": supported, "drift": drift})


@router.get("/drafts")
async def list_outbound_drafts():
    """Outbound tool calls the agent drafted; nothing here has been sent (IDEA-032)."""
    drafts = await asyncio.to_thread(outbound.default_store().pending)
    return JSONResponse({"drafts": drafts, "send_phrase": outbound.SEND_PHRASE})


@router.post("/drafts/{draft_id}/send")
async def send_outbound_draft(draft_id: str, confirm: str = Query(None)):
    """Send one draft; the exact phrase is required because the agent cannot supply it."""
    try:
        draft = await asyncio.to_thread(outbound.send_draft, draft_id, confirm)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Why: there is no draft {draft_id}. Fix: list drafts with GET /tools/drafts.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Why: {exc}. Fix: only a pending draft can be sent, and only once.")
    return JSONResponse(draft)


@router.post("/drafts/{draft_id}/discard")
async def discard_outbound_draft(draft_id: str):
    """Drop a draft without sending it."""
    try:
        draft = await asyncio.to_thread(outbound.default_store().transition, draft_id, "pending", "discarded")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Why: there is no draft {draft_id}. Fix: list drafts with GET /tools/drafts.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Why: {exc}. Fix: only a pending draft can be discarded.")
    return JSONResponse(draft)


@router.get("/approvals")
async def list_tool_approvals():
    """HIGH-tier tool calls held for a person; nothing here has run (IDEA-060)."""
    items = await asyncio.to_thread(approvals.default_store().pending)
    return JSONResponse({"approvals": items, "run_phrase": approvals.RUN_PHRASE})


@router.post("/approvals/{approval_id}/run")
async def run_tool_approval(approval_id: str, confirm: str = Query(None)):
    """Run one held call; the exact phrase is required because the model cannot supply it."""
    try:
        item = await asyncio.to_thread(approvals.default_store().run, approval_id, confirm)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Why: there is no approval {approval_id}. Fix: list approvals with GET /tools/approvals.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Why: {exc}. Fix: only a pending approval can run, and only once.")
    return JSONResponse(item)


@router.post("/approvals/{approval_id}/reject")
async def reject_tool_approval(approval_id: str):
    """Drop a held call without running it."""
    try:
        item = await asyncio.to_thread(
            approvals.default_store().transition, approval_id, "pending", "rejected")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Why: there is no approval {approval_id}. Fix: list approvals with GET /tools/approvals.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Why: {exc}. Fix: only a pending approval can be rejected.")
    return JSONResponse(item)


@router.post("/{name}/toggle")
async def toggle_tool(request: Request, name: str):
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)

    def _mutate(tools: dict) -> dict:
        if name in tools:
            current_state = tools[name].get("enabled", True)
            tools[name]["enabled"] = not current_state
        return tools

    try:
        update_tools(_mutate)
    except ValueError as exc:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Tool store corrupt, not saving: {exc}"}})})
    return await tools_list(request)

@router.delete("/{name}")
async def delete_tool(request: Request, name: str):
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)

    def _mutate(tools: dict) -> dict:
        if name in tools:
            del tools[name]
        return tools

    try:
        update_tools(_mutate)
    except ValueError as exc:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Tool store corrupt, not saving: {exc}"}})})
    # Remove associated python file
    code_path = _library_path(name)
    if os.path.exists(code_path):
        os.remove(code_path)
    return await tools_list(request)

@router.get("/edit/{name}")
async def edit_tool(name: str):
    """Returns tool metadata and code for editing."""
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    tools = load_tools()
    if name not in tools:
        return Response(status_code=404)

    tool_data = tools[name]
    code = ""
    code_path = _library_path(name)
    if os.path.exists(code_path):
        with open(code_path, "r", encoding="utf-8") as f:
            code = f.read()
            
    return {
        "name": name,
        "description": tool_data["definition"]["function"]["description"],
        "parameters": json.dumps(tool_data["definition"]["function"]["parameters"], indent=2),
        "code": code
    }

@router.get("/names")
async def get_tool_names():
    """Returns a list of all tool names."""
    tools = load_tools()
    return list(tools.keys())

@router.get("/with-descriptions")
async def get_tools_with_descriptions():
    """Returns all tools with their names and descriptions."""
    tools = load_tools()
    result = []
    for name, tool_data in tools.items():
        if not tool_data.get("enabled", True):
            continue
        desc = ""
        if "definition" in tool_data and "function" in tool_data["definition"]:
            desc = tool_data["definition"]["function"].get("description", "")
        result.append({"name": name, "description": desc})
    return result

@router.get("/definitions")
async def get_definitions():
    """Returns the JSON definitions for all tools to be used in LLM config."""
    tools = load_tools()
    definitions = []
    for name, tool in tools.items():
        if tool.get("enabled", True):
            definition = tool["definition"]
            # NOTE: Time injection was removed - it caused stale timestamps
            # The SaveReminder tool should inject current time at runtime instead
            definitions.append(definition)
    return definitions

@router.get("/export")
async def export_tools(name: str = Query(None)):
    # Sanitize tool name if provided to prevent path traversal
    if name:
        name = _sanitize_tool_name(name)
    tools = load_tools()
    export_data = []
    
    if name:
        if name not in tools:
            return Response(status_code=404, content="Tool not found")
        items_to_export = [(name, tools[name])]
        filename = f"{name}.json"
    else:
        items_to_export = tools.items()
        filename = "neurocore_tools.json"
    
    for name, data in items_to_export:
        tool_export = {
            "name": name,
            "enabled": data.get("enabled", True),
            "description": data["definition"]["function"]["description"],
            "parameters": data["definition"]["function"]["parameters"]
        }
        
        # Read code
        code_path = _library_path(name)
        if os.path.exists(code_path):
            with open(code_path, "r", encoding="utf-8") as f:
                tool_export["code"] = f.read()
        else:
            tool_export["code"] = ""
            
        export_data.append(tool_export)
        
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.get("/config/{name}", response_class=HTMLResponse)
async def get_tool_config(request: Request, name: str):
    """Returns the configuration form for a tool."""
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    
    if name == "SendEmail":
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = os.getenv("SMTP_PORT", "587")
        smtp_email = os.getenv("SMTP_EMAIL", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")
        
        return templates.TemplateResponse(request, "tool_config_sendemail.html", {
            "smtp_server": smtp_server,
            "smtp_port": smtp_port,
            "smtp_email": smtp_email,
            "smtp_password": smtp_password
        })
    
    return Response(status_code=404, content="Configuration not available for this tool")

@router.post("/config/{name}", response_class=HTMLResponse)
async def save_tool_config(request: Request, name: str):
    """Saves the configuration for a tool."""
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    
    try:
        form_data = await request.form()
        
        if name == "SendEmail":
            smtp_server = form_data.get("smtp_server", "smtp.gmail.com")
            smtp_port = form_data.get("smtp_port", "587")
            smtp_email = form_data.get("smtp_email", "")
            smtp_password = form_data.get("smtp_password", "")
            
            # Update process environment
            os.environ.update({
                "SMTP_SERVER": smtp_server,
                "SMTP_PORT": smtp_port,
                "SMTP_EMAIL": smtp_email,
                "SMTP_PASSWORD": smtp_password,
            })
            
            # Atomic rewrite of .env.local to prevent duplicate entries
            env_path = ".env.local"
            env_vars = {}
            
            # Read existing environment variables
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and '=' in line:
                            key, val = line.split('=', 1)
                            env_vars[key] = val
            
            # Update with new values
            env_vars['SMTP_SERVER'] = smtp_server
            env_vars['SMTP_PORT'] = smtp_port
            env_vars['SMTP_EMAIL'] = smtp_email
            env_vars['SMTP_PASSWORD'] = smtp_password
            
            # Write all environment variables atomically
            with open(env_path, "w", encoding="utf-8") as f:
                for key, val in env_vars.items():
                    f.write(f"{key}={val}\n")
            
            return Response(content="<div class='text-center py-8 text-emerald-400 font-semibold'>Configuration saved successfully!</div>", status_code=200)
        
        return Response(status_code=404, content="Configuration not available for this tool")
    except Exception as e:
        return Response(status_code=400, content=f"<div class='text-center py-8 text-red-500'>Error saving configuration: {str(e)}</div>")

@router.post("/import")
async def import_tools(request: Request, file: UploadFile = File(...)):
    try:
        content = await file.read()
        filename = file.filename
        tools = load_tools()
        
        if filename.endswith(".json"):
            import_data = json.loads(content)
            
            if isinstance(import_data, dict):
                import_data = [import_data]
            
            for tool_data in import_data:
                name = tool_data.get("name")
                if not name: continue
                
                # Sanitize tool name to prevent path traversal
                try:
                    name = _sanitize_tool_name(name)
                except ValueError:
                    continue  # Skip invalid tool names
                
                # Update tools.json structure
                tools[name] = {
                    "definition": {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool_data.get("description", ""),
                            "parameters": tool_data.get("parameters", {"type": "object", "properties": {}})
                        }
                    },
                    "enabled": tool_data.get("enabled", True),
                    # An imported package cannot choose its own tier; a person lowers it after review.
                    "tier": "high",
                    "tier_reason": "imported tool; review before lowering",
                }
                
                # Save code
                code = tool_data.get("code", "")
                with open(os.path.join(LIBRARY_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
                    f.write(code)

        elif filename.endswith(".py"):
            name = os.path.splitext(os.path.basename(filename))[0]
            # Sanitize tool name to prevent path traversal
            try:
                name = _sanitize_tool_name(name)
            except ValueError:
                return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid tool name in filename"}})})
            
            code = content.decode("utf-8")
            
            if name not in tools:
                tools[name] = {
                    "definition": {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": "Imported Python Tool",
                            "parameters": {"type": "object", "properties": {}}
                        }
                    },
                    "enabled": True,
                    "tier": "high",
                    "tier_reason": "imported tool; review before lowering",
                }
            
            with open(os.path.join(LIBRARY_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
                f.write(code)
        else:
            return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Unsupported file type. Please upload .json or .py"}})})
                
        save_tools(tools)
        
        return await tools_list(request)
    except Exception as e:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Import failed: {str(e)}"}})})

