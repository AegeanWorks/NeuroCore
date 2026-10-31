class CommentExecutor:
    """
    Executor for the Comment Node.
    Since comments are purely visual annotations on the frontend and cannot be connected
    to other nodes, this executor returns None from receive() to stop branch propagation.
    If a user accidentally connects a comment node, returning None makes the misuse obvious
    rather than silently passing data through.
    """
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        # Return None to stop branch propagation - comments should not pass data
        return None

    async def send(self, processed_data: dict) -> dict:
        # Return None to indicate this node does not produce output
        return None

async def get_executor_class(node_type_id: str):
    if node_type_id == "comment_node":
        return CommentExecutor
    return None