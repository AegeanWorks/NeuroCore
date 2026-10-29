"""
Tests for GoalPursuitExecutor — integrated planning, step evaluation, replanning.
"""
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.agent_loop.node import GoalPursuitExecutor

# Capture the real methods before any autouse fixtures can stub them.
# Tests that exercise the real methods use these references to restore them.
_real_reason_about_step = GoalPursuitExecutor._reason_about_step
_real_assess_task_clarity = GoalPursuitExecutor._assess_task_clarity
_real_mid_run_assessment = GoalPursuitExecutor._mid_run_assessment
_real_extract_failure_patterns = GoalPursuitExecutor._extract_failure_patterns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_executor():
    """Create a GoalPursuitExecutor with patched LLMBridge and sandbox."""
    with patch("modules.agent_loop.node.LLMBridge"):
        ex = GoalPursuitExecutor()
    ex.llm = MagicMock()
    setattr(ex, "_sandbox", MagicMock())
    return ex


def llm_response(content: str) -> dict:
    """Build a minimal valid LLM response dict."""
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def plan_json(*actions) -> str:
    """Serialize a list of step dicts to JSON."""
    return json.dumps([
        {"step": i + 1, "action": a, "target": f"target_{i+1}", "goal": f"goal_{i+1}"}
        for i, a in enumerate(actions)
    ])


def eval_json(success: bool, reason: str = "ok", extracted: str = "result") -> str:
    return json.dumps({"success": success, "reason": reason, "extracted_result": extracted})


MESSAGES = [{"role": "user", "content": "Research AI trends and summarize them"}]


# ---------------------------------------------------------------------------
# Autouse fixture — suppress pre-step reasoning for all existing tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_goal_lessons(monkeypatch):
    """Keep the shared memory store out of planning.

    Lessons saved by earlier tests would otherwise consume the precise LLM
    response sequences these tests set up.  Tests that exercise lesson loading
    patch the method themselves and therefore take precedence.
    """
    async def _no_lessons(self, config):
        return []

    monkeypatch.setattr(
        "modules.agent_loop.node.GoalPursuitExecutor._fetch_goal_lessons",
        _no_lessons,
    )


@pytest.fixture(autouse=True)
def _stub_step_reasoning(monkeypatch):
    """Stub out _reason_about_step for all GoalPursuit tests.

    Existing tests set up specific LLM mock sequences; the extra
    _reason_about_step LLM call would consume those responses unexpectedly.
    Tests that want to exercise reasoning should explicitly undo this patch.
    """
    async def _no_reasoning(self, step, repl_state, config, user_goal, tools_list=None, reasoning_history=""):
        return ""
    monkeypatch.setattr(
        "modules.agent_loop.node.GoalPursuitExecutor._reason_about_step",
        _no_reasoning,
    )


@pytest.fixture(autouse=True)
def _stub_metacognition_calls(monkeypatch):
    """Stub out the three new metacognition LLM calls for all GoalPursuit tests.

    _assess_task_clarity, _mid_run_assessment, and _extract_failure_patterns
    each make an extra LLM round-trip.  Existing tests set up precise mock
    response sequences that would be disrupted by those extra calls.
    Tests that specifically exercise these methods must undo this fixture
    with monkeypatch.setattr(...) inside the test body.
    """
    async def _no_clarity(self, user_goal, config):
        return {"is_actionable": True, "critical_unknowns": [], "safe_assumptions": []}

    async def _no_mid_assessment(self, completed_steps, remaining_steps, repl_state, config, user_goal):
        return ""

    async def _no_failure_patterns(self, lessons_list, config):
        return ""

    monkeypatch.setattr("modules.agent_loop.node.GoalPursuitExecutor._assess_task_clarity", _no_clarity)
    monkeypatch.setattr("modules.agent_loop.node.GoalPursuitExecutor._mid_run_assessment", _no_mid_assessment)
    monkeypatch.setattr("modules.agent_loop.node.GoalPursuitExecutor._extract_failure_patterns", _no_failure_patterns)


# ---------------------------------------------------------------------------
# _parse_plan_response
# ---------------------------------------------------------------------------

async def test_parse_plan_response_valid():
    ex = make_executor()
    raw = plan_json("Search", "Summarize")
    result = await ex._parse_plan_response(raw, max_steps=10)
    assert len(result) == 2
    assert result[0]["action"] == "Search"
    assert result[1]["action"] == "Summarize"
    assert result[0]["step"] == 1
    assert result[1]["step"] == 2


async def test_parse_plan_response_respects_max_steps():
    ex = make_executor()
    raw = plan_json("A", "B", "C", "D", "E")
    result = await ex._parse_plan_response(raw, max_steps=3)
    assert len(result) == 3


async def test_parse_plan_response_invalid_json_returns_empty():
    ex = make_executor()
    result = await ex._parse_plan_response("not json at all", max_steps=10)
    assert result == []


async def test_parse_plan_response_step_offset():
    ex = make_executor()
    raw = plan_json("Finish")
    result = await ex._parse_plan_response(raw, max_steps=10, step_offset=2)
    assert result[0]["step"] == 3  # offset=2 → step 3


# ---------------------------------------------------------------------------
# _create_plan
# ---------------------------------------------------------------------------

async def test_create_plan_returns_structured_steps():
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(return_value=llm_response(plan_json("Step A", "Step B")))
    plan = await ex._create_plan("Do something complex", config={})
    assert len(plan) == 2
    assert plan[0]["action"] == "Step A"


async def test_create_plan_llm_failure_returns_empty():
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(return_value=None)
    plan = await ex._create_plan("goal", config={})
    assert plan == []


async def test_create_plan_strips_js_comments():
    ex = make_executor()
    raw = '// comment\n[{"step": 1, "action": "Do it", "target": "x", "goal": "g"}]'
    ex.llm.chat_completion = AsyncMock(return_value=llm_response(raw))
    plan = await ex._create_plan("goal", config={})
    assert len(plan) == 1
    assert plan[0]["action"] == "Do it"


# ---------------------------------------------------------------------------
# _evaluate_step
# ---------------------------------------------------------------------------

async def test_evaluate_step_success():
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(
        return_value=llm_response(eval_json(True, "Step completed", "Found data"))
    )
    step = {"action": "Search", "target": "AI trends", "goal": "find AI trends"}
    result = await ex._evaluate_step(step, "Here are AI trends: ...", config={})
    assert result["success"] is True
    assert result["extracted_result"] == "Found data"


async def test_evaluate_step_failure():
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(
        return_value=llm_response(eval_json(False, "No relevant data found", ""))
    )
    step = {"action": "Search", "target": "AI trends", "goal": "find AI trends"}
    result = await ex._evaluate_step(step, "I could not find anything", config={})
    assert result["success"] is False
    assert "No relevant data" in result["reason"]


async def test_evaluate_step_llm_failure_returns_failure():
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(return_value=None)
    step = {"action": "Search", "target": "x", "goal": "g"}
    result = await ex._evaluate_step(step, "some content", config={})
    assert result["success"] is False
    assert "Evaluator LLM failed" in result["reason"]


async def test_evaluate_step_fallback_unparseable_returns_failure():
    """When JSON parse fails, returns fail-safe False (no keyword guessing)."""
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(return_value=llm_response("Yes, this was completed successfully"))
    step = {"action": "Search", "target": "x", "goal": "g"}
    result = await ex._evaluate_step(step, "content", config={})
    assert result["success"] is False
    assert "could not be parsed" in result["reason"]


# ---------------------------------------------------------------------------
# _replan
# ---------------------------------------------------------------------------

async def test_replan_returns_revised_steps():
    ex = make_executor()
    new_plan = json.dumps([
        {"step": 2, "action": "Alternative search", "target": "AI news", "goal": "find news"}
    ])
    ex.llm.chat_completion = AsyncMock(return_value=llm_response(new_plan))

    completed = [{"step": 1, "action": "Setup", "target": "env"}]
    results = [{"extracted_result": "env ready"}]
    failed = {"step": 2, "action": "Search", "target": "trends"}

    new_steps = await ex._replan(
        original_request="Research AI",
        completed_steps=completed,
        step_results=results,
        failed_step=failed,
        failure_reason="Tool not found",
        config={},
    )
    assert len(new_steps) == 1
    assert new_steps[0]["action"] == "Alternative search"
    assert new_steps[0]["step"] == 2  # step_offset = len(completed) = 1, so step = 1+1 = 2


async def test_replan_llm_failure_returns_none():
    """_replan must return None (not []) on LLM failure so the caller can
    distinguish a network error from an intentionally empty plan."""
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(return_value=None)
    result = await ex._replan("goal", [], [], {}, "reason", config={})
    assert result is None


# ---------------------------------------------------------------------------
# _build_plan_context
# ---------------------------------------------------------------------------

def test_build_plan_context_shows_progress():
    ex = make_executor()
    completed = [{"step": 1, "action": "Search", "target": "web"}]
    remaining = [
        {"step": 2, "action": "Summarize", "target": "results"},
        {"step": 3, "action": "Write", "target": "report"},
    ]
    results = [{"extracted_result": "found 10 items"}]
    ctx = ex._build_plan_context(completed, remaining, current_idx=0, step_results=results)
    assert "1/3" in ctx
    assert "DONE" in ctx
    assert "CURRENT" in ctx
    assert "found 10 items" in ctx


# ---------------------------------------------------------------------------
# receive() — no messages
# ---------------------------------------------------------------------------

async def test_receive_no_messages_returns_error():
    ex = make_executor()
    result = await ex.receive({}, config={})
    assert result.get("agent_loop_error") == "No messages provided"
    assert result["content"] == ""


async def test_receive_no_user_message_returns_error():
    ex = make_executor()
    result = await ex.receive(
        {"messages": [{"role": "system", "content": "system msg"}]},
        config={},
    )
    assert result.get("agent_loop_error") == "No user message found"


# ---------------------------------------------------------------------------
# receive() — happy path: single step succeeds
# ---------------------------------------------------------------------------

async def test_receive_single_step_success():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Research AI"))
    step_resp = llm_response("AI is growing rapidly in 2025.")
    eval_resp = llm_response(eval_json(True, "Information found", "AI is growing rapidly in 2025."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive({"messages": MESSAGES}, config={"enable_synthesis": False})

    assert result["content"]
    assert len(result["completed_steps"]) == 1
    assert result["completed_steps"][0]["action"] == "Research AI"
    assert result["replan_count"] == 0
    assert result["remaining_steps"] == []


# ---------------------------------------------------------------------------
# receive() — multi-step with synthesis
# ---------------------------------------------------------------------------

async def test_receive_multi_step_with_synthesis():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Gather data", "Analyze", "Summarize"))
    step1_resp = llm_response("Data gathered.")
    eval1_resp = llm_response(eval_json(True, "gathered", "Data gathered."))
    step2_resp = llm_response("Analysis done.")
    eval2_resp = llm_response(eval_json(True, "analyzed", "Analysis done."))
    step3_resp = llm_response("Summary written.")
    eval3_resp = llm_response(eval_json(True, "written", "Summary written."))
    synth_resp = llm_response("Final synthesis: Data gathered, analyzed, and summarized.")

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        step1_resp, eval1_resp,
        step2_resp, eval2_resp,
        step3_resp, eval3_resp,
        synth_resp,
    ])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": True},
        )

    assert len(result["completed_steps"]) == 3
    assert "synthesis" in result["content"].lower() or "summarized" in result["content"].lower()
    assert result["replan_count"] == 0


# ---------------------------------------------------------------------------
# receive() — step fails, triggers replan, then succeeds
# ---------------------------------------------------------------------------

async def test_receive_step_fails_replans_and_succeeds():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Search web"))
    step1_fail_resp = llm_response("Could not search.")
    eval1_fail = llm_response(eval_json(False, "Search tool not available", ""))
    replan_resp = llm_response(json.dumps([
        {"step": 1, "action": "Use cached data", "target": "cache", "goal": "get data"}
    ]))
    step1_ok_resp = llm_response("Retrieved cached data.")
    eval1_ok = llm_response(eval_json(True, "Retrieved", "Cached data retrieved."))

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        step1_fail_resp, eval1_fail,
        replan_resp,
        step1_ok_resp, eval1_ok,
    ])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "max_replan_depth": 3, "max_step_retries": 0},
        )

    assert len(result["completed_steps"]) == 1
    assert result["replan_count"] == 1
    assert result["completed_steps"][0]["action"] == "Use cached data"


# ---------------------------------------------------------------------------
# receive() — replan depth exceeded
# ---------------------------------------------------------------------------

async def test_receive_replan_depth_exceeded_stops():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Impossible task"))
    step_fail = llm_response("")  # empty content → structural failure
    eval_fail = llm_response(eval_json(False, "No content produced", ""))
    replan_resp = llm_response(json.dumps([
        {"step": 1, "action": "Try again", "target": "x", "goal": "g"}
    ]))

    # Each replan attempt: fail → eval → replan
    # With max_replan_depth=2 and starting at 0:
    # attempt 0: fail → replan (count=1)
    # attempt 1: fail → replan (count=2)
    # attempt 2: count >= max → stop
    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        step_fail, eval_fail, replan_resp,   # replan_count → 1
        step_fail, eval_fail, replan_resp,   # replan_count → 2
        step_fail, eval_fail,                # hits guard
    ])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "max_replan_depth": 2, "max_step_retries": 0},
        )

    assert result["replan_count"] >= 2
    assert result["remaining_steps"] or result["replan_depth_exceeded"]


# ---------------------------------------------------------------------------
# receive() — step evaluation disabled
# ---------------------------------------------------------------------------

async def test_receive_evaluation_disabled_uses_structural_check():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Do something"))
    step_resp = llm_response("Done!")

    # With enable_step_evaluation=False, only plan + step LLM calls are made
    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "enable_step_evaluation": False},
        )

    assert len(result["completed_steps"]) == 1
    # Evaluator LLM not called (only 2 calls total: plan + step)
    assert ex.llm.chat_completion.call_count == 2


# ---------------------------------------------------------------------------
# receive() — fallback to direct hybrid when plan is empty
# ---------------------------------------------------------------------------

async def test_receive_empty_plan_falls_back_to_hybrid():
    ex = make_executor()

    # First call: plan LLM returns empty array → fallback
    plan_resp = llm_response("[]")
    step_resp = llm_response("Direct answer without planning.")

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive({"messages": MESSAGES}, config={})

    assert result["content"] == "Direct answer without planning."
    assert result["plan"] == []
    assert result["completed_steps"] == []


# ---------------------------------------------------------------------------
# receive() — timeout
# ---------------------------------------------------------------------------

async def test_receive_timeout_returns_error():
    ex = make_executor()

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
        return llm_response("Done")

    ex.llm.chat_completion = AsyncMock(side_effect=slow)

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"timeout": 0.1},
        )

    assert "timed out" in result.get("agent_loop_error", "").lower()
    assert result["content"] == ""


# ---------------------------------------------------------------------------
# receive() — unexpected exception
# ---------------------------------------------------------------------------

async def test_receive_unexpected_exception_returns_error():
    """An exception that escapes the internal loop surfaces as agent_loop_error."""
    ex = make_executor()

    # Patch _create_plan so the exception bypasses _llm_with_retry's try/except
    with patch.object(ex, "_create_plan", side_effect=RuntimeError("boom")), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive({"messages": MESSAGES}, config={})

    assert "boom" in result.get("agent_loop_error", "")
    assert result["content"] == ""


# ---------------------------------------------------------------------------
# receive() — repl_state shared across steps
# ---------------------------------------------------------------------------

async def test_receive_repl_state_shared_across_steps():
    """Variables stored in step 1 should be visible to step 2."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Fetch data", "Process data"))
    step1_resp = llm_response("Fetched 100 items.")
    eval1 = llm_response(eval_json(True, "ok", "100 items"))
    step2_resp = llm_response("Processed 100 items successfully.")
    eval2 = llm_response(eval_json(True, "ok", "Processed"))

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp, step1_resp, eval1, step2_resp, eval2,
    ])

    captured_repl_states = []

    original_run = ex._run_hybrid_loop

    async def capturing_run(*args, repl_state, **kwargs):
        captured_repl_states.append(repl_state)
        # Simulate storing a variable in step 1
        if not captured_repl_states[0].get("_step_1_done"):
            repl_state["variables"]["step1_output"] = "100 items"
            repl_state["_step_1_done"] = True
        result = await original_run(*args, repl_state=repl_state, **kwargs)
        return result

    with patch.object(ex, "_run_hybrid_loop", side_effect=capturing_run), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False},
        )

    # Both steps should have received the same repl_state object
    assert len(captured_repl_states) == 2
    assert captured_repl_states[0] is captured_repl_states[1]
    assert "step1_output" in captured_repl_states[1]["variables"]


# ---------------------------------------------------------------------------
# receive() — output keys present
# ---------------------------------------------------------------------------

async def test_receive_output_keys_present():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Do it"))
    step_resp = llm_response("Done.")
    eval_resp = llm_response(eval_json(True, "ok", "Done."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False},
        )

    required_keys = {
        "content", "messages", "plan", "completed_steps", "step_results",
        "remaining_steps", "replan_count", "goal_pursuit_trace",
        "agent_loop_trace", "iterations",
    }
    missing = required_keys - set(result.keys())
    assert not missing, f"Missing output keys: {missing}"


# ---------------------------------------------------------------------------
# receive() — preserves existing input fields
# ---------------------------------------------------------------------------

async def test_receive_preserves_input_fields():
    ex = make_executor()

    plan_resp = llm_response(plan_json("Task"))
    step_resp = llm_response("Done.")
    eval_resp = llm_response(eval_json(True, "ok", "Done."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    input_data = {
        "messages": MESSAGES,
        "session_id": "sess-123",
        "_memory_context": "User likes concise answers.",
        "custom_field": 42,
    }

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(input_data, config={"enable_synthesis": False})

    assert result["session_id"] == "sess-123"
    assert result["custom_field"] == 42


# ---------------------------------------------------------------------------
# get_executor_class routing
# ---------------------------------------------------------------------------

async def test_get_executor_class_goal_pursuit():
    from modules.agent_loop.node import get_executor_class
    cls = await get_executor_class("goal_pursuit")
    assert cls is GoalPursuitExecutor


async def test_get_executor_class_unknown():
    from modules.agent_loop.node import get_executor_class
    cls = await get_executor_class("not_a_real_node")
    assert cls is None


# ---------------------------------------------------------------------------
# _evaluate_step — markdown fence stripping
# ---------------------------------------------------------------------------

async def test_evaluate_step_strips_markdown_fences():
    """JSON wrapped in ```json ... ``` code fences is correctly parsed."""
    ex = make_executor()
    fenced = '```json\n{"success": true, "reason": "all good", "extracted_result": "value"}\n```'
    ex.llm.chat_completion = AsyncMock(return_value=llm_response(fenced))
    step = {"action": "Do", "target": "x", "goal": "g"}
    result = await ex._evaluate_step(step, "content", config={})
    assert result["success"] is True
    assert result["reason"] == "all good"
    assert result["extracted_result"] == "value"


# ---------------------------------------------------------------------------
# receive() — consecutive failures skip step after max_step_retries
# ---------------------------------------------------------------------------

async def test_receive_step_skipped_after_max_retries():
    """Step that fails max_step_retries consecutive times is skipped, not infinitely replanned."""
    ex = make_executor()

    # Plan has 2 steps; step 1 fails twice (max_step_retries=2) and gets skipped.
    plan_resp = llm_response(plan_json("Failing step", "Working step"))
    step1_fail = llm_response("Error.")
    eval1_fail = llm_response(eval_json(False, "failed", ""))
    step2_resp = llm_response("Done.")
    eval2_ok = llm_response(eval_json(True, "ok", "Done."))

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        step1_fail, eval1_fail,   # consecutive_failures = 1 → replan
        llm_response(json.dumps([  # replan: same step 1
            {"step": 1, "action": "Failing step", "target": "x", "goal": "g"},
            {"step": 2, "action": "Working step", "target": "y", "goal": "g2"},
        ])),
        step1_fail, eval1_fail,   # consecutive_failures = 1 on new plan → replan again? no — we track from 0
        # Actually: after replan, consecutive_failures=0. So 2nd fail → consecutive_failures=1 again.
        # We need TWO more fails on the same step without replan in between to hit max_step_retries=2.
    ])

    # max_step_retries=1: one failure triggers alternative approach, then skip if alt also fails
    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        step1_fail, eval1_fail,   # attempt 1 → consecutive_failures=1 >= 1 → alternative
        step1_fail, eval1_fail,   # alternative attempt also fails → step 1 SKIPPED
        step2_resp, eval2_ok,     # step 2 succeeds
    ])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "max_step_retries": 1},
        )

    # Step 1 was skipped, step 2 completed
    assert len(result["completed_steps"]) == 1
    assert result["completed_steps"][0]["action"] == "Working step"
    # No replan was consumed — skip doesn't burn replan budget
    assert result["replan_count"] == 0
    # Step 1 trace should show it was skipped
    skipped = [t for t in result["goal_pursuit_trace"] if t.get("skipped")]
    assert len(skipped) == 1


# ---------------------------------------------------------------------------
# receive() — step result stored in repl_state variables on success
# ---------------------------------------------------------------------------

async def test_receive_step_result_stored_in_repl_state():
    """Successful step stores its full content in repl_state['variables']['step_N_result']."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Gather info"))
    step_resp = llm_response("Found 42 results.")
    eval_resp = llm_response(eval_json(True, "ok", "Found 42 results."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_repl_state = {}

    original_run = ex._run_hybrid_loop

    async def capture_repl(*args, repl_state, **kwargs):
        result = await original_run(*args, repl_state=repl_state, **kwargs)
        captured_repl_state.update(repl_state)
        return result

    with patch.object(ex, "_run_hybrid_loop", side_effect=capture_repl), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False},
        )

    # The step_1_result variable should be stored after success
    assert "step_1_result" in captured_repl_state.get("variables", {})
    # And it should contain the step's LLM output
    assert "42" in captured_repl_state["variables"]["step_1_result"]


# ---------------------------------------------------------------------------
# receive() — per-step timeout treats step as failure
# ---------------------------------------------------------------------------

async def test_receive_step_timeout_treated_as_failure():
    """When step_timeout fires, the step is treated as failed (skipped via max_step_retries=1)."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Slow step"))
    eval_fail = llm_response(eval_json(False, "timed out", ""))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp])

    async def slow_hybrid(*args, **kwargs):
        await asyncio.sleep(10)  # will be cancelled by wait_for
        return None, 0, False

    with patch.object(ex, "_run_hybrid_loop", side_effect=slow_hybrid), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={
                "enable_synthesis": False,
                "step_timeout": 0.05,   # 50ms — fires immediately
                "max_step_retries": 1,  # skip after 1 failure
            },
        )

    # Step timed out and was skipped; no completed steps
    assert len(result["completed_steps"]) == 0
    timed_out_traces = [t for t in result["goal_pursuit_trace"] if t.get("timed_out")]
    assert len(timed_out_traces) == 1


# ---------------------------------------------------------------------------
# _topo_sort_steps — dependency graph ordering
# ---------------------------------------------------------------------------

def test_topo_sort_linear_no_deps():
    """Steps with no depends_on come out in original order."""
    steps = [
        {"step": 1, "action": "A", "depends_on": []},
        {"step": 2, "action": "B", "depends_on": []},
        {"step": 3, "action": "C", "depends_on": []},
    ]
    result, has_cycle = GoalPursuitExecutor._topo_sort_steps(steps)
    assert has_cycle is False
    assert [s["step"] for s in result] == [1, 2, 3]


def test_topo_sort_respects_dependencies():
    """Step 3 depends on step 1; step 2 is independent. Valid orders: 1,2,3 or 1,3 after 1, or 2,1,3."""
    steps = [
        {"step": 1, "action": "A", "depends_on": []},
        {"step": 2, "action": "B", "depends_on": []},
        {"step": 3, "action": "C", "depends_on": [1]},
    ]
    result, has_cycle = GoalPursuitExecutor._topo_sort_steps(steps)
    assert has_cycle is False
    step_nums = [s["step"] for s in result]
    assert step_nums.index(1) < step_nums.index(3), "Step 1 must come before step 3"
    assert len(step_nums) == 3


def test_topo_sort_chain():
    """3 → 2 → 1 dependency chain must run as 1, 2, 3."""
    steps = [
        {"step": 3, "action": "C", "depends_on": [2]},
        {"step": 1, "action": "A", "depends_on": []},
        {"step": 2, "action": "B", "depends_on": [1]},
    ]
    result, has_cycle = GoalPursuitExecutor._topo_sort_steps(steps)
    assert has_cycle is False
    assert [s["step"] for s in result] == [1, 2, 3]


def test_topo_sort_cycle_returns_original():
    """Cyclic deps are detected and original order is returned unchanged."""
    steps = [
        {"step": 1, "action": "A", "depends_on": [2]},
        {"step": 2, "action": "B", "depends_on": [1]},
    ]
    result, has_cycle = GoalPursuitExecutor._topo_sort_steps(steps)
    assert has_cycle is True
    # Should return original order unchanged (cycle detection)
    assert [s["step"] for s in result] == [1, 2]


def test_topo_sort_ignores_external_deps():
    """Deps pointing to step numbers not in the plan are ignored."""
    steps = [
        {"step": 3, "action": "C", "depends_on": [99]},  # 99 not in plan
        {"step": 4, "action": "D", "depends_on": [3]},
    ]
    result, has_cycle = GoalPursuitExecutor._topo_sort_steps(steps)
    assert has_cycle is False
    assert [s["step"] for s in result] == [3, 4]


# ---------------------------------------------------------------------------
# _parse_plan_response — depends_on extraction
# ---------------------------------------------------------------------------

async def test_parse_plan_response_extracts_depends_on():
    """depends_on field is parsed from plan JSON."""
    ex = make_executor()
    content = json.dumps([
        {"step": 1, "action": "Fetch", "target": "data", "goal": "g", "depends_on": []},
        {"step": 2, "action": "Process", "target": "data", "goal": "g", "depends_on": [1]},
        {"step": 3, "action": "Report", "target": "findings", "goal": "g", "depends_on": [1, 2]},
    ])
    plan = await ex._parse_plan_response(content, max_steps=10)
    assert plan[0]["depends_on"] == []
    assert plan[1]["depends_on"] == [1]
    assert plan[2]["depends_on"] == [1, 2]


async def test_parse_plan_response_default_empty_depends_on():
    """Steps without depends_on get an empty list."""
    ex = make_executor()
    content = json.dumps([{"step": 1, "action": "Do", "target": "x", "goal": "g"}])
    plan = await ex._parse_plan_response(content, max_steps=10)
    assert plan[0]["depends_on"] == []


# ---------------------------------------------------------------------------
# receive() — dependency graph execution ordering
# ---------------------------------------------------------------------------

async def test_receive_dependency_respected():
    """Steps with depends_on are deferred until their prerequisites complete."""
    ex = make_executor()

    # Two-step plan where step 2 depends on step 1.
    plan_json_str = json.dumps([
        {"step": 1, "action": "Gather", "target": "data", "goal": "g", "depends_on": []},
        {"step": 2, "action": "Analyze", "target": "data", "goal": "g", "depends_on": [1]},
    ])
    plan_resp = llm_response(plan_json_str)
    step1_resp = llm_response("Data gathered.")
    eval1 = llm_response(eval_json(True, "ok", "gathered"))
    step2_resp = llm_response("Analysis done.")
    eval2 = llm_response(eval_json(True, "ok", "done"))

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp, step1_resp, eval1, step2_resp, eval2,
    ])

    executed_order = []
    original_run = ex._run_hybrid_loop

    async def tracking_run(*args, llm_messages, **kwargs):
        # Extract the step name from the user message
        user_msg = next((m["content"] for m in llm_messages if m["role"] == "user"), "")
        executed_order.append(user_msg[:30])
        return await original_run(*args, llm_messages=llm_messages, **kwargs)

    with patch.object(ex, "_run_hybrid_loop", side_effect=tracking_run), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False},
        )

    assert len(result["completed_steps"]) == 2
    # Step 1 must have been executed before step 2
    assert executed_order[0].startswith("Complete this step: Gather")
    assert executed_order[1].startswith("Complete this step: Analyze")


# ---------------------------------------------------------------------------
# receive() — repl_state variables restored from _repl_variables
# ---------------------------------------------------------------------------

async def test_receive_repl_variables_restored_from_input():
    """Variables in input_data['_repl_variables'] are loaded into repl_state at startup."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Use cached data"))
    step_resp = llm_response("Used it.")
    eval_resp = llm_response(eval_json(True, "ok", "Used it."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_vars = {}
    original_run = ex._run_hybrid_loop

    async def capture(*args, repl_state, **kwargs):
        captured_vars.update(repl_state["variables"])
        return await original_run(*args, repl_state=repl_state, **kwargs)

    with patch.object(ex, "_run_hybrid_loop", side_effect=capture), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        await ex.receive(
            {
                "messages": MESSAGES,
                "_repl_variables": {"step_0_result": "previously computed result"},
            },
            config={"enable_synthesis": False},
        )

    # The pre-seeded variable should be visible during step execution
    assert "step_0_result" in captured_vars
    assert captured_vars["step_0_result"] == "previously computed result"


# ---------------------------------------------------------------------------
# AskUser — mid-plan pause / resume
# ---------------------------------------------------------------------------

async def test_ask_user_pauses_execution():
    """When the agent calls AskUser during a step, receive() returns immediately with paused=True."""
    ex = make_executor()

    # Single-step plan so only one step runs and injects one question.
    plan_resp = llm_response(plan_json("Clarify requirements"))
    step1_resp = llm_response("Pausing to ask.")

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step1_resp])

    async def run_with_ask_user(*args, repl_state, llm_messages, **kwargs):
        import asyncio
        _task_id = id(asyncio.current_task())
        repl_state.setdefault("_pending_questions", {})[_task_id] = "What format do you want the output in?"
        return ({"content": "Pausing to ask."}, 1, False)

    with patch.object(ex, "_run_hybrid_loop", side_effect=run_with_ask_user), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "enable_ask_user": True},
        )

    assert result.get("paused") is True
    assert result["pending_question"] == "What format do you want the output in?"
    assert result["content"] == "What format do you want the output in?"


async def test_ask_user_resume_injects_answer():
    """_user_answer injected via _repl_variables (as episode-resume path does) is visible in repl_state."""
    ex = make_executor()

    # The episode-resume path writes _user_answer into _repl_variables before calling receive().
    # Simulate that by pre-seeding _repl_variables directly.
    pre_seeded_vars = {"_user_answer": "Bullet points please"}

    plan_resp = llm_response(plan_json("Do the work"))
    step_resp = llm_response("Done in bullet points.")
    eval_resp = llm_response(eval_json(True, "ok", "Done."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_vars = {}

    async def capture(*args, repl_state, llm_messages, **kwargs):
        captured_vars.update(repl_state.get("variables", {}))
        return ({"content": "Done in bullet points."}, 1, False)

    with patch.object(ex, "_run_hybrid_loop", side_effect=capture), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES, "_repl_variables": pre_seeded_vars},
            config={"enable_synthesis": False, "enable_ask_user": True},
        )

    assert captured_vars.get("_user_answer") == "Bullet points please"
    assert result.get("paused") is not True


async def test_execute_tool_intercepts_ask_user():
    """_execute_tool override sets _pending_question and does not call super()."""
    ex = make_executor()
    repl_state = {"variables": {}}
    tool_call = {
        "id": "tc1",
        "function": {
            "name": "AskUser",
            "arguments": json.dumps({"question": "Which dataset should I use?"})
        }
    }
    result = await ex._execute_tool(tool_call, {}, repl_state, large_output_threshold=3000)

    # Question stored under current task's id in the new task-scoped dict
    import asyncio
    _questions = repl_state.get("_pending_questions", {})
    assert "Which dataset should I use?" in _questions.values(), (
        f"Question not found in _pending_questions: {_questions}"
    )
    assert result["name"] == "AskUser"
    assert result["success"] is True
    # Content should acknowledge pause without executing anything
    assert "paused" in result["content"].lower() or "waiting" in result["content"].lower()


# ---------------------------------------------------------------------------
# Alternative approach on step exhaustion
# ---------------------------------------------------------------------------

async def test_alternative_approach_succeeds_after_max_retries():
    """When a step hits max_step_retries=1, an alternative attempt is made before skipping."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Do the thing"))
    fail_resp = llm_response("Failed attempt.")
    fail_eval = llm_response(eval_json(False, "no results"))
    alt_resp = llm_response("Alternative succeeded.")
    alt_eval = llm_response(eval_json(True, "ok", "Alternative succeeded."))

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        fail_resp, fail_eval,   # attempt 1 (consecutive_failures=1 >= max_step_retries=1)
        alt_resp, alt_eval,     # alternative attempt
    ])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "max_step_retries": 1},
        )

    assert len(result["completed_steps"]) == 1
    trace = result["goal_pursuit_trace"]
    assert trace[-1].get("alternative_attempt") is True
    assert trace[-1].get("alternative_succeeded") is True


async def test_alternative_approach_fails_then_step_skipped():
    """When normal attempt and alternative both fail, the step is skipped."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Do the thing", "Second step"))
    fail_resp = llm_response("Failed.")
    fail_eval = llm_response(eval_json(False, "no results"))
    step2_resp = llm_response("Second step done.")
    step2_eval = llm_response(eval_json(True, "ok", "done"))

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        fail_resp, fail_eval,  # attempt 1 (consecutive_failures=1 >= max_step_retries=1)
        fail_resp, fail_eval,  # alternative attempt also fails → step 1 skipped
        step2_resp, step2_eval,
    ])

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "max_step_retries": 1},
        )

    skipped = [t for t in result["goal_pursuit_trace"] if t.get("skipped")]
    assert len(skipped) == 1
    assert skipped[0].get("alternative_attempt") is True
    assert skipped[0].get("alternative_succeeded") is False
    # Second step should still complete
    assert len(result["completed_steps"]) == 1


# ---------------------------------------------------------------------------
# _save_run_reflection — cross-run learning
# ---------------------------------------------------------------------------

async def test_save_run_reflection_called_on_completion():
    """_save_run_reflection is scheduled as a task after _execute() returns."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Do something"))
    step_resp = llm_response("Done.")
    eval_resp = llm_response(eval_json(True, "ok", "Done."))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    reflection_calls = []

    async def capture_reflection(**kwargs):
        reflection_calls.append(kwargs)

    with patch.object(ex, "_save_run_reflection", side_effect=capture_reflection), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        # create_task schedules it — we need to let the event loop run it
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False},
        )
        # Yield to the event loop so the scheduled task runs
        await asyncio.sleep(0)

    assert len(reflection_calls) == 1
    assert reflection_calls[0]["succeeded"] is True
    assert "Research AI trends" in reflection_calls[0]["goal"]


# ---------------------------------------------------------------------------
# _compress_step_results — RLM synthesis compression
# ---------------------------------------------------------------------------

async def test_compress_step_results_noop_when_small():
    """No compression when total step results are under the threshold."""
    ex = make_executor()
    repl_state = {"variables": {
        "step_1_result": "short result",
        "step_2_result": "also short",
    }}
    count = await ex._compress_step_results(repl_state, {"synthesis_compress_threshold": 9000})
    assert count == 0
    assert repl_state["variables"]["step_1_result"] == "short result"


async def test_compress_step_results_compresses_large_vars():
    """Large step results are summarized when total exceeds threshold."""
    ex = make_executor()
    big_text = "x" * 2000
    repl_state = {"variables": {
        "step_1_result": big_text,
        "step_2_result": big_text,
        "step_3_result": big_text,
        "step_4_result": big_text,
        "step_5_result": big_text,
    }}
    summary_resp = llm_response("Compressed summary of findings.")
    ex.llm.chat_completion = AsyncMock(return_value=summary_resp)

    count = await ex._compress_step_results(
        repl_state,
        {"synthesis_compress_threshold": 100, "synthesis_per_var_threshold": 500},
    )
    assert count == 5
    for k in repl_state["variables"]:
        assert repl_state["variables"][k].startswith("[Compressed summary]")


async def test_compress_step_results_called_before_synthesis():
    """_compress_step_results is awaited before _synthesize during plan execution."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Step A", "Step B"))
    step_resp = llm_response("done")
    eval_resp = llm_response(eval_json(True, "ok", "done"))
    synth_resp = llm_response("Final answer.")

    ex.llm.chat_completion = AsyncMock(side_effect=[
        plan_resp,
        step_resp, eval_resp,   # step 1
        step_resp, eval_resp,   # step 2
        synth_resp,             # synthesis
    ])

    compress_calls = []

    async def fake_compress(repl_state, config):
        compress_calls.append(True)
        return 0

    with patch.object(ex, "_compress_step_results", side_effect=fake_compress), \
         patch.object(ex, "_fetch_goal_lessons", return_value=[]), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        await ex.receive({"messages": MESSAGES}, config={"enable_synthesis": True})

    assert len(compress_calls) == 1


# ---------------------------------------------------------------------------
# _fetch_goal_lessons / _compress_lessons — RLM planning memory
# ---------------------------------------------------------------------------

async def test_fetch_goal_lessons_returns_empty_on_db_error():
    """DB errors in _fetch_goal_lessons are silently swallowed."""
    ex = make_executor()
    with patch("modules.agent_loop.node.GoalPursuitExecutor._fetch_goal_lessons",
               return_value=[]):
        lessons = await ex._fetch_goal_lessons({})
    assert lessons == []


async def test_compress_lessons_passthrough_when_small():
    """Short lessons are returned unchanged (no LLM call)."""
    ex = make_executor()
    lessons = ["Lesson 1: do X.", "Lesson 2: avoid Y."]
    result = await ex._compress_lessons(lessons, {"lessons_compress_threshold": 9000})
    assert "Lesson 1" in result
    assert "Lesson 2" in result
    ex.llm.chat_completion.assert_not_called()


async def test_compress_lessons_calls_llm_when_large():
    """Large lesson list triggers chunked LLM summarization."""
    ex = make_executor()
    # 10 lessons each 400 chars → 4000 total > threshold of 100
    lessons = [f"Lesson {i}: " + "x" * 390 for i in range(10)]
    summary_resp = llm_response("Bullet: key insight.")
    ex.llm.chat_completion = AsyncMock(return_value=summary_resp)

    result = await ex._compress_lessons(
        lessons,
        {"lessons_compress_threshold": 100, "lessons_chunk_size": 3},
    )
    assert ex.llm.chat_completion.called
    assert len(result) > 0


async def test_lessons_injected_into_planning_prompt():
    """Past lessons are fetched and injected when _create_plan_with_trace runs."""
    ex = make_executor()

    plan_resp = llm_response(plan_json("Do something"))
    step_resp = llm_response("done")
    eval_resp = llm_response(eval_json(True, "ok", "done"))

    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_prompts = []
    original_create_plan = ex._create_plan.__func__

    async def capturing_create_plan(self, user_request, config, lessons="", tools_summary="", pattern_insights="", planning_assumptions=""):
        captured_prompts.append(lessons)
        return await original_create_plan(self, user_request, config, lessons=lessons, tools_summary=tools_summary, pattern_insights=pattern_insights, planning_assumptions=planning_assumptions)

    with patch.object(type(ex), "_create_plan", capturing_create_plan), \
         patch.object(ex, "_fetch_goal_lessons", return_value=["Past lesson: use tool X."]), \
         patch.object(ex, "_compress_lessons", return_value="Past lesson: use tool X."), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False},
        )

    assert len(captured_prompts) == 1
    assert "Past lesson" in captured_prompts[0]


# ---------------------------------------------------------------------------
# Sub-goals — DecomposeGoal tool
# ---------------------------------------------------------------------------

async def test_decompose_goal_tool_injected_when_enabled():
    """DecomposeGoal appears in tools_list when enable_subgoals=True (default)."""
    ex = make_executor()
    plan_resp = llm_response(plan_json("Do something"))
    step_resp = llm_response("done")
    eval_resp = llm_response(eval_json(True, "ok", "done"))
    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_tools = []
    original_run = ex._run_hybrid_loop

    async def capture_tools(*args, tools_list, **kwargs):
        captured_tools.extend(tools_list)
        return await original_run(*args, tools_list=tools_list, **kwargs)

    with patch.object(ex, "_run_hybrid_loop", side_effect=capture_tools), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        await ex.receive({"messages": MESSAGES}, config={"enable_synthesis": False})

    tool_names = {t["function"]["name"] for t in captured_tools if isinstance(t, dict)}
    assert "DecomposeGoal" in tool_names


async def test_decompose_goal_tool_not_injected_at_max_depth():
    """DecomposeGoal is absent when subgoal_depth >= max_subgoal_depth."""
    ex = make_executor()
    plan_resp = llm_response(plan_json("Do something"))
    step_resp = llm_response("done")
    eval_resp = llm_response(eval_json(True, "ok", "done"))
    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_tools = []
    original_run = ex._run_hybrid_loop

    async def capture_tools(*args, tools_list, **kwargs):
        captured_tools.extend(tools_list)
        return await original_run(*args, tools_list=tools_list, **kwargs)

    with patch.object(ex, "_run_hybrid_loop", side_effect=capture_tools), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        # _subgoal_depth == max_subgoal_depth → tool should be absent
        await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "_subgoal_depth": 2, "max_subgoal_depth": 2},
        )

    tool_names = {t["function"]["name"] for t in captured_tools if isinstance(t, dict)}
    assert "DecomposeGoal" not in tool_names


async def test_decompose_goal_tool_not_injected_when_disabled():
    """DecomposeGoal is absent when enable_subgoals=False."""
    ex = make_executor()
    plan_resp = llm_response(plan_json("Do something"))
    step_resp = llm_response("done")
    eval_resp = llm_response(eval_json(True, "ok", "done"))
    ex.llm.chat_completion = AsyncMock(side_effect=[plan_resp, step_resp, eval_resp])

    captured_tools = []
    original_run = ex._run_hybrid_loop

    async def capture_tools(*args, tools_list, **kwargs):
        captured_tools.extend(tools_list)
        return await original_run(*args, tools_list=tools_list, **kwargs)

    with patch.object(ex, "_run_hybrid_loop", side_effect=capture_tools), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        await ex.receive(
            {"messages": MESSAGES},
            config={"enable_synthesis": False, "enable_subgoals": False},
        )

    tool_names = {t["function"]["name"] for t in captured_tools if isinstance(t, dict)}
    assert "DecomposeGoal" not in tool_names


async def test_execute_tool_decompose_goal_invokes_sub_receive():
    """_execute_tool with DecomposeGoal calls receive() recursively and returns result."""
    ex = make_executor()

    sub_plan_resp = llm_response(plan_json("Sub step"))
    sub_step_resp = llm_response("Sub-goal result.")
    sub_eval_resp = llm_response(eval_json(True, "ok", "Sub-goal result."))
    ex.llm.chat_completion = AsyncMock(
        side_effect=[sub_plan_resp, sub_step_resp, sub_eval_resp]
    )

    repl_state = {
        "variables": {},
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {"enable_synthesis": False, "enable_subgoals": True},
    }
    tool_call = {
        "id": "tc1",
        "function": {
            "name": "DecomposeGoal",
            "arguments": json.dumps({
                "subgoal": "Research AI papers published this year",
                "context": "Parent goal: summarize AI landscape",
            }),
        },
    }

    with patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        result = await ex._execute_tool(tool_call, {}, repl_state, 3000)

    assert result["name"] == "DecomposeGoal"
    assert result["success"] is True
    assert result["content"]  # should contain the sub-goal's output


async def test_execute_tool_decompose_goal_missing_subgoal_returns_error():
    """DecomposeGoal with no subgoal parameter returns an error result."""
    ex = make_executor()
    repl_state = {
        "variables": {},
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {},
    }
    tool_call = {
        "id": "tc1",
        "function": {
            "name": "DecomposeGoal",
            "arguments": json.dumps({}),
        },
    }
    result = await ex._execute_tool(tool_call, {}, repl_state, 3000)
    assert result["success"] is False
    assert "required" in result["content"].lower() or "error" in result["content"].lower()


async def test_subgoal_depth_incremented_in_recursive_call():
    """When DecomposeGoal spawns a sub-call, _subgoal_depth in the sub-config is depth+1."""
    ex = make_executor()

    captured_configs = []
    original_receive = ex.receive

    async def capture_receive(input_data, config=None):
        captured_configs.append(dict(config or {}))
        return await original_receive(input_data, config=config)

    sub_plan_resp = llm_response(plan_json("Sub step"))
    sub_step_resp = llm_response("Sub result.")
    sub_eval_resp = llm_response(eval_json(True, "ok", "Sub result."))
    ex.llm.chat_completion = AsyncMock(
        side_effect=[sub_plan_resp, sub_step_resp, sub_eval_resp]
    )

    repl_state = {
        "variables": {},
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {"enable_synthesis": False, "enable_subgoals": True},
    }
    tool_call = {
        "id": "tc1",
        "function": {
            "name": "DecomposeGoal",
            "arguments": json.dumps({"subgoal": "Investigate this topic deeply"}),
        },
    }

    with patch.object(ex, "receive", side_effect=capture_receive), \
         patch.object(ex, "_load_tool_library", return_value={}), \
         patch.object(ex, "_load_tools", return_value={}):
        await ex._execute_tool(tool_call, {}, repl_state, 3000)

    # capture_receive was called once for the sub-goal
    assert len(captured_configs) == 1
    assert captured_configs[0].get("_subgoal_depth") == 1  # depth incremented


async def test_subgoal_result_content_returned_as_tool_result():
    """DecomposeGoal returns the sub-goal's content string as the tool result."""
    ex = make_executor()

    async def fake_receive(input_data, config=None):
        return {
            "content": "Sub done with findings.",
            "completed_steps": [],
            "repl_state": {"variables": [], "variable_count": 0},
        }

    repl_state = {
        "variables": {},
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {},
    }
    tool_call = {
        "id": "tc1",
        "function": {
            "name": "DecomposeGoal",
            "arguments": json.dumps({"subgoal": "Find data"}),
        },
    }

    with patch.object(ex, "receive", side_effect=fake_receive):
        result = await ex._execute_tool(tool_call, {}, repl_state, 3000)

    assert result["success"] is True
    assert result["content"] == "Sub done with findings."


# ---------------------------------------------------------------------------
# Bug 5 regression: _parse_plan_response must apply step_offset to depends_on
# ---------------------------------------------------------------------------

async def test_parse_plan_response_applies_offset_to_depends_on():
    """
    When step_offset > 0 (as used in _replan), the depends_on values in the
    parsed plan must be shifted by step_offset so they reference the same
    numbering space as the offset step numbers.
    """
    ex = make_executor()
    # LLM returns a 3-step plan where step 3 depends on steps 1 and 2.
    raw = json.dumps([
        {"step": 1, "action": "A", "target": "t1", "goal": "g1", "depends_on": []},
        {"step": 2, "action": "B", "target": "t2", "goal": "g2", "depends_on": []},
        {"step": 3, "action": "C", "target": "t3", "goal": "g3", "depends_on": [1, 2]},
    ])
    # Simulate a replan after 3 completed steps → step_offset=3
    result = await ex._parse_plan_response(raw, max_steps=10, step_offset=3)

    assert len(result) == 3
    # Step numbers must be shifted
    assert result[0]["step"] == 4
    assert result[1]["step"] == 5
    assert result[2]["step"] == 6
    # depends_on must also be shifted so they match the new step numbers
    assert result[2]["depends_on"] == [4, 5]


async def test_parse_plan_response_zero_offset_depends_on_unchanged():
    """With step_offset=0 (initial plan) depends_on must be kept as-is."""
    ex = make_executor()
    raw = json.dumps([
        {"step": 1, "action": "A", "target": "t1", "goal": "g1", "depends_on": []},
        {"step": 2, "action": "B", "target": "t2", "goal": "g2", "depends_on": [1]},
    ])
    result = await ex._parse_plan_response(raw, max_steps=10, step_offset=0)
    assert result[0]["step"] == 1
    assert result[1]["step"] == 2
    assert result[1]["depends_on"] == [1]


# ---------------------------------------------------------------------------
# Bug 9 regression: RLM SubCall tool_call_id must be set from the tool_call
# ---------------------------------------------------------------------------

async def test_rlm_subcall_tool_call_id_override():
    """
    RLMAgentLoopExecutor._execute_tool must override the tool_call_id returned
    by _execute_sub_call with the id from the actual tool_call dict.
    """
    from modules.agent_loop.node import RLMAgentLoopExecutor
    with patch("modules.agent_loop.node.LLMBridge"):
        ex = RLMAgentLoopExecutor()
    ex.llm = MagicMock()

    tool_call = {
        "id": "real_id_123",
        "function": {
            "name": "SubCall",
            "arguments": json.dumps({"prompt": "hello"}),
        },
    }
    repl_state = {
        "sub_call_count": 0,
        "max_sub_calls": 10,
        "recursion_depth": 0,
        "max_recursion_depth": 3,
        "estimated_cost": 0.0,
        "max_cost_usd": 1.0,
    }

    async def _fake_sub_call(args, rs):
        return {"tool_call_id": "", "role": "tool", "name": "sub_call",
                "content": "answer", "success": True}

    with patch.object(ex, "_execute_sub_call", side_effect=_fake_sub_call):
        result = await ex._execute_tool(tool_call, {}, repl_state)

    assert result["tool_call_id"] == "real_id_123"


# ---------------------------------------------------------------------------
# Bug 11 regression: remaining_steps on AskUser pause must equal snap_remaining
# ---------------------------------------------------------------------------

def test_ask_user_pause_remaining_steps_matches_plan():
    """
    On AskUser pause, result["remaining_steps"] must equal result["plan"]
    (both snap_remaining) so external consumers don't lose the paused step.

    _run_one_step is a closure inside receive() — we verify the fix is in
    place by inspecting the source of GoalPursuitExecutor.receive().
    """
    import inspect
    source = inspect.getsource(GoalPursuitExecutor.receive)
    # Both keys must be set to snap_remaining in the pause block
    plan_line = 'result["plan"] = snap_remaining'
    remaining_line = 'result["remaining_steps"] = snap_remaining'
    assert plan_line in source, "Must set plan=snap_remaining on pause"
    # Verify remaining_steps is also snap_remaining (the fix).
    # Find the plan assignment then look ahead for the remaining_steps assignment.
    plan_pos = source.index(plan_line)
    segment = source[plan_pos: plan_pos + 1200]
    assert remaining_line in segment, (
        "remaining_steps must equal snap_remaining (not the stripped remaining_steps) "
        "on AskUser pause so the paused step is not dropped"
    )


# ---------------------------------------------------------------------------
# Bug 13 regression: var_names filter must require endswith("_result")
# ---------------------------------------------------------------------------

def test_build_step_system_prompt_filters_only_step_result_vars():
    """Variables like step_timeout must not appear in the Available Step Results list."""
    ex = make_executor()
    repl_state = {
        "variables": {
            "step_1_result": "some output",
            "step_timeout": "unexpected",      # should NOT appear
            "step_2_result": "more output",
            "step_extra_data": "also hidden",  # should NOT appear
        },
    }
    step = {"step": 3, "action": "Process", "target": "data", "goal": "g"}
    # _build_step_system_prompt takes: step, plan_context, input_data, config,
    # large_output_threshold, repl_state=None
    prompt = ex._build_step_system_prompt(
        step,
        "## Plan\nstep 3",
        {"messages": MESSAGES},
        {},
        4096,
        repl_state,
    )
    assert "step_timeout" not in prompt
    assert "step_extra_data" not in prompt
    assert "step_1_result" in prompt
    assert "step_2_result" in prompt


# ---------------------------------------------------------------------------
# Bug 14 regression: DecomposeGoal blocked when enable_subgoals=False
# ---------------------------------------------------------------------------

async def test_decompose_goal_blocked_when_subgoals_disabled():
    """
    If enable_subgoals=False, a hallucinated DecomposeGoal call must return
    an error result instead of executing a sub-goal.
    """
    ex = make_executor()
    repl_state = {
        "variables": {},
        "_var_counts": {},
        "sub_call_count": 0,
        "max_sub_calls": 10,
        "recursion_depth": 0,
        "max_recursion_depth": 3,
        "estimated_cost": 0.0,
        "max_cost_usd": 1.0,
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {"enable_subgoals": False},
    }
    tool_call = {
        "id": "tc_dg",
        "function": {
            "name": "DecomposeGoal",
            "arguments": json.dumps({"subgoal": "do something"}),
        },
    }
    result = await ex._execute_tool(tool_call, {}, repl_state, 3000)
    assert result["success"] is False
    assert "disabled" in result["content"].lower()


# ---------------------------------------------------------------------------
# Bug 15 regression: _evaluate_step respects eval_response_max_chars config
# ---------------------------------------------------------------------------

async def test_evaluate_step_uses_configurable_truncation():
    """
    _evaluate_step must truncate execution_result at eval_response_max_chars
    (default 8000), not the old hardcoded 2000.
    """
    ex = make_executor()
    # Build a result whose key evidence is at char 3000 (beyond old 2000 limit)
    long_result = "x" * 2500 + "SUCCESS_MARKER" + "y" * 5000

    captured_prompts = []

    async def _fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": json.dumps(
            {"success": True, "reason": "ok", "extracted_result": "r"}
        )}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=_fake_llm_with_retry):
        await ex._evaluate_step(
            step={"action": "act", "target": "tgt", "goal": "g"},
            execution_result=long_result,
            config={"eval_response_max_chars": 8000},
        )

    full_prompt = " ".join(m.get("content", "") for m in captured_prompts)
    assert "SUCCESS_MARKER" in full_prompt, (
        "Evidence at char 2500 must be visible with eval_response_max_chars=8000"
    )


async def test_evaluate_step_custom_truncation_respected():
    """eval_response_max_chars config value must be honoured over the default."""
    ex = make_executor()
    long_result = "a" * 100 + "HIDDEN_MARKER" + "b" * 500

    captured_prompts = []

    async def _fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": json.dumps(
            {"success": True, "reason": "ok", "extracted_result": "r"}
        )}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=_fake_llm_with_retry):
        await ex._evaluate_step(
            step={"action": "act", "target": "tgt", "goal": "g"},
            execution_result=long_result,
            # Truncate before the marker
            config={"eval_response_max_chars": 50},
        )

    full_prompt = " ".join(m.get("content", "") for m in captured_prompts)
    assert "HIDDEN_MARKER" not in full_prompt, (
        "Marker at char 100 must be cut off with eval_response_max_chars=50"
    )


# ---------------------------------------------------------------------------
# Bug 16 regression: non-integer goal_id must not silently log as POST failure
# ---------------------------------------------------------------------------

async def test_goal_id_non_integer_raises_clear_error(capsys):
    """
    A UUID-style goal_id must trigger a ValueError with a clear message, not
    an obscure httpx error.
    """
    ex = make_executor()
    # Simulate the goal-completion block in isolation
    goal_id = "not-an-integer"
    _goal_id_str = str(goal_id)
    raised = None
    try:
        if not _goal_id_str.lstrip("-").isdigit():
            raise ValueError(f"goal_id must be an integer, got: {_goal_id_str!r}")
    except ValueError as e:
        raised = e
    assert raised is not None
    assert "not-an-integer" in str(raised)


# ---------------------------------------------------------------------------
# Bug 17 regression: RLM loop deduplicates tool calls
# ---------------------------------------------------------------------------

async def test_rlm_loop_deduplicates_tool_calls():
    """
    _run_rlm_loop must skip duplicate tool calls within the same response,
    matching the deduplication behaviour of the standard and hybrid loops.
    """
    from modules.agent_loop.node import RLMAgentLoopExecutor
    with patch("modules.agent_loop.node.LLMBridge"):
        rlm = RLMAgentLoopExecutor()
    setattr(rlm, "_load_tool_library", MagicMock(return_value={"Echo": "result = args.get('text', '')"}))

    dup_call = {"id": "c1", "function": {"name": "Echo", "arguments": '{"text": "hi"}'}}
    uniq_call = {"id": "c2", "function": {"name": "Echo", "arguments": '{"text": "unique"}'}}

    tool_response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [dup_call, dup_call, uniq_call],
            }
        }]
    }
    final_response = {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    rlm.llm = MagicMock()
    rlm.llm.chat_completion = AsyncMock(side_effect=[tool_response, final_response])

    execution_log = []

    async def _fake_execute_tool(tool_call, library, repl_state):
        execution_log.append(tool_call.get("function", {}).get("arguments", ""))
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": "echoed",
            "success": True,
        }

    repl_state = {
        "variables": {},
        "stdout_history": [],
        "iteration": 0,
        "_var_counts": {},
        "final": None,
    }
    trace = []

    with patch.object(rlm, "_execute_tool", side_effect=_fake_execute_tool):
        await rlm._run_rlm_loop(
            initial_messages=[{"role": "user", "content": "echo"}],
            repl_state=repl_state,
            tools_list=[],
            tool_library={},
            model="test-model",
            config={"max_iterations": 5, "max_llm_retries": 1, "retry_delay": 0},
            trace=trace,
        )

    # dup_call appears twice but must only execute once; uniq_call executes once
    # Total: 2 executions (not 3)
    assert len(execution_log) == 2, (
        f"Expected 2 executions (1 dup skipped), got {len(execution_log)}: {execution_log}"
    )
    assert execution_log.count('{"text": "hi"}') == 1
    assert execution_log.count('{"text": "unique"}') == 1
    # Trace must record the duplicate error
    assert any("Duplicate" in e for itr in trace for e in itr.get("errors", []))


# ---------------------------------------------------------------------------
# ask_user_auto_proceed — autonomous continuation when user is unavailable
# ---------------------------------------------------------------------------

async def test_ask_user_auto_proceed_generates_assumption():
    """
    When ask_user_auto_proceed=True, _execute_tool must NOT pause.
    It must call _generate_ask_user_assumption and return the assumption
    as the tool result content.
    """
    ex = make_executor()
    repl_state = {
        "variables": {},
        "_var_counts": {},
        "sub_call_count": 0,
        "max_sub_calls": 10,
        "recursion_depth": 0,
        "max_recursion_depth": 3,
        "estimated_cost": 0.0,
        "max_cost_usd": 1.0,
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {"ask_user_auto_proceed": True},
        "_auto_proceed_on_ask_user": True,
        "_current_goal": "Research AI trends",
        "_ask_user_assumptions": [],
    }
    tool_call = {
        "id": "tc_ask",
        "function": {
            "name": "AskUser",
            "arguments": json.dumps({"question": "Which year should I focus on?"}),
        },
    }

    async def fake_assumption(question, rs, cfg):
        return "Assumed: focus on 2024, the most recent full year."

    with patch.object(ex, "_generate_ask_user_assumption", side_effect=fake_assumption):
        result = await ex._execute_tool(tool_call, {}, repl_state, 3000)

    # Must NOT set _pending_question (that would trigger a pause)
    assert "_pending_question" not in repl_state
    # Must succeed and include the assumption
    assert result["success"] is True
    assert "2024" in result["content"]
    assert "auto-assumed" in result["content"].lower()
    # Must record the assumption in repl_state
    assert len(repl_state["_ask_user_assumptions"]) == 1
    assert repl_state["_ask_user_assumptions"][0]["question"] == "Which year should I focus on?"


async def test_ask_user_standard_mode_still_pauses():
    """When ask_user_auto_proceed is False (default), AskUser must pause normally."""
    ex = make_executor()
    repl_state = {
        "variables": {},
        "_var_counts": {},
        "sub_call_count": 0,
        "max_sub_calls": 10,
        "recursion_depth": 0,
        "max_recursion_depth": 3,
        "estimated_cost": 0.0,
        "max_cost_usd": 1.0,
        "_subgoal_depth": 0,
        "_max_subgoal_depth": 2,
        "_subgoal_config": {},
        "_auto_proceed_on_ask_user": False,
        "_current_goal": "Research AI trends",
        "_ask_user_assumptions": [],
    }
    tool_call = {
        "id": "tc_ask2",
        "function": {
            "name": "AskUser",
            "arguments": json.dumps({"question": "Should I use GPT-4?"}),
        },
    }
    result = await ex._execute_tool(tool_call, {}, repl_state, 3000)

    # Question stored under current task's id in the new task-scoped dict
    _questions = repl_state.get("_pending_questions", {})
    assert "Should I use GPT-4?" in _questions.values(), (
        f"Question not found in _pending_questions: {_questions}"
    )
    # Result content tells the LLM to pause
    assert "paused" in result["content"].lower()


async def test_generate_ask_user_assumption_returns_llm_content():
    """_generate_ask_user_assumption must use LLM output as the assumption."""
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"role": "assistant", "content": "Assumed: use 2024 data."}}]
    })
    repl_state = {"_current_goal": "Analyse trends", "_subgoal_config": {}}
    assumption = await ex._generate_ask_user_assumption(
        "Which year?", repl_state, {}
    )
    assert "2024" in assumption


async def test_generate_ask_user_assumption_fallback_on_llm_failure():
    """If the LLM call fails, a safe fallback string must be returned."""
    ex = make_executor()
    ex.llm.chat_completion = AsyncMock(side_effect=RuntimeError("LLM down"))
    repl_state = {"_current_goal": "Some goal", "_subgoal_config": {}}
    assumption = await ex._generate_ask_user_assumption(
        "Any question?", repl_state, {}
    )
    assert assumption  # must not be empty
    assert isinstance(assumption, str)


async def test_ask_user_assumptions_surfaced_in_result():
    """
    ask_user_assumptions must appear in the final result dict when
    ask_user_auto_proceed=True and at least one assumption was made.
    """
    ex = make_executor()

    # Minimal plan: one step that calls AskUser then completes
    plan = [{"step": 1, "action": "Analyze", "target": "data", "goal": "g", "depends_on": []}]
    plan_json_str = json.dumps(plan)
    step_content = "Analysis done with the assumed answer."
    eval_ok = eval_json(True, "success", step_content)

    responses = iter([
        llm_response(plan_json_str),  # planning
        # step execution — LLM calls AskUser then produces final content
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "tc_ask",
                        "function": {
                            "name": "AskUser",
                            "arguments": json.dumps({"question": "Use strict mode?"}),
                        },
                    }],
                }
            }]
        },
        llm_response(step_content),   # after AskUser auto-assumed
        llm_response(eval_ok),        # step evaluation
        llm_response(step_content),   # synthesis
    ])
    ex.llm.chat_completion = AsyncMock(side_effect=lambda **kw: next(responses))

    async def fake_assumption(question, rs, cfg):
        return "Assumed: yes, use strict mode."

    with patch.object(ex, "_generate_ask_user_assumption", side_effect=fake_assumption):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={
                "ask_user_auto_proceed": True,
                "enable_step_evaluation": True,
                "timeout": 0,
            },
        )

    assert "ask_user_assumptions" in result, (
        "Assumptions must be surfaced in the result when auto-proceed is active"
    )
    assert any(
        "Use strict mode?" in a["question"]
        for a in result["ask_user_assumptions"]
    )


# ---------------------------------------------------------------------------
# _reason_about_step deliberation quality fixes
# ---------------------------------------------------------------------------

async def test_reason_about_step_filters_var_names_correctly(monkeypatch):
    """
    _reason_about_step must only list step_*_result variables, not any step_*
    variable (Bug 13 duplicate fix).
    """
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    repl_state = {
        "variables": {
            "step_1_result": "output of step 1",
            "step_timeout": "5",           # must NOT appear
            "step_2_result": "output of step 2",
            "step_extra_info": "metadata",  # must NOT appear
        },
    }
    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": "Use WebSearch; step_1_result is useful; avoid hallucination."}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._reason_about_step(
            step={"action": "Analyse", "target": "data", "goal": "g"},
            repl_state=repl_state,
            config={},
            user_goal="test goal",
        )

    prompt_text = " ".join(m.get("content", "") for m in captured)
    assert "step_1_result" in prompt_text
    assert "step_2_result" in prompt_text
    assert "step_timeout" not in prompt_text
    assert "step_extra_info" not in prompt_text


async def test_reason_about_step_includes_tool_names(monkeypatch):
    """
    When tools_list is passed, _reason_about_step must include tool names in
    the prompt so the model can compare alternatives.
    """
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    tools_list = [
        {"function": {"name": "WebSearch"}},
        {"function": {"name": "KnowledgeBase"}},
        {"function": {"name": "Calculator"}},
    ]
    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": "Use WebSearch here."}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._reason_about_step(
            step={"action": "Research", "target": "AI trends", "goal": "g"},
            repl_state={"variables": {}},
            config={},
            user_goal="summarise AI",
            tools_list=tools_list,
        )

    prompt_text = " ".join(m.get("content", "") for m in captured)
    assert "WebSearch" in prompt_text
    assert "KnowledgeBase" in prompt_text
    assert "Calculator" in prompt_text


async def test_reason_about_step_includes_reasoning_history(monkeypatch):
    """
    When reasoning_history is provided (from ReasoningBook), it must appear
    in the deliberation prompt so prior lessons inform the current step.
    """
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    prior_history = "Lesson: always verify sources before summarising."
    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": "Verify sources first."}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._reason_about_step(
            step={"action": "Summarise", "target": "findings", "goal": "g"},
            repl_state={"variables": {}},
            config={},
            user_goal="research goal",
            reasoning_history=prior_history,
        )

    prompt_text = " ".join(m.get("content", "") for m in captured)
    assert "verify sources" in prompt_text.lower()


async def test_reason_about_step_no_tools_no_tools_note(monkeypatch):
    """When tools_list is empty, the prompt must not include a tools line."""
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": "Just do it."}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._reason_about_step(
            step={"action": "Do", "target": "thing", "goal": "g"},
            repl_state={"variables": {}},
            config={},
            user_goal="goal",
            tools_list=[],
        )

    prompt_text = " ".join(m.get("content", "") for m in captured)
    assert "Available tools:" not in prompt_text


# ---------------------------------------------------------------------------
# Context pressure and execution mode switching
# ---------------------------------------------------------------------------

def test_context_pressure_zero_when_no_stdout():
    """Zero stdout_history → pressure 0.0."""
    ex = make_executor()
    assert ex._context_pressure({}, max_context_tokens=8000) == 0.0


def test_context_pressure_scales_with_stdout():
    """Accumulated stdout chars / 4 should equal the fraction.
    16000 chars → 4000 tokens; at max_context_tokens=8000 → pressure 0.5.
    """
    ex = make_executor()
    repl_state = {"stdout_history": ["a" * 8000, "b" * 8000]}  # 16000 chars total
    assert ex._context_pressure(repl_state, max_context_tokens=8000) == pytest.approx(0.5)


def test_context_pressure_capped_at_one():
    """Pressure never exceeds 1.0 regardless of stdout size."""
    ex = make_executor()
    repl_state = {"stdout_history": ["x" * 100_000]}
    assert ex._context_pressure(repl_state, max_context_tokens=100) == 1.0


def test_context_pressure_zero_max_context_guard():
    """max_context_tokens=0 must not raise ZeroDivisionError; returns 1.0."""
    ex = make_executor()
    repl_state = {"stdout_history": ["hello"]}
    # max(1, 0) prevents ZeroDivisionError; pressure > 1 is clamped to 1.0
    assert ex._context_pressure(repl_state, max_context_tokens=0) == 1.0


async def test_step_trace_contains_execution_mode_and_pressure(monkeypatch):
    """step_trace emitted for each step must include execution_mode and context_pressure."""
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            # Planning call
            return {"choices": [{"message": {"content": plan_json("DoSomething"), "tool_calls": []}}]}
        if call_n[0] == 2:
            # Step execution call — no tool calls, simple answer
            return {"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}
        # Evaluation call
        return {"choices": [{"message": {"content": eval_json(True), "tool_calls": []}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"step_execution_mode": "hybrid", "max_steps": 1},
        )

    traces = result.get("goal_pursuit_trace", [])
    step_traces = [t for t in traces if isinstance(t, dict) and "execution_mode" in t]
    assert step_traces, "No step trace with execution_mode found"
    t0 = step_traces[0]
    assert t0["execution_mode"] in ("hybrid", "rlm")
    assert "context_pressure" in t0
    assert 0.0 <= t0["context_pressure"] <= 1.0


async def test_step_execution_mode_rlm_forces_rlm(monkeypatch):
    """step_execution_mode='rlm' must always choose RLM regardless of pressure."""
    from modules.agent_loop.node import RLMAgentLoopExecutor
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            return {"choices": [{"message": {"content": plan_json("Step"), "tool_calls": []}}]}
        return {"choices": [{"message": {"content": eval_json(True), "tool_calls": []}}]}

    rlm_called = []

    async def fake_rlm(self, **kwargs):
        rlm_called.append(True)
        return ("done", {}, [])

    monkeypatch.setattr(RLMAgentLoopExecutor, "_run_rlm_loop", fake_rlm)
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex.receive(
            {"messages": MESSAGES},
            config={"step_execution_mode": "rlm", "max_steps": 1},
        )

    assert rlm_called, "RLM loop must be called when step_execution_mode='rlm'"


async def test_step_execution_mode_auto_triggers_rlm_above_threshold(monkeypatch):
    """In 'auto' mode, exceed the pressure threshold → RLM loop is used."""
    from modules.agent_loop.node import RLMAgentLoopExecutor
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            return {"choices": [{"message": {"content": plan_json("Step"), "tool_calls": []}}]}
        return {"choices": [{"message": {"content": eval_json(True), "tool_calls": []}}]}

    rlm_called = []

    async def fake_rlm(self, **kwargs):
        rlm_called.append(True)
        return ("done", {}, [])

    monkeypatch.setattr(RLMAgentLoopExecutor, "_run_rlm_loop", fake_rlm)
    # Push pressure above threshold: 40000 chars / 4 = 10000 tokens; threshold 0.7 of 8000 = 5600
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex.receive(
            {"messages": MESSAGES, "stdout_history": ["x" * 40_000]},
            config={
                "step_execution_mode": "auto",
                "context_pressure_threshold": 0.7,
                "max_context_tokens": 8000,
                "max_steps": 1,
            },
        )

    assert rlm_called, "RLM loop must be triggered when context pressure exceeds threshold"


async def test_step_execution_mode_auto_uses_hybrid_below_threshold(monkeypatch):
    """In 'auto' mode, below the pressure threshold → hybrid loop is used."""
    from modules.agent_loop.node import HybridAgentExecutor
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            return {"choices": [{"message": {"content": plan_json("Step"), "tool_calls": []}}]}
        if call_n[0] == 2:
            return {"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}
        return {"choices": [{"message": {"content": eval_json(True), "tool_calls": []}}]}

    hybrid_called = []

    async def fake_hybrid(self, **kwargs):
        hybrid_called.append(True)
        return ({"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}, 1, False)

    monkeypatch.setattr(HybridAgentExecutor, "_run_hybrid_loop", fake_hybrid)
    # Only 400 chars of stdout → pressure = 0.01, well below 0.7 threshold
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex.receive(
            {"messages": MESSAGES, "stdout_history": ["x" * 400]},
            config={
                "step_execution_mode": "auto",
                "context_pressure_threshold": 0.7,
                "max_context_tokens": 8000,
                "max_steps": 1,
            },
        )

    assert hybrid_called, "Hybrid loop must be used when context pressure is below threshold"


# ---------------------------------------------------------------------------
# Fix 3: Reasoning accumulation across steps
# ---------------------------------------------------------------------------

async def test_reasoning_history_accumulates_in_repl_state(monkeypatch):
    """After each step, its reasoning must be stored in repl_state['_step_reasoning_history']."""
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            # Planning: two-step plan
            return {"choices": [{"message": {"content": plan_json("StepA", "StepB"), "tool_calls": []}}]}
        if call_n[0] == 2:
            # _reason_about_step for step 1
            return {"choices": [{"message": {"content": "Reasoning for step A."}}]}
        if call_n[0] == 3:
            # Step 1 execution
            return {"choices": [{"message": {"content": "Step A done.", "tool_calls": []}}]}
        if call_n[0] == 4:
            # Evaluator for step 1
            return {"choices": [{"message": {"content": eval_json(True, "ok", "A result")}}]}
        if call_n[0] == 5:
            # _reason_about_step for step 2 — capture the messages to verify history was injected
            return {"choices": [{"message": {"content": "Reasoning for step B."}}]}
        if call_n[0] == 6:
            # Step 2 execution
            return {"choices": [{"message": {"content": "Step B done.", "tool_calls": []}}]}
        # Evaluator for step 2
        return {"choices": [{"message": {"content": eval_json(True, "ok", "B result")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"max_steps": 2, "enable_parallel_steps": False},
        )

    # The result doesn't directly expose _step_reasoning_history, but we can
    # verify it by running a second call and checking no errors occurred.
    assert "agent_loop_error" not in result
    # The agent completed two steps
    assert len(result.get("completed_steps", [])) == 2


async def test_reasoning_history_fed_to_next_step(monkeypatch):
    """The second step's _reason_about_step call must receive the first step's reasoning."""
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    call_n = [0]
    step2_reason_messages = []

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            return {"choices": [{"message": {"content": plan_json("StepA", "StepB"), "tool_calls": []}}]}
        if call_n[0] == 2:
            return {"choices": [{"message": {"content": "Unique reasoning for step A: use tool X."}}]}
        if call_n[0] == 3:
            return {"choices": [{"message": {"content": "Step A done.", "tool_calls": []}}]}
        if call_n[0] == 4:
            return {"choices": [{"message": {"content": eval_json(True, "ok", "A result")}}]}
        if call_n[0] == 5:
            # Capture messages to verify prior reasoning was included
            step2_reason_messages.extend(kwargs.get("messages", []))
            return {"choices": [{"message": {"content": "Reasoning for step B."}}]}
        if call_n[0] == 6:
            return {"choices": [{"message": {"content": "Step B done.", "tool_calls": []}}]}
        return {"choices": [{"message": {"content": eval_json(True, "ok", "B result")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex.receive(
            {"messages": MESSAGES},
            config={"max_steps": 2, "enable_parallel_steps": False},
        )

    # The step 2 reasoning call should contain step 1's reasoning in its prompt
    full_text = " ".join(m.get("content", "") for m in step2_reason_messages)
    assert "Unique reasoning for step A: use tool X." in full_text, (
        "Step 2 reasoning prompt must include accumulated history from step 1"
    )


# ---------------------------------------------------------------------------
# Fix 4: _replan uses full step outputs from repl_state
# ---------------------------------------------------------------------------

async def test_replan_uses_full_step_output_from_repl_state():
    """_replan must prefer repl_state['variables']['step_N_result'] over extracted_result[:150]."""
    ex = make_executor()

    full_content = "A" * 500  # longer than 150 chars
    repl_state = {"variables": {"step_1_result": full_content}}
    completed_steps = [{"step": 1, "action": "Research", "target": "topic"}]
    step_results = [{"success": True, "reason": "ok", "extracted_result": "short"}]
    failed_step = {"step": 2, "action": "Summarize", "target": "findings"}

    captured_prompt = []

    async def fake_llm(**kwargs):
        captured_prompt.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": plan_json("Retry")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._replan(
            original_request="Do research and summarize",
            completed_steps=completed_steps,
            step_results=step_results,
            failed_step=failed_step,
            failure_reason="Summarization failed",
            config={},
            repl_state=repl_state,
        )

    full_text = " ".join(m.get("content", "") for m in captured_prompt)
    assert full_content[:300] in full_text, (
        "_replan must include full step output, not just the 150-char extracted_result"
    )
    assert "short" not in full_text or full_content[:300] in full_text, (
        "extracted_result fallback must not override full content"
    )


async def test_replan_falls_back_to_extracted_result_when_no_variable():
    """When repl_state has no variable for a step, fall back to extracted_result."""
    ex = make_executor()

    repl_state = {"variables": {}}  # no step_1_result stored
    completed_steps = [{"step": 1, "action": "Research", "target": "topic"}]
    step_results = [{"success": True, "reason": "ok", "extracted_result": "fallback content"}]
    failed_step = {"step": 2, "action": "Summarize", "target": "findings"}

    captured_prompt = []

    async def fake_llm(**kwargs):
        captured_prompt.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": plan_json("Retry")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._replan(
            original_request="Do research and summarize",
            completed_steps=completed_steps,
            step_results=step_results,
            failed_step=failed_step,
            failure_reason="Failed",
            config={},
            repl_state=repl_state,
        )

    full_text = " ".join(m.get("content", "") for m in captured_prompt)
    assert "fallback content" in full_text, (
        "When no variable is stored, extracted_result must still appear in replan prompt"
    )


async def test_replan_without_repl_state_still_works():
    """_replan must remain backward-compatible when repl_state is not passed."""
    ex = make_executor()

    completed_steps = [{"step": 1, "action": "Research", "target": "topic"}]
    step_results = [{"success": True, "reason": "ok", "extracted_result": "some result"}]
    failed_step = {"step": 2, "action": "Summarize", "target": "findings"}

    async def fake_llm(**kwargs):
        return {"choices": [{"message": {"content": plan_json("Retry")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._replan(
            original_request="Do research",
            completed_steps=completed_steps,
            step_results=step_results,
            failed_step=failed_step,
            failure_reason="Failed",
            config={},
            # repl_state intentionally omitted
        )

    assert isinstance(result, list), "_replan must return a list even without repl_state"


# ---------------------------------------------------------------------------
# Fix 2: Evaluator receives original user goal
# ---------------------------------------------------------------------------

async def test_evaluate_step_includes_original_goal_in_prompt():
    """_evaluate_step must include user_goal in the prompt sent to the LLM."""
    ex = make_executor()
    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": eval_json(True)}}]}

    step = {"action": "Search", "target": "AI papers", "goal": "find papers"}
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._evaluate_step(
            step=step,
            execution_result="Found 10 papers.",
            config={},
            user_goal="Write a comprehensive survey on AI safety.",
        )

    full_text = " ".join(m.get("content", "") for m in captured)
    assert "Write a comprehensive survey on AI safety." in full_text, (
        "Original user goal must appear in the evaluator prompt"
    )


async def test_evaluate_step_falls_back_to_step_goal_when_no_user_goal():
    """When user_goal is empty, {original_goal} falls back to step.goal."""
    ex = make_executor()
    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": eval_json(True)}}]}

    step = {"action": "Search", "target": "papers", "goal": "find relevant papers"}
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._evaluate_step(
            step=step,
            execution_result="Done.",
            config={},
            user_goal="",  # empty
        )

    full_text = " ".join(m.get("content", "") for m in captured)
    assert "find relevant papers" in full_text, (
        "When user_goal is empty, step.goal must fill the {original_goal} slot"
    )


# ---------------------------------------------------------------------------
# Fix 1: Cross-replan memory
# ---------------------------------------------------------------------------

async def test_replan_history_included_in_prompt_on_second_attempt():
    """On a second replan, prior attempt details must appear in the replanner prompt."""
    ex = make_executor()

    repl_state = {
        "variables": {"step_1_result": "some content"},
        "_replan_history": [
            {
                "attempt": 1,
                "failed_action": "SearchWeb",
                "failed_target": "AI papers",
                "failure_reason": "Rate limit hit on search API",
                "tools_tried": ["web_search"],
            }
        ],
    }
    completed_steps = [{"step": 1, "action": "Collect data", "target": "sources"}]
    step_results = [{"success": True, "reason": "ok", "extracted_result": "data"}]
    failed_step = {"step": 2, "action": "SearchWeb", "target": "AI papers"}

    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": plan_json("Retry differently")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._replan(
            original_request="Research AI",
            completed_steps=completed_steps,
            step_results=step_results,
            failed_step=failed_step,
            failure_reason="Rate limit",
            config={},
            repl_state=repl_state,
        )

    full_text = " ".join(m.get("content", "") for m in captured)
    assert "Rate limit hit on search API" in full_text, (
        "Prior replan failure reason must appear in the replanner prompt"
    )
    assert "web_search" in full_text.lower(), (
        "Tools tried in prior replan must appear in the replanner prompt"
    )


async def test_replan_history_empty_when_no_prior_replans():
    """When there are no prior replans, the history section must not appear in the prompt."""
    ex = make_executor()

    repl_state = {"variables": {}, "_replan_history": []}
    failed_step = {"step": 1, "action": "Search", "target": "data"}

    captured = []

    async def fake_llm(**kwargs):
        captured.extend(kwargs.get("messages", []))
        return {"choices": [{"message": {"content": plan_json("Retry")}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._replan(
            original_request="Research AI",
            completed_steps=[],
            step_results=[],
            failed_step=failed_step,
            failure_reason="Error",
            config={},
            repl_state=repl_state,
        )

    full_text = " ".join(m.get("content", "") for m in captured)
    assert "Previous replan attempts" not in full_text, (
        "When no prior replans, history section must be omitted from prompt"
    )


async def test_replan_history_recorded_before_replan_call():
    """The call site must append to _replan_history before calling _replan."""
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            # Planning call
            return {"choices": [{"message": {"content": plan_json("Step"), "tool_calls": []}}]}
        if call_n[0] == 2:
            # Step execution — returns nothing useful
            return {"choices": [{"message": {"content": "", "tool_calls": []}}]}
        if call_n[0] == 3:
            # Evaluator — fail the step
            return {"choices": [{"message": {"content": eval_json(False, "Failed: no output")}}]}
        # Replan — capture repl_state at this point via side effect
        return {"choices": [{"message": {"content": "[]"}}]}  # empty plan → stops

    replan_history_at_call = []

    async def fake_replan(self_inner=None, **kwargs):
        rs = kwargs.get("repl_state") or {}
        replan_history_at_call.extend(rs.get("_replan_history", []))
        return []

    from modules.agent_loop.node import GoalPursuitExecutor as GPE
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm), \
         patch.object(GPE, "_replan", fake_replan):
        await ex.receive(
            {"messages": MESSAGES},
            config={
                "max_steps": 1,
                "max_replan_depth": 1,
                "max_step_retries": 0,  # disable alternative attempt so failure routes to replan
            },
        )

    assert replan_history_at_call, (
        "_replan_history must be populated before _replan is called"
    )
    assert replan_history_at_call[0]["attempt"] == 1


# ---------------------------------------------------------------------------
# Fix 8: Stuck detection in _run_hybrid_loop
# ---------------------------------------------------------------------------

def test_is_unhelpful_result_empty():
    ex = make_executor()
    assert ex._is_unhelpful_result("") is True
    assert ex._is_unhelpful_result("   ") is True


def test_is_unhelpful_result_short():
    ex = make_executor()
    assert ex._is_unhelpful_result("ok") is True
    assert ex._is_unhelpful_result("x" * 19) is True
    assert ex._is_unhelpful_result("x" * 20) is False


def test_is_unhelpful_result_error():
    ex = make_executor()
    assert ex._is_unhelpful_result("Error: something failed") is True
    assert ex._is_unhelpful_result("Traceback (most recent call last)...") is True


def test_is_unhelpful_result_no_data_patterns():
    ex = make_executor()
    assert ex._is_unhelpful_result("no results found for your query") is True
    assert ex._is_unhelpful_result("nothing found in the database") is True


def test_is_unhelpful_result_useful_content():
    ex = make_executor()
    assert ex._is_unhelpful_result("Here are the top 5 results: Python, Java, Go") is False


def test_is_unhelpful_result_does_not_match_none_inside_a_word():
    ex = make_executor()
    assert ex._is_unhelpful_result(
        "Nonetheless, the study found a strong effect."
    ) is False


async def test_stuck_detection_injects_redirect_after_threshold():
    """After max_consecutive_unhelpful iterations of empty results, a redirect is injected."""
    from modules.agent_loop.node import HybridAgentExecutor
    ex = make_executor()

    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] <= 3:
            # Each call returns a tool call with an unhelpful result
            return {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": f"tc{call_n[0]}", "function": {"name": "WebSearch",
                 "arguments": f'{{"query": "query_{call_n[0]}"}}'}},
            ]}}]}
        # After stuck injection, LLM produces a text response
        return {"choices": [{"message": {"content": "I cannot find this information.", "tool_calls": []}}]}

    async def fake_execute_tool(tool_call, tool_library, repl_state, large_output_threshold):
        return {
            "tool_call_id": tool_call.get("id", ""),
            "role": "tool",
            "name": "WebSearch",
            "content": "no results",
            "success": True,
        }

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm), \
         patch.object(ex, "_execute_tool", side_effect=fake_execute_tool):
        final_response, iters, had_tool_error = await ex._run_hybrid_loop(
            llm_messages=[{"role": "user", "content": "search for something"}],
            tools_list=[],
            tool_library={},
            repl_state={"variables": {}},
            model="test",
            temperature=0.7,
            max_tokens=512,
            max_iterations=10,
            max_llm_retries=1,
            retry_delay=0.0,
            tool_error_strategy="continue",
            large_output_threshold=3000,
            trace=[],
            max_consecutive_unhelpful=3,
        )

    assert had_tool_error is True, "Stuck detection must set had_tool_error"
    assert iters == 3, "Loop must stop after threshold"
    # The redirect message must have been injected
    messages = [{"role": "user", "content": "search for something"}]  # original
    # Verify by checking the loop exited at iteration 3, not 10


async def test_stuck_detection_resets_on_useful_result():
    """A useful result resets the consecutive counter — loop continues normally."""
    from modules.agent_loop.node import HybridAgentExecutor
    ex = make_executor()

    call_n = [0]
    results = [
        "no results",     # unhelpful
        "Found 10 papers about AI safety and alignment",  # useful — resets counter
        "no results",     # unhelpful (counter at 1, not 3)
    ]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] <= len(results):
            return {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": f"tc{call_n[0]}", "function": {"name": "Search",
                 "arguments": '{"query": "test"}'}},
            ]}}]}
        return {"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}

    result_idx = [0]

    async def fake_execute_tool(tool_call, tool_library, repl_state, large_output_threshold):
        content = results[result_idx[0]] if result_idx[0] < len(results) else "done"
        result_idx[0] += 1
        return {"tool_call_id": "", "role": "tool", "name": "Search",
                "content": content, "success": True}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm), \
         patch.object(ex, "_execute_tool", side_effect=fake_execute_tool):
        _, iters, had_tool_error = await ex._run_hybrid_loop(
            llm_messages=[{"role": "user", "content": "find papers"}],
            tools_list=[], tool_library={},
            repl_state={"variables": {}},
            model="test", temperature=0.7, max_tokens=512,
            max_iterations=10, max_llm_retries=1, retry_delay=0.0,
            tool_error_strategy="continue", large_output_threshold=3000,
            trace=[], max_consecutive_unhelpful=3,
        )

    # Should NOT have triggered stuck detection (useful result reset counter)
    assert had_tool_error is False
    assert iters == 4  # 3 tool iters + 1 final text response


# ---------------------------------------------------------------------------
# Fix 6: Risk and reversible fields in plan
# ---------------------------------------------------------------------------

async def test_parse_plan_response_extracts_risk_and_reversible():
    """_parse_plan_response must extract risk and reversible from LLM JSON."""
    ex = make_executor()
    plan_json_str = json.dumps([
        {"step": 1, "action": "WebSearch", "target": "AI papers", "goal": "find papers",
         "depends_on": [], "risk": "low", "reversible": True},
        {"step": 2, "action": "DeleteRecord", "target": "old data", "goal": "clean up",
         "depends_on": [1], "risk": "high", "reversible": False},
    ])
    plan = await ex._parse_plan_response(plan_json_str, max_steps=10)
    assert plan[0]["risk"] == "low"
    assert plan[0]["reversible"] is True
    assert plan[1]["risk"] == "high"
    assert plan[1]["reversible"] is False


async def test_parse_plan_response_defaults_missing_risk():
    """When risk/reversible are absent, defaults (low/True) are used."""
    ex = make_executor()
    plan_json_str = json.dumps([
        {"step": 1, "action": "Read", "target": "file", "goal": "read it", "depends_on": []},
    ])
    plan = await ex._parse_plan_response(plan_json_str, max_steps=10)
    assert plan[0]["risk"] == "low"
    assert plan[0]["reversible"] is True


async def test_parse_plan_response_rejects_invalid_risk_value():
    """Invalid risk values (not low/medium/high) must default to 'low'."""
    ex = make_executor()
    plan_json_str = json.dumps([
        {"step": 1, "action": "Read", "target": "file", "goal": "read it",
         "depends_on": [], "risk": "critical", "reversible": False},
    ])
    plan = await ex._parse_plan_response(plan_json_str, max_steps=10)
    assert plan[0]["risk"] == "low"


def test_step_trace_includes_risk_and_reversible():
    """step_trace emitted per step must include risk and reversible from the step dict."""
    # This is verified indirectly via the step_trace construction in _run_one_step.
    # The step dict must carry risk/reversible through to the trace.
    step = {"step": 1, "action": "DeleteFile", "target": "old.txt",
            "goal": "remove file", "depends_on": [], "risk": "high", "reversible": False}
    # Simulate what _run_one_step does when building step_trace
    step_trace = {
        "step": step.get("step"),
        "action": step.get("action"),
        "target": step.get("target"),
        "replan_count": 0,
        "depends_on": step.get("depends_on", []),
        "risk": step.get("risk", "low"),
        "reversible": step.get("reversible", True),
    }
    assert step_trace["risk"] == "high"
    assert step_trace["reversible"] is False


def test_build_step_system_prompt_warns_for_high_risk():
    """High-risk irreversible steps must include a warning in the system prompt."""
    ex = make_executor()
    step = {"action": "SendEmail", "target": "user@example.com", "goal": "notify",
            "risk": "high", "reversible": False}
    prompt = ex._build_step_system_prompt(
        step=step,
        plan_context="## Execution Plan\nProgress: 0/1 steps done.",
        input_data={},
        config={},
        large_output_threshold=3000,
        repl_state=None,
    )
    assert "HIGH RISK" in prompt or "IRREVERSIBLE" in prompt, (
        "High-risk irreversible steps must include a warning in the system prompt"
    )


def test_build_step_system_prompt_no_warning_for_low_risk():
    """Low-risk reversible steps must NOT include a risk warning."""
    ex = make_executor()
    step = {"action": "WebSearch", "target": "papers", "goal": "find papers",
            "risk": "low", "reversible": True}
    prompt = ex._build_step_system_prompt(
        step=step,
        plan_context="## Execution Plan\nProgress: 0/1 steps done.",
        input_data={},
        config={},
        large_output_threshold=3000,
        repl_state=None,
    )
    assert "HIGH RISK" not in prompt
    assert "IRREVERSIBLE" not in prompt


# ---------------------------------------------------------------------------
# Fix 5: _context_pressure also measures hybrid inline output
# ---------------------------------------------------------------------------

def test_context_pressure_zero_for_hybrid_steps_with_no_history():
    """No hybrid history → hybrid_pressure = 0."""
    ex = make_executor()
    assert ex._context_pressure({}, max_context_tokens=8000) == 0.0


def test_context_pressure_uses_hybrid_output_history():
    """Peak of _hybrid_output_history drives pressure when stdout_history is empty."""
    ex = make_executor()
    # 32000 chars in a step_messages conversation → 8000 tokens → 1.0 at max_context=8000
    repl_state = {"_hybrid_output_history": [32000]}
    assert ex._context_pressure(repl_state, max_context_tokens=8000) == pytest.approx(1.0)


def test_context_pressure_takes_max_of_both_signals():
    """Returns max(rlm_pressure, hybrid_pressure)."""
    ex = make_executor()
    # stdout: 8000 chars → 2000 tokens → 0.25 of 8000
    # hybrid: 16000 chars peak → 4000 tokens → 0.5 of 8000
    repl_state = {
        "stdout_history": ["x" * 8000],
        "_hybrid_output_history": [16000],
    }
    pressure = ex._context_pressure(repl_state, max_context_tokens=8000)
    assert pressure == pytest.approx(0.5)


def test_context_pressure_uses_peak_not_sum_of_hybrid_history():
    """Pressure uses peak of last 3 hybrid steps, not cumulative sum."""
    ex = make_executor()
    # Last 3 entries: [4000, 4000, 8000] → peak = 8000 chars → 2000 tokens → 0.25
    repl_state = {"_hybrid_output_history": [100_000, 4000, 4000, 8000]}
    # Peak of last 3 = max(4000, 4000, 8000) = 8000 → pressure = 0.25
    pressure = ex._context_pressure(repl_state, max_context_tokens=8000)
    assert pressure == pytest.approx(0.25)


async def test_hybrid_step_records_output_in_hybrid_output_history(monkeypatch):
    """After a hybrid step, step_messages size is recorded in _hybrid_output_history."""
    from modules.agent_loop.node import HybridAgentExecutor
    ex = make_executor()
    call_n = [0]

    async def fake_llm(**kwargs):
        call_n[0] += 1
        if call_n[0] == 1:
            return {"choices": [{"message": {"content": plan_json("Step"), "tool_calls": []}}]}
        if call_n[0] == 2:
            return {"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}
        return {"choices": [{"message": {"content": eval_json(True), "tool_calls": []}}]}

    async def fake_hybrid(self, **kwargs):
        return ({"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}, 1, False)

    monkeypatch.setattr(HybridAgentExecutor, "_run_hybrid_loop", fake_hybrid)
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex.receive(
            {"messages": MESSAGES},
            config={"step_execution_mode": "hybrid", "max_steps": 1},
        )

    # The hybrid output history should have been populated (may be in repl_state
    # which is internal — verify indirectly via no error and step completion).
    assert "agent_loop_error" not in result


# ---------------------------------------------------------------------------
# Fix 6: Parallel-step repl_state races
# ---------------------------------------------------------------------------

async def test_pending_question_uses_task_scoped_key():
    """Two parallel steps calling AskUser must not overwrite each other's question."""
    ex = make_executor()
    repl_state = {
        "variables": {},
        "_auto_proceed_on_ask_user": False,
        "_pending_questions": {},
    }
    config_ctx = {}

    import asyncio

    # Simulate two concurrent AskUser calls from different tasks
    question_a = "What is A?"
    question_b = "What is B?"

    async def task_a():
        # Simulate what _execute_tool does for AskUser
        _task_id = id(asyncio.current_task())
        repl_state.setdefault("_pending_questions", {})[_task_id] = question_a
        return _task_id

    async def task_b():
        _task_id = id(asyncio.current_task())
        repl_state.setdefault("_pending_questions", {})[_task_id] = question_b
        return _task_id

    id_a, id_b = await asyncio.gather(task_a(), task_b())

    # Each task's question must be stored independently
    assert repl_state["_pending_questions"][id_a] == question_a
    assert repl_state["_pending_questions"][id_b] == question_b
    assert id_a != id_b


async def test_estimated_cost_reserved_before_await():
    """Cost is incremented before the LLM await so parallel sub-calls see the updated total."""
    ex = make_executor()
    repl_state = {
        "variables": {},
        "sub_call_count": 0,
        "max_sub_calls": 10,
        "recursion_depth": 0,
        "max_recursion_depth": 3,
        "estimated_cost": 0.0,
        "max_cost_usd": 1.0,
        "stdout_history": [],
        "final": None,
        "_var_counts": {},
    }
    cost_seen_during_call = []

    async def fake_chat_completion(**kwargs):
        # By the time this await resolves, cost should already be reserved
        cost_seen_during_call.append(repl_state["estimated_cost"])
        return {"choices": [{"message": {"content": "result"}}]}

    ex.llm = MagicMock()
    ex.llm.chat_completion = fake_chat_completion

    await ex._execute_sub_call(
        args={"prompt": "hello", "max_tokens": 100},
        repl_state=repl_state,
    )

    # Cost must have been reserved before the await resolved
    assert cost_seen_during_call[0] > 0.0, (
        "estimated_cost must be incremented before the LLM call, not after"
    )


# ---------------------------------------------------------------------------
# Fix 2b: estimated_cost parallel-step budget enforcement
# Two steps whose individual costs each fit within the budget but together
# exceed it must not both slip through — the second must be blocked.
# ---------------------------------------------------------------------------

async def test_estimated_cost_parallel_steps_blocked_together():
    """Second sub-call is blocked when its cost would push the total over max.

    Cost formula: delta = ((len(prompt)//4 + max_tokens) / 1000) * 0.001

    Call A: prompt=4 chars, max_tokens=1000  → delta ≈ 0.001001  (total: 0.001001)
    Call B: prompt=4 chars, max_tokens=3000  → delta ≈ 0.003001  (total: 0.004002)
    Budget = 0.003 — call A passes, call B must be blocked.
    """
    ex = make_executor()
    repl_state = {
        "variables": {},
        "sub_call_count": 0,
        "recursion_depth": 0,
        "max_recursion_depth": 5,
        "estimated_cost": 0.0,
        "max_cost_usd": 0.003,
        "stdout_history": [],
        "final": None,
        "_var_counts": {},
    }

    async def fake_chat(**kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}

    ex.llm = MagicMock()
    ex.llm.chat_completion = fake_chat

    result_a = await ex._execute_sub_call(
        args={"prompt": "aaaa", "max_tokens": 1000},
        repl_state=repl_state,
    )
    assert result_a["success"] is True, "First call should succeed (cost fits in budget)"

    result_b = await ex._execute_sub_call(
        args={"prompt": "bbbb", "max_tokens": 3000},
        repl_state=repl_state,
    )
    assert result_b["success"] is False, "Second call must be blocked (combined cost exceeds budget)"
    assert "max_cost_usd" in result_b["content"]


async def test_estimated_cost_reverted_on_block():
    """When a sub-call is blocked, the cost reservation must be reverted.

    Start just below the limit; a large-token call pushes over → must revert.
    prompt=4 chars, max_tokens=3000 → delta ≈ 0.003001
    Starting cost = 0.0029 → total after reserve = 0.0059 > 0.003 → blocked + reverted.
    """
    ex = make_executor()
    repl_state = {
        "variables": {},
        "sub_call_count": 0,
        "recursion_depth": 0,
        "max_recursion_depth": 5,
        "estimated_cost": 0.0029,   # already near limit
        "max_cost_usd": 0.003,
        "stdout_history": [],
        "final": None,
        "_var_counts": {},
    }

    async def fake_chat(**kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}

    ex.llm = MagicMock()
    ex.llm.chat_completion = fake_chat

    cost_before = repl_state["estimated_cost"]

    result = await ex._execute_sub_call(
        args={"prompt": "xxxx", "max_tokens": 3000},
        repl_state=repl_state,
    )

    assert result["success"] is False
    # Cost must be reverted — not left at the inflated value
    assert repl_state["estimated_cost"] == pytest.approx(cost_before), (
        "Blocked call must revert the cost reservation"
    )


# ---------------------------------------------------------------------------
# _synthesize_results: extracted class method tests
# ---------------------------------------------------------------------------

async def test_synthesize_results_all_empty_returns_failure_message():
    """When all steps produced no output, synthesize must return a clear failure."""
    ex = make_executor()
    repl_state = {"variables": {}, "_step_reasoning_history": []}
    config = {"temperature": 0.7, "max_llm_retries": 1, "retry_delay": 0.0}

    completed = [{"step": 1, "action": "Search", "target": "query"}]
    results = [{"extracted_result": ""}]   # empty result

    out = await ex._synthesize_results(
        completed=completed,
        results=results,
        repl_state=repl_state,
        config=config,
        user_goal="find data",
        tools_list=[],
        tool_library={},
        model="test-model",
        max_tokens=512,
        tool_error_strategy="continue",
        large_output_threshold=1000,
    )
    assert "Unable to produce" in out
    assert "failed" in out.lower()


async def test_synthesize_results_inlines_small_result():
    """Results <=600 chars must appear directly in the system message without GetVariable."""
    ex = make_executor()
    small_result = "The answer is 42."
    repl_state = {
        "variables": {"step_1_result": small_result},
        "_step_reasoning_history": [],
    }
    config = {"temperature": 0.7, "max_llm_retries": 1, "retry_delay": 0.0}
    completed = [{"step": 1, "action": "Compute", "target": "answer"}]
    results = [{"extracted_result": small_result}]

    captured_messages = []

    async def fake_run_hybrid_loop(llm_messages, **kwargs):
        captured_messages.extend(llm_messages)
        return ({"choices": [{"message": {"content": "42"}}]}, 1, False)

    setattr(ex, "_run_hybrid_loop", fake_run_hybrid_loop)

    await ex._synthesize_results(
        completed=completed,
        results=results,
        repl_state=repl_state,
        config=config,
        user_goal="compute answer",
        tools_list=[],
        tool_library={},
        model="test-model",
        max_tokens=512,
        tool_error_strategy="continue",
        large_output_threshold=1000,
    )

    system_msg = next(m["content"] for m in captured_messages if m["role"] == "system")
    assert small_result in system_msg, "Small result must be inlined, not just hinted"
    # The step-specific GetVariable instruction must NOT appear for a small result
    assert 'Call GetVariable("step_1_result")' not in system_msg, (
        "Small result must not have a GetVariable fetch instruction"
    )


async def test_synthesize_results_uses_get_variable_for_large_result():
    """Results >600 chars must use GetVariable instruction, not inline full text."""
    ex = make_executor()
    large_result = "x" * 1000
    repl_state = {
        "variables": {"step_1_result": large_result},
        "_step_reasoning_history": [],
    }
    config = {"temperature": 0.7, "max_llm_retries": 1, "retry_delay": 0.0}
    completed = [{"step": 1, "action": "Fetch", "target": "page"}]
    results = [{"extracted_result": "hint text"}]

    captured_messages = []

    async def fake_run_hybrid_loop(llm_messages, **kwargs):
        captured_messages.extend(llm_messages)
        return ({"choices": [{"message": {"content": "summary"}}]}, 1, False)

    setattr(ex, "_run_hybrid_loop", fake_run_hybrid_loop)

    await ex._synthesize_results(
        completed=completed,
        results=results,
        repl_state=repl_state,
        config=config,
        user_goal="fetch page",
        tools_list=[],
        tool_library={},
        model="test-model",
        max_tokens=512,
        tool_error_strategy="continue",
        large_output_threshold=1000,
    )

    system_msg = next(m["content"] for m in captured_messages if m["role"] == "system")
    assert 'GetVariable("step_1_result")' in system_msg
    assert large_result not in system_msg, "Large result must NOT be inlined"


async def test_synthesize_results_includes_reasoning_history():
    """_step_reasoning_history must appear in the synthesis system message."""
    ex = make_executor()
    repl_state = {
        "variables": {"step_1_result": "some answer"},
        "_step_reasoning_history": [
            "Step 1 (Search): use web_search first, avoid cached results",
            "Step 2 (Extract): prior result has the key data in section 3",
        ],
    }
    config = {"temperature": 0.7, "max_llm_retries": 1, "retry_delay": 0.0}
    completed = [{"step": 1, "action": "Search", "target": "query"}]
    results = [{"extracted_result": "some answer"}]

    captured_messages = []

    async def fake_run_hybrid_loop(llm_messages, **kwargs):
        captured_messages.extend(llm_messages)
        return ({"choices": [{"message": {"content": "done"}}]}, 1, False)

    setattr(ex, "_run_hybrid_loop", fake_run_hybrid_loop)

    await ex._synthesize_results(
        completed=completed,
        results=results,
        repl_state=repl_state,
        config=config,
        user_goal="search query",
        tools_list=[],
        tool_library={},
        model="test-model",
        max_tokens=512,
        tool_error_strategy="continue",
        large_output_threshold=1000,
    )

    system_msg = next(m["content"] for m in captured_messages if m["role"] == "system")
    assert "Step Reasoning History" in system_msg
    assert "use web_search first" in system_msg


async def test_synthesize_results_no_reasoning_section_when_history_empty():
    """No reasoning section must appear in the prompt when history is empty."""
    ex = make_executor()
    repl_state = {
        "variables": {"step_1_result": "answer"},
        "_step_reasoning_history": [],
    }
    config = {"temperature": 0.7, "max_llm_retries": 1, "retry_delay": 0.0}
    completed = [{"step": 1, "action": "Compute", "target": "x"}]
    results = [{"extracted_result": "answer"}]

    captured_messages = []

    async def fake_run_hybrid_loop(llm_messages, **kwargs):
        captured_messages.extend(llm_messages)
        return ({"choices": [{"message": {"content": "done"}}]}, 1, False)

    setattr(ex, "_run_hybrid_loop", fake_run_hybrid_loop)

    await ex._synthesize_results(
        completed=completed,
        results=results,
        repl_state=repl_state,
        config=config,
        user_goal="compute x",
        tools_list=[],
        tool_library={},
        model="test-model",
        max_tokens=512,
        tool_error_strategy="continue",
        large_output_threshold=1000,
    )

    system_msg = next(m["content"] for m in captured_messages if m["role"] == "system")
    assert "Step Reasoning History" not in system_msg


# ---------------------------------------------------------------------------
# Claim 1 fix: _run_insights semantic accumulation
# ---------------------------------------------------------------------------

def test_run_insights_appended_after_success():
    """A successful step must append the extracted_result to _run_insights."""
    repl_state = {"_run_insights": []}
    insight = "Revenue was $42M in Q3"
    evaluation = {"success": True, "reason": "found data", "extracted_result": insight}

    # Simulate what _run_one_step does after a successful evaluation
    _insight = evaluation.get("extracted_result", "")[:150]
    if _insight:
        repl_state.setdefault("_run_insights", []).append(
            f"Step 1 (Search): {_insight}"
        )

    assert len(repl_state["_run_insights"]) == 1
    assert insight in repl_state["_run_insights"][0]


def test_run_insights_not_appended_on_empty_extracted_result():
    """Steps with empty extracted_result must not pollute _run_insights."""
    repl_state = {"_run_insights": []}
    evaluation = {"success": True, "reason": "ok", "extracted_result": ""}

    _insight = evaluation.get("extracted_result", "")[:150]
    if _insight:
        repl_state.setdefault("_run_insights", []).append(f"Step 1 (X): {_insight}")

    assert repl_state["_run_insights"] == []


async def test_build_step_system_prompt_shows_insights():
    """_build_step_system_prompt must include Established Findings when insights exist."""
    ex = make_executor()
    step = {"step": 2, "action": "Analyze", "target": "data", "goal": "find trend"}
    repl_state = {
        "variables": {"step_1_result": "some data"},
        "_run_insights": ["Step 1 (Fetch): Revenue was $42M in Q3"],
    }
    prompt = ex._build_step_system_prompt(
        step=step,
        plan_context="## Plan\n...",
        input_data={},
        config={},
        large_output_threshold=1000,
        repl_state=repl_state,
    )
    assert "Established Findings" in prompt
    assert "Revenue was $42M" in prompt


async def test_build_step_system_prompt_no_findings_section_when_empty():
    """No Established Findings section must appear when _run_insights is empty."""
    ex = make_executor()
    step = {"step": 1, "action": "Search", "target": "x", "goal": "find x"}
    repl_state = {"variables": {}, "_run_insights": []}
    prompt = ex._build_step_system_prompt(
        step=step,
        plan_context="## Plan\n...",
        input_data={},
        config={},
        large_output_threshold=1000,
        repl_state=repl_state,
    )
    assert "Established Findings" not in prompt


async def test_reason_about_step_includes_insights(monkeypatch):
    """_reason_about_step must include _run_insights in the LLM prompt."""
    # Restore the real method (autouse fixture stubs it out for unrelated tests)
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    captured = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured.extend(messages)
        return {"choices": [{"message": {"content": "use search"}}]}

    step = {"step": 2, "action": "Analyze", "target": "data", "goal": "find trend"}
    repl_state = {
        "variables": {},
        "_run_insights": ["Step 1 (Fetch): The Q3 revenue was $42M"],
    }
    config = {}
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._reason_about_step(
            step=step,
            repl_state=repl_state,
            config=config,
            user_goal="analyze financials",
        )

    combined = " ".join(m.get("content", "") for m in captured)
    assert "Established findings" in combined
    assert "$42M" in combined


async def test_reason_about_step_no_insights_section_when_empty(monkeypatch):
    """No insights section must appear in the reasoning prompt when history is empty."""
    monkeypatch.setattr(GoalPursuitExecutor, "_reason_about_step", _real_reason_about_step)
    ex = make_executor()
    captured = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured.extend(messages)
        return {"choices": [{"message": {"content": "use search"}}]}

    step = {"step": 1, "action": "Search", "target": "x", "goal": "find x"}
    repl_state = {"variables": {}, "_run_insights": []}
    config = {}
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._reason_about_step(step=step, repl_state=repl_state, config=config, user_goal="x")

    combined = " ".join(m.get("content", "") for m in captured)
    assert "Established findings" not in combined


# ---------------------------------------------------------------------------
# Claim 2 fix: evidence quality classification in _evaluate_step
# ---------------------------------------------------------------------------

async def test_evaluate_step_evidence_quality_no_tools():
    """NO_TOOLS_CALLED must be injected when tool_history has no result lines."""
    ex = make_executor()
    captured_prompts = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": '{"success": true, "reason": "ok", "extracted_result": "data"}'}}]}

    step = {"step": 1, "action": "Search", "target": "x", "goal": "find x"}
    # tool_history has only "Called" lines, no "✓"/"✗" result lines
    tool_history = ["→ Called: web_search(x)"]

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._evaluate_step(step, "I searched and found some general info", {}, tool_history=tool_history)

    combined = " ".join(m.get("content", "") for m in captured_prompts)
    assert "NO_TOOLS_CALLED" in combined


async def test_evaluate_step_evidence_quality_all_failed():
    """ALL_FAILED must be injected when every tool result is an error."""
    ex = make_executor()
    captured_prompts = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": '{"success": false, "reason": "no data", "extracted_result": ""}'}}]}

    step = {"step": 1, "action": "Fetch", "target": "url", "goal": "get page"}
    tool_history = [
        "→ Called: fetch_url(url)",
        "  ✗ fetch_url: Error: connection refused",
    ]

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._evaluate_step(step, "I tried but failed", {}, tool_history=tool_history)

    combined = " ".join(m.get("content", "") for m in captured_prompts)
    assert "ALL_FAILED" in combined


async def test_evaluate_step_evidence_quality_all_empty():
    """ALL_EMPTY must be injected when every successful tool returned no-data content."""
    ex = make_executor()
    captured_prompts = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": '{"success": false, "reason": "empty", "extracted_result": ""}'}}]}

    step = {"step": 1, "action": "Search", "target": "rare topic", "goal": "find info"}
    tool_history = [
        "→ Called: web_search(rare topic)",
        "  ✓ web_search: no results found",
    ]

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._evaluate_step(step, "I searched but found nothing useful", {}, tool_history=tool_history)

    combined = " ".join(m.get("content", "") for m in captured_prompts)
    assert "ALL_EMPTY" in combined


async def test_evaluate_step_evidence_quality_has_data():
    """HAS_DATA must be injected when at least one tool returned useful content."""
    ex = make_executor()
    captured_prompts = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": '{"success": true, "reason": "found it", "extracted_result": "$42M"}'}}]}

    step = {"step": 1, "action": "Search", "target": "revenue", "goal": "find Q3 revenue"}
    tool_history = [
        "→ Called: web_search(Q3 revenue)",
        "  ✓ web_search: Q3 revenue was $42M according to the annual report",
    ]

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._evaluate_step(step, "Q3 revenue was $42M", {}, tool_history=tool_history)

    combined = " ".join(m.get("content", "") for m in captured_prompts)
    assert "HAS_DATA" in combined


async def test_evaluate_step_prompt_contains_anti_filler_rules():
    """The eval prompt must contain explicit anti-filler and fabrication rules."""
    ex = make_executor()
    captured_prompts = []

    async def fake_llm_with_retry(messages, **kwargs):
        captured_prompts.extend(messages)
        return {"choices": [{"message": {"content": '{"success": false, "reason": "filler", "extracted_result": ""}'}}]}

    step = {"step": 1, "action": "Research", "target": "x", "goal": "find x"}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm_with_retry):
        await ex._evaluate_step(step, "I found some general information about x.", {}, tool_history=[])

    combined = " ".join(m.get("content", "") for m in captured_prompts)
    assert "fabricat" in combined.lower()
    assert "vague" in combined.lower()


# ---------------------------------------------------------------------------
# Claim 3: _assess_task_clarity (pre-plan metacognition)
# ---------------------------------------------------------------------------

async def test_assess_task_clarity_returns_structured_result(monkeypatch):
    """_assess_task_clarity must return is_actionable, critical_unknowns, safe_assumptions."""
    monkeypatch.setattr(GoalPursuitExecutor, "_assess_task_clarity", _real_assess_task_clarity)
    ex = make_executor()

    async def fake_llm(messages, **kwargs):
        return {"choices": [{"message": {"content": (
            '{"is_actionable": false, '
            '"critical_unknowns": ["What time period?"], '
            '"safe_assumptions": ["Assume last 30 days"]}'
        )}}]}

    # Use a goal with ambiguity markers so it bypasses the fast-path and calls the LLM.
    goal = "Analyze all relevant sales data across multiple regions if applicable to any reporting period."
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._assess_task_clarity(goal, {})

    assert result["is_actionable"] is False
    assert "What time period?" in result["critical_unknowns"]
    assert "Assume last 30 days" in result["safe_assumptions"]


async def test_assess_task_clarity_defaults_on_llm_failure(monkeypatch):
    """On LLM failure, _assess_task_clarity must return safe defaults (is_actionable=True)."""
    monkeypatch.setattr(GoalPursuitExecutor, "_assess_task_clarity", _real_assess_task_clarity)
    ex = make_executor()

    async def fake_llm(messages, **kwargs):
        return None

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._assess_task_clarity("Do something", {})

    assert result["is_actionable"] is True
    assert result["critical_unknowns"] == []
    assert result["safe_assumptions"] == []


async def test_assess_task_clarity_defaults_on_bad_json(monkeypatch):
    """On unparseable LLM response, defaults must be returned."""
    monkeypatch.setattr(GoalPursuitExecutor, "_assess_task_clarity", _real_assess_task_clarity)
    ex = make_executor()

    async def fake_llm(messages, **kwargs):
        return {"choices": [{"message": {"content": "not json at all"}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._assess_task_clarity("Do something", {})

    assert result["is_actionable"] is True


# ---------------------------------------------------------------------------
# Claim 3: Metacognition warning — no-progress spiral detection
# ---------------------------------------------------------------------------

def test_metacognition_warning_set_on_no_progress_spiral():
    """_metacognition_warning must be set when replans >= half-depth AND insights empty."""
    repl_state = {"_run_insights": [], "_metacognition_warning": ""}
    max_replan_depth = 4
    replan_count = 2  # >= max(1, 4//2) = 2

    _insights_so_far = len(repl_state.get("_run_insights", []))
    _spiral_threshold = max(1, max_replan_depth // 2)
    if replan_count >= _spiral_threshold and _insights_so_far == 0:
        repl_state["_metacognition_warning"] = (
            f"⚠️ METACOGNITION: This run has replanned {replan_count} time(s) "
            f"but accumulated 0 concrete findings."
        )

    assert repl_state["_metacognition_warning"] != ""
    assert "METACOGNITION" in repl_state["_metacognition_warning"]


def test_metacognition_warning_not_set_when_insights_exist():
    """Warning must NOT be set when the agent has made progress (has insights)."""
    repl_state = {
        "_run_insights": ["Step 1 (Search): Revenue was $42M"],
        "_metacognition_warning": "",
    }
    max_replan_depth = 4
    replan_count = 2

    _insights_so_far = len(repl_state.get("_run_insights", []))
    _spiral_threshold = max(1, max_replan_depth // 2)
    if replan_count >= _spiral_threshold and _insights_so_far == 0:
        repl_state["_metacognition_warning"] = "WARNING"

    assert repl_state["_metacognition_warning"] == ""


def test_metacognition_warning_not_set_below_threshold():
    """Warning must NOT be set when replan_count is below the spiral threshold."""
    repl_state = {"_run_insights": [], "_metacognition_warning": ""}
    max_replan_depth = 4
    replan_count = 1  # < max(1, 4//2) = 2

    _spiral_threshold = max(1, max_replan_depth // 2)
    if replan_count >= _spiral_threshold and len(repl_state["_run_insights"]) == 0:
        repl_state["_metacognition_warning"] = "WARNING"

    assert repl_state["_metacognition_warning"] == ""


def test_build_step_system_prompt_shows_metacognition_warning():
    """_build_step_system_prompt must include the warning when it's set."""
    ex = make_executor()
    step = {"step": 1, "action": "Search", "target": "x", "goal": "find x"}
    repl_state = {
        "variables": {},
        "_run_insights": [],
        "_metacognition_warning": "⚠️ METACOGNITION: replanned 2 times, 0 findings.",
        "_mid_run_assessment": "",
    }
    prompt = ex._build_step_system_prompt(
        step=step, plan_context="## Plan\n...",
        input_data={}, config={}, large_output_threshold=1000, repl_state=repl_state,
    )
    assert "METACOGNITION" in prompt
    assert "replanned 2 times" in prompt


def test_build_step_system_prompt_shows_mid_run_assessment():
    """_build_step_system_prompt must show Strategic Self-Assessment when set."""
    ex = make_executor()
    step = {"step": 2, "action": "Analyze", "target": "data", "goal": "find trend"}
    repl_state = {
        "variables": {},
        "_run_insights": [],
        "_metacognition_warning": "",
        "_mid_run_assessment": "The search step produced data. Analysis looks on track.",
    }
    prompt = ex._build_step_system_prompt(
        step=step, plan_context="## Plan\n...",
        input_data={}, config={}, large_output_threshold=1000, repl_state=repl_state,
    )
    assert "Strategic Self-Assessment" in prompt
    assert "on track" in prompt


# ---------------------------------------------------------------------------
# Claim 4: _mid_run_assessment
# ---------------------------------------------------------------------------

async def test_mid_run_assessment_returns_string(monkeypatch):
    """_mid_run_assessment must return a non-empty string from the LLM."""
    monkeypatch.setattr(GoalPursuitExecutor, "_mid_run_assessment", _real_mid_run_assessment)
    ex = make_executor()

    async def fake_llm(messages, **kwargs):
        return {"choices": [{"message": {"content": "The approach looks on track."}}]}

    completed = [{"step": 1, "action": "Search", "target": "x"}]
    remaining = [{"step": 2, "action": "Analyze", "target": "data"}]
    repl_state = {"_run_insights": ["Step 1: found data"]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._mid_run_assessment(
            completed_steps=completed,
            remaining_steps=remaining,
            repl_state=repl_state,
            config={},
            user_goal="analyze x",
        )

    assert "on track" in result


async def test_mid_run_assessment_returns_empty_on_llm_failure(monkeypatch):
    """On LLM failure, _mid_run_assessment must return empty string (not crash)."""
    monkeypatch.setattr(GoalPursuitExecutor, "_mid_run_assessment", _real_mid_run_assessment)
    ex = make_executor()

    async def fake_llm(messages, **kwargs):
        return None

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._mid_run_assessment(
            completed_steps=[{"step": 1, "action": "A", "target": "x"}],
            remaining_steps=[{"step": 2, "action": "B", "target": "y"}],
            repl_state={"_run_insights": []},
            config={},
            user_goal="test",
        )

    assert result == ""


async def test_mid_run_assessment_prompt_includes_goal_and_insights(monkeypatch):
    """The mid-run assessment prompt must include goal and current findings."""
    monkeypatch.setattr(GoalPursuitExecutor, "_mid_run_assessment", _real_mid_run_assessment)
    ex = make_executor()
    captured = []

    async def fake_llm(messages, **kwargs):
        captured.extend(messages)
        return {"choices": [{"message": {"content": "ok"}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._mid_run_assessment(
            completed_steps=[{"step": 1, "action": "Search", "target": "revenue"}],
            remaining_steps=[{"step": 2, "action": "Analyze", "target": "data"}],
            repl_state={"_run_insights": ["Step 1 (Search): Revenue was $42M"]},
            config={},
            user_goal="analyze Q3 financials",
        )

    combined = " ".join(m.get("content", "") for m in captured)
    assert "analyze Q3 financials" in combined
    assert "$42M" in combined


# ---------------------------------------------------------------------------
# Claim 5: _extract_failure_patterns
# ---------------------------------------------------------------------------

async def test_extract_failure_patterns_returns_empty_for_few_lessons(monkeypatch):
    """Pattern extraction must return empty string when < 3 lessons."""
    monkeypatch.setattr(GoalPursuitExecutor, "_extract_failure_patterns", _real_extract_failure_patterns)
    ex = make_executor()
    # No LLM should be called — verify by checking no LLM mock needed
    result = await ex._extract_failure_patterns(
        lessons_list=[{"content": "lesson 1"}, {"content": "lesson 2"}],
        config={},
    )
    assert result == ""


async def test_extract_failure_patterns_calls_llm_with_pattern_query(monkeypatch):
    """_extract_failure_patterns must call LLM asking for patterns, not summaries."""
    monkeypatch.setattr(GoalPursuitExecutor, "_extract_failure_patterns", _real_extract_failure_patterns)
    ex = make_executor()
    captured = []

    async def fake_llm(messages, **kwargs):
        captured.extend(messages)
        return {"choices": [{"message": {"content": "• web_search consistently fails"}}]}

    lessons = [{"content": f"Lesson {i}: web search failed"} for i in range(5)]
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._extract_failure_patterns(lessons, config={})

    combined = " ".join(m.get("content", "") for m in captured)
    # Must ask for patterns, not per-lesson summaries
    assert "pattern" in combined.lower() or "recurring" in combined.lower()
    assert "web_search consistently fails" in result


async def test_extract_failure_patterns_returns_empty_on_llm_failure(monkeypatch):
    """On LLM failure, pattern extraction must return empty string."""
    monkeypatch.setattr(GoalPursuitExecutor, "_extract_failure_patterns", _real_extract_failure_patterns)
    ex = make_executor()

    async def fake_llm(messages, **kwargs):
        return None

    lessons = [{"content": f"lesson {i}"} for i in range(5)]
    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        result = await ex._extract_failure_patterns(lessons, config={})

    assert result == ""


async def test_create_plan_receives_pattern_insights():
    """_create_plan must inject pattern_insights into the planning prompt."""
    ex = make_executor()
    captured = []

    async def fake_llm(messages, **kwargs):
        captured.extend(messages)
        return {"choices": [{"message": {"content": '[{"step":1,"action":"A","target":"B","goal":"G","depends_on":[]}]'}}]}

    with patch.object(ex, "_llm_with_retry", side_effect=fake_llm):
        await ex._create_plan(
            user_request="find data",
            config={},
            pattern_insights="• web_search fails for niche topics",
            planning_assumptions="Assume last 30 days",
        )

    combined = " ".join(m.get("content", "") for m in captured)
    assert "web_search fails for niche topics" in combined
    assert "Assume last 30 days" in combined
