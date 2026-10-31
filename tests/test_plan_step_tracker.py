"""
Tests for modules/planner/node.py — PlanStepTracker
"""
import pytest
from modules.planner.node import PlanStepTracker, get_executor_class


@pytest.fixture
def executor():
    return PlanStepTracker()



@pytest.mark.asyncio
async def test_tracks_single_step_completion(executor):
    """Should increment current_step and mark step as completed."""
    data = {
        "plan": [{"step": 1, "action": "Search", "target": "docs"}],
        "current_step": 0
    }
    
    result = await executor.receive(data)
    
    assert result["current_step"] == 1
    assert result["step_completed"] == {"step": 1, "action": "Search", "target": "docs"}
    assert result["plan_complete"] is True


@pytest.mark.asyncio
async def test_tracks_multi_step_progress(executor):
    """Should track progress through multiple steps."""
    data = {
        "plan": [
            {"step": 1, "action": "Search", "target": "docs"},
            {"step": 2, "action": "Analyze", "target": "results"},
            {"step": 3, "action": "Report", "target": "findings"}
        ],
        "current_step": 0
    }
    
    # Step 1
    result = await executor.receive(data)
    assert result["current_step"] == 1
    assert result["step_completed"]["action"] == "Search"
    assert result["plan_complete"] is False
    
    # Step 2
    result = await executor.receive(result)
    assert result["current_step"] == 2
    assert result["step_completed"]["action"] == "Analyze"
    assert result["plan_complete"] is False
    
    # Step 3
    result = await executor.receive(result)
    assert result["current_step"] == 3
    assert result["step_completed"]["action"] == "Report"
    assert result["plan_complete"] is True


@pytest.mark.asyncio
async def test_resumes_from_middle_of_plan(executor):
    """Should work correctly when starting from non-zero current_step."""
    data = {
        "plan": [
            {"step": 1, "action": "A"},
            {"step": 2, "action": "B"},
            {"step": 3, "action": "C"}
        ],
        "current_step": 1  # Ready to complete step 2
        ,"completed_steps": [0]
    }
    
    result = await executor.receive(data)
    
    assert result["current_step"] == 2
    assert result["step_completed"]["action"] == "B"
    assert result["plan_complete"] is False


@pytest.mark.asyncio
async def test_empty_plan_passthrough(executor):
    """Should return data unchanged when plan is empty."""
    data = {"messages": [{"role": "user", "content": "Hi"}]}
    
    result = await executor.receive(data)
    
    assert result == data


@pytest.mark.asyncio
async def test_no_current_step_defaults_to_zero(executor):
    """Should default current_step to 0 if not provided."""
    data = {
        "plan": [{"step": 1, "action": "Test"}]
    }
    
    result = await executor.receive(data)
    
    assert result["current_step"] == 1
    assert result["step_completed"]["action"] == "Test"


@pytest.mark.asyncio
async def test_non_dict_input_passthrough(executor):
    """Should return non-dict input unchanged."""
    result = await executor.receive("not a dict")
    assert result == "not a dict"


@pytest.mark.asyncio
async def test_preserves_other_data_fields(executor):
    """Should preserve all other fields in input data."""
    data = {
        "plan": [{"step": 1, "action": "Test"}],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Hi"}],
        "session_id": "abc123",
        "custom_field": "value"
    }
    
    result = await executor.receive(data)
    
    assert result["messages"] == [{"role": "user", "content": "Hi"}]
    assert result["session_id"] == "abc123"
    assert result["custom_field"] == "value"


@pytest.mark.asyncio
async def test_send_returns_data_unchanged(executor):
    """send() should return data unchanged."""
    data = {"test": "value"}
    result = await executor.send(data)
    assert result is data


@pytest.mark.asyncio
async def test_get_executor_class_returns_tracker():
    """get_executor_class('plan_step_tracker') should return PlanStepTracker."""
    cls = await get_executor_class("plan_step_tracker")
    assert cls is PlanStepTracker


@pytest.mark.asyncio
async def test_plan_complete_only_when_all_steps_done(executor):
    """plan_complete should only be True when all steps are finished."""
    plan = [
        {"step": 1, "action": "A"},
        {"step": 2, "action": "B"}
    ]
    
    # After first step
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["plan_complete"] is False
    
    # After second step
    result = await executor.receive(result)
    assert result["plan_complete"] is True
    
    # After trying to go beyond
    result = await executor.receive(result)
    # Should stay complete and not crash
    assert result["plan_complete"] is True


@pytest.mark.asyncio
async def test_step_completed_field_set_correctly(executor):
    """step_completed should always reflect the step that was just completed."""
    plan = [
        {"step": 1, "action": "Search", "target": "docs"},
        {"step": 2, "action": "Analyze", "target": "data"}
    ]
    
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["step_completed"] == plan[0]
    
    result = await executor.receive(result)
    assert result["step_completed"] == plan[1]


# ============ NEW TESTS FOR DEPENDENCY-AWARE ORDERING ============

@pytest.mark.asyncio
async def test_dependency_aware_ordering_simple(executor):
    """Should respect simple dependencies (step 2 depends on step 1)."""
    plan = [
        {"step": 1, "action": "Fetch data", "target": "API"},
        {"step": 2, "action": "Process data", "target": "results", "depends_on": 1}
    ]
    
    # Start at step 0 (step 1)
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["step_completed"]["step"] == 1
    assert result["current_step"] == 1  # Should go to step 2 (index 1)
    assert result["next_step"]["step"] == 2


@pytest.mark.asyncio
async def test_dependency_aware_ordering_out_of_order(executor):
    """Should execute step 3 before step 2 if step 2 depends on step 3."""
    plan = [
        {"step": 1, "action": "A", "target": ""},
        {"step": 2, "action": "B", "target": "", "depends_on": 3},  # Depends on step 3
        {"step": 3, "action": "C", "target": ""}
    ]
    
    # After completing step 1, should go to step 3 (not step 2)
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["step_completed"]["step"] == 1
    assert result["current_step"] == 2  # Step 3 (index 2)
    assert result["next_step"]["step"] == 3


@pytest.mark.asyncio
async def test_circular_dependency_detection(executor):
    """Should detect and report circular dependencies."""
    plan = [
        {"step": 1, "action": "A", "target": "", "depends_on": 2},
        {"step": 2, "action": "B", "target": "", "depends_on": 1}
    ]
    
    result = await executor.receive({"plan": plan, "current_step": 0})
    
    assert "dependency_error" in result
    assert "Circular dependency" in result["dependency_error"]
    assert result["plan_complete"] is True  # Should stop execution


@pytest.mark.asyncio
async def test_multiple_dependencies(executor):
    """Should handle steps with multiple dependencies."""
    plan = [
        {"step": 1, "action": "Prepare", "target": ""},
        {"step": 2, "action": "Setup", "target": ""},
        {"step": 3, "action": "Execute", "target": "", "depends_on": [1, 2]}
    ]
    
    # Complete step 1
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["current_step"] == 1  # Should go to step 2 (step 3 still blocked)
    
    # Complete step 2
    result = await executor.receive(result)
    assert result["step_completed"]["step"] == 2
    assert result["current_step"] == 2  # Now step 3 is unblocked
    assert result["next_step"]["step"] == 3


@pytest.mark.asyncio
async def test_completed_steps_tracking(executor):
    """Should track completed_steps across multiple calls."""
    plan = [
        {"step": 1, "action": "A", "target": ""},
        {"step": 2, "action": "B", "target": ""},
        {"step": 3, "action": "C", "target": ""}
    ]
    
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["completed_steps"] == [0]
    
    result = await executor.receive(result)
    assert set(result["completed_steps"]) == {0, 1}
    
    result = await executor.receive(result)
    assert set(result["completed_steps"]) == {0, 1, 2}


@pytest.mark.asyncio
async def test_next_step_field_set_correctly(executor):
    """next_step should indicate the step to execute next."""
    plan = [
        {"step": 1, "action": "First", "target": ""},
        {"step": 2, "action": "Second", "target": ""}
    ]
    
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["next_step"]["action"] == "Second"
    
    # After completing last step, next_step should be None
    result = await executor.receive(result)
    assert result["next_step"] is None


@pytest.mark.asyncio
async def test_dependency_with_nonexistent_step(executor):
    """Should handle dependencies on non-existent steps gracefully."""
    plan = [
        {"step": 1, "action": "A", "target": ""},
        {"step": 2, "action": "B", "target": "", "depends_on": 99}
    ]
    
    # Step 2 depends on non-existent step 99, so it should be executable immediately
    # (treat missing dependency as satisfied)
    result = await executor.receive({"plan": plan, "current_step": 0})
    assert result["current_step"] == 1  # Should proceed to step 2


@pytest.mark.asyncio
async def test_complex_dependency_chain(executor):
    """Should handle complex dependency chains correctly."""
    plan = [
        {"step": 1, "action": "Foundation", "target": ""},
        {"step": 2, "action": "Walls", "target": "", "depends_on": 1},
        {"step": 3, "action": "Roof", "target": "", "depends_on": 2},
        {"step": 4, "action": "Paint", "target": "", "depends_on": 3},
        {"step": 5, "action": "Cleanup", "target": "", "depends_on": [2, 4]}
    ]
    
    # Execute through the chain
    result = await executor.receive({"plan": plan, "current_step": 0})  # Complete 1
    assert result["current_step"] == 1  # Walls
    
    result = await executor.receive(result)  # Complete 2
    # Step 5 depends on 2 and 4, so should go to 3 (Roof) not 5
    assert result["current_step"] == 2  # Roof
    
    result = await executor.receive(result)  # Complete 3
    assert result["current_step"] == 3  # Paint
    
    result = await executor.receive(result)  # Complete 4
    # Now step 5 is unblocked (depends on 2 and 4, both done)
    assert result["current_step"] == 4  # Cleanup


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
