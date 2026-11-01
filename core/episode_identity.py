"""Episode identity and the resume guard (IDEA-1110).

An episode is resumed only when the caller addresses the same episode and the normalised request,
model and tool set match the digest stored with it. A changed digest starts a new episode and
leaves the old one paused, so a stale plan cannot silently run against a different configuration.
Legacy episodes without a digest ask instead of resuming.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

VOLATILE_KEYS = {
    "request_id", "trace_id", "session_id", "episode_id", "correlation_id", "nonce",
    "uuid", "timestamp", "timestamps", "ts", "created_at", "updated_at", "checkpoint_id",
    "parent_checkpoint_id",
}

RESUME_POLICIES = ("strict", "ask", "any")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if str(key).lower() not in VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _tool_names(config: Dict[str, Any]) -> List[str]:
    tools = config.get("allowed_tools") or config.get("tools") or []
    if isinstance(tools, dict):
        tools = list(tools.keys())
    names = []
    for tool in tools or []:
        if isinstance(tool, dict):
            name = tool.get("name") or tool.get("id")
            if name:
                names.append(str(name))
        elif tool:
            names.append(str(tool))
    return sorted(set(names))


def canonical_projection(input_data: Optional[Dict[str, Any]],
                         config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The stable part of a request that must match before a resume is allowed."""
    config = config or {}
    request = _clean(input_data or {})
    projection = {
        "request": request,
        "model": config.get("model") or config.get("default_model") or "",
        "tools": _tool_names(config),
    }
    if config.get("plan") is not None:
        projection["plan"] = _clean(config.get("plan"))
    return projection


def digest_projection(projection: Dict[str, Any]) -> str:
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def input_digest(input_data: Optional[Dict[str, Any]],
                 config: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    projection = canonical_projection(input_data, config)
    return digest_projection(projection), projection


def normalise_policy(policy: Any) -> str:
    return policy if policy in RESUME_POLICIES else "strict"


def resume_decision(existing_sha: Optional[str], current_sha: str,
                    policy: str = "strict", resume_any: bool = False) -> str:
    """Return ``resume``, ``new`` or ``ask`` for one episode comparison."""
    if resume_any or normalise_policy(policy) == "any":
        return "resume"
    if not existing_sha:
        return "ask"
    if existing_sha == current_sha:
        return "resume"
    if normalise_policy(policy) == "ask":
        return "ask"
    return "new"


def resume_guard(episode: Any, input_data: Optional[Dict[str, Any]],
                 config: Optional[Dict[str, Any]] = None,
                 policy: str = "strict") -> Tuple[str, str, Dict[str, Any]]:
    """Compare an existing episode with this request.

    Returns ``(decision, current_sha, projection)`` so the caller can store the digest on the new
    episode and name the decision in a log line.
    """
    current_sha, projection = input_digest(input_data, config)
    decision = resume_decision(
        getattr(episode, "input_sha256", None),
        current_sha,
        policy,
        bool((config or {}).get("resume_any", False)),
    )
    return decision, current_sha, projection


def prepare_resume(session_manager: Any, episode: Any,
                   input_data: Optional[Dict[str, Any]],
                   config: Optional[Dict[str, Any]] = None
                   ) -> Tuple[bool, str, str, Dict[str, Any]]:
    """Guard one candidate episode, pausing it when it must not be resumed.

    Returns ``(allowed, decision, current_sha, projection)``. A refused candidate is left paused
    on disk for inspection instead of being overwritten by the new episode.
    """
    config = config or {}
    policy = config.get("resume_policy", "strict")
    decision, current_sha, projection = resume_guard(episode, input_data, config, policy)
    if decision == "resume":
        if not getattr(episode, "input_sha256", None):
            episode.input_sha256 = current_sha
            episode.input_projection = projection
        return True, decision, current_sha, projection
    episode.update_phase("paused")
    session_manager.save_episode_state(phase="paused", add_checkpoint=False)
    return False, decision, current_sha, projection
