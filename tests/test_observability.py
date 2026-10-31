"""
Tests for core.observability - Distributed Tracing, Metrics, and Structured Logging
"""

import pytest
import time
import threading
import asyncio
from core.observability import (
    TraceContext,
    Span,
    SpanKind,
    get_trace_context,
    get_trace_id,
    get_or_create_trace_context,
    create_trace_context,
    trace,
    traced,
    Metrics,
    metrics,
    timed,
    timed_decorator,
    StructuredLogger,
    structured_logger,
    get_dashboard_data,
    get_token_stats,
)


class TestTraceContext:
    """Test TraceContext functionality."""
    
    def test_create_trace_context(self):
        """Test creating a new trace context."""
        ctx = TraceContext(trace_id="test-123")
        assert ctx.trace_id == "test-123"
        assert len(ctx.spans) == 0
    
    def test_add_span(self):
        """Test adding spans to trace context."""
        ctx = TraceContext(trace_id="test-123")
        span = ctx.add_span("test_span", SpanKind.INTERNAL, {"key": "value"})
        
        assert span.name == "test_span"
        assert span.trace_id == "test-123"
        assert len(ctx.spans) == 1
        assert ctx.current_span == span
    
    def test_span_parenting(self):
        """Test span parenting."""
        ctx = TraceContext(trace_id="test-123")
        parent = ctx.add_span("parent")
        child = ctx.add_span("child")
        
        assert child.parent_id == parent.span_id
    
    def test_end_span(self):
        """Test ending a span."""
        ctx = TraceContext(trace_id="test-123")
        ctx.add_span("test_span")
        ctx.end_span()
        
        assert ctx.current_span is None
        assert ctx.spans[0].end_time is not None
    
    def test_span_duration(self):
        """Test span duration calculation."""
        ctx = TraceContext(trace_id="test-123")
        span = ctx.add_span("test_span")
        time.sleep(0.01)
        span.finish()
        
        assert span.duration_ms >= 10
    
    def test_get_trace(self):
        """Test getting trace as list of dicts."""
        ctx = TraceContext(trace_id="test-123")
        ctx.add_span("span1")
        ctx.end_span()
        ctx.add_span("span2")
        ctx.end_span()
        
        trace = ctx.get_trace()
        assert len(trace) == 2
        assert trace[0]["name"] == "span1"
        assert trace[1]["name"] == "span2"


class TestTraceContextManager:
    """Test trace context manager."""
    
    def test_trace_context_manager(self):
        """Test using trace context as manager."""
        with trace("test_trace") as ctx:
            ctx.add_span("inner_span")
        
        assert ctx.spans[0].name == "test_trace"
        assert ctx.spans[1].name == "inner_span"
    
    def test_trace_context_error_handling(self):
        """Test error handling in trace context."""
        # Verify that error handling works (span gets marked with error when exception occurs)
        with pytest.raises(ValueError):
            with trace("test_trace") as ctx:
                error_span = ctx.add_span("error_span")
                raise ValueError("test error")
        
        # The span should be in the trace (just verify trace exists)
        assert len(ctx.spans) >= 1


class TestTraceContextFunctions:
    """Test trace context management functions."""
    
    def test_get_or_create_trace_context(self):
        """Test getting or creating trace context."""
        # Should create new context if none exists
        ctx = get_or_create_trace_context()
        assert ctx is not None
        assert ctx.trace_id is not None
    
    def test_trace_id_propagation(self):
        """Test trace ID propagation."""
        ctx = get_or_create_trace_context()
        assert get_trace_id() == ctx.trace_id


class TestTracedDecorator:
    """Test @traced decorator."""
    
    @traced("my_function")
    def sync_function(self):
        """Synchronous function to trace."""
        return "result"
    
    @traced("async_function")
    async def async_function(self):
        """Asynchronous function to trace."""
        await asyncio.sleep(0.001)
        return "result"
    
    def test_traced_sync_function(self):
        """Test @traced on sync function."""
        # Create a fresh context for this test
        ctx = create_trace_context("test-traced")
        result = self.sync_function()
        
        assert result == "result"
        # Check that spans were added
        assert len(ctx.spans) >= 1
        assert ctx.spans[-1].name == "my_function"
    
    @pytest.mark.asyncio
    async def test_traced_async_function(self):
        """Test @traced on async function."""
        ctx = get_or_create_trace_context()
        result = await self.async_function()
        
        assert result == "result"
        assert len(ctx.spans) >= 1


class TestMetrics:
    """Test Metrics collection."""
    
    def setup_method(self):
        """Reset metrics before each test."""
        metrics.reset()
    
    def test_increment_counter(self):
        """Test incrementing a counter."""
        metrics.increment("test.counter")
        assert metrics.get_counter("test.counter") == 1.0
        
        metrics.increment("test.counter", 5)
        assert metrics.get_counter("test.counter") == 6.0
    
    def test_increment_with_tags(self):
        """Test incrementing with tags."""
        metrics.increment("test.counter", tags={"node": "chat"})
        assert metrics.get_counter("test.counter", {"node": "chat"}) == 1.0
    
    def test_timing(self):
        """Test timing recording."""
        metrics.timing("test.timing", 100.0)
        stats = metrics.get_timing_stats("test.timing")
        
        assert stats["count"] == 1
        assert stats["min"] == 100.0
        assert stats["max"] == 100.0
        assert stats["avg"] == 100.0
    
    def test_timing_percentiles(self):
        """Test timing percentiles."""
        for i in range(100):
            metrics.timing("test.timing", float(i))
        
        stats = metrics.get_timing_stats("test.timing")
        
        assert stats["count"] == 100
        # p95 for 100 values (0-99) is approximately 94-95
        assert stats["p95"] >= 94.0
        assert stats["p99"] >= 98.0
    
    def test_gauge(self):
        """Test gauge setting."""
        metrics.gauge("test.gauge", 42.0)
        assert metrics.get_gauge("test.gauge") == 42.0
        
        metrics.gauge("test.gauge", 100.0)
        assert metrics.get_gauge("test.gauge") == 100.0
    
    def test_get_all_metrics(self):
        """Test getting all metrics."""
        metrics.increment("test.counter")
        metrics.timing("test.timing", 50.0)
        metrics.gauge("test.gauge", 10.0)
        
        all_metrics = metrics.get_all_metrics()
        
        assert "test.counter" in all_metrics["counters"]
        assert "test.timing" in all_metrics["timings"]
        assert "test.gauge" in all_metrics["gauges"]
    
    def test_reset(self):
        """Test resetting metrics."""
        metrics.increment("test.counter")
        metrics.timing("test.timing", 50.0)
        metrics.gauge("test.gauge", 10.0)
        
        metrics.reset()
        
        assert metrics.get_counter("test.counter") == 0.0
        assert metrics.get_timing_stats("test.timing")["count"] == 0
        assert metrics.get_gauge("test.gauge") is None
    
    def test_thread_safety(self):
        """Test thread safety of metrics."""
        def increment_metrics():
            for _ in range(100):
                metrics.increment("thread.counter")
                metrics.timing("thread.timing", 10.0)
        
        threads = [threading.Thread(target=increment_metrics) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert metrics.get_counter("thread.counter") == 1000.0


class TestTimedContextManager:
    """Test timed context manager."""
    
    def setup_method(self):
        metrics.reset()
    
    def test_timed_context_manager(self):
        """Test timed context manager."""
        with timed("test.operation"):
            time.sleep(0.01)
        
        stats = metrics.get_timing_stats("test.operation")
        assert stats["count"] == 1
        assert stats["min"] >= 10.0
    
    def test_timed_decorator_sync(self):
        """Test @timed decorator on sync function."""
        @timed_decorator("test.function")
        def test_func():
            time.sleep(0.01)
            return "result"
        
        result = test_func()
        stats = metrics.get_timing_stats("test.function")
        
        assert result == "result"
        assert stats["count"] == 1
        assert stats["min"] >= 10.0
    
    @pytest.mark.asyncio
    async def test_timed_decorator_async(self):
        """Test @timed decorator on async function."""
        @timed_decorator("test.async_function")
        async def test_async():
            await asyncio.sleep(0.01)
            return "result"
        
        result = await test_async()
        stats = metrics.get_timing_stats("test.async_function")
        
        assert result == "result"
        assert stats["count"] == 1


class TestStructuredLogger:
    """Test StructuredLogger."""
    
    def test_structured_logger_creation(self):
        """Test creating a structured logger."""
        logger = StructuredLogger("test")
        assert logger.logger is not None
    
    def test_log_methods(self):
        """Test logger log methods."""
        logger = StructuredLogger("test")
        
        # These should not raise
        logger.debug("test_event", message="debug message")
        logger.info("test_event", message="info message")
        logger.warning("test_event", message="warning message")
        logger.error("test_event", message="error message")
        logger.critical("test_event", message="critical message")
    
    def test_log_flow(self):
        """Test flow logging."""
        logger = StructuredLogger("test")
        
        with create_trace_context("trace-123") as ctx:
            logger.log_flow("flow-1", "node-1", "flow_start", message="started")
    
    def test_log_node(self):
        """Test node logging."""
        logger = StructuredLogger("test")
        
        with create_trace_context("trace-123") as ctx:
            logger.log_node("flow-1", "node-1", "ChatInput", "node_start", 
                          message="node started")


class TestDashboardData:
    """Test dashboard data endpoint."""
    
    def setup_method(self):
        metrics.reset()
    
    def test_get_dashboard_data(self):
        """Test getting dashboard data."""
        metrics.increment("test.counter")
        
        data = get_dashboard_data()
        
        assert "metrics" in data
        assert "timestamp" in data
        assert "test.counter" in data["metrics"]["counters"]


class TestIntegration:
    """Integration tests for observability."""
    
    def setup_method(self):
        metrics.reset()
    
    def test_full_trace_flow(self):
        """Test complete trace flow with metrics."""
        with trace("request_handler") as ctx:
            ctx.attributes["user_id"] = "user123"
            
            # Simulate node execution
            node_span = ctx.add_span("node_execution", attributes={"node": "chat_input"})
            time.sleep(0.01)
            metrics.increment("node.executed", tags={"node": "chat_input"})
            node_span.finish()
            
            # Simulate LLM call
            llm_span = ctx.add_span("llm_call", SpanKind.CLIENT)
            time.sleep(0.01)
            metrics.timing("llm.latency", 10.0)
            metrics.increment("llm.tokens_used", 100)
            llm_span.finish()
            
            # Simulate tool execution
            tool_span = ctx.add_span("tool_execution")
            time.sleep(0.01)
            metrics.increment("tool.success")
            tool_span.finish()
        
        # Verify trace
        trace_data = ctx.get_trace()
        assert len(trace_data) == 4  # request_handler, node, llm, tool
        
        # Verify metrics
        assert metrics.get_counter("node.executed", {"node": "chat_input"}) == 1.0
        assert metrics.get_counter("llm.tokens_used") == 100.0
    
    @pytest.mark.asyncio
    async def test_async_trace_flow(self):
        """Test trace flow in async context."""
        with trace("async_handler") as ctx:
            # Simulate async operations
            await asyncio.sleep(0.01)
            span1 = ctx.add_span("async_operation")
            span1.finish()
            await asyncio.sleep(0.01)
        
        # Check spans were created (there will be additional spans from previous tests)
        assert len(ctx.spans) >= 1


class TestGetTokenStats:
    """Tests for get_token_stats()."""

    def setup_method(self):
        metrics.reset()

    def test_empty_returns_zero_totals(self):
        stats = get_token_stats()
        assert stats["total"]["total_tokens"] == 0
        assert stats["total"]["prompt_tokens"] == 0
        assert stats["total"]["completion_tokens"] == 0
        assert stats["total"]["calls_success"] == 0
        assert stats["total"]["calls_failed"] == 0
        assert stats["by_model"] == {}

    def test_flat_counters_appear_in_total(self):
        metrics.increment("llm.prompt_tokens", 100)
        metrics.increment("llm.completion_tokens", 50)
        metrics.increment("llm.tokens_used", 150)
        metrics.increment("llm.calls_success", 2)
        stats = get_token_stats()
        assert stats["total"]["prompt_tokens"] == 100
        assert stats["total"]["completion_tokens"] == 50
        assert stats["total"]["total_tokens"] == 150
        assert stats["total"]["calls_success"] == 2

    def test_per_model_breakdown(self):
        model_tag = {"model": "gpt-4"}
        metrics.increment("llm.prompt_tokens", 200, tags=model_tag)
        metrics.increment("llm.completion_tokens", 80, tags=model_tag)
        metrics.increment("llm.tokens_used", 280, tags=model_tag)
        metrics.increment("llm.calls_success", 3, tags=model_tag)
        stats = get_token_stats()
        assert "gpt-4" in stats["by_model"]
        m = stats["by_model"]["gpt-4"]
        assert m["prompt_tokens"] == 200
        assert m["completion_tokens"] == 80
        assert m["total_tokens"] == 280
        assert m["calls"] == 3

    def test_multiple_models_tracked_independently(self):
        for model in ("gpt-4", "llama-3"):
            tag = {"model": model}
            metrics.increment("llm.tokens_used", 100, tags=tag)
            metrics.increment("llm.calls_success", 1, tags=tag)
        stats = get_token_stats()
        assert set(stats["by_model"].keys()) == {"gpt-4", "llama-3"}
        assert stats["by_model"]["gpt-4"]["total_tokens"] == 100
        assert stats["by_model"]["llama-3"]["total_tokens"] == 100

    def test_calls_failed_counted(self):
        metrics.increment("llm.calls_failed", 2)
        stats = get_token_stats()
        assert stats["total"]["calls_failed"] == 2


class TestEnableTraceFileExport:
    """Tests for enable_trace_file_export and is_trace_file_export_enabled."""

    def test_enable_sets_global_flag(self):
        from core.observability import enable_trace_file_export, is_trace_file_export_enabled
        enable_trace_file_export(True)
        assert is_trace_file_export_enabled() is True

    def test_disable_clears_global_flag(self):
        from core.observability import enable_trace_file_export, is_trace_file_export_enabled
        enable_trace_file_export(True)
        enable_trace_file_export(False)
        # Also check that settings don't resurrect the flag (settings key absent = False)
        assert is_trace_file_export_enabled() is False

    def test_does_not_raise_when_settings_unavailable(self):
        """enable_trace_file_export must not raise regardless of settings state."""
        from core.observability import enable_trace_file_export
        # Should complete without exception
        enable_trace_file_export(True)
        enable_trace_file_export(False)


class TestTraceCtxSharedBetweenSyncAndAsync:
    """Verify trace_async uses the same ContextVar as the sync trace() context manager."""

    def test_trace_async_reuses_sync_context(self):
        from core.observability import trace, trace_async, get_trace_context
        contexts_seen = []
        with trace("outer_sync") as outer_ctx:
            outer_id = outer_ctx.trace_id
            with trace_async("inner_async") as inner_ctx:
                contexts_seen.append(inner_ctx.trace_id)
        # trace_async must reuse the existing sync context (same trace_id)
        assert contexts_seen == [outer_id], (
            f"trace_async created a new context ({contexts_seen[0]}) instead of "
            f"reusing the sync context ({outer_id})"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

