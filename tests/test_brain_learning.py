"""Learning loop: outcomes, consolidation into global memory and held-out recall."""

import json

from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from main import app
from modules.brain.evaluation import EvalStore
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore

from tests.test_brain_substrate import FakeSelfModel


class FakeMemoryBridge:
    """In-memory stand-in for the global MemoryStore used by the learning loop."""

    def __init__(self):
        self.entries = {}
        self._next_id = 1

    def add_entry(self, text, mem_type="BELIEF", subject="User", source="chat",
                  confidence=1.0, verified=False, **kwargs):
        for entry in self.entries.values():
            if entry["text"] == text:
                return -1
        entry_id = self._next_id
        self._next_id += 1
        self.entries[entry_id] = {
            "id": entry_id,
            "text": text,
            "type": mem_type,
            "subject": subject,
            "source": source,
            "confidence": confidence,
            "verified": verified,
        }
        return entry_id

    def update_entry(self, memory_id, text=None, mem_type=None, verified=None, **kwargs):
        if memory_id not in self.entries:
            return None
        old = self.entries[memory_id]
        new_id = self._next_id
        self._next_id += 1
        self.entries[new_id] = {
            **old,
            "id": new_id,
            "text": text if text is not None else old["text"],
            "type": mem_type or old["type"],
            "verified": old["verified"] if verified is None else verified,
        }
        return new_id

    def delete_entry(self, memory_id, reason=None):
        return self.entries.pop(memory_id, None) is not None

    def browse(self, limit=50, search_text=None, mem_type=None, source=None, **kwargs):
        rows = []
        for entry in self.entries.values():
            if mem_type and entry["type"] != mem_type:
                continue
            if source and entry["source"] != source:
                continue
            if search_text and search_text not in entry["text"]:
                continue
            rows.append(dict(entry))
        return rows[-limit:]


def make_loop(tmp_path, min_occurrences=2):
    memory = FakeMemoryBridge()
    loop = LearningLoop(
        memory=memory,
        store=LearningStore(str(tmp_path / "learning.json")),
        min_occurrences=min_occurrences,
        holdout_ratio=0.2,
    )
    return loop, memory


def record(loop, action="tool:Calculator", input_data=None, output="4",
           success=True, tags=None):
    return loop.record_outcome(
        action=action,
        input_data=input_data if input_data is not None else {"expression": "2+2"},
        output=output,
        success=success,
        tags=tags or [],
    )


def test_record_outcome_mirrors_experience_and_persists(tmp_path):
    loop, memory = make_loop(tmp_path)
    record = loop.record_outcome(
        action="tool:Calculator",
        input_data={"expression": "2+2"},
        output="4",
        success=True,
        latency_ms=12,
        tags=["smoke"],
    )

    assert record["success"] is True
    assert loop.status()["experiences"] == 1
    assert any(entry["type"] == "EXPERIENCE" for entry in memory.entries.values())
    assert LearningStore(str(tmp_path / "learning.json")).load()["experiences"]


def test_consolidation_creates_one_skill_from_repeated_successes(tmp_path):
    loop, memory = make_loop(tmp_path)
    for _ in range(4):
        record(loop, tags=["train"])
    record(loop, tags=["holdout"])

    result = loop.consolidate()

    assert result["created"] == 1
    rules = [entry for entry in memory.entries.values() if entry["type"] == "RULE"]
    assert len(rules) == 1
    payload = json.loads(rules[0]["text"].removeprefix("[skill] "))
    assert payload["action"] == "tool:Calculator"
    assert payload["occurrences"] == 4
    assert payload["success_rate"] == 1.0


def test_consolidation_skips_failures_and_below_threshold(tmp_path):
    loop, _ = make_loop(tmp_path, min_occurrences=3)
    record(loop, success=False)
    record(loop, success=False)
    record(loop, input_data={"expression": "different"}, success=True)
    record(loop, input_data={"expression": "different"}, success=True)

    result = loop.consolidate()

    assert result["created"] == 0
    assert result["skills"] == 0


def test_skill_success_rate_counts_training_failures(tmp_path):
    loop, memory = make_loop(tmp_path)
    record(loop, tags=["train"])
    record(loop, tags=["train"])
    record(loop, success=False, tags=["train"])

    loop.consolidate()

    rules = [entry for entry in memory.entries.values() if entry["type"] == "RULE"]
    payload = json.loads(rules[0]["text"].removeprefix("[skill] "))
    assert payload["occurrences"] == 2
    assert payload["success_rate"] == 0.6667


def test_second_consolidation_does_not_duplicate_skill(tmp_path):
    loop, memory = make_loop(tmp_path)
    for _ in range(2):
        record(loop, tags=["train"])

    first = loop.consolidate()
    second = loop.consolidate()

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped"] == 1
    rules = [entry for entry in memory.entries.values() if entry["type"] == "RULE"]
    assert len(rules) == 1


def test_held_out_recall_uses_only_training_experiences(tmp_path):
    loop, _ = make_loop(tmp_path)
    for _ in range(4):
        record(loop, tags=["train"])
    record(loop, tags=["holdout"])

    loop.consolidate()
    recall = loop.evaluate_recall(top_k=3)

    assert recall["holdout"] == 1
    assert recall["train"] == 4
    assert recall["hits"] == 1
    assert recall["recall"] == 1.0
    assert recall["precision_at_k"] == 1.0
    assert recall["mrr"] == 1.0


def test_experiences_page_zero_is_empty(tmp_path):
    loop, _ = make_loop(tmp_path)
    record(loop)

    assert loop.experiences_page(0) == []
    assert len(loop.experiences_page(1)) == 1


def test_recall_is_zero_without_holdout_examples(tmp_path):
    loop, _ = make_loop(tmp_path)
    record(loop, tags=["train"])

    recall = loop.evaluate_recall()

    assert recall["holdout"] == 0
    assert recall["recall"] is None


def test_skill_ledger_is_capped_and_prunes_memory(tmp_path):
    memory = FakeMemoryBridge()
    loop = LearningLoop(
        memory=memory,
        store=LearningStore(str(tmp_path / "learning.json")),
        min_occurrences=1,
        max_skills=10,
    )
    for index in range(20):
        record(loop, input_data={"expression": f"{index}+{index}"}, tags=["train"])
    loop.consolidate()

    assert len(loop.skills) == 10
    rules = [entry for entry in memory.entries.values() if entry["type"] == "RULE"]
    assert len(rules) == 10


def test_experience_cap_prunes_mirrored_memory(tmp_path):
    memory = FakeMemoryBridge()
    loop = LearningLoop(
        memory=memory,
        store=LearningStore(str(tmp_path / "learning.json")),
        max_experiences=10,
    )
    for index in range(12):
        record(loop, input_data={"index": index}, tags=["train"])

    experiences = [
        entry for entry in memory.entries.values() if entry["type"] == "EXPERIENCE"
    ]
    assert len(loop.experiences) == 10
    assert len(experiences) == 10


def test_learning_ledger_survives_reload_and_quarantines_corrupt_data(tmp_path):
    path = tmp_path / "learning.json"
    loop, _ = make_loop(tmp_path)
    for _ in range(2):
        record(loop, tags=["train"])
    loop.consolidate()

    restored = LearningLoop(
        memory=FakeMemoryBridge(),
        store=LearningStore(str(path)),
        min_occurrences=2,
    )
    assert restored.status()["skills"] == 1

    path.write_text("[]", encoding="utf-8")
    quarantined = LearningLoop(
        memory=FakeMemoryBridge(),
        store=LearningStore(str(path)),
    )
    assert quarantined.status()["experiences"] == 0
    assert list(tmp_path.glob("learning.json.corrupt-*"))


async def test_service_evaluation_records_outcomes(tmp_path):
    learning, _ = make_loop(tmp_path)
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=learning,
    )

    result = await service.run_evaluation()
    status = service.learning_status()

    assert result["pass_rate"] == 1.0
    assert status["experiences"] == len(result["tasks"])


def test_learning_routes_record_consolidate_and_recall(tmp_path, monkeypatch):
    learning, _ = make_loop(tmp_path)
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=learning,
    )
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/brain/learn", json={
                "action": "tool:Calculator",
                "input": {"expression": "2+2"},
                "output": "4",
                "success": True,
                "tags": ["train"],
            })
            assert response.status_code == 200
        status = client.get("/brain/learn/status")
        consolidated = client.post("/brain/learn/consolidate")
        recalled = client.post("/brain/learn/recall", json={"top_k": 3})
        invalid = client.post("/brain/learn", json={"action": ""})

    assert status.json()["experiences"] == 2
    assert consolidated.json()["created"] == 1
    assert recalled.status_code == 200
    assert invalid.status_code == 400


def test_repeated_failures_consolidate_into_one_avoidance(tmp_path):
    loop, memory = make_loop(tmp_path)
    for _ in range(3):
        record(loop, success=False, tags=["train"], output=None)
    if not loop.experiences or loop.experiences[0].get("error") is None:
        pass

    result = loop.consolidate()

    assert result["avoidances"] == 1
    avoids = [entry for entry in memory.entries.values()
              if str(entry["text"]).startswith("[avoid] ")]
    assert len(avoids) == 1
    payload = json.loads(avoids[0]["text"].removeprefix("[avoid] "))
    assert payload["action"] == "tool:Calculator"
    assert payload["failures"] == 3
    assert avoids[0]["confidence"] == 0.3
    assert avoids[0]["verified"] is False


def test_avoidance_consolidation_is_idempotent(tmp_path):
    loop, memory = make_loop(tmp_path)
    for _ in range(2):
        record(loop, success=False, tags=["train"])
    loop.consolidate()
    before = len([e for e in memory.entries.values() if str(e["text"]).startswith("[avoid] ")])

    second = loop.consolidate()

    after = len([e for e in memory.entries.values() if str(e["text"]).startswith("[avoid] ")])
    assert second["avoidance_changes"] == {"created": 0, "updated": 0}
    assert after == before == 1


def test_a_single_failure_does_not_become_an_avoidance(tmp_path):
    loop, _ = make_loop(tmp_path)
    record(loop, success=False, tags=["train"])
    result = loop.consolidate()
    assert result["avoidances"] == 0


def test_later_success_does_not_erase_an_avoidance(tmp_path):
    loop, memory = make_loop(tmp_path)
    for _ in range(2):
        record(loop, success=False, tags=["train"])
    loop.consolidate()
    record(loop, success=True, tags=["train"])
    loop.consolidate()

    avoids = [entry for entry in memory.entries.values()
              if str(entry["text"]).startswith("[avoid] ")]
    assert len(avoids) == 1, "one success is not enough evidence to forget two failures"


def test_recall_returns_skills_and_avoidance_warnings(tmp_path):
    loop, _ = make_loop(tmp_path)
    for _ in range(3):
        record(loop, success=True, tags=["train"])
    for _ in range(2):
        record(loop, success=False, tags=["train"], output=None)
    loop.consolidate()

    result = loop.recall("tool:Calculator", top_k=3)

    assert result["query"] == "tool:Calculator"
    assert len(result["skills"]) == 1
    assert len(result["avoidances"]) == 1
    assert "verify before retrying" in result["avoidances"][0]["warning"]


def test_avoidance_recall_is_empty_for_an_unknown_query(tmp_path):
    loop, _ = make_loop(tmp_path)
    for _ in range(2):
        record(loop, success=False, tags=["train"])
    loop.consolidate()

    assert loop.recall("something-unrelated", top_k=3)["avoidances"] == []


def test_avoidance_history_is_bounded_and_memory_is_pruned(tmp_path):
    loop, memory = make_loop(tmp_path)
    loop.max_avoidances = 10
    loop.min_failures = 1
    for index in range(12):
        loop.record_outcome(
            action=f"tool:Fail{index}", input_data={"expression": "1+1"},
            output=None, success=False, error=f"boom {index}", tags=["train"],
        )
    loop.consolidate()
    assert len(loop.avoidances) <= 10
    avoids = [e for e in memory.entries.values() if str(e["text"]).startswith("[avoid] ")]
    assert len(avoids) <= 10, "pruned avoidances leave no orphan memory rows"


def test_the_suggest_route_requires_a_query():
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as client:
        missing = client.post("/brain/learn/suggest", json={})
        invalid = client.post("/brain/learn/suggest", json={"query": "  "})

    assert missing.status_code == 400
    assert invalid.status_code == 400
