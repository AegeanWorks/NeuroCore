"""Autonomous mode: the brain carries out its own safe proposals inside the kernel and budget rails."""

import pytest

from modules.brain.autonomy import AUTONOMOUS_ACTIONS
from modules.brain.drift import DriftMonitor
from modules.brain.evaluation import EvalStore
from modules.brain.kernel import BrainKernel, KernelBlockedError, KernelStore
from modules.brain.self_budget import SelfBudget
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore
from tests.test_brain_substrate import FakeSelfModel, temp_learning


class PerfectRunner:
    def __init__(self):
        self.runs = 0

    async def run(self, flow_id=None, **_kwargs):
        self.runs += 1
        return {"pass_rate": 1.0, "flow_id": flow_id or "flow-1", "suite": "core", "total": 3,
                "failed_tasks": [], "tasks": []}


def make_service(tmp_path, mode="autonomous", caps=None, **config):
    service = BrainService(
        config={"mode": mode, "auto_start": False, **config},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "state.json")),
        eval_store=EvalStore(last_path=str(tmp_path / "last.json"),
                             baseline_path=str(tmp_path / "baseline.json")),
        learning=temp_learning(tmp_path),
        kernel=BrainKernel(store=KernelStore(str(tmp_path / "kernel.json"))),
        drift=DriftMonitor(path=str(tmp_path / "drift.json")),
        evaluation_runner=PerfectRunner(),
    )
    service.self_budget = SelfBudget(path=str(tmp_path / "budget.json"), caps=caps or {})
    return service


async def test_autonomous_mode_is_accepted_but_not_in_safe_kernel(tmp_path):
    service = make_service(tmp_path, mode="observe")
    assert (await service.set_mode("autonomous"))["mode"] == "autonomous"
    service.engage_kill_switch("test", approved_by="tester")
    with pytest.raises(KernelBlockedError):
        await service.set_mode("autonomous")


async def test_an_autonomous_cycle_acts_on_safe_proposals_and_journals_them(tmp_path):
    service = make_service(tmp_path)
    state = await service.run_cycle()
    [entry] = [e for e in state["actions"] if e["kind"] == "record_baseline"]
    assert entry["status"] == "done" and entry["actor"] == "brain:autonomous"
    assert service.eval_store.load_baseline()["recorded_by"] == "brain:autonomous"
    pending = {p["kind"]: p["status"] for p in state["proposals"]}
    assert pending["record_baseline"] == "done"
    assert pending["seed_memory"] == "pending", "a proposal without a safe action waits for the user"


async def test_actions_per_cycle_are_capped_and_suggest_mode_never_acts(tmp_path):
    service = make_service(tmp_path, max_autonomous_actions_per_cycle=1)
    setattr(service, "_propose", lambda _snapshot: [
        BrainService._proposal(kind, kind, "r", 0.9) for kind in ("review_eval_failure", "improve_recall")])
    assert len((await service.run_cycle())["actions"]) == 1
    quiet = make_service(tmp_path / "s", mode="suggest")
    assert (await quiet.run_cycle())["actions"] == []


async def test_an_exhausted_self_budget_skips_the_action(tmp_path):
    service = make_service(tmp_path, caps={"promotion": 1})
    service.self_budget.reserve("promotion")
    state = await service.run_cycle()
    [entry] = [e for e in state["actions"] if e["kind"] == "record_baseline"]
    assert entry["status"] == "skipped" and "budget" in entry["result"]
    assert service.eval_store.load_baseline() in (None, {})


def test_only_allow_listed_kinds_have_actions():
    assert set(AUTONOMOUS_ACTIONS) == {"record_baseline", "review_eval_failure",
                                       "consolidate_learning", "improve_recall"}


def test_a_proposal_offers_an_action_or_a_place_where_a_person_can_act():
    runnable = BrainService._proposal("record_baseline", "Baseline", "r", 0.9)
    assert runnable["action"] == "record_baseline" and runnable["link"] == ""
    manual = BrainService._proposal("repair_provider", "Repair", "r", 0.8)
    assert manual["action"] is None and manual["link"] == "/settings"
    assert BrainService._proposal("seed_memory", "Seed", "r", 0.6)["link"] == "/memory_browser"


class FreshInstallSelfModel(FakeSelfModel):
    """What the real self-model reports before any evaluation has run."""

    async def snapshot(self, mode="observe"):
        snapshot = await super().snapshot(mode)
        snapshot["evaluation"] = {"baseline_present": False, "last_pass_rate": None}
        return snapshot


async def test_a_fresh_install_with_no_evaluation_still_cycles(tmp_path):
    # Every live cycle and /brain/state failed: drift read float(None) for the pass rate.
    service = make_service(tmp_path)
    service.self_model = FreshInstallSelfModel()
    state = await service.run_cycle()
    assert state["cycle_count"] == 1 and state["last_error"] is None


async def test_the_operator_mode_and_running_state_survive_a_restart(tmp_path):
    first = make_service(tmp_path, mode="observe")
    await first.set_mode("autonomous")
    await first.start()
    await first.shutdown_keep_resume()
    again = make_service(tmp_path, mode="observe")
    assert again.mode == "autonomous" and again.state.mode == "autonomous"
    assert again.resume_requested is True
    assert (await again.start())["running"] is True
    await again.stop()
    assert make_service(tmp_path, mode="observe").resume_requested is False, "an explicit stop is not resumed"


async def test_a_shutdown_after_an_explicit_stop_does_not_resume(tmp_path):
    stopped = make_service(tmp_path, mode="observe")
    await stopped.start()
    await stopped.stop()
    await stopped.shutdown_keep_resume()
    assert make_service(tmp_path, mode="observe").resume_requested is False


async def test_a_safe_kernel_on_load_forces_observe(tmp_path):
    first = make_service(tmp_path, mode="observe")
    await first.set_mode("autonomous")
    first.engage_kill_switch("test", approved_by="tester")
    assert make_service(tmp_path, mode="observe").mode == "observe"
