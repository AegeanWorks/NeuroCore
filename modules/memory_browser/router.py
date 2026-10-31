from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
import html
import json
import logging
from modules.memory.backend import memory_store
from datetime import datetime
import asyncio
from functools import partial
from core.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

def format_timestamp(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')

templates.env.filters["datetime"] = format_timestamp

@router.get("", response_class=HTMLResponse)
async def browser_page(request: Request):
    module_manager = request.app.state.module_manager
    enabled_modules = [m for m in module_manager.get_all_modules() if m.get("enabled")]
    
    return templates.TemplateResponse(request, "index.html", {
        "modules": enabled_modules,
        "active_module": "memory_browser",
        "settings": settings.settings,
        "full_width_content": True
    })

@router.get("/gui", response_class=HTMLResponse)
async def browser_gui(request: Request):
    loop = asyncio.get_running_loop()
    
    def fetch():
        return memory_store.browse(limit=50)
    
    memories = await loop.run_in_executor(None, fetch)
    return templates.TemplateResponse(request, "memory_browser.html", {
        "memories": memories
    })

@router.get("/list", response_class=HTMLResponse)
async def list_memories(
    request: Request, 
    q: str = Query(None),
    filter_date: str = Query("ALL"),
    filter_type: str = Query("")
):
    try:
        loop = asyncio.get_running_loop()
        
        def fetch_memories():
            search_text = str(q) if q else None
            mem_type = str(filter_type) if filter_type else None
            fdate = str(filter_date) if filter_date else "ALL"
            return memory_store.browse(search_text=search_text, filter_date=fdate, mem_type=mem_type, limit=50)
        
        memories = await loop.run_in_executor(None, fetch_memories)
        
        if memories is None:
            memories = []
            
        return templates.TemplateResponse(request, "memory_list.html", {"memories": memories})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(content=f"<div class='p-4 text-red-400 italic'>Error loading memories: {html.escape(str(e))}</div>", status_code=500)

@router.delete("/delete/{memory_id}")
async def delete_memory(request: Request, memory_id: int):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(memory_store.executor, partial(memory_store.delete_entry, memory_id))
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "info", "message": "Memory deleted"}})})
    except Exception as e:
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Delete failed"}})})

@router.post("/boost/{memory_id}")
async def boost_memory(request: Request, memory_id: int, boost: int = 1):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(memory_store.executor, partial(memory_store.boost_importance, memory_id, boost))
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Memory boosted"}, "memoryRefresh": None})})
    except Exception as e:
        logger.error(f"[Boost Error] {e}")
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Boost failed"}})})

@router.post("/unboost/{memory_id}")
async def unboost_memory(request: Request, memory_id: int):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(memory_store.executor, partial(memory_store.boost_importance, memory_id, -1))
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "info", "message": "Boost removed"}, "memoryRefresh": None})})
    except Exception as e:
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Unboost failed"}})})

@router.get("/provenance/{memory_id}", response_class=JSONResponse)
async def get_memory_provenance(request: Request, memory_id: int):
    """Return the sessions that have recalled a specific memory entry."""
    loop = asyncio.get_running_loop()
    sessions = await loop.run_in_executor(
        memory_store.executor,
        partial(memory_store.get_memory_sessions, memory_id),
    )
    return JSONResponse(content={"memory_id": memory_id, "sessions": sessions})

@router.get("/session/{session_id}", response_class=JSONResponse)
async def get_session_provenance(request: Request, session_id: str):
    """Return all memories recalled in a specific session."""
    loop = asyncio.get_running_loop()
    memories = await loop.run_in_executor(
        memory_store.executor,
        partial(memory_store.get_session_memories, session_id),
    )
    return JSONResponse(content={"session_id": session_id, "memories": memories})
