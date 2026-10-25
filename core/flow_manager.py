import copy
import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime

from core.paths import state_path
from core.settings import settings

logger = logging.getLogger(__name__)

FLOWS_FILE = state_path("ai_flows.json")

# Validation constants
REQUIRED_FLOW_KEYS = {"id", "nodes", "connections"}
REQUIRED_NODE_KEYS = {"id", "moduleId", "nodeTypeId"}
MAX_BACKUP_COUNT = 5  # Maximum number of default flow backups to keep
MAX_VERSIONS_PER_FLOW = 20  # Maximum saved versions per flow


class FlowManager:
    def __init__(self, storage_file=FLOWS_FILE, versions_file=None):
        # Initialize lock FIRST before any method calls (RLock for reentrant locking)
        self.lock = threading.RLock()
        self.storage_file = storage_file
        # Derive versions file alongside the storage file (e.g. ai_flows.json → ai_flows_versions.json)
        if versions_file is None:
            base, ext = os.path.splitext(storage_file)
            versions_file = base + "_versions" + (ext if ext else ".json")
        self.versions_file = versions_file
        self.flows = self._load_flows()
        self.versions = self._load_versions()

    @staticmethod
    def _fsync_directory(directory: str) -> None:
        """Best-effort directory fsync so a rename survives a power loss."""
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError as exc:
            logger.debug("Directory fsync unavailable: %s", exc)
        finally:
            os.close(fd)

    def _write_json_atomic(self, path: str, data, indent: int = 4) -> None:
        """Write JSON through a fsynced temp file, keeping the previous file as .bak."""
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=indent)
                handle.flush()
                os.fsync(handle.fileno())

            if os.path.exists(path):
                backup_path = path + ".bak"
                backup_tmp = backup_path + ".tmp"
                try:
                    with open(path, "rb") as source, open(backup_tmp, "wb") as sink:
                        sink.write(source.read())
                        sink.flush()
                        os.fsync(sink.fileno())
                    os.replace(backup_tmp, backup_path)
                except OSError as exc:
                    logger.warning("Could not refresh flow backup %s: %s", backup_path, exc)

            os.replace(temp_path, path)
            self._fsync_directory(directory)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError as exc:
                logger.debug("Could not remove temp file %s: %s", temp_path, exc)
            raise

    def _quarantine_file(self, path: str) -> str:
        """Move a corrupt state file aside instead of overwriting it."""
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
        target = f"{path}.corrupt-{stamp}"
        try:
            os.replace(path, target)
            logger.error("Quarantined invalid flow store as %s", target)
        except OSError as exc:
            logger.error("Could not quarantine invalid flow store %s: %s", path, exc)
        return target

    @staticmethod
    def _read_flow_file(path: str):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_backup_flows(self):
        """Return validated flows from the .bak file, or None when unusable."""
        backup_path = self.storage_file + ".bak"
        if not os.path.exists(backup_path):
            return None
        try:
            flows = self._read_flow_file(backup_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(flows, dict) or not flows:
            return None
        if not self._validate_flows(flows)["valid"]:
            return None
        logger.warning("Recovered flows from backup %s", backup_path)
        return flows

    def _restore_default_flows(self):
        """Create and persist the default flow set after an unrecoverable store."""
        default_flows = self._create_default_flows()
        self._ensure_default_active()
        try:
            self._save_flows_to_disk_no_lock_required(default_flows)
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist default flows: %s", exc)
        return default_flows

    def _load_flows(self):
        # Note: lock is already created in __init__ before this is called.
        if not os.path.exists(self.storage_file):
            return self._restore_default_flows()

        try:
            flows = self._read_flow_file(self.storage_file)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Could not read flow store %s: %s", self.storage_file, exc)
            self._quarantine_file(self.storage_file)
            recovered = self._load_backup_flows()
            return recovered if recovered is not None else self._restore_default_flows()

        if not flows:
            return self._restore_default_flows()

        validation_result = self._validate_flows(flows)
        if not validation_result["valid"]:
            logger.warning(
                "Flow validation failed: %s. Quarantining the store.",
                validation_result["errors"],
            )
            self._quarantine_file(self.storage_file)
            recovered = self._load_backup_flows()
            return recovered if recovered is not None else self._restore_default_flows()
        return flows

    def _validate_flows(self, flows: dict) -> dict:
        """Validate flows structure and integrity. Returns {valid: bool, errors: list}."""
        if not isinstance(flows, dict):
            return {"valid": False, "errors": ["flows_data must be a dictionary"]}

        # Empty dict is valid - represents "no flows" state
        if not flows:
            return {"valid": True, "errors": []}

        errors = []
        for flow_id, flow in flows.items():
            errors.extend(self._validate_flow_structure(flow_id, flow))
        return {"valid": len(errors) == 0, "errors": errors}

    def _validate_flow_structure(self, flow_id, flow) -> list:
        """Structural checks shared by load and opt-in validated saves."""
        if not isinstance(flow, dict):
            return [f"Flow '{flow_id}' must be a dictionary"]

        errors = []
        missing_keys = REQUIRED_FLOW_KEYS - set(flow.keys())
        if missing_keys:
            errors.append(f"Flow '{flow_id}' missing required keys: {missing_keys}")
            return errors

        node_ids = set()
        nodes = flow.get("nodes")
        if not isinstance(nodes, list):
            errors.append(f"Flow '{flow_id}' nodes must be a list")
        else:
            for index, node in enumerate(nodes):
                if not isinstance(node, dict):
                    errors.append(f"Flow '{flow_id}' node {index} must be a dictionary")
                    continue
                missing_node_keys = REQUIRED_NODE_KEYS - set(node.keys())
                if missing_node_keys:
                    errors.append(
                        f"Flow '{flow_id}' node {index} missing keys: {missing_node_keys}"
                    )
                node_id = node.get("id")
                if not isinstance(node_id, str) or not node_id:
                    errors.append(
                        f"Flow '{flow_id}' node {index} needs a non-empty string id"
                    )
                elif node_id in node_ids:
                    errors.append(
                        f"Flow '{flow_id}' has duplicate node id '{node_id}'"
                    )
                else:
                    node_ids.add(node_id)

        for label, key in (("connection", "connections"), ("bridge", "bridges")):
            edges = flow.get(key, [])
            if edges is None:
                edges = []
            if not isinstance(edges, list):
                errors.append(f"Flow '{flow_id}' {key} must be a list")
                continue
            for index, edge in enumerate(edges):
                if not isinstance(edge, dict):
                    errors.append(
                        f"Flow '{flow_id}' {label} {index} must be a dictionary"
                    )
                    continue
                source = edge.get("from")
                target = edge.get("to")
                for endpoint_name, endpoint in (("from", source), ("to", target)):
                    if not isinstance(endpoint, str) or not endpoint:
                        errors.append(
                            f"Flow '{flow_id}' {label} {index} needs a non-empty "
                            f"'{endpoint_name}'"
                        )
                    elif endpoint not in node_ids:
                        errors.append(
                            f"Flow '{flow_id}' {label} {index} references unknown "
                            f"node '{endpoint}'"
                        )
        return errors

    def _create_default_flows(self):
        flow_id = "default-flow-001"
        
        system_prompt = (
            "You are an **Autonomous Recursive Thinking Agent**.\n"
            "Your goal is to solve complex problems by breaking them down, executing steps, and iterating until the solution is found.\n\n"
            "### 🧠 CONTEXT AWARENESS\n"
            "You have access to the following dynamic context streams. Use them to maintain continuity:\n"
            "1. **Past Reasoning**: Review your previous thoughts to ensure logical progression and avoid loops.\n"
            "2. **Long-Term Memory**: Recall facts and preferences about the project.\n"
            "3. **Knowledge Base**: Use retrieved document snippets to ground your answers in facts.\n\n"
            "### 🔄 OPERATIONAL LOOP\n"
            "Your existence is a continuous loop of thought and action. In each cycle, perform **ONE** of the following:\n\n"
            "**OPTION A: ACTION (Tool Call)**\n"
            "If you need external information (e.g., check time, search web, read file) or need to perform an action:\n"
            "- CALL THE APPROPRIATE TOOL.\n"
            "- Do not output reasoning text if you are calling a tool (unless the tool requires it).\n\n"
            "**OPTION B: REASONING (Internal Thought)**\n"
            "If you have enough information to proceed or need to analyze data:\n"
            "1. **Synthesize**: Combine new tool outputs with your Past Reasoning.\n"
            "2. **Plan**: Determine the immediate next step.\n"
            "3. **Output**: Write a clear, concise reasoning entry. This text will be saved to your reasoning history for the next cycle.\n\n"
            "### 🛑 CRITICAL RULES\n"
            "- **AUTONOMY**: Do not ask the user for input or clarification. If information is missing, use tools to find it or make a reasonable assumption to proceed.\n"
            "- **NO REPETITION**: Check Past Reasoning. If you have already tried something that failed, try a different approach.\n"
            "- **INCREMENTAL PROGRESS**: Do not try to solve everything at once. Take one logical step per cycle.\n"
            "- **TERMINATION**: When the objective is fully satisfied, output a final summary and explicitly state \"TASK COMPLETED\"."
        )

        return {
            flow_id: {
                "id": flow_id,
                "name": "Default Chat Flow",
                "nodes": [
                    {"id": "node-0", "moduleId": "chat", "nodeTypeId": "chat_input", "name": "Chat Input", "x": -97, "y": 248, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-1", "moduleId": "system_prompt", "nodeTypeId": "system_prompt", "name": "System Prompt", "x": 343.9, "y": 203.9, "config": {"system_prompt": system_prompt, "enabled_tools": ["Weather", "Calculator", "TimeZoneConverter", "ConversionCalculator", "SystemTime", "FetchURL", "CurrencyConverter", "SaveReminder", "CheckCalendar"], "explanation": ""}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-2", "moduleId": "llm_module", "nodeTypeId": "llm_module", "name": "LLM Core", "x": 551, "y": 250, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-3", "moduleId": "chat", "nodeTypeId": "chat_output", "name": "Chat Output", "x": 908, "y": 250, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-4", "moduleId": "memory", "nodeTypeId": "memory_save", "name": "Memory Save", "x": 123, "y": 163, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-5", "moduleId": "memory", "nodeTypeId": "memory_save", "name": "Memory Save", "x": 1134, "y": 249, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-6", "moduleId": "memory", "nodeTypeId": "memory_recall", "name": "Memory Recall", "x": 123, "y": 249, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-7", "moduleId": "telegram", "nodeTypeId": "telegram_output", "name": "Telegram Output", "x": 909, "y": 337, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-8", "moduleId": "telegram", "nodeTypeId": "telegram_input", "name": "Telegram Input", "x": -289, "y": 249, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-9", "moduleId": "tools", "nodeTypeId": "tool_dispatcher", "name": "Tool Dispatcher", "x": 632.1, "y": -28.2, "config": {"allowed_tools": ["Weather", "Calculator", "TimeZoneConverter", "ConversionCalculator", "SystemTime", "FetchURL", "CurrencyConverter", "SaveReminder", "CheckCalendar"], "explanation": ""}, "isReverted": True, "outputDot": {}, "inputDot": {}},
                    {"id": "node-10", "moduleId": "logic", "nodeTypeId": "conditional_router", "name": "Conditional Router", "x": 717, "y": 250, "config": {"check_field": "tool_calls", "true_branches": ["node-9"], "false_branches": ["node-3", "node-7"], "explanation": ""}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-11", "moduleId": "calendar", "nodeTypeId": "calendar_watcher", "name": "Calendar Watcher", "x": 719.2, "y": 336.8, "config": {}, "isReverted": False, "outputDot": {}, "inputDot": {}},
                    {"id": "node-12", "moduleId": "logic", "nodeTypeId": "repeater_node", "name": "Repeater", "x": 549.9, "y": 336.0, "config": {"delay": 30, "max_repeats": 0, "explanation": ""}, "isReverted": False, "outputDot": {}, "inputDot": {}}
                ],
                "connections": [
                    {"from": "node-1", "to": "node-2"},
                    {"from": "node-0", "to": "node-4"},
                    {"from": "node-3", "to": "node-5"},
                    {"from": "node-0", "to": "node-6"},
                    {"from": "node-6", "to": "node-1"},
                    {"from": "node-8", "to": "node-0"},
                    {"from": "node-9", "to": "node-2"},
                    {"from": "node-10", "to": "node-3"},
                    {"from": "node-10", "to": "node-7"},
                    {"from": "node-2", "to": "node-10"},
                    {"from": "node-10", "to": "node-9"},
                    {"from": "node-11", "to": "node-7"},
                    {"from": "node-12", "to": "node-11"}
                ],
                "bridges": [],
                "created_at": datetime.utcnow().isoformat() + 'Z',
                "updated_at": datetime.utcnow().isoformat() + 'Z'
            }
        }

    def _ensure_default_active(self, flows=None):
        """Ensure a default flow is active if one exists.
        
        Only sets active_ai_flows to include default flow if:
        1. No active flows are currently set, AND
        2. A default flow (default-flow-001) exists in the flows dictionary
        
        Args:
            flows: Optional flows dictionary to check. If not provided, uses self.flows.
        """
        if not settings.get("active_ai_flows"):
            # Use provided flows dict or self.flows
            flows_to_check = flows if flows is not None else getattr(self, 'flows', {})
            # Only set if default flow exists
            if "default-flow-001" in flows_to_check:
                settings.save_settings({"active_ai_flows": ["default-flow-001"]})
            else:
                settings.save_settings({"active_ai_flows": []})

    def _save_flows_to_disk_no_lock_required(self, flows):
        """Internal method to save flows to disk WITHOUT acquiring lock.
        
        This method can be called without holding self.lock. It is safe to call
        when: (a) the file doesn't exist yet (initialization), (b) the file is
        empty/corrupt (resetting to defaults), or (c) from _load_flows() during
        initialization before other threads can access the manager.
        
        Args:
            flows: The flows dictionary to save to disk.
        """
        self._write_json_atomic(self.storage_file, flows)

    def _save_flows_to_disk(self, flows):
        """Save flows to disk. Acquires lock before writing."""
        with self.lock:
            self._write_json_atomic(self.storage_file, flows)

    def _save_flows(self):
        self._save_flows_to_disk(self.flows)

    # ------------------------------------------------------------------
    # Version history
    # ------------------------------------------------------------------

    def _load_versions(self) -> dict:
        """Load the per-flow version store from disk. Returns {} on missing/corrupt file."""
        if not os.path.exists(self.versions_file):
            return {}
        try:
            with open(self.versions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_versions_to_disk(self):
        """Persist the versions dict to disk (must be called while holding self.lock)."""
        try:
            self._write_json_atomic(self.versions_file, self.versions, indent=2)
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"Failed to save flow versions: {e}")

    def _save_version(self, flow_id: str, persist: bool = True):
        """Snapshot the current state of *flow_id*.

        Used before overwrite, rename, delete, import and rollback. The caller must hold
        self.lock. Set ``persist=False`` when snapshotting several flows before one write.
        """
        current = self.flows.get(flow_id)
        if current is None:
            return

        flow_versions = self.versions.setdefault(flow_id, [])
        next_version = (max(v["version"] for v in flow_versions) + 1) if flow_versions else 1

        snapshot = {
            "version": next_version,
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "name": current.get("name", ""),
            "nodes": copy.deepcopy(current.get("nodes", [])),
            "connections": copy.deepcopy(current.get("connections", [])),
            "bridges": copy.deepcopy(current.get("bridges", [])),
            "created_at": current.get("created_at"),
        }
        if "metadata" in current:
            snapshot["metadata"] = copy.deepcopy(current["metadata"])
        if "reverted_nodes" in current:
            snapshot["reverted_nodes"] = copy.deepcopy(current["reverted_nodes"])
        flow_versions.append(snapshot)

        # Prune oldest entries when over the limit
        if len(flow_versions) > MAX_VERSIONS_PER_FLOW:
            flow_versions.sort(key=lambda v: v["version"])
            self.versions[flow_id] = flow_versions[-MAX_VERSIONS_PER_FLOW:]

        if persist:
            self._save_versions_to_disk()

    def get_versions(self, flow_id: str) -> list:
        """Return lightweight version metadata for *flow_id*, newest first.

        Each entry: {version, saved_at, name, node_count, connection_count}
        """
        with self.lock:
            flow_versions = self.versions.get(flow_id, [])
            result = [
                {
                    "version": v["version"],
                    "saved_at": v["saved_at"],
                    "name": v["name"],
                    "node_count": len(v.get("nodes", [])),
                    "connection_count": len(v.get("connections", [])),
                }
                for v in flow_versions
            ]
            result.sort(key=lambda v: v["version"], reverse=True)
            return result

    def rollback_version(self, flow_id: str, version_num: int) -> dict | None:
        """Restore *flow_id* to the snapshot identified by *version_num*.

        A redo snapshot of the current state is recorded first, so a rollback can itself
        be undone. Deleted flows are restored from their retained history. Returns the
        restored flow dict, or None if no matching version exists.
        """
        with self.lock:
            flow_versions = self.versions.get(flow_id, [])
            snapshot = next((v for v in flow_versions if v["version"] == version_num), None)
            if snapshot is None:
                return None

            if flow_id in self.flows:
                self._save_version(flow_id)

            restored = {
                "id": flow_id,
                "name": snapshot["name"],
                "nodes": copy.deepcopy(snapshot["nodes"]),
                "connections": copy.deepcopy(snapshot["connections"]),
                "bridges": copy.deepcopy(snapshot["bridges"]),
                "created_at": snapshot.get("created_at") or
                datetime.utcnow().isoformat() + "Z",
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
            if "metadata" in snapshot:
                restored["metadata"] = copy.deepcopy(snapshot["metadata"])
            if "reverted_nodes" in snapshot:
                restored["reverted_nodes"] = copy.deepcopy(snapshot["reverted_nodes"])
            self.flows[flow_id] = restored
            self._save_flows()
            return copy.deepcopy(self.flows[flow_id])

    # ------------------------------------------------------------------

    def save_flow(self, name, nodes, connections, bridges=None, flow_id=None,
                  metadata=None, reverted_nodes=None, validate=False):
        with self.lock:
            if flow_id is None:
                flow_id = str(uuid.uuid4())
            if validate:
                candidate = {
                    "id": flow_id,
                    "name": name,
                    "nodes": nodes,
                    "connections": connections,
                    "bridges": bridges or [],
                }
                errors = self._validate_flow_structure(flow_id, candidate)
                if errors:
                    raise ValueError("flow validation failed: " + "; ".join(errors))
            existing = self.flows.get(flow_id)

            # Snapshot the existing flow before overwriting (for version history)
            if existing is not None:
                self._save_version(flow_id)

            now = datetime.utcnow().isoformat() + 'Z'
            saved = {
                "id": flow_id,
                "name": name,
                "nodes": copy.deepcopy(nodes),
                "connections": copy.deepcopy(connections),
                "bridges": copy.deepcopy(bridges or []),
                "created_at": existing.get("created_at") if existing else now,
                "updated_at": now
            }
            if metadata is not None:
                saved["metadata"] = copy.deepcopy(metadata)
            elif existing is not None and "metadata" in existing:
                saved["metadata"] = copy.deepcopy(existing["metadata"])
            if reverted_nodes is not None:
                saved["reverted_nodes"] = copy.deepcopy(reverted_nodes)
            elif existing is not None and "reverted_nodes" in existing:
                saved["reverted_nodes"] = copy.deepcopy(existing["reverted_nodes"])
            self.flows[flow_id] = saved
            self._save_flows()
            return copy.deepcopy(self.flows[flow_id])

    def import_flows(self, flows_data: dict):
        """Replaces all flows with the provided dictionary after validation."""
        with self.lock:
            # Validate before importing
            validation_result = self._validate_flows(flows_data)
            if not validation_result["valid"]:
                logger.error(f"Flow import validation failed: {validation_result['errors']}")
                return {"success": False, "errors": validation_result["errors"]}
            
            for existing_id in list(self.flows):
                self._save_version(existing_id, persist=False)
            self._save_versions_to_disk()
            self.flows = copy.deepcopy(flows_data)
            self._save_flows()
            self._ensure_default_active()
            return {"success": True}

    def rename_flow(self, flow_id, new_name):
        with self.lock:
            if flow_id in self.flows:
                self._save_version(flow_id)
                self.flows[flow_id]["name"] = new_name
                self.flows[flow_id]["updated_at"] = (
                    datetime.utcnow().isoformat() + "Z"
                )
                self._save_flows()
                return True
            return False

    def get_flow(self, flow_id):
        with self.lock:
            return copy.deepcopy(self.flows.get(flow_id))

    def list_flows(self):
        with self.lock:
            return copy.deepcopy(
                sorted(self.flows.values(),
                       key=lambda x: x.get('updated_at') or x.get('created_at', ''),
                       reverse=True)
            )

    def get_all_flows_dict(self):
        """Get a copy of all flows as a dictionary (thread-safe)."""
        with self.lock:
            return copy.deepcopy(self.flows)

    def delete_flow(self, flow_id):
        with self.lock:
            if flow_id in self.flows:
                self._save_version(flow_id)
                del self.flows[flow_id]
                self._save_flows()
                active = settings.get("active_ai_flows", []) or []
                if flow_id in active:
                    try:
                        settings.save_settings({
                            "active_ai_flows": [
                                item for item in active if item != flow_id
                            ]
                        })
                    except Exception as exc:
                        logger.error(
                            "Flow %s deleted but active_ai_flows could not be pruned: %s",
                            flow_id, exc,
                        )
                return True
            return False

    def make_active_flow_default(self):
        """Overwrites the default flow with the first active flow after backing up current default."""
        with self.lock:
            active_ids = settings.get("active_ai_flows", [])
            if not active_ids or active_ids[0] not in self.flows:
                return False
            
            active_id = active_ids[0]
            active_flow = copy.deepcopy(self.flows[active_id])
            
            default_id = "default-flow-001"
            
            # Backup current default flow before overwriting
            if default_id in self.flows:
                backup_id = f"default-flow-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
                self.flows[backup_id] = copy.deepcopy(self.flows[default_id])
                logger.info(f"Backed up default flow to {backup_id}")
                
                # Cleanup old backups - keep only the most recent MAX_BACKUP_COUNT backups
                backup_keys = [k for k in self.flows.keys() if k.startswith("default-flow-backup-")]
                if len(backup_keys) > MAX_BACKUP_COUNT:
                    # Sort by timestamp (newest first)
                    backup_keys.sort(reverse=True)
                    # Remove oldest backups exceeding the limit
                    for old_backup in backup_keys[MAX_BACKUP_COUNT:]:
                        del self.flows[old_backup]
                        logger.info(f"Removed old backup flow: {old_backup}")
            
            active_flow["id"] = default_id
            active_flow["name"] = "Default Chat Flow"
            active_flow["created_at"] = datetime.utcnow().isoformat() + 'Z'
            active_flow["updated_at"] = datetime.utcnow().isoformat() + 'Z'
            
            self.flows[default_id] = active_flow
            self._save_flows()
            return True

flow_manager = FlowManager()
