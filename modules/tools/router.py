import logging
import os
import json
import asyncio
import filelock
from fastapi import APIRouter, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from core.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")
TOOLS_FILE = os.path.join(os.path.dirname(__file__), "tools.json")
LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "library")
TOOLS_LOCK_FILE = TOOLS_FILE + ".lock"

os.makedirs(LIBRARY_DIR, exist_ok=True)


def _sanitize_tool_name(name: str) -> str:
    """Sanitize tool name to prevent path traversal attacks.
    
    Removes any path separators, parent directory references, and other
    characters that could be used for path traversal.
    
    Args:
        name: The raw tool name from user input.
        
    Returns:
        Sanitized tool name safe for filesystem operations.
        
    Raises:
        ValueError: If the sanitized name is empty or invalid.
    """
    if not name:
        raise ValueError("Tool name cannot be empty")
    
    # Remove path separators and parent directory references
    sanitized = name.replace("/", "").replace("\\", "")
    sanitized = sanitized.replace("..", "")
    
    # Remove any characters that could be used for path traversal
    # Only allow alphanumeric, underscore, hyphen, and spaces
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in ("_", "-", " "))
    
    # Strip leading/trailing whitespace
    sanitized = sanitized.strip()
    
    if not sanitized:
        raise ValueError("Invalid tool name after sanitization")
    
    return sanitized

def load_tools():
    """Load tools from JSON file with file locking for thread safety."""
    lock = filelock.FileLock(TOOLS_LOCK_FILE)
    with lock:
        if os.path.exists(TOOLS_FILE):
            with open(TOOLS_FILE, "r", encoding="utf-8") as f:
                try: return json.load(f)
                except (json.JSONDecodeError, OSError): return {}
        return {}

def save_tools(tools):
    """Save tools to JSON file with file locking for thread safety."""
    lock = filelock.FileLock(TOOLS_LOCK_FILE)
    with lock:
        with open(TOOLS_FILE, "w", encoding="utf-8") as f:
            json.dump(tools, f, indent=4)

async def load_tools_async():
    """Async wrapper for load_tools to avoid blocking event loop."""
    return await asyncio.to_thread(load_tools)

async def save_tools_async(tools):
    """Async wrapper for save_tools to avoid blocking event loop."""
    return await asyncio.to_thread(save_tools, tools)

@router.get("", response_class=HTMLResponse)
async def tools_page(request: Request):
    """Loads the main dashboard with the Tool Library active."""
    module_manager = request.app.state.module_manager
    enabled_modules = [m for m in module_manager.get_all_modules() if m.get("enabled")]
    return templates.TemplateResponse(request, "index.html", {
        "modules": enabled_modules,
        "active_module": "tools",
        "full_width_content": True,
        "settings": settings.settings
    })

@router.get("/gui", response_class=HTMLResponse)
async def tools_gui(request: Request):
    """Returns the Tool Library GUI fragment."""
    tools = load_tools()
    return templates.TemplateResponse(request, "tools_gui.html", {"tools": tools})

@router.get("/list", response_class=HTMLResponse)
async def tools_list(request: Request):
    tools = load_tools()
    return templates.TemplateResponse(request, "tools_list.html", {"tools": tools})

@router.post("/save")
async def save_tool(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    parameters: str = Form(...),
    code: str = Form(...),
    original_name: str = Form("")
):
    try:
        # Validate JSON parameters
        params_json = json.loads(parameters)
        tools = load_tools()
        
        # Determine enabled state and handle renaming
        name = name.strip()
        # Sanitize tool name to prevent path traversal
        name = _sanitize_tool_name(name)
        
        if original_name:
            original_name = original_name.strip()
            # Sanitize original name as well for consistency
            original_name = _sanitize_tool_name(original_name)
            
        is_enabled = True
        
        if original_name and original_name != name:
            if original_name in tools:
                is_enabled = tools[original_name].get("enabled", True)
                del tools[original_name]
                old_code_path = os.path.join(LIBRARY_DIR, f"{original_name}.py")
                if os.path.exists(old_code_path):
                    os.remove(old_code_path)
        elif name in tools:
            is_enabled = tools[name].get("enabled", True)
        
        # OpenAI Function Calling Format
        tools[name] = {
            "definition": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": params_json
                }
            },
            "enabled": is_enabled
        }
        
        # Save Python logic to standalone file
        with open(os.path.join(LIBRARY_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
            f.write(code)

        save_tools(tools)
        return await tools_list(request)
    except Exception as e:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to save tool: {str(e)}"}})})

@router.post("/{name}/toggle")
async def toggle_tool(request: Request, name: str):
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    tools = load_tools()
    if name in tools:
        current_state = tools[name].get("enabled", True)
        tools[name]["enabled"] = not current_state
        save_tools(tools)
    return await tools_list(request)

@router.delete("/{name}")
async def delete_tool(request: Request, name: str):
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    tools = load_tools()
    if name in tools:
        del tools[name]
        # Remove associated python file
        code_path = os.path.join(LIBRARY_DIR, f"{name}.py")
        if os.path.exists(code_path):
            os.remove(code_path)
        save_tools(tools)
    return await tools_list(request)

@router.get("/edit/{name}")
async def edit_tool(name: str):
    """Returns tool metadata and code for editing."""
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    tools = load_tools()
    if name not in tools:
        return Response(status_code=404)
    
    tool_data = tools[name]
    code = ""
    code_path = os.path.join(LIBRARY_DIR, f"{name}.py")
    if os.path.exists(code_path):
        with open(code_path, "r", encoding="utf-8") as f:
            code = f.read()
            
    return {
        "name": name,
        "description": tool_data["definition"]["function"]["description"],
        "parameters": json.dumps(tool_data["definition"]["function"]["parameters"], indent=2),
        "code": code
    }

@router.get("/names")
async def get_tool_names():
    """Returns a list of all tool names."""
    tools = load_tools()
    return list(tools.keys())

@router.get("/with-descriptions")
async def get_tools_with_descriptions():
    """Returns all tools with their names and descriptions."""
    tools = load_tools()
    result = []
    for name, tool_data in tools.items():
        if not tool_data.get("enabled", True):
            continue
        desc = ""
        if "definition" in tool_data and "function" in tool_data["definition"]:
            desc = tool_data["definition"]["function"].get("description", "")
        result.append({"name": name, "description": desc})
    return result

@router.get("/definitions")
async def get_definitions():
    """Returns the JSON definitions for all tools to be used in LLM config."""
    tools = load_tools()
    definitions = []
    for name, tool in tools.items():
        if tool.get("enabled", True):
            definition = tool["definition"]
            # NOTE: Time injection was removed - it caused stale timestamps
            # The SaveReminder tool should inject current time at runtime instead
            definitions.append(definition)
    return definitions

@router.get("/export")
async def export_tools(name: str = Query(None)):
    # Sanitize tool name if provided to prevent path traversal
    if name:
        name = _sanitize_tool_name(name)
    tools = load_tools()
    export_data = []
    
    if name:
        if name not in tools:
            return Response(status_code=404, content="Tool not found")
        items_to_export = [(name, tools[name])]
        filename = f"{name}.json"
    else:
        items_to_export = tools.items()
        filename = "neurocore_tools.json"
    
    for name, data in items_to_export:
        tool_export = {
            "name": name,
            "enabled": data.get("enabled", True),
            "description": data["definition"]["function"]["description"],
            "parameters": data["definition"]["function"]["parameters"]
        }
        
        # Read code
        code_path = os.path.join(LIBRARY_DIR, f"{name}.py")
        if os.path.exists(code_path):
            with open(code_path, "r", encoding="utf-8") as f:
                tool_export["code"] = f.read()
        else:
            tool_export["code"] = ""
            
        export_data.append(tool_export)
        
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.get("/config/{name}", response_class=HTMLResponse)
async def get_tool_config(request: Request, name: str):
    """Returns the configuration form for a tool."""
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    
    if name == "SendEmail":
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = os.getenv("SMTP_PORT", "587")
        smtp_email = os.getenv("SMTP_EMAIL", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")
        
        return templates.TemplateResponse(request, "tool_config_sendemail.html", {
            "smtp_server": smtp_server,
            "smtp_port": smtp_port,
            "smtp_email": smtp_email,
            "smtp_password": smtp_password
        })
    
    return Response(status_code=404, content="Configuration not available for this tool")

@router.post("/config/{name}", response_class=HTMLResponse)
async def save_tool_config(request: Request, name: str):
    """Saves the configuration for a tool."""
    # Sanitize tool name to prevent path traversal
    name = _sanitize_tool_name(name)
    
    try:
        form_data = await request.form()
        
        if name == "SendEmail":
            smtp_server = form_data.get("smtp_server", "smtp.gmail.com")
            smtp_port = form_data.get("smtp_port", "587")
            smtp_email = form_data.get("smtp_email", "")
            smtp_password = form_data.get("smtp_password", "")
            
            # Update process environment
            os.environ.update({
                "SMTP_SERVER": smtp_server,
                "SMTP_PORT": smtp_port,
                "SMTP_EMAIL": smtp_email,
                "SMTP_PASSWORD": smtp_password,
            })
            
            # Atomic rewrite of .env.local to prevent duplicate entries
            env_path = ".env.local"
            env_vars = {}
            
            # Read existing environment variables
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and '=' in line:
                            key, val = line.split('=', 1)
                            env_vars[key] = val
            
            # Update with new values
            env_vars['SMTP_SERVER'] = smtp_server
            env_vars['SMTP_PORT'] = smtp_port
            env_vars['SMTP_EMAIL'] = smtp_email
            env_vars['SMTP_PASSWORD'] = smtp_password
            
            # Write all environment variables atomically
            with open(env_path, "w", encoding="utf-8") as f:
                for key, val in env_vars.items():
                    f.write(f"{key}={val}\n")
            
            return Response(content="<div class='text-center py-8 text-emerald-400 font-semibold'>Configuration saved successfully!</div>", status_code=200)
        
        return Response(status_code=404, content="Configuration not available for this tool")
    except Exception as e:
        return Response(status_code=400, content=f"<div class='text-center py-8 text-red-500'>Error saving configuration: {str(e)}</div>")

@router.post("/import")
async def import_tools(request: Request, file: UploadFile = File(...)):
    try:
        content = await file.read()
        filename = file.filename
        tools = load_tools()
        
        if filename.endswith(".json"):
            import_data = json.loads(content)
            
            if isinstance(import_data, dict):
                import_data = [import_data]
            
            for tool_data in import_data:
                name = tool_data.get("name")
                if not name: continue
                
                # Sanitize tool name to prevent path traversal
                try:
                    name = _sanitize_tool_name(name)
                except ValueError:
                    continue  # Skip invalid tool names
                
                # Update tools.json structure
                tools[name] = {
                    "definition": {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool_data.get("description", ""),
                            "parameters": tool_data.get("parameters", {"type": "object", "properties": {}})
                        }
                    },
                    "enabled": tool_data.get("enabled", True)
                }
                
                # Save code
                code = tool_data.get("code", "")
                with open(os.path.join(LIBRARY_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
                    f.write(code)

        elif filename.endswith(".py"):
            name = os.path.splitext(os.path.basename(filename))[0]
            # Sanitize tool name to prevent path traversal
            try:
                name = _sanitize_tool_name(name)
            except ValueError:
                return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Invalid tool name in filename"}})})
            
            code = content.decode("utf-8")
            
            if name not in tools:
                tools[name] = {
                    "definition": {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": "Imported Python Tool",
                            "parameters": {"type": "object", "properties": {}}
                        }
                    },
                    "enabled": True
                }
            
            with open(os.path.join(LIBRARY_DIR, f"{name}.py"), "w", encoding="utf-8") as f:
                f.write(code)
        else:
            return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Unsupported file type. Please upload .json or .py"}})})
                
        save_tools(tools)
        
        return await tools_list(request)
    except Exception as e:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Import failed: {str(e)}"}})})

