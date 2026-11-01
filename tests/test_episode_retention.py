"""Two-tier episode retention with a reuse guard (IDEA-1153)."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from core.retention import (
    RetentionPolicy,
    compute_retention,
    prune_episodes,
    purge_tombstoned,
)
from core.session_manager import EpisodeState, SessionManager

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _write_episode(directory, episode_id, phase="completed", age_days=0, **extra):
    completed = NOW - timedelta(days=age_days)
    data = {
        "episode_id": episode_id,
        "phase": phase,
        "created_at": completed.isoformat(),
        "updated_at": completed.isoformat(),
        "generation": 1,
    }
    if phase in ("completed", "failed"):
        data["completed_at"] = completed.isoformat()
    data.update(extra)
    path = os.path.join(directory, f"{episode_id}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


@pytest.fixture()
def episode_dir(tmp_path):
    directory = tmp_path / "episodes"
    directory.mkdir()
    return str(directory)


def test_journal_expiry_never_exceeds_completion():
    window = compute_retention(NOW, RetentionPolicy(completion_days=3, journal_days=30))
    assert window["journal_at"] == window["completion_at"]


def test_expired_completion_is_quarantined(episode_dir):
    _write_episode(episode_dir, "old", age_days=40, tool_journal=[{"call_id": "c"}])
    purged = []

    counts = prune_episodes(
        episode_dir, RetentionPolicy(completion_days=30, journal_days=7), now=NOW,
        dry_run=False, purge_journal=purged.append,
    )

    assert counts["purged_status"] == 1
    assert counts["purged_journal"] == 1
    assert not os.path.exists(os.path.join(episode_dir, "old.json"))
    assert os.path.isdir(os.path.join(episode_dir, "quarantine"))
    assert purged == ["old"]
    assert purge_tombstoned(episode_dir, "old")["generation"] == 1


def test_live_completion_expires_its_journal_first(episode_dir):
    _write_episode(episode_dir, "fresh", age_days=10, tool_journal=[{"call_id": "c"}])
    purged = []

    counts = prune_episodes(
        episode_dir, RetentionPolicy(completion_days=30, journal_days=7), now=NOW,
        dry_run=False, purge_journal=purged.append,
    )

    assert counts["purged_status"] == 0
    assert counts["purged_journal"] == 1
    assert purged == ["fresh"]
    assert os.path.exists(os.path.join(episode_dir, "fresh.json"))


def test_executing_episode_is_never_pruned(episode_dir):
    _write_episode(episode_dir, "live", phase="executing", age_days=400)

    counts = prune_episodes(episode_dir, now=NOW, dry_run=False)

    assert counts["skipped_active"] == 1
    assert counts["purged_status"] == 0
    assert os.path.exists(os.path.join(episode_dir, "live.json"))


def test_corrupt_file_is_quarantined_as_corrupt_not_purged(episode_dir):
    with open(os.path.join(episode_dir, "broken.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    counts = prune_episodes(episode_dir, now=NOW, dry_run=False)

    assert counts["skipped_corrupt"] == 1
    assert counts["purged_status"] == 0
    assert os.path.exists(os.path.join(episode_dir, "broken.json"))


def test_future_timestamp_is_treated_as_a_clock_problem(episode_dir):
    future = (NOW + timedelta(days=30)).isoformat()
    _write_episode(episode_dir, "future", age_days=0)
    path = os.path.join(episode_dir, "future.json")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    data["completed_at"] = future
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    counts = prune_episodes(episode_dir, now=NOW, dry_run=False)

    assert counts["skipped_corrupt"] == 1
    assert counts["purged_status"] == 0


def test_dry_run_reports_without_moving_anything(episode_dir):
    _write_episode(episode_dir, "old", age_days=40)

    counts = prune_episodes(episode_dir, now=NOW, dry_run=True)

    assert counts["purged_status"] == 1
    assert os.path.exists(os.path.join(episode_dir, "old.json"))
    assert not os.path.isdir(os.path.join(episode_dir, "quarantine"))


def test_scan_is_bounded_by_page_size(episode_dir):
    for index in range(5):
        _write_episode(episode_dir, f"old-{index}", age_days=40)

    counts = prune_episodes(episode_dir, now=NOW, dry_run=True, page_size=2)

    assert counts["purged_status"] == 2


def test_policy_validation_refuses_a_zero_window():
    with pytest.raises(ValueError):
        RetentionPolicy(completion_days=0).validate()


def test_terminal_episode_gets_both_expiry_stamps():
    episode = EpisodeState()
    episode.tool_journal = [{"call_id": "c"}]
    episode.phase = EpisodeState.PHASE_COMPLETED

    episode.stamp_retention(RetentionPolicy(completion_days=30, journal_days=7))

    assert episode.completed_at is not None
    assert episode.expires_at is not None
    assert episode.journal_expires_at is not None
    assert episode.journal_expires_at <= episode.expires_at


def test_running_episode_is_not_stamped():
    episode = EpisodeState()
    episode.phase = EpisodeState.PHASE_EXECUTING
    episode.stamp_retention()
    assert episode.completed_at is None


def test_reused_episode_id_bumps_the_generation(tmp_path):
    directory = tmp_path / "episodes"
    directory.mkdir()
    _write_episode(str(directory), "reuse", age_days=40)
    prune_episodes(str(directory), now=NOW, dry_run=False)

    manager = SessionManager(
        session_file=str(tmp_path / "session.json"),
        trace_file=str(tmp_path / "trace.jsonl"),
        episode_dir=str(directory),
    )
    episode = manager.create_episode(episode_id="reuse")

    assert episode.generation == 2
