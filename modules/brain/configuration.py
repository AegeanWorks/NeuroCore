"""Policy-gated self-configuration for settings, modules and brain options."""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.paths import state_path

from .persistence import LedgerLock, atomic_write_json, engine_transaction

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("module.json")

ALLOWED_SETTINGS = {
    "temperature": ("float", 0.0, 2.0),
    "max_tokens": ("int", 1, 32768),
    "ui_wide_mode": ("bool", None, None),
    "ui_show_footer": ("bool", None, None),
}

CONFIGURABLE_MODULES = {"annotations", "planner", "reflection", "reasoning_book"}
PROTECTED_MODULES = {
    "brain", "autonomous_agent", "tools", "messaging_bridge",
    "browser_automation", "memory", "knowledge_base", "llm_module", "chat",
}

BRAIN_OPTIONS = {
    "cycle_interval_seconds": ("float", 5.0, 3600.0),
    "max_cycles_per_hour": ("int", 1, 360),
    "max_candidates_per_day": ("int", 1, 100),
    "max_evaluations_per_day": ("int", 1, 100),
    "max_promotions_per_day": ("int", 1, 20),
    "max_candidates": ("int", 10, 1000),
    "max_experiences": ("int", 10, 10000),
    "max_skills": ("int", 10, 5000),
    "min_occurrences": ("int", 1, 10),
    "holdout_ratio": ("float", 0.0, 0.5),
}


class ConfigurationPolicyError(ValueError):
    """Raised when a requested change violates the configuration policy."""


class ConfigurationQuotaError(ValueError):
    """Raised when the daily configuration-change quota is exhausted (HTTP 429)."""


class ConfigurationStore:
    """Atomic ledger for applied changes and their rollback snapshots."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or state_path("data", "brain_configurations.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read configuration ledger: %s", exc)
                self._quarantine()
                return {}
            if not isinstance(data, dict):
                self._quarantine()
                return {}
            if not isinstance(data.get("changes"), list):
                data["changes"] = []
            if not isinstance(data.get("change_times"), list):
                data["change_times"] = []
            if not isinstance(data.get("latest_change"), dict):
                data["latest_change"] = {}
            return data

    def _quarantine(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target = f"{self.path}.corrupt-{stamp}"
        try:
            os.replace(self.path, target)
            logger.error("Quarantined invalid configuration ledger as %s", target)
        except OSError as exc:
            logger.error("Could not quarantine configuration ledger: %s", exc)

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock.held():
            atomic_write_json(self.path, data, default=str)


class ConfigurationEngine:
    """Preview, apply and roll back bounded configuration changes."""

    def __init__(self, settings_manager=None, module_manager=None,
                 store: Optional[ConfigurationStore] = None,
                 runtime_apply: Optional[Callable[[str, Any], None]] = None,
                 config_path: Optional[Path] = None,
                 allowed_settings: Optional[set] = None,
                 configurable_modules: Optional[set] = None,
                 max_changes_per_day: int = 20,
                 max_changes: int = 200):
        if settings_manager is None:
            from core.settings import settings as settings_manager

        self.settings_manager = settings_manager
        self.module_manager = module_manager
        self.store = store or ConfigurationStore()
        self.runtime_apply = runtime_apply
        self.config_path = Path(config_path or CONFIG_PATH)
        self.allowed_settings = set(allowed_settings or ALLOWED_SETTINGS)
        self.configurable_modules = set(
            configurable_modules or CONFIGURABLE_MODULES
        )
        self.max_changes_per_day = max(1, int(max_changes_per_day))
        self.max_changes = max(10, int(max_changes))

        data = self.store.load()
        self.changes: List[Dict[str, Any]] = data.get("changes", [])[-self.max_changes:]
        self.change_times: List[str] = data.get("change_times", [])
        self.latest_change: Dict[str, str] = data.get("latest_change", {})

    def _reload_from_store(self) -> None:
        data = self.store.load()
        self.changes = data.get("changes", [])[-self.max_changes:]
        self.change_times = data.get("change_times", [])
        self.latest_change = data.get("latest_change", {})

    def attach_module_manager(self, module_manager) -> None:
        self.module_manager = module_manager

    def preview(self, operation: Dict[str, Any],
                module_manager=None) -> Dict[str, Any]:
        manager = module_manager or self.module_manager
        op = self._normalize(operation)
        if op["kind"] == "setting":
            key = op["key"]
            if key not in self.allowed_settings:
                raise ConfigurationPolicyError(f"setting '{key}' is not configurable")
            self._validate_setting(key, op["value"])
            before = self.settings_manager.get(key)
        elif op["kind"] == "module":
            manager = self._require_manager(manager)
            module_id = op["module_id"]
            if module_id in PROTECTED_MODULES:
                raise ConfigurationPolicyError(f"module '{module_id}' is protected")
            if module_id not in self.configurable_modules:
                raise ConfigurationPolicyError(
                    f"module '{module_id}' is not in the configurable allowlist"
                )
            module = manager.get_module(module_id)
            if module is None:
                raise ConfigurationPolicyError(f"module '{module_id}' does not exist")
            if not isinstance(op["enabled"], bool):
                raise ConfigurationPolicyError("module 'enabled' must be a boolean")
            before = bool(module.get("enabled"))
        elif op["kind"] == "brain_option":
            key = op["key"]
            if key not in BRAIN_OPTIONS and key != "allowed_tools":
                raise ConfigurationPolicyError(f"brain option '{key}' is not configurable")
            config = self._read_brain_config()
            if key == "allowed_tools":
                self._validate_allowed_tools(op["value"], config)
                before = list(config.get("allowed_tools", []))
            else:
                self._validate_brain_option(key, op["value"])
                before = config.get(key)
        else:
            raise ConfigurationPolicyError(f"unsupported operation kind '{op['kind']}'")

        return {
            "operation": op,
            "before": before,
            "after": op.get("value", op.get("enabled")),
            "policy": "allowed",
        }

    @engine_transaction
    def apply(self, operation: Dict[str, Any], approved_by: str,
              module_manager=None) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise ConfigurationPolicyError("approved_by is required")
        manager = module_manager or self.module_manager
        preview = self.preview(operation, module_manager=manager)
        self._prune_quotas()
        if len(self.change_times) >= self.max_changes_per_day:
            raise ConfigurationQuotaError("daily configuration change quota exceeded")

        op = preview["operation"]
        change = {
            "id": uuid.uuid4().hex,
            "operation": op,
            "before": preview["before"],
            "after": preview["after"],
            "approved_by": approved_by.strip(),
            "status": "applied",
            "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._apply_value(op, op.get("value", op.get("enabled")), manager)
        self.changes.append(change)
        self.change_times.append(change["applied_at"])
        self.latest_change[self._target(op)] = change["id"]
        self._prune_changes()
        self._save()
        return change

    @engine_transaction
    def rollback(self, change_id: str, approved_by: str,
                 module_manager=None) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise ConfigurationPolicyError("approved_by is required")
        manager = module_manager or self.module_manager
        change = next(
            (item for item in self.changes if item.get("id") == change_id), None
        )
        if change is None:
            raise ConfigurationPolicyError(f"configuration change '{change_id}' not found")
        if change.get("status") != "applied":
            raise ConfigurationPolicyError("only applied changes can be rolled back")
        target = self._target(change["operation"])
        latest = self.latest_change.get(target)
        if latest and latest != change_id:
            raise ConfigurationPolicyError(
                "a newer configuration change exists for this target; roll it back first"
            )

        self._apply_value(
            change["operation"], change["before"], manager
        )
        change["status"] = "rolled_back"
        change["rolled_back_by"] = approved_by.strip()
        change["rolled_back_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.latest_change.pop(target, None)
        self._save()
        return change

    @staticmethod
    def _target(op: Dict[str, Any]) -> str:
        if op["kind"] == "setting":
            return f"setting:{op['key']}"
        if op["kind"] == "module":
            return f"module:{op['module_id']}"
        return f"brain_option:{op['key']}"

    def _apply_value(self, op: Dict[str, Any], value: Any, manager) -> None:
        if op["kind"] == "setting":
            self.settings_manager.save_settings({op["key"]: value})
        elif op["kind"] == "module":
            manager = self._require_manager(manager)
            result = (
                manager.enable_module(op["module_id"]) if value
                else manager.disable_module(op["module_id"])
            )
            if result is None:
                raise ConfigurationPolicyError(
                    f"module '{op['module_id']}' could not be updated"
                )
        elif op["kind"] == "brain_option":
            config = self._read_brain_config()
            config[op["key"]] = value
            self._write_brain_config(config)
            if self.runtime_apply is not None:
                self.runtime_apply(op["key"], value)
        else:
            raise ConfigurationPolicyError(f"unsupported operation kind '{op['kind']}'")

    def status(self) -> Dict[str, Any]:
        self._prune_quotas()
        return {
            "changes": list(reversed(self.changes)),
            "quota": {
                "used": len(self.change_times),
                "limit": self.max_changes_per_day,
            },
            "allowed_settings": sorted(self.allowed_settings),
            "configurable_modules": sorted(self.configurable_modules - PROTECTED_MODULES),
        }

    def summary(self) -> Dict[str, Any]:
        """Compact status for brain snapshots (never embeds the change list)."""
        self._prune_quotas()
        return {
            "changes": len(self.changes),
            "last_change_at": self.changes[-1].get("applied_at") if self.changes else None,
            "quota": {
                "used": len(self.change_times),
                "limit": self.max_changes_per_day,
            },
            "allowed_settings": len(self.allowed_settings),
            "configurable_modules": len(self.configurable_modules - PROTECTED_MODULES),
        }

    @staticmethod
    def _normalize(operation: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(operation, dict):
            raise ConfigurationPolicyError("operation must be an object")
        kind = operation.get("kind")
        if kind == "setting":
            key = operation.get("key")
            if not isinstance(key, str) or "value" not in operation:
                raise ConfigurationPolicyError("setting operation needs key and value")
            return {"kind": kind, "key": key, "value": operation["value"]}
        if kind == "module":
            module_id = operation.get("module_id")
            if not isinstance(module_id, str) or "enabled" not in operation:
                raise ConfigurationPolicyError("module operation needs module_id and enabled")
            return {
                "kind": kind,
                "module_id": module_id,
                "enabled": operation["enabled"],
            }
        if kind == "brain_option":
            key = operation.get("key")
            if not isinstance(key, str) or "value" not in operation:
                raise ConfigurationPolicyError("brain_option needs key and value")
            return {"kind": kind, "key": key, "value": operation["value"]}
        raise ConfigurationPolicyError("unsupported operation kind")

    @staticmethod
    def _require_manager(manager):
        if manager is None:
            raise ConfigurationPolicyError(
                "module manager is required for module configuration"
            )
        return manager

    @staticmethod
    def _validate_setting(key: str, value: Any) -> None:
        kind, minimum, maximum = ALLOWED_SETTINGS[key]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ConfigurationPolicyError(f"setting '{key}' must be a boolean")
            return
        if kind == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigurationPolicyError(f"setting '{key}' must be an integer")
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigurationPolicyError(f"setting '{key}' must be a number")
        if minimum is not None and value < minimum:
            raise ConfigurationPolicyError(f"setting '{key}' is below {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigurationPolicyError(f"setting '{key}' is above {maximum}")

    @staticmethod
    def _validate_brain_option(key: str, value: Any) -> None:
        kind, minimum, maximum = BRAIN_OPTIONS[key]
        if kind == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigurationPolicyError(f"brain option '{key}' must be an integer")
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigurationPolicyError(f"brain option '{key}' must be a number")
        if minimum is not None and value < minimum:
            raise ConfigurationPolicyError(f"brain option '{key}' is below {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigurationPolicyError(f"brain option '{key}' is above {maximum}")

    @staticmethod
    def _validate_allowed_tools(value: Any, config: Dict[str, Any]) -> None:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigurationPolicyError("allowed_tools must be a list of strings")
        current = set(config.get("allowed_tools") or [])
        if not set(value) <= current:
            raise ConfigurationPolicyError(
                "self-configuration may narrow allowed_tools, never widen them"
            )

    def _read_brain_config(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            config = manifest.get("config") if isinstance(manifest, dict) else None
            return dict(config) if isinstance(config, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationPolicyError(f"brain config is unreadable: {exc}") from exc

    def _write_brain_config(self, config: Dict[str, Any]) -> None:
        with open(self.config_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["config"] = config
        atomic_write_json(str(self.config_path), manifest)

    def _prune_quotas(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        kept = []
        for value in self.change_times:
            try:
                stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
            if stamp >= cutoff:
                kept.append(value)
        self.change_times = kept

    def _prune_changes(self) -> None:
        if len(self.changes) > self.max_changes:
            self.changes = self.changes[-self.max_changes:]

    def _save(self) -> None:
        try:
            self.store.save({
                "changes": self.changes,
                "change_times": self.change_times,
                "latest_change": self.latest_change,
            })
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist configuration ledger: %s", exc)
