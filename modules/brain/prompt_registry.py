"""Versioned prompt registry with digests and rollout labels (IDEA-1238).

A prompt used to live inside node code or module config with no identity, so a "rollout" was an
edit in place and a rollback meant finding the old text by hand. Here a prompt version is
immutable: publishing the same text is idempotent, a changed text gets the next monotonic version,
and a rollout is a label move (``production`` -> version N), never a content edit.

Each version carries a SHA-256 digest so two runs can be compared by ``(name, version)`` or by
digest, and the label history makes a rollback a single auditable operation.
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.paths import data_path
from modules.brain.persistence import LedgerLock, atomic_write_json

logger = logging.getLogger(__name__)

RESERVED_LABELS = ("latest",)


class PromptConflict(ValueError):
    """A label or version request that would make history ambiguous."""


@dataclass(frozen=True)
class PromptVersion:
    name: str
    version: int
    prompt: str
    digest: str
    labels: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    commit_message: str = ""
    created_at: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def digest_prompt(prompt: str, config: Optional[Dict[str, Any]] = None) -> str:
    payload = json.dumps(
        {"prompt": prompt, "config": config or {}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PromptRegistry:
    """A versioned prompt registry with an explicit serialisable state and optional persistence."""

    def __init__(self, state: Optional[Dict[str, Any]] = None, path: Optional[str] = None) -> None:
        self.path = path
        self._lock = LedgerLock(self.path) if self.path else None
        self._versions: Dict[str, List[PromptVersion]] = {}
        self._labels: Dict[str, Dict[str, int]] = {}
        self._label_history: List[Dict[str, Any]] = []
        if state:
            self.load_state(state)
        elif self.path and os.path.exists(self.path):
            self._reload_from_disk()

    def _reload_from_disk(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    self.load_state(data)
            except Exception as exc:
                logger.warning("Could not read prompt registry from %s: %s", self.path, exc)

    def _persist_to_disk(self) -> None:
        if self.path:
            try:
                atomic_write_json(self.path, self.dump_state())
            except Exception as exc:
                logger.warning("Could not save prompt registry to %s: %s", self.path, exc)

    # -- publishing ---------------------------------------------------------------------

    def publish(self, name: str, prompt: str, config: Optional[Dict[str, Any]] = None,
                tags: Optional[List[str]] = None, commit_message: str = "",
                labels: Optional[List[str]] = None) -> PromptVersion:
        """Publish an immutable version; identical content returns the existing version."""
        if self._lock:
            with self._lock.held():
                self._reload_from_disk()
                entry = self._publish_entry(name, prompt, config, tags, commit_message, labels)
                self._persist_to_disk()
                return entry
        entry = self._publish_entry(name, prompt, config, tags, commit_message, labels)
        self._persist_to_disk()
        return entry

    def _publish_entry(self, name: str, prompt: str, config: Optional[Dict[str, Any]] = None,
                       tags: Optional[List[str]] = None, commit_message: str = "",
                       labels: Optional[List[str]] = None) -> PromptVersion:
        name = str(name or "").strip()
        if not name:
            raise PromptConflict(
                "prompt name is required. Why: a version without a name cannot be resolved. "
                "Fix: pass a stable name such as 'chat.system'."
            )
        if not isinstance(prompt, str) or not prompt.strip():
            raise PromptConflict(
                f"prompt '{name}' is empty. Why: an empty prompt would erase the instruction. "
                "Fix: pass the prompt text."
            )
        digest = digest_prompt(prompt, config)
        versions = self._versions.setdefault(name, [])
        for existing in versions:
            if existing.digest == digest:
                if labels:
                    self._move_label_entry(name, existing.version, labels)
                return self._with_labels(existing)
        version = (versions[-1].version + 1) if versions else 1
        entry = PromptVersion(
            name=name, version=version, prompt=prompt, digest=digest,
            labels=[], tags=list(tags or []), config=dict(config or {}),
            commit_message=commit_message.strip(), created_at=time.time(),
        )
        versions.append(entry)
        if labels:
            self._move_label_entry(name, version, labels)
        return self._with_labels(entry)

    def _with_labels(self, entry: PromptVersion) -> PromptVersion:
        labels = sorted(
            label for label, version in self._labels.get(entry.name, {}).items()
            if version == entry.version
        )
        return PromptVersion(**{**entry.__dict__, "labels": labels})

    # -- labels -------------------------------------------------------------------------

    def move_label(self, name: str, version: int, labels: List[str],
                   actor: str = "api") -> Dict[str, Any]:
        """Point one or more rollout labels at a version and record the move."""
        if self._lock:
            with self._lock.held():
                self._reload_from_disk()
                res = self._move_label_entry(name, version, labels, actor)
                self._persist_to_disk()
                return res
        res = self._move_label_entry(name, version, labels, actor)
        self._persist_to_disk()
        return res

    def _move_label_entry(self, name: str, version: int, labels: List[str],
                          actor: str = "api") -> Dict[str, Any]:
        known = {entry.version for entry in self._versions.get(name, [])}
        if int(version) not in known:
            raise PromptConflict(
                f"version {version} does not exist for prompt '{name}'. "
                "Why: a label must resolve to a real version. Fix: publish it first."
            )
        moved = {}
        target = self._labels.setdefault(name, {})
        for label in labels:
            label = str(label or "").strip()
            if not label:
                continue
            if label in RESERVED_LABELS:
                raise PromptConflict(
                    f"'{label}' is reserved. Why: it would collide with the latest-version "
                    "lookup. Fix: use a rollout label such as 'production' or 'canary'."
                )
            previous = target.get(label)
            target[label] = int(version)
            moved[label] = {"from": previous, "to": int(version)}
            self._label_history.append({
                "name": name, "label": label, "from": previous, "to": int(version),
                "actor": actor, "at": time.time(),
            })
        return moved

    def resolve(self, name: str, label: str = "latest") -> Optional[PromptVersion]:
        if self._lock:
            with self._lock.held():
                self._reload_from_disk()
                return self._resolve_entry(name, label)
        return self._resolve_entry(name, label)

    def _resolve_entry(self, name: str, label: str = "latest") -> Optional[PromptVersion]:
        versions = self._versions.get(name) or []
        if not versions:
            return None
        if label == "latest":
            return self._with_labels(versions[-1])
        version = self._labels.get(name, {}).get(label)
        if version is None:
            return None
        for entry in versions:
            if entry.version == version:
                return self._with_labels(entry)
        return None

    def history(self, name: str) -> List[PromptVersion]:
        if self._lock:
            with self._lock.held():
                self._reload_from_disk()
                return [self._with_labels(entry) for entry in self._versions.get(name, [])]
        return [self._with_labels(entry) for entry in self._versions.get(name, [])]

    def label_history(self, name: str = None) -> List[Dict[str, Any]]:
        if self._lock:
            with self._lock.held():
                self._reload_from_disk()
                return [
                    item for item in self._label_history
                    if name is None or item.get("name") == name
                ]
        return [
            item for item in self._label_history
            if name is None or item.get("name") == name
        ]

    def rollback(self, name: str, label: str, actor: str = "api") -> Optional[PromptVersion]:
        """Move a label back to the version it pointed at before the latest move."""
        if self._lock:
            with self._lock.held():
                self._reload_from_disk()
                res = self._rollback_entry(name, label, actor)
                self._persist_to_disk()
                return res
        res = self._rollback_entry(name, label, actor)
        self._persist_to_disk()
        return res

    def _rollback_entry(self, name: str, label: str, actor: str = "api") -> Optional[PromptVersion]:
        moves = [
            item for item in self._label_history
            if item.get("name") == name and item.get("label") == label
        ]
        if not moves:
            return None
        previous = moves[-1].get("from")
        if previous is None:
            return None
        self._move_label_entry(name, int(previous), [label], actor=actor)
        return self._resolve_entry(name, label)

    # -- persistence --------------------------------------------------------------------

    def dump_state(self) -> Dict[str, Any]:
        return {
            "versions": {
                name: [version.as_dict() for version in versions]
                for name, versions in self._versions.items()
            },
            "labels": {name: dict(labels) for name, labels in self._labels.items()},
            "label_history": list(self._label_history),
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        self._versions = {
            name: [
                PromptVersion(
                    name=item["name"], version=int(item["version"]),
                    prompt=item["prompt"], digest=item["digest"],
                    labels=list(item.get("labels", [])), tags=list(item.get("tags", [])),
                    config=dict(item.get("config", {})),
                    commit_message=item.get("commit_message", ""),
                    created_at=float(item.get("created_at", 0.0)),
                )
                for item in versions
            ]
            for name, versions in (state.get("versions") or {}).items()
        }
        self._labels = {
            name: {label: int(version) for label, version in labels.items()}
            for name, labels in (state.get("labels") or {}).items()
        }
        self._label_history = list(state.get("label_history") or [])

    def compare(self, name: str, label_a: str, label_b: str) -> Dict[str, Any]:
        """Compare two rollout labels by identity, not by prose."""
        first = self.resolve(name, label_a)
        second = self.resolve(name, label_b)
        if first is None or second is None:
            raise PromptConflict(
                f"cannot compare '{label_a}' with '{label_b}' for '{name}'. "
                "Why: at least one label does not resolve. Fix: check GET /prompts/{name}."
            )
        return {
            "name": name,
            "a": {"label": label_a, "version": first.version, "digest": first.digest},
            "b": {"label": label_b, "version": second.version, "digest": second.digest},
            "same_version": first.version == second.version,
            "same_content": first.digest == second.digest,
        }


_DEFAULT_REGISTRY: Optional[PromptRegistry] = None


def default_registry(path: Optional[str] = None) -> PromptRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None or path is not None:
        file_path = path or data_path("prompt_registry.json")
        _DEFAULT_REGISTRY = PromptRegistry(path=file_path)
    return _DEFAULT_REGISTRY
