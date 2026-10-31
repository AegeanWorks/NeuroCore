"""Cross-process safety of the brain ledgers: reload, mutate, persist under one lock."""

import pytest
from filelock import Timeout

from core.flow_evolution import FlowEvolution
from core.flow_manager import FlowManager
from modules.brain.auth import BrainActorRegistry
from modules.brain.configuration import ConfigurationEngine, ConfigurationStore
from modules.brain.events import EventBus
from modules.brain.improvement import ImprovementEngine, ImprovementStore
from modules.brain.kernel import BrainKernel, KernelStore
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.persistence import LedgerLock
from modules.brain.reactivity import ReactionStore, ReactivityEngine


def learning_loop(tmp_path):
    return LearningLoop(
        store=LearningStore(str(tmp_path / "learning.json")),
        mirror_experiences=False,
        holdout_ratio=0.0,
        min_occurrences=2,
    )


def test_learning_ledgers_do_not_clobber_each_other(tmp_path):
    first = learning_loop(tmp_path)
    second = learning_loop(tmp_path)

    first.record_outcome("chat", {"prompt": "hello"}, "hi", success=True, tags=["train"])
    second.record_outcome("chat", {"prompt": "bye"}, "later", success=True, tags=["train"])

    stored = LearningStore(str(tmp_path / "learning.json")).load()
    prompts = {item["input"]["prompt"] for item in stored["experiences"]}
    assert prompts == {"hello", "bye"}


def test_consolidation_sees_another_process_experience(tmp_path):
    first = learning_loop(tmp_path)
    second = learning_loop(tmp_path)

    first.record_outcome("chat", {"prompt": "ping"}, "pong", success=True, tags=["train"])
    second.record_outcome("chat", {"prompt": "ping"}, "pong", success=True, tags=["train"])

    result = second.consolidate()

    assert result["created"] == 1
    stored = LearningStore(str(tmp_path / "learning.json")).load()
    assert len(stored["skills"]) == 1
    assert next(iter(stored["skills"].values()))["occurrences"] == 2


def test_kernel_instances_share_blocked_action_counts(tmp_path):
    path = str(tmp_path / "kernel.json")
    first = BrainKernel(store=KernelStore(path))
    second = BrainKernel(store=KernelStore(path))

    first.record_blocked_action("first block")
    second.record_blocked_action("second block")

    assert KernelStore(path).load()["blocked_actions"] == 2


class FakeSettings:
    def __init__(self):
        self.values = {"temperature": 0.7}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save_settings(self, values):
        self.values.update(values)


def test_configuration_engines_keep_both_changes(tmp_path):
    path = str(tmp_path / "configuration.json")
    first = ConfigurationEngine(settings_manager=FakeSettings(), store=ConfigurationStore(path))
    second = ConfigurationEngine(settings_manager=FakeSettings(), store=ConfigurationStore(path))

    first.apply({"kind": "setting", "key": "temperature", "value": 0.8}, "first")
    second.apply({"kind": "setting", "key": "temperature", "value": 0.9}, "second")

    stored = ConfigurationStore(path).load()
    assert [change["approved_by"] for change in stored["changes"]] == ["first", "second"]


def test_actor_registries_keep_both_actors(tmp_path):
    path = str(tmp_path / "actors.json")
    first = BrainActorRegistry(path, iterations=10000)
    second = BrainActorRegistry(path, iterations=10000)

    first.create("alice", role="viewer")
    second.create("bob", role="operator")

    assert {actor["name"] for actor in first.list()} == {"alice", "bob"}


def test_reaction_engines_keep_both_mutes(tmp_path):
    path = str(tmp_path / "reactions.json")
    first = ReactivityEngine(EventBus(), store=ReactionStore(path))
    second = ReactivityEngine(EventBus(), store=ReactionStore(path))

    first.mute("file.created")
    second.mute("metric.threshold")

    assert set(ReactionStore(path).load()["muted"]) == {"file.created", "metric.threshold"}


async def test_improvement_engines_keep_both_candidates(tmp_path):
    manager = FlowManager(storage_file=str(tmp_path / "flows.json"))
    manager.save_flow(
        "Echo",
        [
            {"id": "in", "moduleId": "chat", "nodeTypeId": "chat_input"},
            {"id": "out", "moduleId": "chat", "nodeTypeId": "chat_output"},
        ],
        [{"from": "in", "to": "out"}],
        flow_id="echo-flow",
    )
    path = str(tmp_path / "improvements.json")
    first = ImprovementEngine(
        flow_manager=manager,
        evolution=FlowEvolution(manager),
        store=ImprovementStore(path),
    )
    second = ImprovementEngine(
        flow_manager=manager,
        evolution=FlowEvolution(manager),
        store=ImprovementStore(path),
    )

    first_candidate = await first.propose(
        "echo-flow", [{"op": "rename", "name": "One"}], "first"
    )
    second_candidate = await second.propose(
        "echo-flow", [{"op": "rename", "name": "Two"}], "second"
    )

    stored = ImprovementStore(path).load()
    assert set(stored["candidates"]) == {first_candidate["id"], second_candidate["id"]}


def test_ledger_lock_excludes_a_second_writer(tmp_path):
    path = str(tmp_path / "ledger.json")
    holder = LedgerLock(path)
    contender = LedgerLock(path, timeout=0.05)

    with holder.held():
        with pytest.raises(Timeout):
            with contender.held():
                pass
