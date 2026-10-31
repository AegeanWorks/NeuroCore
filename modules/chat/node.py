import logging

logger = logging.getLogger(__name__)

class ChatInputExecutor:
    """
    Node to start a flow with chat data.
    """
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        # Ignore if triggered by Repeater (background loop) or if upstream failed
        if input_data.get("_repeat_count", 0) > 0:
            return None
        if "error" in input_data:
            return None
        # This node's main job is to start the flow, so receive just accepts the data.
        return input_data

    async def send(self, processed_data: dict) -> dict:
        if "messages" not in processed_data:
            return {"error": "Flow started without 'messages'. 'Chat Input' node requires it."}
        # Pass the validated data onward.
        return processed_data

class ChatOutputExecutor:
    """
    Node to format the final AI response for the chat UI.
    """
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        # Ignore background repeats to prevent log spam
        if input_data and input_data.get("_repeat_count", 0) > 0:
            return None
            
        if input_data is None:
            return {"content": "Error: No data received from flow."}
        
        if isinstance(input_data, dict):
            if "error" in input_data:
                return input_data
            
            # 1. Check for direct content (e.g. from a simple processor or memory recall acting as source)
            if "content" in input_data:
                 return {"content": input_data["content"]}
                 
            # 2. Check for OpenAI format (LLM)
            if "choices" in input_data:
                try:
                    message = input_data.get("choices", [{}])[0].get("message", {})
                    content = message.get("content", "")
                    if content:
                        return {"content": content}
                    # Fallback: Check for tool calls to give better error/info
                    if message.get("tool_calls"):
                        return {"content": f"<i>(Tool Call Generated: {message['tool_calls'][0]['function']['name']})</i>"}
                except (IndexError, AttributeError, TypeError) as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            # 3. Fallback/Echo (if input was just messages)
            if "messages" in input_data and input_data["messages"]:
                 return {"content": input_data["messages"][-1].get("content", "")}
        else:
            # Handle primitives (strings, lists) gracefully
            return {"content": str(input_data)}

        return {"error": "Could not parse AI response. Flow finished but produced no valid response."}

    async def send(self, processed_data: dict) -> dict:
        # This is the final node, so it sends the simplified data back to the FlowRunner.
        return processed_data

async def get_executor_class(node_type_id: str):
    """Acts as a dispatcher to get the correct node executor class."""
    if node_type_id == 'chat_input':
        return ChatInputExecutor
    if node_type_id == 'chat_output':
        return ChatOutputExecutor
    return None