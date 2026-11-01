"""Thread-scoped episode identity and the resume guard (IDEA-1110)."""

from core.episode_identity import (
    canonical_projection,
    input_digest,
    prepare_resume,
    resume_decision,
    resume_guard,
)
from core.session_manager import EpisodeState


def _request(text="summarise the report", **extra):
    data = {"messages": [{"role": "user", "content": text}], "plan": [{"step": "read"}]}
    data.update(extra)
    return data


def _episode(input_data=None, config=None):
    config = config or {}
    digest, projection = input_digest(input_data or _request(), config)
    return EpisodeState(input_sha256=digest, input_projection=projection), digest


def test_same_input_resumes():
    episode, digest = _episode()
    decision, current_sha, _ = resume_guard(episode, _request(), {})
    assert decision == "resume"
    assert current_sha == digest


def test_changed_model_starts_a_new_episode():
    episode, _ = _episode(config={"model": "model-a"})
    decision, _, _ = resume_guard(episode, _request(), {"model": "model-b"})
    assert decision == "new"


def test_changed_tool_set_starts_a_new_episode():
    episode, _ = _episode(config={"allowed_tools": ["search"]})
    decision, _, _ = resume_guard(episode, _request(), {"allowed_tools": ["search", "write"]})
    assert decision == "new"


def test_changed_user_request_starts_a_new_episode():
    episode, _ = _episode()
    decision, _, _ = resume_guard(episode, _request("do something else"), {})
    assert decision == "new"


def test_legacy_episode_without_digest_asks():
    episode = EpisodeState()
    decision, _, _ = resume_guard(episode, _request(), {})
    assert decision == "ask"


def test_resume_any_overrides_a_changed_digest():
    episode, _ = _episode(config={"model": "model-a"})
    decision, _, _ = resume_guard(
        episode, _request(), {"model": "model-b", "resume_any": True}
    )
    assert decision == "resume"


def test_resume_decision_matrix():
    assert resume_decision("abc", "abc") == "resume"
    assert resume_decision("abc", "def") == "new"
    assert resume_decision(None, "def") == "ask"
    assert resume_decision("abc", "def", policy="any") == "resume"
    assert resume_decision("abc", "def", policy="ask") == "ask"


def test_volatile_keys_do_not_change_the_digest():
    first, _ = input_digest(_request(session_id="one", timestamp=1), {})
    second, _ = input_digest(_request(session_id="two", timestamp=2), {})
    assert first == second


def test_projection_keeps_stable_fields_and_sorts_tools():
    projection = canonical_projection(
        _request(model="x"), {"allowed_tools": ["b", "a", "b"], "model": "m"}
    )
    assert projection["model"] == "m"
    assert projection["tools"] == ["a", "b"]
    assert projection["request"]["plan"] == [{"step": "read"}]


def test_checkpoint_ids_sort_in_creation_order_and_parents_chain():
    episode = EpisodeState()
    first = episode.add_checkpoint()
    second = episode.add_checkpoint()
    third = episode.add_checkpoint()

    ids = [first["checkpoint_id"], second["checkpoint_id"], third["checkpoint_id"]]
    assert ids == sorted(ids)
    assert len(set(ids)) == 3
    assert first["parent_checkpoint_id"] is None
    assert second["parent_checkpoint_id"] == first["checkpoint_id"]
    assert third["parent_checkpoint_id"] == second["checkpoint_id"]


def test_checkpoint_source_follows_phase():
    episode = EpisodeState()
    loop = episode.add_checkpoint()
    episode.update_phase(EpisodeState.PHASE_REPLANNING)
    replan = episode.add_checkpoint()
    episode.update_phase(EpisodeState.PHASE_COMPLETED)
    done = episode.add_checkpoint()
    assert loop["source"] == "loop"
    assert replan["source"] == "replan"
    assert done["source"] == "complete"


def test_episode_round_trip_keeps_identity():
    episode, digest = _episode()
    episode.add_checkpoint()
    restored = EpisodeState.from_dict(episode.to_dict())
    assert restored.input_sha256 == digest
    assert restored.input_projection == episode.input_projection
    assert restored.checkpoints[0]["checkpoint_id"] == episode.checkpoints[0]["checkpoint_id"]


class _FakeManager:
    def __init__(self):
        self.saved = []

    def save_episode_state(self, **kwargs):
        self.saved.append(kwargs)


def test_prepare_resume_pauses_a_refused_episode():
    episode, _ = _episode(config={"model": "model-a"})
    manager = _FakeManager()

    allowed, decision, _, _ = prepare_resume(
        manager, episode, _request(), {"model": "model-b"}
    )

    assert allowed is False
    assert decision == "new"
    assert episode.phase == EpisodeState.PHASE_PAUSED
    assert manager.saved[-1]["phase"] == EpisodeState.PHASE_PAUSED
