"""Declared tool primitives and pre-call capability refusal (IDEA-1172).

A tool manifest entry may name the runtime primitives it needs (``requires``) and where its code
lives (``source``). Before the tier gate, outbound staging or the sandbox start, ``preflight``
checks that every named primitive has a live code path here and that the source file exists. A
missing primitive or file is a refusal, not a silently empty execution: the legacy dispatcher used
to read ``library/<name>.py`` and run ``""`` when the file was missing, returning a success-shaped
empty result.

The supported set is built from the objects that actually execute (the sandbox, the library
directories, outbound staging and the approval ledger), not from a hand-kept constant.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

MANIFEST_VERSION = 2

SANDBOX = "sandbox"
LIBRARY_SOURCE = "library_source"
RLM_SOURCE = "rlm_library_source"
OUTBOUND_STAGING = "outbound_staging"
HUMAN_APPROVAL = "human_approval"
NETWORK = "network"
INLINE_SOURCE = "inline_source"
PROGRESS = "progress"
CANCEL = "cancel"

KNOWN_PRIMITIVES = frozenset({
    SANDBOX, LIBRARY_SOURCE, RLM_SOURCE, OUTBOUND_STAGING, HUMAN_APPROVAL,
    NETWORK, INLINE_SOURCE, PROGRESS, CANCEL,
})

SOURCE_MODES = {"library": LIBRARY_SOURCE, "rlm_library": RLM_SOURCE, "inline": INLINE_SOURCE}


@dataclass(frozen=True)
class ToolRequirement:
    name: str
    params: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityRefusal:
    tool: str
    missing: Tuple[str, ...]
    reason: str
    message: str
    status: str = "unsupported_capability"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "tool": self.tool,
            "missing": list(self.missing),
            "reason": self.reason,
            "message": self.message,
        }


def supported_primitives(library_dirs: Optional[Sequence[str]] = None) -> frozenset:
    """The primitives this runtime can actually honour, derived from live objects."""
    supported = {SANDBOX, OUTBOUND_STAGING, HUMAN_APPROVAL, NETWORK}
    for directory in library_dirs or _default_library_dirs():
        if os.path.isdir(directory):
            supported.add(
                RLM_SOURCE if os.path.basename(directory) == "rlm_library" else LIBRARY_SOURCE
            )
    return frozenset(supported)


def _default_library_dirs() -> Tuple[str, ...]:
    base = os.path.dirname(__file__)
    return (os.path.join(base, "library"), os.path.join(base, "rlm_library"))


def describe_supported(library_dirs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    dirs = tuple(library_dirs or _default_library_dirs())
    return {
        "manifest_version": MANIFEST_VERSION,
        "supported": sorted(supported_primitives(dirs)),
        "declared": sorted(KNOWN_PRIMITIVES),
        "library_dirs": [os.path.basename(path) for path in dirs],
    }


def parse_requirements(entry: Any) -> Tuple[List[ToolRequirement], Optional[str]]:
    """Read ``requires`` from a manifest entry; returns the list and an error message."""
    if not isinstance(entry, dict):
        return [], None
    raw = entry.get("requires")
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "Why: `requires` must be a list. Fix: use [\"sandbox\"], or omit the key."
    requirements: List[ToolRequirement] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            params: Dict[str, str] = {}
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"].strip()
            params = {str(k): str(v) for k, v in (item.get("params") or {}).items()}
        else:
            return [], (
                "Why: each `requires` entry must be a primitive name or {\"name\", \"params\"}. "
                "Fix: use [\"sandbox\"] or [{\"name\": \"network\"}]."
            )
        if name not in KNOWN_PRIMITIVES:
            return [], (
                f"Why: `{name}` is not a primitive this manifest version defines. "
                f"Fix: use one of {', '.join(sorted(KNOWN_PRIMITIVES))}, or remove it."
            )
        requirements.append(ToolRequirement(name=name, params=params))
    return requirements, None


def declared_primitives(entry: Any) -> List[str]:
    requirements, _ = parse_requirements(entry)
    return [requirement.name for requirement in requirements]


def resolve_source(tool: str, entry: Any,
                   library_dirs: Optional[Sequence[str]] = None) -> Optional[str]:
    """The on-disk file a manifest entry runs, or ``None`` when it cannot be found.

    An explicit ``source`` block wins. Without one, the legacy convention applies: the entry's
    name in ``library/``, with a case-insensitive fallback so a manifest that says ``Calculator``
    still finds ``calculator.py`` (the old exact-path lookup silently ran empty code).
    """
    dirs = [path for path in (library_dirs or _default_library_dirs()) if os.path.isdir(path)]
    source = entry.get("source") if isinstance(entry, dict) else None
    if isinstance(source, dict):
        mode = str(source.get("mode") or "library")
        if mode == "inline":
            return None
        filename = str(source.get("entry") or f"{tool}.py")
        base = dirs
        if mode == "rlm_library":
            base = [d for d in dirs if os.path.basename(d) == "rlm_library"]
        elif mode == "library":
            base = [d for d in dirs if os.path.basename(d) == "library"]
        return _find_file(filename, base)
    return _find_file(f"{tool}.py", dirs)


def _find_file(filename: str, dirs: Sequence[str]) -> Optional[str]:
    for directory in dirs:
        candidate = os.path.join(directory, filename)
        if os.path.isfile(candidate):
            return candidate
    lowered = filename.lower()
    for directory in dirs:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if name.lower() == lowered and os.path.isfile(os.path.join(directory, name)):
                return os.path.join(directory, name)
    return None


def preflight(tool: str, entry: Any, supported: Optional[frozenset] = None,
              library_dirs: Optional[Sequence[str]] = None) -> Optional[CapabilityRefusal]:
    """Return a refusal when the runtime cannot honour this entry, else ``None``.

    An entry with no ``requires`` and no explicit ``source`` keeps the legacy behaviour and is
    checked only for a resolvable file.
    """
    supported = supported if supported is not None else supported_primitives(library_dirs)
    requirements, error = parse_requirements(entry)
    if error:
        return CapabilityRefusal(tool, (), "invalid_manifest", error)

    missing = [requirement.name for requirement in requirements
               if requirement.name not in supported]
    if missing:
        names = ", ".join(missing)
        return CapabilityRefusal(
            tool, tuple(missing), "unsupported_primitives",
            f"Why: this runtime does not implement {names}, which tool '{tool}' declares it "
            f"needs, so running it would not do what the manifest says. "
            f"Fix: remove {names} from the tool's `requires`, or run it on a runtime that "
            "supports them.",
        )

    source = entry.get("source") if isinstance(entry, dict) else None
    if isinstance(source, dict) and str(source.get("mode") or "") == "inline":
        if INLINE_SOURCE not in supported:
            return CapabilityRefusal(
                tool, (INLINE_SOURCE,), "unsupported_primitives",
                f"Why: tool '{tool}' uses inline source, which this runtime cannot execute. "
                "Fix: ship it as a library file and set source.mode to \"library\".",
            )
        return None

    if resolve_source(tool, entry, library_dirs) is None:
        expected = (source or {}).get("entry") if isinstance(source, dict) else f"{tool}.py"
        return CapabilityRefusal(
            tool, (), "source_missing",
            f"Why: tool '{tool}' declares no runnable source file ('{expected}' was not found), "
            "so the dispatcher would otherwise run empty code and report success-shaped output. "
            "Fix: add the .py file to the tools library or correct source.entry.",
            status="missing_source",
        )
    return None


def manifest_from_legacy(tool: str, entry: Any,
                         library_dirs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """A v2 manifest for a tool that does not declare one yet, for the save path to persist."""
    resolved = resolve_source(tool, entry, library_dirs)
    mode = "library"
    entry_name = f"{tool}.py"
    if resolved:
        entry_name = os.path.basename(resolved)
        if os.path.basename(os.path.dirname(resolved)) == "rlm_library":
            mode = "rlm_library"
    return {
        "manifest_version": MANIFEST_VERSION,
        "requires": [SANDBOX, RLM_SOURCE if mode == "rlm_library" else LIBRARY_SOURCE],
        "source": {"mode": mode, "entry": entry_name},
    }
