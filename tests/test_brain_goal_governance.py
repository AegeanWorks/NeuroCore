"""Goal-governed improvement: a promotion names an active goal or a self-repair reason."""

import pytest

from tests.test_brain_improvement import make_engine, make_flow_manager, rename_ops


def linked_engine(tmp_path, goal_map, marked=None):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(
        tmp_path, manager,
        require_goal_link=True,
        goal_lookup=goal_map.get,
        goal_marker=(marked.append if marked is not None else lambda _goal_id: None),
    )
    return engine


async def test_a_candidate_without_a_goal_or_repair_reason_is_rejected(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(
        tmp_path, manager, require_goal_link=True, goal_lookup=lambda _id: None,
    )

    with pytest.raises(ValueError, match="link an active goal_id"):
        await engine.propose("echo-flow", rename_ops(), "unjustified change")


async def test_an_unknown_goal_is_rejected(tmp_path):
    engine = linked_engine(tmp_path, {})

    with pytest.raises(ValueError, match="does not exist"):
        await engine.propose("echo-flow", rename_ops(), "change", goal_id="404")


async def test_a_completed_goal_cannot_justify_a_change(tmp_path):
    engine = linked_engine(
        tmp_path, {"1": {"id": 1, "status": "completed", "description": "old"}},
    )

    with pytest.raises(ValueError, match="completed"):
        await engine.propose("echo-flow", rename_ops(), "change", goal_id="1")


async def test_an_active_goal_is_recorded_on_the_candidate(tmp_path):
    marked = []
    engine = linked_engine(
        tmp_path,
        {"1": {"id": 1, "status": "in_progress", "description": "Make echo robust"}},
        marked=marked,
    )

    candidate = await engine.propose(
        "echo-flow", rename_ops(), "change", goal_id="1",
    )

    assert candidate["goal_id"] == "1"
    assert candidate["goal_kind"] == "goal"
    assert candidate["goal_title"] == "Make echo robust"
    assert marked == [], "proposing does not touch the goal; promotion does"


async def test_a_self_repair_candidate_needs_a_reason(tmp_path):
    engine = linked_engine(tmp_path, {})
    with pytest.raises(ValueError, match="self_repair"):
        await engine.propose("echo-flow", rename_ops(), "", goal_kind="self_repair")

    candidate = await engine.propose(
        "echo-flow", rename_ops(), "repair the broken echo node",
        goal_kind="self_repair",
    )
    assert candidate["goal_id"] is None
    assert candidate["goal_kind"] == "self_repair"
    assert "repair" in candidate["goal_title"]


async def test_an_unknown_goal_kind_is_rejected(tmp_path):
    engine = linked_engine(tmp_path, {})
    with pytest.raises(ValueError, match="goal_kind"):
        await engine.propose("echo-flow", rename_ops(), "x", goal_kind="vibes")


async def test_an_unknown_active_goal_is_rejected(tmp_path):
    engine = linked_engine(tmp_path, {})
    with pytest.raises(ValueError, match="does not exist"):
        await engine.propose("echo-flow", rename_ops(), "x", goal_id="7")


async def test_promotion_revalidates_the_goal_and_marks_it_in_progress(tmp_path):
    goal_map = {"1": {"id": 1, "status": "pending", "description": "Improve echo"}}
    marked = []
    engine = linked_engine(tmp_path, goal_map, marked=marked)
    candidate = await engine.propose(
        "echo-flow", rename_ops(), "change", goal_id="1",
    )

    # The operator completes the goal while the candidate is being evaluated.
    goal_map["1"] = {"id": 1, "status": "completed", "description": "Improve echo"}
    with pytest.raises(ValueError, match="no longer active"):
        await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    # Reactivating it allows the promotion, which starts the goal.
    goal_map["1"] = {"id": 1, "status": "pending", "description": "Improve echo"}
    promoted = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")
    assert promoted["status"] == "promoted"
    assert promoted["goal_id"] == "1"
    assert marked == ["1"]


async def test_unlinked_mode_keeps_the_engine_usable_for_direct_embedding(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager)

    candidate = await engine.propose("echo-flow", rename_ops(), "direct use")

    assert candidate["goal_id"] is None
    assert candidate["goal_kind"] == "goal"


def configuration_engine(tmp_path, goals):
    from tests.test_brain_configuration import make_engine as make_config_engine

    return make_config_engine(
        tmp_path, require_goal_link=True, goal_lookup=goals.get,
    )


def test_configuration_apply_requires_a_goal_or_repair_reason(tmp_path):
    from modules.brain.configuration import ConfigurationPolicyError

    engine, settings, _, _, _ = configuration_engine(tmp_path, {})
    with pytest.raises(ConfigurationPolicyError, match="link an active goal_id"):
        engine.apply(
            {"kind": "setting", "key": "temperature", "value": 0.2},
            approved_by="tester",
        )
    assert settings.values["temperature"] == 0.7, "a rejected change is never applied"


def test_configuration_apply_records_an_active_goal(tmp_path):
    engine, settings, _, _, _ = configuration_engine(
        tmp_path, {"1": {"id": 1, "status": "pending", "description": "Tune temperature"}},
    )

    change = engine.apply(
        {"kind": "setting", "key": "temperature", "value": 0.2, "goal_id": "1"},
        approved_by="tester",
    )

    assert change["goal_id"] == "1"
    assert change["goal_title"] == "Tune temperature"
    assert settings.values["temperature"] == 0.2


def test_configuration_apply_accepts_an_explicit_self_repair(tmp_path):
    engine, _, _, _, _ = configuration_engine(tmp_path, {})

    change = engine.apply(
        {"kind": "setting", "key": "temperature", "value": 0.2,
         "goal_kind": "self_repair", "reason": "the previous value starved the retry loop"},
        approved_by="tester",
    )

    assert change["goal_kind"] == "self_repair"
    assert "starved" in change["goal_title"]


def test_configuration_apply_rejects_a_completed_goal(tmp_path):
    from modules.brain.configuration import ConfigurationPolicyError

    engine, _, _, _, _ = configuration_engine(
        tmp_path, {"1": {"id": 1, "status": "completed", "description": "Done"}},
    )
    with pytest.raises(ConfigurationPolicyError, match="completed"):
        engine.apply(
            {"kind": "setting", "key": "temperature", "value": 0.2, "goal_id": "1"},
            approved_by="tester",
        )
