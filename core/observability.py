"""
Observability Infrastructure for NeuroCore

Provides:
1. Distributed Tracing - trace_id propagation through all operations
2. Metrics Collection - latency, token usage, tool success rates
3. Structured Logging - JSON formatted, searchable logs

Usage:
    from core.observability import trace_context, metrics, structured_logger
    
    # Start a trace
    with trace_context("flow_execution") as ctx:
        ctx.add_span("node_execution", node_id="chat_input")
        # ... do work ...
        metrics.increment("node.executed", node_type="chat")
        metrics.timing("node.latency", elapsed_ms)
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from core.paths import data_path
from core.node_stall import configure_node_stall

logger = logging.getLogger(__name__)

# Thread-local trace context
_trace_ctx: contextvars.ContextVar[Optional['TraceContext']] = contextvars.ContextVar('trace_ctx', default=None)


# =============================================================================
# Distributed Tracing
# =============================================================================

class SpanKind(Enum):
    """Types of spans in distributed tracing."""
    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


@dataclass
class Span:
    """Represents a single operation in a trace."""
    name: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    trace_id: Optional[str] = None
    kind: SpanKind = SpanKind.INTERNAL
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok, error
    error_message: Optional[str] = None
    
    def finish(self, status: str = "ok", error: Optional[str] = None):
        """Mark span as finished."""
        self.end_time = time.time()
        self.status = status
        self.error_message = error
    
    @property
    def duration_ms(self) -> float:
        """Get duration in milliseconds."""
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "kind": self.kind.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
            "error_message": self.error_message,
        }
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.finish(status="error", error=str(exc_val))
        else:
            self.finish(status="ok")
        return False


@dataclass 
class TraceContext:
    """Context for distributed tracing."""
    trace_id: str
    spans: List[Span] = field(default_factory=list)
    current_span: Optional[Span] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.trace_id:
            self.trace_id = uuid.uuid4().hex
    
    def add_span(self, name: str, kind: SpanKind = SpanKind.INTERNAL, 
                 attributes: Optional[Dict[str, Any]] = None, 
                 parent_id: Optional[str] = None) -> Span:
        """Create and start a new span."""
        span = Span(
            name=name,
            trace_id=self.trace_id,
            kind=kind,
            parent_id=parent_id or (self.current_span.span_id if self.current_span else None),
            attributes=attributes or {},
        )
        self.spans.append(span)
        self.current_span = span
        return span
    
    def end_span(self, status: str = "ok", error: Optional[str] = None):
        """End the current span."""
        if self.current_span:
            self.current_span.finish(status=status, error=error)
            self.current_span = None
    
    def get_trace(self) -> List[Dict[str, Any]]:
        """Get complete trace as list of span dicts."""
        return [span.to_dict() for span in self.spans]
    
    def __enter__(self):
        _trace_ctx.set(self)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        _trace_ctx.set(None)
        if exc_type:
            if self.current_span and self.current_span.end_time is None:
                self.current_span.finish(status="error", error=str(exc_val))
        else:
            # Finish the current span on normal (non-exception) exit
            if self.current_span and self.current_span.end_time is None:
                self.current_span.finish(status="ok")
        return False


# =============================================================================
# Trace Context Management
# =============================================================================

def get_trace_context() -> Optional[TraceContext]:
    """Get current trace context."""
    return _trace_ctx.get()


def get_trace_id() -> Optional[str]:
    """Get current trace ID."""
    ctx = _trace_ctx.get()
    return ctx.trace_id if ctx else None


def get_or_create_trace_context() -> TraceContext:
    """Get current trace context or create a new one."""
    ctx = _trace_ctx.get()
    if ctx is None:
        ctx = TraceContext(trace_id=uuid.uuid4().hex)
        _trace_ctx.set(ctx)
    return ctx


def create_trace_context(trace_id: Optional[str] = None) -> TraceContext:
    """Create a new trace context with optional trace ID."""
    ctx = TraceContext(trace_id=trace_id or uuid.uuid4().hex)
    _trace_ctx.set(ctx)
    return ctx


@contextlib.contextmanager
def trace(span_name: str, kind: SpanKind = SpanKind.INTERNAL, 
           attributes: Optional[Dict[str, Any]] = None):
    """
    Context manager for creating spans.
    
    Usage:
        with trace("process_request") as ctx:
            ctx.add_span("database_query", attributes={"query": "SELECT *"})
    """
    ctx = get_or_create_trace_context()
    span = ctx.add_span(span_name, kind=kind, attributes=attributes)
    try:
        yield ctx
    except Exception as e:
        span.finish(status="error", error=str(e))
        raise
    finally:
        ctx.end_span()


@contextlib.contextmanager
def trace_async(span_name: str, kind: SpanKind = SpanKind.INTERNAL, 
                attributes: Optional[Dict[str, Any]] = None):
    """
    Context manager for creating spans in async context.
    Uses the same contextvar as sync code for proper trace propagation.
    
    Usage:
        with trace_async("process_request") as ctx:
            ctx.add_span("database_query", attributes={"query": "SELECT *"})
    """
    # Use the same trace context as sync code for proper propagation
    # Create new trace only if no existing context
    existing_ctx = _trace_ctx.get()
    if existing_ctx is not None:
        # Reuse existing context for trace propagation
        ctx = existing_ctx
    else:
        # Create new context for this async operation
        ctx = TraceContext(trace_id=uuid.uuid4().hex)
        _trace_ctx.set(ctx)
    
    span = ctx.add_span(span_name, kind=kind, attributes=attributes)
    try:
        yield ctx
    except Exception as e:
        span.finish(status="error", error=str(e))
        raise
    finally:
        ctx.end_span()
        # Only clear if we created a new context
        if existing_ctx is None:
            _trace_ctx.set(None)


def traced(span_name: str = None, kind: SpanKind = SpanKind.INTERNAL):
    """
    Decorator for automatic tracing of functions.
    
    Usage:
        @traced("my_function")
        def my_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        name = span_name or func.__name__
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with trace(name, kind=kind) as ctx:
                ctx.current_span.attributes["function"] = func.__name__
                return func(*args, **kwargs)
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Use trace_async to create a new TraceContext for each async call
            # to avoid concurrent coroutines clobbering each other's current_span
            with trace_async(name, kind=kind) as ctx:
                ctx.current_span.attributes["function"] = func.__name__
                return await func(*args, **kwargs)
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# =============================================================================
# Metrics Collection
# =============================================================================

class Metrics:
    """Simple metrics collector with counters, timers, and gauges."""
    
    def __init__(self):
        self._counters: Dict[str, float] = defaultdict(float)
        self._timings: Dict[str, List[float]] = defaultdict(list)
        self._gauges: Dict[str, float] = {}
        # FIX: Use threading.Lock for sync-safe operations instead of asyncio.Lock
        self._lock = threading.Lock()
        self._max_timings_per_metric = 1000  # Keep last 1000 timings
        self._metrics_file = data_path("metrics_counters.json")
        self._load_counters()
        
    def _load_counters(self):
        """Load persisted counters from disk on startup."""
        import os
        try:
            if os.path.exists(self._metrics_file):
                with open(self._metrics_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self._counters[k] = float(v)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)  # Fail silently to avoid interrupting startup
            
    def _save_counters(self):
        """Save counters to disk on updates."""
        import os
        try:
            # Atomic save to prevent corruption
            import tempfile
            os.makedirs(os.path.dirname(self._metrics_file), exist_ok=True)
            with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(self._metrics_file), delete=False) as tf:
                json.dump(dict(self._counters), tf)
                temp_name = tf.name
            os.replace(temp_name, self._metrics_file)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)  # Fail silently
    
    def increment(self, metric: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None):
        """Increment a counter metric."""
        key = self._make_key(metric, tags)
        with self._lock:
            self._counters[key] += value
            # Only save LLM metrics immediately to avoid disk I/O bloat
            if metric.startswith("llm."):
                self._save_counters()
    
    def timing(self, metric: str, duration_ms: float, tags: Optional[Dict[str, str]] = None):
        """Record a timing metric in milliseconds."""
        key = self._make_key(metric, tags)
        with self._lock:
            self._timings[key].append(duration_ms)
            # Keep only last N timings
            if len(self._timings[key]) > self._max_timings_per_metric:
                self._timings[key] = self._timings[key][-self._max_timings_per_metric:]
    
    def gauge(self, metric: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Set a gauge value."""
        key = self._make_key(metric, tags)
        with self._lock:
            self._gauges[key] = value
    
    def _make_key(self, metric: str, tags: Optional[Dict[str, str]] = None) -> str:
        """Create metric key with tags."""
        if not tags:
            return metric
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{metric}{{{tag_str}}}"
    
    def get_counter(self, metric: str, tags: Optional[Dict[str, str]] = None) -> float:
        """Get counter value."""
        key = self._make_key(metric, tags)
        with self._lock:
            return self._counters.get(key, 0.0)
    
    def get_timing_stats(self, metric: str, tags: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """Get timing statistics (min, max, avg, p50, p95, p99)."""
        key = self._make_key(metric, tags)
        with self._lock:
            timings = self._timings.get(key, [])
            if not timings:
                return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
            
            sorted_timings = sorted(timings)
            n = len(sorted_timings)
            
            def percentile(p: float) -> float:
                idx = int(n * p)
                return sorted_timings[min(idx, n - 1)]
            
            return {
                "count": n,
                "min": sorted_timings[0],
                "max": sorted_timings[-1],
                "avg": sum(timings) / n,
                "p50": percentile(0.5),
                "p95": percentile(0.95),
                "p99": percentile(0.99),
            }
    
    def get_gauge(self, metric: str, tags: Optional[Dict[str, str]] = None) -> Optional[float]:
        """Get gauge value."""
        key = self._make_key(metric, tags)
        with self._lock:
            return self._gauges.get(key)
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all metrics."""
        with self._lock:
            result = {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timings": {},
            }
            for key, timings in self._timings.items():
                if timings:
                    sorted_timings = sorted(timings)
                    n = len(sorted_timings)
                    
                    def percentile(p: float) -> float:
                        idx = int(n * p)
                        return sorted_timings[min(idx, n - 1)]
                    
                    result["timings"][key] = {
                        "count": n,
                        "min": sorted_timings[0],
                        "max": sorted_timings[-1],
                        "avg": sum(timings) / n,
                        "p50": percentile(0.5),
                        "p95": percentile(0.95),
                    }
            return result
    
    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._timings.clear()
            self._gauges.clear()


# Timing context manager
@contextlib.contextmanager
def timed(metric: str, tags: Optional[Dict[str, str]] = None):
    """Context manager for timing operations."""
    start = time.time()
    try:
        yield
    finally:
        duration_ms = (time.time() - start) * 1000
        metrics.timing(metric, duration_ms, tags)


def timed_decorator(metric: str, tags: Optional[Dict[str, str]] = None):
    """Decorator for timing functions."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                duration_ms = (time.time() - start) * 1000
                metrics.timing(metric, duration_ms, tags)
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                duration_ms = (time.time() - start) * 1000
                metrics.timing(metric, duration_ms, tags)
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# Global metrics instance
metrics = Metrics()


# =============================================================================
# Structured Logging
# =============================================================================

class StructuredLogger:
    """
    Structured JSON logger for NeuroCore.
    
    Replaces print() statements with searchable, filterable JSON logs.
    """
    
    def __init__(self, name: str = "neurocore"):
        self.logger = logging.getLogger(name)
        self._ensure_handler()
    
    def _ensure_handler(self):
        """Ensure logger has a handler configured."""
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JsonFormatter())
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
            # Prevent log records from propagating to root handlers to avoid double-output
            self.logger.propagate = False
    
    def _log(self, level: int, event: str, message: str = "", 
             trace_id: Optional[str] = None, **kwargs):
        """Internal log method."""
        # Get trace context if available
        if trace_id is None:
            ctx = get_trace_context()
            if ctx:
                trace_id = ctx.trace_id
        
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event,
            "message": message,
            "trace_id": trace_id,
        }
        
        # Add current span info if available
        ctx = get_trace_context()
        if ctx and ctx.current_span:
            log_data["span"] = {
                "name": ctx.current_span.name,
                "span_id": ctx.current_span.span_id,
            }
        
        # Add any extra kwargs
        log_data.update(kwargs)
        
        self.logger.log(level, json.dumps(log_data, default=str))
    
    def debug(self, event: str, message: str = "", **kwargs):
        """Log debug level."""
        self._log(logging.DEBUG, event, message, **kwargs)
    
    def info(self, event: str, message: str = "", **kwargs):
        """Log info level."""
        self._log(logging.INFO, event, message, **kwargs)
    
    def warning(self, event: str, message: str = "", **kwargs):
        """Log warning level."""
        self._log(logging.WARNING, event, message, **kwargs)
    
    def error(self, event: str, message: str = "", **kwargs):
        """Log error level."""
        self._log(logging.ERROR, event, message, **kwargs)
    
    def critical(self, event: str, message: str = "", **kwargs):
        """Log critical level."""
        self._log(logging.CRITICAL, event, message, **kwargs)
    
    def log_flow(self, flow_id: str, node_id: str, event: str, **kwargs):
        """Log flow-related event with trace context."""
        trace_id = get_trace_id()
        self.info(event, trace_id=trace_id, flow_id=flow_id, node_id=node_id, **kwargs)
    
    def log_node(self, flow_id: str, node_id: str, node_name: str, 
                 event: str, **kwargs):
        """Log node-related event."""
        trace_id = get_trace_id()
        self.info(event, trace_id=trace_id, flow_id=flow_id, 
                  node_id=node_id, node_name=node_name, **kwargs)
    
    def log_llm(self, flow_id: str, event: str, **kwargs):
        """Log LLM-related event."""
        trace_id = get_trace_id()
        self.info(event, trace_id=trace_id, flow_id=flow_id, **kwargs)
    
    def log_tool(self, flow_id: str, tool_name: str, event: str, **kwargs):
        """Log tool-related event."""
        trace_id = get_trace_id()
        self.info(event, trace_id=trace_id, flow_id=flow_id, 
                  tool_name=tool_name, **kwargs)
    
    def log_memory(self, flow_id: str, operation: str, **kwargs):
        """Log memory operation."""
        trace_id = get_trace_id()
        self.info(f"memory_{operation}", trace_id=trace_id, flow_id=flow_id, **kwargs)


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        # If message is already JSON, return it
        try:
            data = json.loads(record.getMessage())
            return json.dumps(data)
        except (json.JSONDecodeError, TypeError) as _exc:
            logger.debug("ignored exception: %s", _exc)
        
        # Otherwise, format as JSON
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields
        if hasattr(record, 'trace_id') and record.trace_id:
            log_data['trace_id'] = record.trace_id
        
        if hasattr(record, 'flow_id') and record.flow_id:
            log_data['flow_id'] = record.flow_id
        
        if hasattr(record, 'node_id') and record.node_id:
            log_data['node_id'] = record.node_id
        
        # Add exception info
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, default=str)


# Global structured logger
structured_logger = StructuredLogger("neurocore")


# =============================================================================
# Auto-instrumentation Helpers
# =============================================================================

def instrument_flow_runner():
    """
    Instrument the flow runner for automatic tracing.
    
    This should be called during app initialization.
    """
    from core.flow_runner import FlowRunner
    
    # Wrap the run method
    original_run = FlowRunner.run
    
    @wraps(original_run)
    async def traced_run(self, initial_input: dict, start_node_id: str = None,
                         timeout: float = None, raise_errors: bool = False,
                         episode_id: str = None, stream_queue=None):
        # Create or propagate trace context
        trace_id = initial_input.get("_trace_id") if isinstance(initial_input, dict) else None
        with create_trace_context(trace_id) as ctx:
            ctx.attributes["flow_id"] = self.flow_id
            ctx.attributes["start_node"] = start_node_id
            
            with ctx.add_span("flow_execution", attributes={
                "flow_id": self.flow_id,
                "timeout": timeout,
            }):
                structured_logger.log_flow(self.flow_id, "system", "flow_start", 
                                           start_node=start_node_id, timeout=timeout)
                
                start_time = time.time()
                try:
                    result = await original_run(self, initial_input, start_node_id, timeout,
                                                raise_errors, episode_id, stream_queue)
                    elapsed_ms = (time.time() - start_time) * 1000
                    
                    metrics.timing("flow.latency", elapsed_ms, {"flow_id": self.flow_id})
                    metrics.increment("flow.completed", tags={"flow_id": self.flow_id})
                    
                    structured_logger.log_flow(self.flow_id, "system", "flow_complete",
                                               duration_ms=elapsed_ms)
                    return result
                except Exception as e:
                    elapsed_ms = (time.time() - start_time) * 1000
                    metrics.timing("flow.latency", elapsed_ms, {"flow_id": self.flow_id})
                    metrics.increment("flow.failed", tags={"flow_id": self.flow_id})
                    structured_logger.log_flow(self.flow_id, "system", "flow_error",
                                               error=str(e), duration_ms=elapsed_ms)
                    raise
    
    FlowRunner.run = traced_run


def instrument_llm_calls():
    """
    Instrument LLM calls for automatic tracing and metrics.
    
    This should be called during app initialization.
    """
    from core.llm import LLMBridge, get_shared_client
    
    # Patch the chat_completion method on LLMBridge class
    if hasattr(LLMBridge, 'chat_completion'):
        original_chat_completion = LLMBridge.chat_completion
        
        @wraps(original_chat_completion)
        async def traced_chat_completion(self, *args, **kwargs):
            model_name = str(kwargs.get("model") or "unknown")
            ctx = get_or_create_trace_context()
            span = ctx.add_span("llm_call", SpanKind.CLIENT, attributes={
                "model": model_name,
            })

            start_time = time.time()
            try:
                result = await original_chat_completion(self, *args, **kwargs)
                elapsed_ms = (time.time() - start_time) * 1000

                # Extract token usage and record flat + per-model counters
                if isinstance(result, dict):
                    tokens = result.get("usage", {})
                    if tokens:
                        pt = tokens.get("prompt_tokens", 0)
                        ct = tokens.get("completion_tokens", 0)
                        tt = tokens.get("total_tokens", 0) or (pt + ct)
                        model_tag = {"model": model_name}
                        # Flat totals (backwards-compatible)
                        metrics.increment("llm.tokens_used", tt)
                        metrics.increment("llm.prompt_tokens", pt)
                        metrics.increment("llm.completion_tokens", ct)
                        # Per-model breakdown
                        metrics.increment("llm.tokens_used", tt, tags=model_tag)
                        metrics.increment("llm.prompt_tokens", pt, tags=model_tag)
                        metrics.increment("llm.completion_tokens", ct, tags=model_tag)

                metrics.timing("llm.latency", elapsed_ms)
                metrics.increment("llm.calls_success")
                metrics.increment("llm.calls_success", tags={"model": model_name})

                span.finish()
                return result
            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                metrics.timing("llm.latency", elapsed_ms)
                metrics.increment("llm.calls_failed")
                metrics.increment("llm.calls_failed", tags={"model": model_name})
                span.finish(status="error", error=str(e))
                raise
        
        LLMBridge.chat_completion = traced_chat_completion
    
    # Also patch get_shared_client to return instrumented client
    original_get_shared_client = get_shared_client
    
    async def instrumented_get_shared_client(*args, **kwargs):
        client = await original_get_shared_client(*args, **kwargs)
        # The client is an httpx.AsyncClient, we can't easily instrument it
        # Instead, we rely on patching LLMBridge methods which is done above
        return client
    
    # Note: We don't replace get_shared_client as it returns the raw httpx client
    # The actual LLM calls go through LLMBridge.chat_completion which is patched above


def instrument_tools():
    """
    Instrument tool execution for automatic tracing and metrics.
    """
    from modules.tools.node import ToolDispatcherExecutor
    
    if hasattr(ToolDispatcherExecutor, 'receive'):
        original_receive = ToolDispatcherExecutor.receive
        
        @wraps(original_receive)
        async def traced_receive(self, input_data, config=None):
            ctx = get_or_create_trace_context()
            tool_calls = input_data.get("tool_calls", []) if isinstance(input_data, dict) else []
            
            with ctx.add_span("tool_execution", attributes={
                "tool_count": len(tool_calls),
            }):
                start_time = time.time()
                try:
                    result = await original_receive(self, input_data, config)
                    elapsed_ms = (time.time() - start_time) * 1000
                    
                    metrics.timing("tool.latency", elapsed_ms)
                    metrics.increment("tool.calls_success", len(tool_calls))
                    
                    return result
                except Exception as e:
                    elapsed_ms = (time.time() - start_time) * 1000
                    metrics.timing("tool.latency", elapsed_ms)
                    metrics.increment("tool.calls_failed", len(tool_calls))
                    raise
        
        ToolDispatcherExecutor.receive = traced_receive


# =============================================================================
# Dashboard Data Endpoint
# =============================================================================

def get_dashboard_data() -> Dict[str, Any]:
    """
    Get data for observability dashboard.

    Returns:
        Dict with metrics, traces, and system info
    """
    return {
        "metrics": metrics.get_all_metrics(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def get_token_stats() -> Dict[str, Any]:
    """Get LLM token usage stats, broken down by model.

    Returns a dict with two keys:
    - ``total``: aggregate counters across all models.
    - ``by_model``: per-model breakdown, keyed by model name.
    """
    counters = metrics.get_all_metrics().get("counters", {})

    total = {
        "prompt_tokens":     int(counters.get("llm.prompt_tokens", 0)),
        "completion_tokens": int(counters.get("llm.completion_tokens", 0)),
        "total_tokens":      int(counters.get("llm.tokens_used", 0)),
        "calls_success":     int(counters.get("llm.calls_success", 0)),
        "calls_failed":      int(counters.get("llm.calls_failed", 0)),
    }

    by_model: Dict[str, Dict[str, int]] = {}
    for key, value in counters.items():
        if "{" not in key or "model=" not in key:
            continue
        brace = key.index("{")
        metric_name = key[:brace]
        tags_str = key[brace + 1 : key.index("}")]
        tags = dict(t.split("=", 1) for t in tags_str.split(",") if "=" in t)
        model = tags.get("model", "unknown")
        if model not in by_model:
            by_model[model] = {
                "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "calls": 0,
            }
        v = int(value)
        if metric_name == "llm.prompt_tokens":
            by_model[model]["prompt_tokens"] = v
        elif metric_name == "llm.completion_tokens":
            by_model[model]["completion_tokens"] = v
        elif metric_name == "llm.tokens_used":
            by_model[model]["total_tokens"] = v
        elif metric_name == "llm.calls_success":
            by_model[model]["calls"] = v

    return {"total": total, "by_model": by_model}


# =============================================================================
# Optional File-Based Trace Sink (Session Integration)
# =============================================================================

def get_trace_file_path() -> str:
    """Get the path to the execution trace file."""
    return data_path("execution_trace.jsonl")


def export_trace_to_file(trace_data: Dict[str, Any], file_path: Optional[str] = None) -> bool:
    """
    Export trace data to JSON Lines file.
    
    Args:
        trace_data: Dictionary containing trace information
        file_path: Optional custom file path (defaults to execution_trace.jsonl)
    
    Returns:
        True if export was successful, False otherwise
    """
    if file_path is None:
        file_path = get_trace_file_path()
    
    try:
        import os
        # Ensure directory exists
        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        
        # Create trace event
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "observability_export",
            "trace_data": trace_data,
        }
        
        # Append to file as JSON Line
        with open(file_path, 'a', encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + '\n')
        
        return True
    except Exception:
        return False


def get_session_trace_summary() -> Dict[str, Any]:
    """
    Get a summary of the current session's trace data.
    
    Returns:
        Dict with session info and trace summary
    """
    try:
        from core.session_manager import session_manager
        
        trace = session_manager.get_trace()
        
        # Count events by type
        event_counts: Dict[str, int] = {}
        for event in trace:
            event_type = event.get("event", "unknown")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        return {
            "session_id": session_manager.get_session_id(),
            "total_events": len(trace),
            "event_counts": event_counts,
            "tick": session_manager.get_tick(),
        }
    except Exception as e:
        return {"error": str(e)}


# Optional: Auto-export spans to trace file when using observability tracing
def _maybe_export_span(span_data: Dict[str, Any]) -> None:
    """
    Optionally export span data to trace file.
    This is a no-op unless explicitly enabled via settings.
    """
    # Check if file-based trace export is enabled
    try:
        from core.settings import settings
        if settings.get("enable_trace_file_export", False):
            import os
            trace_file = get_trace_file_path()
            
            # Ensure directory exists
            directory = os.path.dirname(trace_file)
            if directory:
                os.makedirs(directory, exist_ok=True)
            
            # Get session info if available
            session_info = {}
            try:
                from core.session_manager import session_manager
                session_info = {
                    "session_id": session_manager.get_session_id(),
                    "tick": session_manager.get_tick()
                }
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)
            
            # Build trace event
            event = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event": "observability_span",
                "trace_id": span_data.get("trace_id"),
                "span_id": span_data.get("span_id"),
                "span_name": span_data.get("name"),
                "duration_ms": span_data.get("duration_ms"),
                "status": span_data.get("status"),
                "session_info": session_info,
                "attributes": span_data.get("attributes", {}),
            }
            
            # Append to trace file
            with open(trace_file, 'a', encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + '\n')
    except Exception as _exc:
        # Silently fail - this is optional functionality
        logger.debug("ignored exception: %s", _exc)


# Global flag for enabling trace file export
_trace_file_export_enabled = False


def enable_trace_file_export(enabled: bool = True) -> None:
    """
    Enable or disable trace file export.
    
    Args:
        enabled: Whether to enable exporting spans to trace file
    """
    global _trace_file_export_enabled
    _trace_file_export_enabled = enabled


def is_trace_file_export_enabled() -> bool:
    """Check if trace file export is enabled."""
    global _trace_file_export_enabled
    if _trace_file_export_enabled:
        return True
    
    # Also check settings
    try:
        from core.settings import settings
        return settings.get("enable_trace_file_export", False)
    except Exception:
        return False


# Wire node latency detection into this module's metrics without a reverse import.
configure_node_stall(
    timing=metrics.timing,
    gauge=metrics.gauge,
    counter=metrics.increment,
    warn=structured_logger.warning,
)
