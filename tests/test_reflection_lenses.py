"""Two-lens epistemic critique in the reflection node (IDEA-629)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution_budget import ExecutionBudget
from core.structured_output import StructuredOutputError
from modules.reflection.lenses import (
    EpistemicCritique,
    LensFinding,
    build_lens_prompt,
    context_text,
    lens_enabled,
)
from modules.reflection.node import ReflectionExecutor

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "reflection_lenses.json").read_text(encoding="utf-8")
)["cases"]
CASES = {case["id"]: case for case in FIXTURES}


def make_executor() -> ReflectionExecutor:
    with patch("modules.reflection.node.LLMBridge"):
        executor = ReflectionExecutor()
    executor.llm = MagicMock()
    executor.llm.chat_completion = AsyncMock()
    return executor


def legacy_response(satisfied=True, reason="legacy", needs_improvement=None):
    payload = {
        "satisfied": satisfied,
        "reason": reason,
        "needs_improvement": needs_improvement,
    }
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}]}


def make_input(case_id="sound", with_context=True):
    case = CASES[case_id]
    data = {
        "messages": [
            {"role": "user", "content": case["request"]},
            {"role": "assistant", "content": case["response"]},
        ],
        "content": case["response"],
    }
    if with_context and case["knowledge_context"]:
        data["knowledge_context"] = case["knowledge_context"]
    return data


def critique_for(case_id, triggered=True):
    expected = CASES[case_id]["expected_lens"]
    findings = []
    if expected and triggered:
        findings.append(LensFinding(lens_id=expected, triggered=True, critique="fixture"))
    return EpistemicCritique(
        satisfied=not findings,
        reason=f"fixture {case_id}",
        needs_improvement="fix it" if findings else None,
        findings=findings,
    )


def structured_messages(mock) -> str:
    messages = mock.await_args.kwargs["messages"]
    return "\n\n".join(str(message.get("content", "")) for message in messages)


async def test_legacy_keys_unchanged_when_no_lenses_configured():
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=legacy_response(reason="plain"))
    with patch("modules.reflection.node.structured_completion", new=AsyncMock()) as structured:
        result = await executor.receive(make_input("sound"))

    assert structured.await_count == 0
    assert result["reflection"] == {
        "satisfied": True,
        "reason": "plain",
        "needs_improvement": None,
    }


async def test_a_sycophantic_fixture_triggers_the_sycophancy_lens():
    executor = make_executor()
    with patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(return_value=critique_for("sycophantic")),
    ) as structured:
        result = await executor.receive(
            make_input("sycophantic"),
            {"cognitive_lenses": ["sycophancy"]},
        )

    assert structured.await_count == 1
    findings = result["reflection"]["findings"]
    assert [finding["lens_id"] for finding in findings] == ["sycophancy"]
    assert findings[0]["triggered"] is True
    assert result["satisfied"] is False


async def test_a_sound_fixture_triggers_no_lens():
    executor = make_executor()
    with patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(return_value=critique_for("sound")),
    ):
        result = await executor.receive(
            make_input("sound"),
            {"cognitive_lenses": ["sycophancy", "ungrounded_extrapolation"]},
        )

    assert result["reflection"]["findings"] == []
    assert result["satisfied"] is True


async def test_ungrounded_claim_is_flagged_only_when_knowledge_context_is_supplied():
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=legacy_response())
    with patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(return_value=critique_for("ungrounded")),
    ) as structured:
        await executor.receive(
            make_input("ungrounded", with_context=False),
            {"cognitive_lenses": ["ungrounded_extrapolation"]},
        )
        assert structured.await_count == 0

        result = await executor.receive(
            make_input("ungrounded", with_context=True),
            {"cognitive_lenses": ["ungrounded_extrapolation"]},
        )

    assert structured.await_count == 1
    prompt = structured_messages(structured)
    assert "Knowledge context" in prompt
    assert CASES["ungrounded"]["knowledge_context"] in prompt
    assert [finding["lens_id"] for finding in result["reflection"]["findings"]] == [
        "ungrounded_extrapolation"
    ]


async def test_lenses_run_when_no_budget_is_active():
    executor = make_executor()
    with patch("modules.reflection.node.current_budget", return_value=None), patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(return_value=critique_for("sycophantic")),
    ) as structured:
        await executor.receive(
            make_input("sycophantic"),
            {"cognitive_lenses": ["sycophancy"]},
        )

    assert structured.await_count == 1


async def test_lenses_are_skipped_at_budget_pressure_0_8_or_more():
    budget = ExecutionBudget()
    budget.used["llm_calls"] = budget.limits["max_llm_calls"] * 0.8
    assert budget.pressure >= 0.8

    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=legacy_response(reason="under pressure"))
    with patch("modules.reflection.node.current_budget", return_value=budget), patch(
        "modules.reflection.node.structured_completion", new=AsyncMock()
    ) as structured:
        result = await executor.receive(
            make_input("sycophantic"),
            {"cognitive_lenses": ["sycophancy"]},
        )

    assert structured.await_count == 0
    assert result["reflection"]["reason"] == "under pressure"
    assert "findings" not in result["reflection"]


async def test_a_structured_failure_falls_back_to_the_legacy_call():
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=legacy_response(reason="fallback"))
    with patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(side_effect=StructuredOutputError("bad json")),
    ):
        result = await executor.receive(
            make_input("sycophantic"),
            {"cognitive_lenses": ["sycophancy"]},
        )

    assert result["reflection"]["reason"] == "fallback"
    assert "findings" not in result["reflection"]


async def test_findings_are_filtered_to_the_enabled_lenses():
    critique = EpistemicCritique(
        satisfied=False,
        reason="two findings",
        needs_improvement="fix",
        findings=[
            LensFinding(lens_id="sycophancy", triggered=True),
            LensFinding(lens_id="ungrounded_extrapolation", triggered=True),
            LensFinding(lens_id="premature_closure", triggered=True),
        ],
    )
    executor = make_executor()
    with patch(
        "modules.reflection.node.structured_completion", new=AsyncMock(return_value=critique)
    ):
        result = await executor.receive(
            make_input("sycophantic"),
            {"cognitive_lenses": ["sycophancy"]},
        )

    assert [finding["lens_id"] for finding in result["reflection"]["findings"]] == [
        "sycophancy"
    ]


async def test_the_structured_call_reuses_the_node_bridge_and_schema():
    executor = make_executor()
    with patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(return_value=critique_for("sycophantic")),
    ) as structured:
        await executor.receive(
            make_input("sycophantic"),
            {"cognitive_lenses": ["sycophancy"]},
        )

    assert structured.await_args.kwargs["schema"] is EpistemicCritique
    assert structured.await_args.kwargs["llm_bridge"] is executor.llm


async def test_knowledge_context_is_bounded_in_the_prompt():
    executor = make_executor()
    data = make_input("sound", with_context=False)
    data["knowledge_context"] = "context " * 2000
    with patch(
        "modules.reflection.node.structured_completion",
        new=AsyncMock(return_value=critique_for("sound")),
    ) as structured:
        await executor.receive(
            data,
            {"cognitive_lenses": ["ungrounded_extrapolation"]},
        )

    prompt = structured_messages(structured)
    assert "[truncated]" in prompt
    assert prompt.count("context") < 700  # 2000 repetitions would be ~2000 without the cap


def test_build_lens_prompt_names_only_the_selected_lenses():
    prompt = build_lens_prompt(["sycophancy"])

    assert "sycophancy" in prompt
    assert "ungrounded" not in prompt.lower()
    assert build_lens_prompt([]) == ""
    assert build_lens_prompt(["unknown"]) == ""


def test_lens_enabled_ignores_unknown_and_duplicate_names():
    assert lens_enabled({}, None) == []
    assert lens_enabled({"cognitive_lenses": "sycophancy"}, None) == ["sycophancy"]
    assert lens_enabled(
        {"cognitive_lenses": ["sycophancy", "sycophancy", "unknown"]}, None
    ) == ["sycophancy"]
    assert lens_enabled({"cognitive_lenses": 5}, None) == []


def test_context_text_accepts_a_string_list_and_mapping():
    assert context_text("plain") == "plain"
    assert context_text([{"content": "first"}, {"text": "second"}]) == "first\n\nsecond"
    assert '"key": "value"' in context_text({"key": "value"})
    assert context_text(None) == ""


def test_the_module_ships_with_no_lenses_enabled():
    manifest = json.loads(
        (Path(__file__).parent.parent / "modules" / "reflection" / "module.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["config"]["cognitive_lenses"] == []


async def test_the_lens_prompt_is_absent_when_no_lens_is_enabled():
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=legacy_response())
    await executor.receive(make_input("sound"))

    prompt = executor.llm.chat_completion.await_args.kwargs["messages"][0]["content"]
    assert "epistemic lenses" not in prompt
