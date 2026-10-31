"""
Tests for SSE streaming (5.1): LLMExecutor streaming path and FlowRunner queue injection.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Import at module level so the class reference is stable across hot-reload scenarios
# (TestClient-based tests can trigger module reloads that change the binding inside test bodies)
from core.flow_runner import FlowRunner


# ---------------------------------------------------------------------------
# LLMExecutor — streaming path
# ---------------------------------------------------------------------------

class TestLLMExecutorStreaming:
    """Tests for the new _receive_with_stream() path in LLMExecutor."""

    def _make_chunks(self, tokens):
        """Helper: build fake streaming chunks from a list of token strings."""
        return [
            {"choices": [{"delta": {"content": t}, "finish_reason": None}]}
            for t in tokens
        ]

    async def _stream_gen(self, chunks):
        for c in chunks:
            yield c

    @pytest.mark.asyncio
    async def test_tokens_put_to_queue(self):
        """Each token delta is put into the queue as a {type: token} event."""
        from modules.llm_module.node import LLMExecutor

        executor = LLMExecutor()
        queue = asyncio.Queue()
        chunks = self._make_chunks(["Hello", " ", "world"])

        with patch.object(executor.bridge, "chat_completion_stream", return_value=self._stream_gen(chunks)):
            result = await executor._receive_with_stream(
                messages=[{"role": "user", "content": "hi"}],
                params={"model": "test", "temperature": 0.7, "max_tokens": 100},
                queue=queue,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        assert len(events) == 3
        assert all(e["type"] == "token" for e in events)
        assert "".join(e["content"] for e in events) == "Hello world"

    @pytest.mark.asyncio
    async def test_returns_assembled_response_dict(self):
        """_receive_with_stream returns a choices dict with the full assembled content."""
        from modules.llm_module.node import LLMExecutor

        executor = LLMExecutor()
        queue = asyncio.Queue()
        chunks = self._make_chunks(["Foo", "Bar"])

        with patch.object(executor.bridge, "chat_completion_stream", return_value=self._stream_gen(chunks)):
            result = await executor._receive_with_stream(
                messages=[], params={"model": "t", "temperature": 0.7, "max_tokens": 10},
                queue=queue,
            )

        assert "choices" in result
        assert result["choices"][0]["message"]["content"] == "FooBar"

    @pytest.mark.asyncio
    async def test_stream_error_chunk_stops_gracefully(self):
        """An error chunk in the stream logs a warning and returns whatever was collected."""
        from modules.llm_module.node import LLMExecutor

        executor = LLMExecutor()
        queue = asyncio.Queue()

        async def bad_stream():
            yield {"choices": [{"delta": {"content": "part"}, "finish_reason": None}]}
            yield {"error": "upstream error"}

        with patch.object(executor.bridge, "chat_completion_stream", return_value=bad_stream()):
            result = await executor._receive_with_stream(
                messages=[], params={"model": "t", "temperature": 0.7, "max_tokens": 10},
                queue=queue,
            )

        assert result["choices"][0]["message"]["content"] == "part"

    @pytest.mark.asyncio
    async def test_receive_uses_streaming_path_when_queue_present(self):
        """receive() delegates to _receive_with_stream when _stream_queue is in config."""
        from modules.llm_module.node import LLMExecutor

        executor = LLMExecutor()
        queue = asyncio.Queue()

        called_streaming = []

        async def fake_stream(messages, params, q):
            called_streaming.append(True)
            return {"choices": [{"message": {"role": "assistant", "content": "streamed"}, "finish_reason": "stop"}]}

        with patch.object(executor, "_receive_with_stream", side_effect=fake_stream):
            await executor.receive(
                {"messages": [{"role": "user", "content": "hi"}]},
                config={"_stream_queue": queue},
            )

        assert called_streaming, "_receive_with_stream was not called"

    @pytest.mark.asyncio
    async def test_receive_uses_normal_path_without_queue(self):
        """receive() uses the normal (non-streaming) path when no queue is in config."""
        from modules.llm_module.node import LLMExecutor

        executor = LLMExecutor()

        normal_called = []

        async def fake_execute(**kwargs):
            normal_called.append(True)
            return {"choices": [{"message": {"role": "assistant", "content": "normal"}}]}

        with patch.object(executor, "_execute_with_retry_and_timeout", side_effect=fake_execute):
            await executor.receive(
                {"messages": []},
                config={},
            )

        assert normal_called, "_execute_with_retry_and_timeout was not called"

    @pytest.mark.asyncio
    async def test_receive_falls_back_to_normal_when_tools_present(self):
        """receive() uses normal path when tools are present (streaming + tools unsupported)."""
        from modules.llm_module.node import LLMExecutor

        executor = LLMExecutor()
        queue = asyncio.Queue()
        normal_called = []

        async def fake_execute(**kwargs):
            normal_called.append(True)
            return {"choices": []}

        with patch.object(executor, "_execute_with_retry_and_timeout", side_effect=fake_execute):
            await executor.receive(
                {"messages": []},
                config={"_stream_queue": queue, "tools": [{"name": "search"}]},
            )

        assert normal_called, "Should fall back to normal path when tools present"


# ---------------------------------------------------------------------------
# FlowRunner — stream_queue injection
# ---------------------------------------------------------------------------

class TestFlowRunnerStreamQueue:
    """Tests that FlowRunner passes _stream_queue through node configs."""

    @pytest.mark.asyncio
    async def test_stream_queue_injected_into_node_config(self):
        """Nodes receive _stream_queue in their config when stream_queue is passed to run()."""
        from core.flow_runner import FlowRunner
        FlowRunner.clear_cache()

        received_configs = []

        class CapturingExecutor:
            async def receive(self, input_data, config=None):
                received_configs.append(dict(config or {}))
                return input_data
            async def send(self, data):
                return data

        flow = {
            "id": "test-stream-flow",
            "name": "test",
            "nodes": [{"id": "n1", "moduleId": "dummy", "nodeTypeId": "dummy", "name": "N1", "config": {}}],
            "connections": [],
            "bridges": [],
        }

        queue = asyncio.Queue()

        with patch.object(FlowRunner, "_get_executor_class", new=AsyncMock(return_value=CapturingExecutor)):
            runner = FlowRunner(flow_id="test-stream-flow", flow_override=flow)
            await runner.run({"messages": []}, stream_queue=queue)

        assert received_configs, "Executor was never called"
        assert received_configs[0].get("_stream_queue") is queue

    @pytest.mark.asyncio
    async def test_no_stream_queue_not_injected(self):
        """_stream_queue is NOT in node config when stream_queue=None."""
        from core.flow_runner import FlowRunner
        FlowRunner.clear_cache()

        received_configs = []

        class CapturingExecutor:
            async def receive(self, input_data, config=None):
                received_configs.append(dict(config or {}))
                return input_data
            async def send(self, data):
                return data

        flow = {
            "id": "test-no-stream-flow",
            "name": "test",
            "nodes": [{"id": "n1", "moduleId": "dummy", "nodeTypeId": "dummy", "name": "N1", "config": {}}],
            "connections": [],
            "bridges": [],
        }

        with patch.object(FlowRunner, "_get_executor_class", new=AsyncMock(return_value=CapturingExecutor)):
            runner = FlowRunner(flow_id="test-no-stream-flow", flow_override=flow)
            await runner.run({"messages": []})

        assert "_stream_queue" not in received_configs[0]

    @pytest.mark.asyncio
    async def test_sentinel_received_after_flow_completes(self):
        """After run() returns, putting None into the queue signals done."""
        from core.flow_runner import FlowRunner
        FlowRunner.clear_cache()

        class NoopExecutor:
            async def receive(self, data, config=None): return data
            async def send(self, data): return data

        flow = {
            "id": "sentinel-flow",
            "name": "test",
            "nodes": [{"id": "n1", "moduleId": "d", "nodeTypeId": "d", "name": "N1", "config": {}}],
            "connections": [],
            "bridges": [],
        }

        queue = asyncio.Queue()

        async def run_and_signal():
            with patch.object(FlowRunner, "_get_executor_class", new=AsyncMock(return_value=NoopExecutor)):
                runner = FlowRunner(flow_id="sentinel-flow", flow_override=flow)
                await runner.run({"messages": []}, stream_queue=queue)
            await queue.put(None)  # caller's responsibility (done by /stream endpoint)

        await run_and_signal()
        sentinel = await queue.get()
        assert sentinel is None
