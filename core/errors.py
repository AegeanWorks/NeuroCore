"""
Error Hierarchy for NeuroCore

Defines typed exceptions for different error categories to enable proper
error propagation and debugging throughout the pipeline.
"""

from typing import Optional, Any, Dict


class NeuroCoreError(Exception):
    """Base exception for all NeuroCore errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dict for backward compatibility."""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


class LLMError(NeuroCoreError):
    """Base class for LLM-related errors."""
    
    def __init__(self, message: str, model: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.model = model
        details = details or {}
        if model:
            details["model"] = model
        super().__init__(message, details)


class LLMTimeoutError(LLMError):
    """Raised when LLM request times out."""
    pass


class LLMHTTPError(LLMError):
    """Raised when LLM returns an HTTP error."""
    
    def __init__(self, message: str, status_code: int, model: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.status_code = status_code
        details = details or {}
        details["status_code"] = status_code
        super().__init__(message, model, details)


class LLMResponseError(LLMError):
    """Raised when LLM returns an invalid or unexpected response."""
    pass


class ToolError(NeuroCoreError):
    """Base class for tool-related errors."""
    
    def __init__(self, message: str, tool_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.tool_name = tool_name
        details = details or {}
        if tool_name:
            details["tool_name"] = tool_name
        super().__init__(message, details)


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""
    pass


class ToolTimeoutError(ToolError):
    """Raised when tool execution times out."""
    pass


class SandboxSecurityError(ToolError):
    """Raised when sandbox security policy is violated."""
    pass


class FlowError(NeuroCoreError):
    """Base class for flow execution errors."""
    
    def __init__(self, message: str, flow_id: Optional[str] = None, node_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.flow_id = flow_id
        self.node_id = node_id
        details = details or {}
        if flow_id:
            details["flow_id"] = flow_id
        if node_id:
            details["node_id"] = node_id
        super().__init__(message, details)


class FlowNotFoundError(FlowError):
    """Raised when a flow is not found."""
    pass


class FlowValidationError(FlowError):
    """Raised when flow validation fails."""
    pass


class NodeExecutionError(FlowError):
    """Raised when a node fails during execution."""
    pass


class ModuleError(NeuroCoreError):
    """Base class for module-related errors."""
    
    def __init__(self, message: str, module_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.module_id = module_id
        details = details or {}
        if module_id:
            details["module_id"] = module_id
        super().__init__(message, details)


class ModuleNotFoundError(ModuleError):
    """Raised when a module is not found."""
    pass


class ModuleLoadError(ModuleError):
    """Raised when a module fails to load."""
    pass


class MemoryError(NeuroCoreError):
    """Base class for memory-related errors."""
    pass


class MemoryConsolidationError(MemoryError):
    """Raised when memory consolidation fails."""
    pass


# Utility function to convert exception to dict (for backward compatibility)
def exception_to_dict(exc: Exception) -> Dict[str, Any]:
    """Convert any exception to a dict representation."""
    if isinstance(exc, NeuroCoreError):
        return exc.to_dict()
    
    # For non-NeuroCore exceptions, wrap them
    return {
        "error": exc.__class__.__name__,
        "message": str(exc),
        "details": {},
    }

