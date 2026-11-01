"""Trace/span identity and outcome stamping (IDEA-1237)."""

from core import outcomes
from core.observability import (
    SpanKind,
    current_span_ids,
    get_trace_context,
    start_root_trace,
    trace,
)


def _reset():
    import core.observability as obs

    obs._trace_ctx.set(None)


def test_root_trace_has_a_trace_and_span_id():
    _reset()
    ctx = start_root_trace("run")
    ids = current_span_ids()
    assert ids["trace_id"] == ctx.trace_id
    assert ids["span_id"] == ctx.current_span.span_id
    assert ids["parent_span_id"] is None


def test_child_span_links_to_its_parent():
    _reset()
    ctx = start_root_trace("run")
    with trace("step"):
        ids = current_span_ids()
        assert ids["trace_id"] == ctx.trace_id
        assert ids["parent_span_id"] == ctx.spans[0].span_id
    assert ctx.spans[-1].parent_id == ctx.spans[0].span_id


def test_sampling_zero_and_one_are_deterministic():
    _reset()
    ctx = start_root_trace("off", sampling=0.0)
    assert ctx.sampled is False
    _reset()
    ctx = start_root_trace("on", sampling=1.0)
    assert ctx.sampled is True


def test_sampling_decision_is_recorded_on_the_context():
    _reset()
    ctx = start_root_trace("half", sampling=0.5)
    assert ctx.sampling == 0.5
    assert isinstance(ctx.sampled, bool)


def test_untraced_current_ids_are_empty():
    _reset()
    assert current_span_ids() == {
        "trace_id": None, "span_id": None, "parent_span_id": None
    }


def test_outcome_is_stamped_when_traced():
    _reset()
    ctx = start_root_trace("run")
    seen = []
    outcomes.set_outcome_listener(seen.append)
    try:
        with trace("node"):
            outcomes.emit_outcome({"flow_id": "f", "success": True})
    finally:
        outcomes.clear_outcome_listener()
    assert seen[0]["trace_id"] == ctx.trace_id
    assert seen[0]["span_id"] == ctx.spans[-1].span_id
    assert seen[0]["success"] is True


def test_outcome_is_unchanged_when_untraced():
    _reset()
    seen = []
    outcomes.set_outcome_listener(seen.append)
    try:
        payload = {"flow_id": "f", "success": True}
        outcomes.emit_outcome(payload)
    finally:
        outcomes.clear_outcome_listener()
    assert "trace_id" not in seen[0]
    assert payload == {"flow_id": "f", "success": True}


def test_a_broken_trace_provider_does_not_drop_the_outcome(monkeypatch):
    _reset()
    seen = []
    outcomes.set_outcome_listener(seen.append)

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(outcomes, "_trace_provider", broken)
    try:
        outcomes.emit_outcome({"flow_id": "f", "success": True})
    finally:
        outcomes.clear_outcome_listener()
        monkeypatch.undo()
    assert seen and seen[0]["success"] is True
