"""Content-addressed versions for skill documents.

A skill is prompt material: editing it in place removes the only evidence of what the model was
told before. Every create and update records the exact content under its SHA-256, keeps a small
per-skill index and bounds history so a busy editor cannot grow the disk without limit. Rollback
reads an old version and writes it as a new version, so a rollback is itself auditable
(ADR-044).
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_VERSIONS_PER_SKILL = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError as exc:
            # The write itself already failed, so just leave the stray temp file for the
            # operator instead of masking the original error.
            logger.debug("Could not remove temporary skill version file %s: %s", temp_path, exc)
        raise


class SkillVersionStore:
    """Per-skill version history rooted beside the skill storage."""

    def __init__(self, root: str):
        self.root = os.path.join(root, "versions")
        os.makedirs(self.root, exist_ok=True)

    def _skill_dir(self, skill_id: str) -> str:
        safe = "".join(ch for ch in str(skill_id) if ch.isalnum() or ch in "-_. ")
        safe = safe.strip().replace(" ", "_")
        if not safe:
            raise ValueError("skill_id must contain at least one safe character")
        return os.path.join(self.root, safe)

    def _index_path(self, skill_id: str) -> str:
        return os.path.join(self._skill_dir(skill_id), "index.json")

    def _load_index(self, skill_id: str) -> Dict[str, Any]:
        path = self._index_path(skill_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("versions"), list):
                return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Could not read the skill version index for %s: %s", skill_id, exc)
        return {"skill_id": str(skill_id), "active": None, "versions": []}

    def _save_index(self, skill_id: str, index: Dict[str, Any]) -> None:
        _atomic_write(self._index_path(skill_id), json.dumps(index, indent=2) + "\n")

    def record(self, skill_id: str, content: str, author: str = "",
               note: str = "") -> Dict[str, Any]:
        """Store one version of the content and mark it active."""
        if not isinstance(content, str):
            raise TypeError("skill content must be text")
        index = self._load_index(skill_id)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        number = (index["versions"][-1]["version"] + 1) if index["versions"] else 1
        skill_dir = self._skill_dir(skill_id)
        body_path = os.path.join(skill_dir, f"v{number}-{digest[:12]}.md")
        _atomic_write(body_path, content)
        record = {
            "version": number,
            "digest": digest,
            "created_at": _now(),
            "author": str(author or ""),
            "note": str(note or ""),
            "parent": index["versions"][-1]["version"] if index["versions"] else None,
            "file": os.path.basename(body_path),
        }
        index["versions"].append(record)
        index["active"] = number
        self._prune(skill_dir, index)
        self._save_index(skill_id, index)
        return dict(record)

    @staticmethod
    def _prune(skill_dir: str, index: Dict[str, Any]) -> None:
        excess = len(index["versions"]) - MAX_VERSIONS_PER_SKILL
        while excess > 0:
            oldest = index["versions"][0]
            if oldest.get("version") == index.get("active") and len(index["versions"]) == 1:
                break
            index["versions"].pop(0)
            try:
                os.remove(os.path.join(skill_dir, str(oldest.get("file"))))
            except OSError as exc:
                logger.warning("Could not prune the skill version file %s: %s",
                               oldest.get("file"), exc)
            excess -= 1

    def versions(self, skill_id: str) -> List[Dict[str, Any]]:
        index = self._load_index(skill_id)
        return [
            {key: value for key, value in record.items() if key != "file"}
            for record in index["versions"]
        ]

    def active(self, skill_id: str) -> Optional[int]:
        return self._load_index(skill_id).get("active")

    def read(self, skill_id: str, version: int) -> Optional[Dict[str, Any]]:
        index = self._load_index(skill_id)
        record = next(
            (item for item in index["versions"] if item.get("version") == int(version)), None
        )
        if record is None:
            return None
        path = os.path.join(self._skill_dir(skill_id), str(record.get("file")))
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                content = handle.read()
        except OSError:
            return None
        result = {key: value for key, value in record.items() if key != "file"}
        result["content"] = content
        result["active"] = index.get("active") == record["version"]
        return result

    def activate(self, skill_id: str, version: int) -> bool:
        """Point the active marker at an existing version without rewriting the file."""
        index = self._load_index(skill_id)
        if not any(item.get("version") == int(version) for item in index["versions"]):
            return False
        index["active"] = int(version)
        self._save_index(skill_id, index)
        return True
