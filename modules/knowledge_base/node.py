from core.settings import settings
from core.llm import LLMBridge
from .backend import document_store

# Module-level cached LLM bridge instance for KnowledgeQueryExecutor
_llm_bridge_instance = None


def get_knowledge_llm_bridge():
    """Get or create a cached LLMBridge instance for knowledge base queries."""
    global _llm_bridge_instance
    if _llm_bridge_instance is None:
        from core.settings import settings
        _llm_bridge_instance = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key"),
            embedding_base_url=settings.get("embedding_api_url"),
            embedding_model=settings.get("embedding_model")
        )
    return _llm_bridge_instance


class KnowledgeQueryExecutor:
    def __init__(self):
        # Use shared cached LLMBridge instead of creating a new one each time
        self.llm = get_knowledge_llm_bridge()

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None: return None

        # Bug fix: guard config against None
        config = config or {}

        query = ""
        if isinstance(input_data, dict):
            if "messages" in input_data and input_data["messages"]:
                # Use last user message
                for msg in reversed(input_data["messages"]):
                    if msg["role"] == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            query = content
                        elif isinstance(content, list): # Multimodal
                             for part in content:
                                 if part.get("type") == "text":
                                     query += part.get("text", "") + " "
                        break
            elif "content" in input_data:
                query = str(input_data["content"])
        elif isinstance(input_data, str):
            query = input_data
            
        if not query:
            return input_data

        # Check if knowledge base is empty before processing
        if document_store.get_total_documents() == 0:
            return input_data

        # Generate embedding for the query
        embedding = await self.llm.get_embedding(query)

        limit = int(config.get("limit", 3))
        
        if embedding:
            results = document_store.search_hybrid(query, embedding, limit)
        else:
            results = document_store.search_keyword(query, limit)
        
        context_str = "\n\n".join([f"--- Source: {r['source']} (Page {r.get('page', '?')}) ---\n{r['content']}" for r in results])
        
        output = input_data.copy() if isinstance(input_data, dict) else {"original": input_data}
        output["knowledge_context"] = context_str
        
        # Don't inject into messages - let System Prompt handle the injection
        # This prevents conflicts when multiple bridge nodes are used
        
        return output

    async def send(self, processed_data: dict) -> dict:
        return processed_data

async def get_executor_class(node_type_id: str):
    if node_type_id == "query_knowledge":
        return KnowledgeQueryExecutor
    return None