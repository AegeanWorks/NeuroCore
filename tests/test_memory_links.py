"""Memory evidence links: CRUD, Bayesian confidence and review-gated contradictions."""

import pytest

import modules.memory.backend as memory_backend
from modules.memory.backend import MemoryStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_backend, "FAISS_AVAILABLE", False)
    instance = MemoryStore(db_path=str(tmp_path / "memory.sqlite3"))
    yield instance


def add(store, text, subject="User", mem_type="FACT", verified=True):
    return store.add_entry(text, subject=subject, mem_type=mem_type, source="chat",
                           verified=verified)


def test_a_link_round_trips_and_cannot_target_itself(store):
    first = add(store, "The deploy target is staging")
    second = add(store, "Staging receives nightly builds")

    link = store.add_link(first, second, relation="supports",
                          evidence="same environment", created_by="tester")

    assert link["relation"] == "supports"
    assert link["status"] == "accepted"
    assert store.get_link(link["id"]) == link
    with pytest.raises(ValueError, match="itself"):
        store.add_link(first, first)


def test_invalid_relations_statuses_and_missing_memories_are_rejected(store):
    first = add(store, "alpha")
    second = add(store, "beta")
    with pytest.raises(ValueError, match="relation"):
        store.add_link(first, second, relation="likes")
    with pytest.raises(ValueError, match="status"):
        store.add_link(first, second, status="maybe")
    with pytest.raises(ValueError, match="must exist"):
        store.add_link(first, 9999)


def test_a_duplicate_link_returns_the_existing_row(store):
    first = add(store, "alpha")
    second = add(store, "beta")
    one = store.add_link(first, second, relation="supports")
    two = store.add_link(first, second, relation="supports", evidence="again")
    assert one["id"] == two["id"]
    assert len(store.get_links(first)) == 1


def test_links_report_direction_and_neighbour_text(store):
    first = add(store, "alpha rule")
    second = add(store, "beta rule")
    store.add_link(first, second, relation="derived_from", evidence="reason")

    outgoing = store.get_links(first)
    incoming = store.get_links(second)

    assert outgoing[0]["direction"] == "outgoing"
    assert outgoing[0]["neighbour_id"] == second
    assert "beta" in outgoing[0]["neighbour_text"]
    assert incoming[0]["direction"] == "incoming"
    assert incoming[0]["neighbour_id"] == first


def test_bayesian_confidence_uses_laplace_smoothing(store):
    target = add(store, "The API is stable")
    a = add(store, "Release notes say stable")
    b = add(store, "Uptime is 99.99%")
    c = add(store, "Incident report says unstable")

    assert store.bayesian_confidence(target)["confidence"] == 0.5

    supports = store.add_link(a, target, relation="supports")
    store.accept_link(supports["id"])
    assert store.bayesian_confidence(target)["confidence"] == round(2 / 3, 4)

    contradiction = store.add_link(c, target, relation="contradicts")
    store.accept_link(contradiction["id"])
    assert store.bayesian_confidence(target) == {
        "memory_id": target, "supports": 1, "contradicts": 1, "confidence": 0.5,
    }


def test_a_proposal_does_not_change_confidence_until_accepted(store):
    target = add(store, "The API is stable")
    source = add(store, "Release notes say stable")
    proposal = store.add_link(source, target, relation="supports", status="proposed")

    assert store.get_links(target, status="proposed")[0]["id"] == proposal["id"]
    assert store.bayesian_confidence(target)["confidence"] == 0.5

    accepted = store.accept_link(proposal["id"])

    assert accepted["status"] == "accepted"
    assert accepted["confidence_after"] == round(2 / 3, 4)
    assert store.bayesian_confidence(target)["supports"] == 1


def test_accept_and_delete_report_missing_rows(store):
    assert store.accept_link(999) is None
    assert store.delete_link(999) is False


def test_deleting_a_link_removes_it_from_both_sides(store):
    first = add(store, "alpha")
    second = add(store, "beta")
    link = store.add_link(first, second)
    assert store.delete_link(link["id"]) is True
    assert store.get_links(first) == []
    assert store.get_links(second) == []


def test_deterministic_conflicts_need_shared_terms_and_opposite_polarity(store):
    first = add(store, "The deploy target is staging")
    second = add(store, "The deploy target is not staging")
    same_polarity = add(store, "The deploy target is production")
    other_subject = add(store, "The deploy target is not staging", subject="Team")

    candidates = store.find_conflicts()

    pairs = {tuple(sorted((item["memory_1"]["id"], item["memory_2"]["id"])))
             for item in candidates}
    assert (min(first, second), max(first, second)) in pairs
    assert not any(same_polarity in pair for pair in pairs)
    assert not any(other_subject in pair for pair in pairs)
    assert "opposite polarity" in candidates[0]["reason"]


def test_conflict_scan_persists_proposals_and_is_idempotent(store):
    first = add(store, "The deploy target is staging")
    second = add(store, "The deploy target is not staging")

    created = store.suggest_contradictions(limit=10)
    again = store.suggest_contradictions(limit=10)

    assert len(created) == 1
    assert created[0]["status"] == "proposed"
    assert created[0]["created_by"] == "scan"
    assert again == [], "an existing proposal is not duplicated"
    proposals = store.get_links(first, relation="contradicts", status="proposed")
    assert len(proposals) == 1
    assert proposals[0]["neighbour_id"] == second


def test_the_conflict_scan_never_calls_an_llm(store, monkeypatch):
    import core.llm as llm_module

    class Exploding:
        def __init__(self, *args, **kwargs):
            raise AssertionError("the deterministic scan must not build an LLM client")

    monkeypatch.setattr(llm_module, "LLMBridge", Exploding)
    add(store, "The deploy target is staging")
    add(store, "The deploy target is not staging")
    assert len(store.find_conflicts()) == 1
    assert len(store.suggest_contradictions()) == 1


def test_the_link_routes_round_trip(store, monkeypatch):
    from fastapi.testclient import TestClient

    import importlib
    memory_router = importlib.import_module("modules.memory.router")
    from main import app

    first = add(store, "alpha fact")
    second = add(store, "beta fact")
    monkeypatch.setattr(memory_router, "memory_store", store)

    with TestClient(app) as client:
        created = client.post("/memory/links", json={
            "source_id": first, "target_id": second,
            "relation": "supports", "evidence": "test", "status": "proposed",
        })
        listed = client.get(f"/memory/links/{second}")
        accepted = client.post(f"/memory/links/{created.json()['id']}/accept")
        deleted = client.delete(f"/memory/links/{created.json()['id']}")
        missing = client.post("/memory/links/9999/accept")

    assert created.status_code == 200
    assert listed.status_code == 200
    assert listed.json()["links"][0]["status"] == "proposed"
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert deleted.json() == {"deleted": True}
    assert missing.status_code == 404


def test_the_scan_route_returns_new_proposals(store, monkeypatch):
    from fastapi.testclient import TestClient

    import importlib
    memory_router = importlib.import_module("modules.memory.router")
    from main import app

    add(store, "The deploy target is staging")
    add(store, "The deploy target is not staging")
    monkeypatch.setattr(memory_router, "memory_store", store)

    with TestClient(app) as client:
        first = client.post("/memory/contradictions/scan", json={"limit": 5})
        second = client.post("/memory/contradictions/scan", json={"limit": 5})
        candidates = client.get("/memory/contradictions")

    assert first.status_code == 200
    assert first.json()["created"] == 1
    assert second.json()["created"] == 0
    assert candidates.status_code == 200
    assert candidates.json()["candidates"] == []
