import json
import os
import asyncio
import logging
import httpx
from .router import TOOLS_FILE, LIBRARY_DIR
from .sandbox import ToolSandbox, SecurityError, ResourceLimitError, TimeoutError

logger = logging.getLogger(__name__)

class ToolDispatcherExecutor:
    def __init__(self):
        # Initialize sandbox with security settings from config
        self.sandbox = ToolSandbox(
            timeout=30.0,  # 30 second timeout for tool execution
            max_output_size=100 * 1024,  # 100KB max output
            read_only_files=True,  # Tools cannot write files by default
            permitted_environment=self._permitted_environment(),
        )

    @staticmethod
    def _permitted_environment() -> dict:
        """Only environment values the tools manifest explicitly authorizes reach a tool."""
        manifest_path = os.path.join(os.path.dirname(__file__), "module.json")
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                config = (json.load(handle) or {}).get("config") or {}
        except (OSError, json.JSONDecodeError):
            return {}
        names = config.get("env_allowlist")
        if not isinstance(names, list):
            return {}
        values = {name: os.getenv(name) for name in names if isinstance(name, str)}
        return {name: value for name, value in values.items() if value is not None}
    
    def _load_tools(self):
        if os.path.exists(TOOLS_FILE):
            with open(TOOLS_FILE, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    # JSONDecodeError: Corrupted JSON file
                    # OSError: File read permissions or I/O issues
                    logger.warning(f"Failed to load tools from {TOOLS_FILE}: {e}")
                    return {}
        return {}

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if not input_data or "choices" not in input_data:
            return input_data

        message = input_data["choices"][0]["message"]
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return input_data

        # Bug fix #3: guard config against None
        config = config or {}

        library = self._load_tools()
        allowed_tools = config.get("allowed_tools", [])  # From node config
        max_tools_per_turn = config.get("max_tools_per_turn", 5)  # Default 5 tools per turn
        results = []

        # Track tool count in context if available
        tool_count = input_data.get("_tool_count", 0)

        # Limit tool calls to max_tools_per_turn
        tool_calls_to_run = tool_calls[:max_tools_per_turn]
        remaining_tools = tool_calls[max_tools_per_turn:]

        # Store remaining tools for next turn
        if remaining_tools:
            input_data["_remaining_tool_calls"] = remaining_tools

        # Track tool count
        input_data["_tool_count"] = tool_count + len(tool_calls_to_run)

        # Bug fix #1: requires_continuation was inverted.
        # has_remaining is True when we truncated the list (remaining tools exist).
        # requires_continuation must be True in that case so the ConditionalRouter
        # loops back to run the remaining tools.
        has_remaining = len(remaining_tools) > 0

        for tool_call in tool_calls_to_run:
            func_name = tool_call["function"]["name"]

            # Bug fix #2: guard json.loads against malformed LLM arguments
            try:
                args = json.loads(tool_call["function"]["arguments"])
            except (json.JSONDecodeError, TypeError) as e:
                results.append({
                    "tool_call_id": tool_call.get("id", "unknown"),
                    "role": "tool",
                    "name": func_name,
                    "content": f"Error: Could not parse tool arguments: {str(e)}"
                })
                continue

            # Check if tool is allowed by this specific dispatcher instance
            if allowed_tools and func_name not in allowed_tools:
                output = f"Error: Tool {func_name} is not enabled for this dispatcher."
            elif func_name in library:
                # Check if tool is enabled in tools.json
                tool_config = library.get(func_name, {})
                if isinstance(tool_config, dict) and not tool_config.get("enabled", True):
                    output = f"Error: Tool {func_name} is disabled."
                else:
                    # Load code from the library directory
                    code_path = os.path.join(os.path.dirname(__file__), "library", f"{func_name}.py")
                    code = ""
                    if os.path.exists(code_path):
                        with open(code_path, "r", encoding="utf-8") as f:
                            code = f.read()
                    
                    # Execute tool code in sandboxed environment for security
                    # Use run_in_executor to avoid blocking the event loop
                    try:
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None,
                            self.sandbox.execute,
                            code,
                            {"args": args}
                        )
                        output = result.get("result", "Success (no result returned)")
                    except SecurityError as e:
                        output = f"Security Error: Tool '{func_name}' violated security policy: {str(e)}"
                    except ResourceLimitError as e:
                        output = f"Resource Limit Error: Tool '{func_name}' exceeded resource limits: {str(e)}"
                    except TimeoutError as e:
                        output = f"Timeout Error: Tool '{func_name}' took too long to execute: {str(e)}"
                    except Exception as e:
                        output = f"Error executing tool {func_name}: {str(e)}"
            else:
                output = f"Error: Tool {func_name} not found in library."

            results.append({
                "tool_call_id": tool_call["id"],
                "role": "tool",
                "name": func_name,
                "content": str(output)
            })

        # Update the conversation history if provided
        messages = []
        if "messages" in input_data:
            messages = list(input_data["messages"])
            messages.append(message)
            messages.extend(results)

        return {
            "tool_results": results,
            "assistant_message": message,
            "messages": messages,
            "requires_continuation": has_remaining,
        }

    async def send(self, processed_data: dict) -> dict:
        return processed_data

async def get_executor_class(node_type_id: str):
    if node_type_id == "tool_dispatcher":
        return ToolDispatcherExecutor
    return None
