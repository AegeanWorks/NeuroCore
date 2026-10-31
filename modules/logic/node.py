import re
import asyncio
import json
import logging
from core.debug import debug_logger
from core.settings import settings
from modules.tools.sandbox import ToolSandbox, SecurityError, ResourceLimitError, TimeoutError as SandboxTimeoutError

logger = logging.getLogger(__name__)

# Module-level set to store active tasks and prevent garbage collection
# This ensures tasks survive even when the executor instance is collected
_active_tasks: set = set()


class ContextLengthRouterExecutor:
    """
    Routes to RLM or standard LLM based on estimated token count.
    
    Implements the paper's finding that RLMs have a crossover point below which
    standard LLM calls are better. Automatically routes long inputs to RLM
    and short inputs to standard processing.
    
    Config:
        - rlm_threshold_tokens (int, default 8000): Token count threshold for RLM routing
        - rlm_branch (list): Node IDs to route to when input exceeds threshold
        - standard_branch (list): Node IDs to route to when input is within threshold
    """
    
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None
            
        config = config or {}
        threshold = config.get("rlm_threshold_tokens", 8000)
        
        messages = input_data.get("messages", [])
        estimated_tokens = self._estimate_tokens(messages)
        
        result = input_data.copy()
        
        if estimated_tokens > threshold:
            result["_route_targets"] = config.get("rlm_branch", [])
            result["routing_reason"] = f"Input ~{estimated_tokens} tokens exceeds threshold {threshold}"
            result["use_rlm"] = True
        else:
            result["_route_targets"] = config.get("standard_branch", [])
            result["routing_reason"] = f"Input ~{estimated_tokens} tokens within standard limit"
            result["use_rlm"] = False
        
        return result
    
    def _estimate_tokens(self, messages: list) -> int:
        """Estimate token count. Rough estimate: 4 chars per token."""
        total_chars = 0
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    total_chars += len(content)
                elif isinstance(content, list):
                    # Handle multimodal messages with content parts
                    for part in content:
                        if isinstance(part, dict):
                            total_chars += len(str(part.get("text", "")))
        return total_chars // 4

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "delay_node":
        return DelayExecutor
    if node_type_id == "script_node":
        return ScriptExecutor
    if node_type_id == "repeater_node":
        return RepeaterExecutor
    if node_type_id == "conditional_router":
        return ConditionalRouterExecutor
    if node_type_id == "trigger_node":
        return TriggerExecutor
    if node_type_id == "schedule_start_node":
        return ScheduleStartExecutor
    if node_type_id == "context_length_router":
        return ContextLengthRouterExecutor
    return None


class DelayExecutor:
    MAX_DELAY = 3600  # 1 hour cap to prevent flow workers from being stuck
    
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None: return None
        config = config or {}
        try:
            seconds = float(config.get("seconds", 1.0))
        except (ValueError, TypeError) as e:
            # ValueError: Invalid string format for float conversion
            # TypeError: Non-numeric type in config
            logger.warning(f"Invalid delay seconds value, using default: {e}")
            seconds = 1.0
        
        if seconds < 0:
            seconds = 0
        
        # Cap the delay to MAX_DELAY to prevent flow workers from being stuck
        if seconds > self.MAX_DELAY:
            logger.warning(f"Delay capped from {seconds}s to {self.MAX_DELAY}s (1 hour) to prevent flow worker from being stuck")
            seconds = self.MAX_DELAY
            
        await asyncio.sleep(seconds)
        return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data


class ScriptExecutor:
    # Sandbox configuration for script execution - now configurable via node config
    DEFAULT_TIMEOUT = 10.0  # seconds
    DEFAULT_MAX_MEMORY_MB = 50  # MB
    
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None: return None
        config = config or {}
        code = config.get("code", "")
        
        if not code:
            return input_data
        
        # FIX: Read timeout and max_memory from config, with fallbacks to class defaults
        timeout = config.get("timeout", self.DEFAULT_TIMEOUT)
        max_memory_mb = config.get("max_memory_mb", self.DEFAULT_MAX_MEMORY_MB)
            
        # Define scope
        if isinstance(input_data, dict):
            data_val = input_data.copy()
            result_val = input_data.copy()
        else:
            # Handle non-dict inputs (lists, strings, etc.) gracefully
            data_val = input_data
            result_val = {}
        
        # Create sandbox for secure execution with configurable limits
        sandbox = ToolSandbox(
            timeout=timeout,
            max_memory_mb=max_memory_mb
        )
        
        try:
            # Execute code in sandbox with data and result in local scope
            result = sandbox.execute(code, {'data': data_val, 'result': result_val})
            # FIX: Return input_data as fallback when result is None (prevents branch termination)
            if result.get('result') is None:
                logger.debug("Script executed but returned no result, passing through input data")
                return input_data
            return result.get('result')
        except SecurityError as e:
            logger.error(f"Script security violation: {e}")
            if isinstance(input_data, dict):
                err_data = input_data.copy()
            else:
                err_data = {"original_input": input_data}
            err_data["error"] = f"Script security violation: {str(e)}"
            return err_data
        except ResourceLimitError as e:
            logger.error(f"Script resource limit exceeded: {e}")
            if isinstance(input_data, dict):
                err_data = input_data.copy()
            else:
                err_data = {"original_input": input_data}
            err_data["error"] = f"Script resource limit exceeded: {str(e)}"
            return err_data
        except SandboxTimeoutError as e:
            logger.error(f"Script timeout: {e}")
            if isinstance(input_data, dict):
                err_data = input_data.copy()
            else:
                err_data = {"original_input": input_data}
            err_data["error"] = f"Script timeout: {str(e)}"
            return err_data
        except Exception as e:
            logger.error(f"Script execution failed: {e}")
            # Return error in data stream so it can be debugged
            if isinstance(input_data, dict):
                err_data = input_data.copy()
            else:
                err_data = {"original_input": input_data}
            err_data["error"] = f"Script failed: {str(e)}"
            return err_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data


class RepeaterExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None: return None
        config = config or {}
        
        flow_id = config.get("_flow_id")
        active_flow_ids = settings.get("active_ai_flows", [])
        logger.debug(f"[Repeater] flow_id={flow_id}, active_flow_ids={active_flow_ids}")
        if flow_id is not None and flow_id not in active_flow_ids:
            debug_logger.log(flow_id, "repeater_node", "Repeater", "stopped", "Flow no longer active")
            logger.debug(f"[Repeater] Stopping - flow {flow_id} is no longer active")
            # FIX: Return None instead of input_data when stopping to prevent downstream execution
            return None
        
        try:
            delay = float(config.get("delay", 5.0))
            max_repeats = int(config.get("max_repeats", 1))
        except (ValueError, TypeError):
            delay = 5.0
            max_repeats = 1
        
        # Use flow_id from earlier read; no need to re-read from config
        node_id = config.get("_node_id")
        current_repeat = input_data.get("_repeat_count", 0)
        
        if flow_id and (max_repeats == 0 or current_repeat < max_repeats):
            async def trigger_next(fid, data, count, start_node):
                # NOTE: Known race condition - between sleep and the check below,
                # another coroutine could deactivate the flow. This is acceptable
                # but worth documenting. The flow may run one extra cycle after deactivation.
                await asyncio.sleep(delay)
                active_flow_ids = settings.get("active_ai_flows", [])
                logger.debug(f"[Repeater trigger_next] fid={fid}, active_flow_ids={active_flow_ids}")
                if fid not in active_flow_ids:
                    debug_logger.log(fid, "repeater_node", "Repeater", "stopped", "Flow no longer active")
                    logger.debug(f"[Repeater] Stopping - flow {fid} is no longer active")
                    return
                try:
                    from core.flow_runner import FlowRunner
                    runner = FlowRunner(fid)
                    # FIX: Strip conversation data to prevent unbounded memory growth
                    # Only carry over fields relevant to the next trigger
                    next_data = {
                        "_repeat_count": count + 1,
                        "_input_source": data.get("_input_source")
                    }
                    # Preserve any explicit output fields from the previous iteration
                    for key in ["content", "response", "messages"]:
                        if key in data and data[key] is not None:
                            next_data[key] = data[key]
                    await runner.run(next_data, start_node_id=start_node)
                except Exception as e:
                    logger.error(f"Repeater failed to trigger next run: {e}")
                    debug_logger.log(fid, "repeater_node", "Repeater", "error", f"Loop failed: {str(e)}")
            
            # Store the task reference in module-level set to prevent garbage collection
            # The instance may be collected after this method returns, but the task
            # must survive to complete its execution.
            task = asyncio.create_task(trigger_next(flow_id, input_data, current_repeat, node_id))
            _active_tasks.add(task)
            task.add_done_callback(_active_tasks.discard)
            
        return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data


class ConditionalRouterExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Routes data flow based on tool existence or other conditions.
        Returns the input_data if tool exists, None otherwise.
        
        Config options:
        - check_field: field to check for existence (default: "tool_calls")
          - "tool_calls" - check if LLM returned tool calls
          - "requires_continuation" - check if more tools need to run (from tool dispatcher)
          - "max_tools_per_turn" - limit tools per turn, route to false when exceeded
          - "satisfied" - check if reflection/agent was satisfied (from reflection node)
        """
        if input_data is None:
            return None
            
        config = config or {}
        check_field = config.get("check_field", "tool_calls")
        max_tools = config.get("max_tools_per_turn", 0)
        
        # Determine condition
        condition_met = False
        if isinstance(input_data, dict):
            # Special handling for requires_continuation (from tool dispatcher)
            if check_field == "requires_continuation":
                condition_met = input_data.get("requires_continuation", False)
            # Check tool count for max_tools_per_turn
            elif check_field == "max_tools_per_turn":
                tool_count = input_data.get("_tool_count", 0)
                condition_met = tool_count < max_tools
            # Check satisfied (from reflection node)
            elif check_field == "satisfied":
                condition_met = input_data.get("satisfied", False)
            # Check if field exists and is truthy at top level
            elif input_data.get(check_field):
                condition_met = True
            # Check OpenAI format for tool_calls if not found at top level
            # This handles the case where tool_calls are nested in choices[0].message.tool_calls
            elif check_field == "tool_calls":
                try:
                    choices = input_data.get("choices")
                    if isinstance(choices, list) and len(choices) > 0:
                        message = choices[0].get("message")
                        if isinstance(message, dict) and message.get("tool_calls"):
                            condition_met = True
                except (AttributeError, TypeError) as e:
                    # AttributeError: Object doesn't have expected attributes
                    # TypeError: Invalid type operations during access
                    logger.debug(f"Error checking tool_calls in OpenAI format: {e}")
                    pass
        
        if config.get("invert", False):
            condition_met = not condition_met
            
        # Determine targets based on config
        targets = config.get("true_branches", []) if condition_met else config.get("false_branches", [])
            
        # Return data with routing info
        result = input_data.copy() if isinstance(input_data, dict) else {"content": input_data}
        result["_route_targets"] = targets
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


class TriggerExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data


class ScheduleStartExecutor:
    LONG_SLEEP_WARNING_THRESHOLD = 3600  # 1 hour - warn if longer
    
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None: return None
        config = config or {}
        
        from datetime import datetime
        
        schedule_time = config.get("schedule_time", "")
        schedule_date = config.get("schedule_date", "")
        
        if not schedule_time:
            return input_data
        
        try:
            from datetime import timedelta
            now = datetime.now()

            if schedule_date:
                target_dt = datetime.strptime(f"{schedule_date} {schedule_time}", "%Y-%m-%d %H:%M")
            else:
                target_dt = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {schedule_time}", "%Y-%m-%d %H:%M")
                if target_dt < now:
                    # Bug fix: replace(day=now.day+1) raises ValueError at end of month.
                    # Use timedelta(days=1) instead.
                    target_dt = target_dt + timedelta(days=1)
            
            wait_seconds = (target_dt - now).total_seconds()
            
            if wait_seconds > 0:
                # Warn if wait time is longer than threshold (for long-running scheduled tasks)
                if wait_seconds > self.LONG_SLEEP_WARNING_THRESHOLD:
                    logger.warning(
                        f"ScheduleStartExecutor: wait_seconds={wait_seconds:.0f}s ({wait_seconds/3600:.1f}h) exceeds "
                        f"{self.LONG_SLEEP_WARNING_THRESHOLD}s threshold. This blocks the event loop worker for the "
                        f"entire duration with no persistence. Consider using external scheduling (APScheduler, Celery beat, cron)."
                    )
                debug_logger.log(config.get("_flow_id"), "schedule_node", "ScheduleStart", "waiting", f"Waiting until {target_dt}")
                
                # FIX: Handle cancellation properly - re-raise CancelledError to allow proper cleanup
                try:
                    await asyncio.sleep(wait_seconds)
                except asyncio.CancelledError:
                    # Re-raise cancellation to allow the flow to be stopped properly
                    logger.info("ScheduleStartExecutor: Wait cancelled, flow stopping")
                    raise
                
            return input_data
        except asyncio.CancelledError:
            # Propagate cancellation up
            raise
        except Exception as e:
            logger.error(f"ScheduleStart error: {e}")
            return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data

