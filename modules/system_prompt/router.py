import asyncio
import html
import json
import os
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.dependencies import get_module_manager

logger = logging.getLogger(__name__)


router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

# Shared tools cache with mtime-based caching (same pattern as node.py)
_tools_lock = asyncio.Lock()
_tools_cache = {"mtime": 0.0, "data": {}}
_tools_path = os.path.join(os.path.dirname(__file__), "..", "tools", "tools.json")

async def _load_tools_cached():
    """Load available tools from the tools module with mtime-based caching."""
    try:
        if os.path.exists(_tools_path):
            mtime = os.path.getmtime(_tools_path)
            async with _tools_lock:
                if mtime > _tools_cache["mtime"]:
                    with open(_tools_path, "r", encoding="utf-8") as f:
                        _tools_cache["data"] = json.load(f)
                    _tools_cache["mtime"] = mtime
            return _tools_cache["data"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"Failed to load tools: {e}")
    return {}

@router.get("/available-tools", response_class=HTMLResponse)
async def get_available_tools(request: Request, module_manager = Depends(get_module_manager)):
    """Get available tools with checkboxes."""
    all_tools = await _load_tools_cached()
    
    # FIX: Use proper accessor method with lock to avoid race condition
    system_prompt_module = module_manager.get_module("system_prompt") or {}
    enabled_tools = system_prompt_module.get("config", {}).get("enabled_tools", [])
    
    tools_html = ""
    for tool_name, tool_data in all_tools.items():
        is_checked = tool_name in enabled_tools
        checked_attr = "checked" if is_checked else ""
        
        if "definition" in tool_data and "function" in tool_data["definition"]:
            func_def = tool_data["definition"]["function"]
            desc = func_def.get("description", "No description available")
            
            # Escape HTML to prevent XSS attacks
            safe_tool_name = html.escape(tool_name)
            safe_desc = html.escape(desc)
            
            tools_html += f"""
            <div class="flex items-start space-x-3 p-3 rounded-lg hover:bg-slate-800/50 transition-colors">
                <input type="checkbox" id="tool_{safe_tool_name}" value="{safe_tool_name}" 
                       class="mt-1 w-4 h-4 rounded border-slate-600 bg-slate-950 text-blue-600 focus:ring-blue-500 cursor-pointer tool-checkbox"
                       {checked_attr}
                       onchange="updateEnabledTools()">
                <label for="tool_{safe_tool_name}" class="cursor-pointer flex-1">
                    <div class="text-sm font-medium text-slate-300">{safe_tool_name}</div>
                    <div class="text-xs text-slate-500">{safe_desc}</div>
                </label>
            </div>
            """
    
    if not tools_html:
        tools_html = '<p class="text-sm text-slate-500">No tools available</p>'
    
    full_html = f"""
    <div class="space-y-2" id="tools-container">
        {tools_html}
    </div>
    <script>
        if (!window.updateEnabledTools) {{
            window.updateEnabledTools = function() {{
                const checked = Array.from(document.querySelectorAll('#tools-container .tool-checkbox:checked'))
                    .map(c => c.value);
                const hiddenField = document.getElementById('enabled_tools');
                if (hiddenField) {{
                    hiddenField.value = JSON.stringify(checked);
                }}
            }};
        }}
    </script>
    """
    
    return full_html

