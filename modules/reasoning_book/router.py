import ast
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from core.dependencies import get_module_manager, get_settings_manager
from .service import service

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

logger = logging.getLogger(__name__)

def format_reasoning_content(content):
    """Extracts the actual content from a raw LLM response dictionary string."""
    if isinstance(content, str) and content.strip().startswith("{") and "'choices':" in content:
        try:
            # Attempt to parse stringified dict
            data = ast.literal_eval(content)
            if isinstance(data, dict):
                # Check for OpenAI format
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    if "content" in message:
                        return message["content"]
        except (ValueError, SyntaxError, KeyError, IndexError) as e:
            # ValueError/SyntaxError: ast.literal_eval failed to parse
            # KeyError/IndexError: Missing expected keys in parsed structure
            logger.debug(f"Failed to parse reasoning content: {e}")
            pass
    return content

templates.env.filters["format_reasoning"] = format_reasoning_content

@router.get("/", response_class=HTMLResponse)
async def reasoning_page(request: Request, module_manager=Depends(get_module_manager), settings_man=Depends(get_settings_manager)):
    """Serves the full page shell with the reasoning book active."""
    return templates.TemplateResponse(request, "index.html", {
        "modules": module_manager.get_all_modules(),
        "active_module": "reasoning_book",
        "settings": settings_man.settings,
        "full_width_content": True
    })

@router.get("/gui", response_class=HTMLResponse)
async def reasoning_gui(request: Request):
    """Serves the inner content of the reasoning book."""
    return templates.TemplateResponse(request, "reasoning_book.html", {
        "thoughts": service.get_thoughts()
    })

@router.get("/thoughts", response_class=HTMLResponse)
async def get_thoughts(request: Request):
    """Returns just the list of thoughts for polling."""
    return templates.TemplateResponse(request, "reasoning_thoughts.html", {
        "thoughts": service.get_thoughts()
    })

@router.post("/clear")
async def clear_thoughts(request: Request, confirm: str = None):
    from core.confirmations import require_confirmation

    require_confirmation(confirm, "wipe the reasoning book", "wiping the reasoning book")
    await service.clear()
    return templates.TemplateResponse(request, "reasoning_thoughts.html", {
        "thoughts": []
    })
