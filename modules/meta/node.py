"""Subflow executor: a ganglion is a reference to another stored flow."""

import contextvars

from core.flow_runner import FlowRunner

_SUBFLOW_STACK = contextvars.ContextVar("neurocore_subflow_stack", default=())
_SUBFLOW_MANAGER = contextvars.ContextVar("neurocore_subflow_manager", default=None)


class SubflowExecutor:
    """Run the flow named by ``config['flow_id']`` and pass its output through."""

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        config = config or {}
        flow_id = config.get("flow_id")
        if not isinstance(flow_id, str) or not flow_id.strip():
            raise ValueError("subflow node needs a non-empty flow_id")
        flow_id = flow_id.strip()

        stack = _SUBFLOW_STACK.get()
        if flow_id in stack:
            raise ValueError(
                f"subflow cycle detected: {' -> '.join(stack + (flow_id,))}"
            )
        raw_depth = config.get("max_depth", 8)
        if isinstance(raw_depth, bool) or not isinstance(raw_depth, int):
            raise ValueError(
                "subflow node needs max_depth as an integer. "
                f"Why: got {raw_depth!r}. Fix: pass a non-negative integer."
            )
        if raw_depth < 0:
            raise ValueError(
                "subflow node needs max_depth as a non-negative integer. "
                f"Why: got {raw_depth!r}. Fix: pass 0 or a positive integer."
            )
        max_depth = raw_depth
        if len(stack) >= max_depth:
            raise ValueError(f"subflow depth limit ({max_depth}) exceeded")

        token = _SUBFLOW_STACK.set(stack + (flow_id,))
        try:
            runner = FlowRunner(
                flow_id, flow_manager_override=_SUBFLOW_MANAGER.get()
            )
            result = await runner.run(input_data or {})
            if result is None:
                return None
            return result if isinstance(result, dict) else {"result": result}
        finally:
            _SUBFLOW_STACK.reset(token)

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "subflow":
        return SubflowExecutor
    return None
