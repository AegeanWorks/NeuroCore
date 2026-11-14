"""Two-tier episode retention with a reuse guard (IDEA-1153).

An episode has two independent lifetimes: when its result may be purged and when its journal may
be purged. The journal lifetime never exceeds the result lifetime, because a journal without the
status that explains it is unreviewable. A completed episode past its result window is moved to
``episode_dir/quarantine/<id>-<timestamp>/`` rather than unlinked, so a bad policy change is
recoverable. An active, executing, replanning or paused episode is never pruned by the scanner.

Reuse guard: a new episode that reuses an ``episode_id`` gets ``generation + 1`` and a tombstone
records the purge, so a caller cannot read a purged predecessor's result under the same id.
"""

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

POLICY_VERSION = 1

logger = logging.getLogger(__name__)

TERMINAL_PHASES = ("completed", "failed")
ACTIVE_PHASES = ("planning", "executing", "replanning", "paused", "started")
DEFAULT_PAGE_SIZE = 100


@dataclass(frozen=True)
class RetentionPolicy:
    """Operator-facing knobs; every duration is in days."""

    completion_days: float = 30.0
    journal_days: float = 7.0
    max_episodes: int = 500
    stale_after_days: float = 7.0

    def validate(self) -> "RetentionPolicy":
        try:
            completion = float(self.completion_days)
            journal = float(self.journal_days)
            max_episodes = int(self.max_episodes)
            stale_after = float(self.stale_after_days)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "retention policy values must be numbers. "
                f"Why: {exc}. Fix: use days and counts, e.g. 30, 7, 500."
            ) from exc
        if completion <= 0 or journal < 0:
            raise ValueError(
                "retention durations must be positive (completion_days > 0, journal_days >= 0). "
                "Why: a zero window would purge a fresh result. Fix: use days, e.g. 30 and 7."
            )
        return self


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compute_retention(completed_at: datetime, policy: RetentionPolicy,
                      has_journal: bool = True) -> Dict[str, Optional[datetime]]:
    """Return both expiry stamps; the journal expiry is capped by the completion expiry."""
    completion_at = completed_at + timedelta(days=policy.completion_days)
    if not has_journal:
        return {"completion_at": completion_at, "journal_at": None}
    journal_at = completed_at + timedelta(days=min(policy.journal_days, policy.completion_days))
    if journal_at > completion_at:
        journal_at = completion_at
    return {"completion_at": completion_at, "journal_at": journal_at}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _episode_files(episode_dir: str, page_size: int) -> List[str]:
    try:
        names = sorted(
            name for name in os.listdir(episode_dir)
            if name.endswith(".json") and name != "current.json"
        )
    except OSError:
        return []
    try:
        limit = max(1, int(page_size))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_SIZE
    return names[:limit]


def _tombstone_path(episode_dir: str) -> str:
    return os.path.join(episode_dir, "tombstones.json")


def _read_tombstones(episode_dir: str) -> Dict[str, Dict[str, Any]]:
    try:
        with open(_tombstone_path(episode_dir), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_tombstones(episode_dir: str, tombstones: Dict[str, Dict[str, Any]]) -> None:
    path = _tombstone_path(episode_dir)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(tombstones, handle, indent=2)
    os.replace(temp, path)


def purge_tombstoned(episode_dir: str, episode_id: str) -> Optional[Dict[str, Any]]:
    """The purge record for an id, if a predecessor was quarantined."""
    return _read_tombstones(episode_dir).get(str(episode_id))


def prune_episodes(episode_dir: str, policy: Optional[RetentionPolicy] = None,
                   now: Optional[datetime] = None, dry_run: bool = False,
                   page_size: int = DEFAULT_PAGE_SIZE,
                   purge_journal: Optional[Callable[[str], int]] = None
                   ) -> Dict[str, int]:
    """Scan completed episodes and quarantine the expired ones, in bounded pages."""
    policy = (policy or RetentionPolicy()).validate()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    counts = {
        "purged_status": 0,
        "purged_journal": 0,
        "skipped_active": 0,
        "skipped_corrupt": 0,
        "bytes_freed": 0,
    }
    if not os.path.isdir(episode_dir):
        return counts

    tombstones = _read_tombstones(episode_dir)
    for name in _episode_files(episode_dir, page_size):
        path = os.path.join(episode_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            counts["skipped_corrupt"] += 1
            continue
        if not isinstance(data, dict):
            counts["skipped_corrupt"] += 1
            continue

        episode_id = str(data.get("episode_id") or os.path.splitext(name)[0])
        phase = str(data.get("phase") or "")
        completed_at = _parse(data.get("completed_at"))
        updated_at = _parse(data.get("updated_at")) or _parse(data.get("created_at"))

        if phase not in TERMINAL_PHASES:
            # Only a completed result has a retention clock; a live or paused episode is never
            # swept, even when it looks stale, because a person may still resume it.
            counts["skipped_active"] += 1
            continue

        if completed_at is None:
            completed_at = updated_at
        if completed_at is None:
            counts["skipped_corrupt"] += 1
            continue
        if completed_at > now:
            # A future timestamp is a clock problem, not an expiry.
            counts["skipped_corrupt"] += 1
            continue

        window = compute_retention(completed_at, policy,
                                   has_journal=bool(data.get("tool_journal")))
        if window["journal_at"] is not None and now >= window["journal_at"]:
            counts["purged_journal"] += 1
            if purge_journal is not None and not dry_run:
                try:
                    purge_journal(episode_id)
                except Exception as exc:
                    logger.warning("Journal purge failed for %s: %s", episode_id, exc)
        if now < window["completion_at"]:
            continue

        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        counts["purged_status"] += 1
        counts["bytes_freed"] += size
        if dry_run:
            continue

        try:
            generation = int(data.get("generation") or 1)
        except (TypeError, ValueError):
            generation = 1
        tombstones[episode_id] = {
            "episode_id": episode_id,
            "generation": generation,
            "purged_at": _iso(now),
            "reason": "completion_retention",
            "policy_version": POLICY_VERSION,
            "policy": asdict(policy),
        }
        try:
            _write_tombstones(episode_dir, tombstones)
        except OSError as exc:
            logger.warning("Could not write a tombstone for %s: %s", episode_id, exc)
        quarantine = os.path.join(
            episode_dir, "quarantine", f"{episode_id}-{int(now.timestamp())}"
        )
        try:
            os.makedirs(quarantine, exist_ok=True)
            shutil.move(path, os.path.join(quarantine, name))
        except OSError:
            counts["purged_status"] -= 1
            counts["bytes_freed"] -= size
            counts["skipped_corrupt"] += 1

    return counts
