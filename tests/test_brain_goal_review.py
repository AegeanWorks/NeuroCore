"""Deterministic goal review: overdue, stale, clarity and near-duplicate detection."""

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.brain.goal_review import clarity, review_goals

NOW = 1_000_000.0
DAY = 86400.0


def goal(goal_id, description, *, status="pending", priority=1, context="ctx",
         deadline=None, updated_at=None):
    return {
        "id": goal_id,
        "description": description,
        "status": status,
        "priority": priority,
        "context": context,
        "created_at": int(NOW - DAY),
        "updated_at": int(updated_at if updated_at is not None else NOW - 60),
        "deadline": deadline,
    }


def test_review_reports_overdue_stale_low_clarity_and_duplicates_without_mutation():
    vague = goal(1, "fix it", priority=0, context="", deadline=int(NOW - 10),
                 updated_at=NOW - 30 * DAY)
    clear = goal(2, "Publish the operator runbook for the brain control plane",
                 deadline=int(NOW + DAY))
    duplicate = goal(3, "Publish the operator runbook for the brain control plane",
                     deadline=int(NOW + DAY))
    completed = goal(4, "Publish the operator runbook for the brain control plane",
                     status="completed")

    original = [dict(vague), dict(clear), dict(duplicate), dict(completed)]
    result = review_goals([vague, clear, duplicate, completed], now=NOW)

    assert result["reviewed"] == 3
    assert [item["id"] for item in result["overdue"]] == [1]
    assert [item["id"] for item in result["stale"]] == [1]
    assert [item["id"] for item in result["low_clarity"]] == [1]
    assert result["low_clarity"][0]["clarity"] == 0.25
    assert result["near_duplicates"][0]["left_id"] == 2
    assert result["near_duplicates"][0]["right_id"] == 3
    assert [dict(item) for item in (vague, clear, duplicate, completed)] == original


def test_clarity_is_bounded_and_explains_what_is_missing():
    assert clarity(goal(1, "short", priority=0, context="", deadline=None)) == {
        "clarity": 0.0,
        "reasons": [
            "description is too short",
            "context is missing",
            "deadline is missing",
            "priority is not set",
        ],
    }
    full = clarity(goal(1, "A sufficiently long and actionable objective",
                        context="why", priority=2, deadline=1))
    assert full == {"clarity": 1.0, "reasons": []}


def test_goal_review_route_is_read_only_and_reports_a_review():
    with TestClient(app) as client:
        response = client.post("/brain/goals/review", json={})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["reviewed"], int)
    assert {"overdue", "stale", "low_clarity", "near_duplicates", "clarity"} <= set(body)


def test_review_rejects_negative_stale_days():
    with pytest.raises(ValueError):
        review_goals([], now=NOW, stale_days=-1)


def test_review_sorts_overdue_and_stale_by_epoch_not_text():
    later = goal(1, "Later deadline objective words here", deadline=100, updated_at=100)
    earlier = goal(2, "Earlier deadline objective words here", deadline=99, updated_at=99)
    result = review_goals([later, earlier], now=1000, stale_days=0)
    assert [item["id"] for item in result["overdue"]] == [2, 1]
    assert [item["id"] for item in result["stale"]] == [2, 1]


def test_goal_review_route_rejects_negative_stale_days():
    with TestClient(app) as client:
        response = client.post("/brain/goals/review", json={"stale_days": -1})

    assert response.status_code == 400
    assert "Fix:" in response.json()["detail"]
