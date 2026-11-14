from fastapi import APIRouter, Query, Request, Form
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from html import escape as html_escape
import json
import time
import asyncio
from core.errors import MemoryContentBlocked
from .backend import memory_store
from .consolidation import MemoryConsolidator, consolidation_state

# A refused write is a guardrail decision (IDEA-334), not a server error.
BLOCKED_FIX = ("remove the instruction-like text and save again, or set brain_guardrail_mode to "
               "observe if this content is trusted")

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

# Concurrency lock for consolidation to prevent parallel execution
_consolidation_lock = asyncio.Lock()


@router.get("/stats", response_class=HTMLResponse)
async def get_stats(request: Request):
    try:
        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(memory_store.executor, memory_store.get_memory_stats)
    except Exception as e:
        return f"<div class='text-red-400 text-sm p-4 border border-red-500/50 rounded-lg bg-red-900/20'>Error loading stats: {html_escape(str(e))}</div>"
    
    # Build type breakdown HTML
    type_html = ""
    types_dict = stats.get('types', {})
    for t_name, count in types_dict.items():
        type_html += f'<span class="px-2 py-0.5 bg-slate-700 text-slate-300 rounded text-[10px] font-mono">{html_escape(str(t_name))}: {int(count)}</span> '

    html = f"""
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700/50">
            <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Total Memories</div>
            <div class="text-2xl font-bold text-slate-100">{stats.get('grand_total', 0)}</div>
        </div>
        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700/50">
            <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Active</div>
            <div class="text-2xl font-bold text-emerald-400">{stats.get('total', 0)}</div>
        </div>
        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700/50">
            <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">User Origin</div>
            <div class="text-2xl font-bold text-blue-400">{stats.get('user', 0)}</div>
        </div>
        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700/50">
            <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Assistant Origin</div>
            <div class="text-2xl font-bold text-indigo-400">{stats.get('assistant', 0)}</div>
        </div>
        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700/50">
            <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Consolidated</div>
            <div class="text-2xl font-bold text-purple-400">{stats.get('consolidated', 0)}</div>
        </div>
    </div>
    <div class="flex flex-wrap gap-2 items-center">
        <span class="text-xs text-slate-500 uppercase font-bold tracking-tighter">Type Breakdown:</span>
        {type_html if type_html else '<span class="text-xs text-slate-600 italic">None</span>'}
    </div>
    """
    
    if stats.get('grand_total', 0) == 0:
        html = "<p class='text-slate-500 italic'>No memories stored yet.</p>"
        
    return html


@router.post("/settings/save")
async def save_memory_settings(
    request: Request,
    recall_limit: int = Form(None),
    recall_min_score: float = Form(None),
    save_confidence_threshold: float = Form(None),
    save_default_confidence: float = Form(None),
    save_delay: float = Form(None),
    consolidation_threshold: float = Form(None),
    auto_consolidation_hours: float = Form(None),
    arbiter_model: str = Form(None),
    arbiter_prompt: str = Form(None),
    similarity_threshold: float = Form(None),
    belief_ttl_days: int = Form(None),
    recall_access_weight: float = Form(None),
    recall_link_boost: float = Form(None),
    recall_max_total_chars: int = Form(None),
    recall_max_item_chars: int = Form(None),
    memory_scope_policy: str = Form(None),
):
    module_manager = request.app.state.module_manager
    memory_module = module_manager.modules.get("memory")
    if not memory_module:
        return Response(status_code=404)

    form = await request.form()
    config = memory_module.get("config", {}).copy()

    def _clamp_int(value, lo, hi):
        try:
            return max(lo, min(hi, int(float(value))))
        except (ValueError, TypeError):
            return None

    def _clamp_float(value, lo, hi):
        try:
            return max(lo, min(hi, float(value)))
        except (ValueError, TypeError):
            return None

    # Server-side clamps mirror the typed form min/max so a crafted POST
    # cannot store negative or unbounded budgets. Unparseable values keep
    # the stored config instead of raising 422/500.
    _int_bounds = {
        "recall_limit": (1, 20),
        "belief_ttl_days": (1, 365),
        "recall_max_total_chars": (500, 20000),
        "recall_max_item_chars": (100, 5000),
    }
    _float_bounds = {
        "recall_min_score": (0.0, 1.0),
        "save_confidence_threshold": (0.0, 1.0),
        "save_default_confidence": (0.0, 1.0),
        "save_delay": (0.0, 60.0),
        "consolidation_threshold": (0.5, 1.0),
        "auto_consolidation_hours": (0.0, 720.0),
        "similarity_threshold": (0.0, 1.0),
        "recall_access_weight": (0.0, 1.0),
        "recall_link_boost": (0.0, 5.0),
    }
    for _key, (_lo, _hi) in _int_bounds.items():
        if _key in form:
            _clamped = _clamp_int(form.get(_key), _lo, _hi)
            if _clamped is not None:
                config[_key] = _clamped
    for _key, (_lo, _hi) in _float_bounds.items():
        if _key in form:
            _clamped = _clamp_float(form.get(_key), _lo, _hi)
            if _clamped is not None:
                config[_key] = _clamped
    # FastAPI Form() params above still bind when valid; keep them as a
    # fallback for clients posting typed values (form dict already handled).
    if recall_limit is not None and "recall_limit" not in form:
        config["recall_limit"] = max(1, min(20, int(recall_limit)))
    if recall_min_score is not None and "recall_min_score" not in form:
        config["recall_min_score"] = max(0.0, min(1.0, float(recall_min_score)))
    if save_confidence_threshold is not None and "save_confidence_threshold" not in form:
        config["save_confidence_threshold"] = max(0.0, min(1.0, float(save_confidence_threshold)))
    if save_default_confidence is not None and "save_default_confidence" not in form:
        config["save_default_confidence"] = max(0.0, min(1.0, float(save_default_confidence)))
    if save_delay is not None and "save_delay" not in form:
        config["save_delay"] = max(0.0, min(60.0, float(save_delay)))
    if consolidation_threshold is not None and "consolidation_threshold" not in form:
        config["consolidation_threshold"] = max(0.5, min(1.0, float(consolidation_threshold)))
    if auto_consolidation_hours is not None and "auto_consolidation_hours" not in form:
        config["auto_consolidation_hours"] = max(0.0, min(720.0, float(auto_consolidation_hours)))
    if arbiter_model is not None: config["arbiter_model"] = arbiter_model
    if arbiter_prompt is not None: config["arbiter_prompt"] = arbiter_prompt
    if similarity_threshold is not None and "similarity_threshold" not in form:
        config["similarity_threshold"] = max(0.0, min(1.0, float(similarity_threshold)))
    if belief_ttl_days is not None and "belief_ttl_days" not in form:
        config["belief_ttl_days"] = max(1, min(365, int(belief_ttl_days)))
    if recall_access_weight is not None and "recall_access_weight" not in form:
        config["recall_access_weight"] = max(0.0, min(1.0, float(recall_access_weight)))
    if recall_link_boost is not None and "recall_link_boost" not in form:
        config["recall_link_boost"] = max(0.0, min(5.0, float(recall_link_boost)))
    if recall_max_total_chars is not None and "recall_max_total_chars" not in form:
        config["recall_max_total_chars"] = max(500, min(20000, int(recall_max_total_chars)))
    if recall_max_item_chars is not None and "recall_max_item_chars" not in form:
        config["recall_max_item_chars"] = max(100, min(5000, int(recall_max_item_chars)))
    if memory_scope_policy in ("strict", "shared"): config["memory_scope_policy"] = memory_scope_policy
    # Checkbox posts "true" when checked; hidden marker distinguishes
    # an unchecked box (disable) from a form that omits the field.
    if "memory_scope_required" in form or "save_memory_scope_required" in form:
        config["memory_scope_required"] = form.get("memory_scope_required") == "true"
    
    module_manager.update_module_config("memory", config)
    
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Memory settings saved"}})})


@router.get("/consolidation/status")
async def consolidation_status(request: Request):
    return JSONResponse(content=consolidation_state.to_dict())


@router.post("/consolidate")
async def consolidate_memories(request: Request):
    # Check if consolidation is already running
    if _consolidation_lock.locked():
        return Response(
            status_code=409,
            headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Consolidation already in progress"}})}
        )

    async with _consolidation_lock:
        module_manager = request.app.state.module_manager
        memory_module = module_manager.modules.get("memory")
        config = memory_module.get("config", {}) if memory_module else {}

        consolidator = MemoryConsolidator(config=config)
        count = await consolidator.run()
        memory_store.last_consolidation_ts = time.time()

        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": f"Consolidated {count} redundant memories"}})})


@router.post("/wipe")
async def wipe_memories(request: Request, confirm: str = None):
    from core.confirmations import require_confirmation

    require_confirmation(confirm, "wipe all memories", "wiping every memory")
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(memory_store.executor, memory_store.wipe_all)
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"memoryWiped": None, "showMessage": {"level": "success", "message": "All memories wiped successfully"}})})
    except Exception as e:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to wipe memories: {str(e)}"}})})


@router.post("/update")
async def update_memory(request: Request, memory_id: int = Form(...), text: str = Form(None), mem_type: str = Form(None), verified: bool = Form(None)):
    try:
        loop = asyncio.get_running_loop()
        new_id = await loop.run_in_executor(
            memory_store.executor, 
            lambda: memory_store.update_entry(memory_id, text=text, mem_type=mem_type, verified=verified)
        )
        if new_id:
            return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": f"Memory updated (new ID: {new_id})"}})})
        return Response(status_code=404, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Memory not found"}})})
    except MemoryContentBlocked as e:
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"{e}. {BLOCKED_FIX}"}})})
    except Exception as e:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to update memory: {str(e)}"}})})


@router.get("/{memory_id}/history")
async def memory_history(memory_id: int, limit: int = 20):
    """Bounded edit history for one memory, with both sides of each change (IDEA-1175)."""
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(
        memory_store.executor, lambda: memory_store.get_edit_history(memory_id, limit)
    )
    return JSONResponse(payload)


@router.post("/edits/{edit_id}/undo")
async def undo_memory_edit(edit_id: str, confirm: str = Query(None)):
    """Restore one edit only while its head and content hash still match."""
    loop = asyncio.get_running_loop()
    try:
        payload = await loop.run_in_executor(
            memory_store.executor, lambda: memory_store.undo_edit(edit_id, confirm)
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except Exception as exc:
        return JSONResponse(
            {"error": f"Failed to undo edit: {exc}"}, status_code=500
        )
    return JSONResponse(payload)


@router.post("/delete")
async def delete_memory(request: Request, memory_id: int = Form(...), reason: str = Form(None)):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(memory_store.executor, lambda: memory_store.delete_entry(memory_id, reason=reason))
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"memoryDeleted": str(memory_id), "showMessage": {"level": "success", "message": f"Memory {memory_id} deleted"}})})
    except Exception as e:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to delete memory: {str(e)}"}})})


@router.post("/merge")
async def merge_memories(request: Request, memory_ids: str = Form(...), new_text: str = Form(...), new_type: str = Form("BELIEF"), new_verified: bool = Form(False)):
    try:
        ids = json.loads(memory_ids)
        if not isinstance(ids, list) or len(ids) < 2:
            return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "At least 2 memory IDs required"}})})
        
        loop = asyncio.get_running_loop()
        new_id = await loop.run_in_executor(
            memory_store.executor, 
            lambda: memory_store.merge_memories(ids, new_text, new_type, new_verified)
        )
        if new_id:
            return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": f"Memories merged into new ID: {new_id}"}})})
        return Response(status_code=400, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": "Failed to merge memories"}})})
    except Exception as e:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Failed to merge memories: {str(e)}"}})})


@router.get("/conflicts")
async def get_conflicts(request: Request, memory_ids: str = None):
    try:
        ids = json.loads(memory_ids) if memory_ids else None
        loop = asyncio.get_running_loop()
        conflicts = await loop.run_in_executor(memory_store.executor, lambda: memory_store.find_conflicts(ids) if ids else memory_store.find_conflicts())
        return {"conflicts": conflicts}
    except Exception as e:
        return {"conflicts": [], "error": str(e)}


@router.post("/conflicts/scan")
async def scan_conflicts(request: Request, memory_ids: str = None):
    try:
        ids = json.loads(memory_ids) if memory_ids else None
        loop = asyncio.get_running_loop()
        conflicts = await loop.run_in_executor(memory_store.executor, lambda: memory_store.find_conflicts(ids) if ids else memory_store.find_conflicts())
        return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": f"Found {len(conflicts)} conflicts"}})})
    except Exception as e:
        return Response(status_code=500, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "error", "message": f"Conflict scan failed: {str(e)}"}})})


@router.get("/meta")
async def get_meta_memories(request: Request, limit: int = 50, offset: int = 0):
    try:
        loop = asyncio.get_running_loop()
        meta = await loop.run_in_executor(memory_store.executor, lambda: memory_store.get_meta_memories(limit, offset))
        return {"meta_memories": meta}
    except Exception as e:
        return {"meta_memories": [], "error": str(e)}


@router.get("/goals")
async def get_goals(request: Request, status: str = None, limit: int = 50, offset: int = 0):
    try:
        loop = asyncio.get_running_loop()
        goals = await loop.run_in_executor(memory_store.executor, lambda: memory_store.get_goals(status, limit, offset))
        return {"goals": goals}
    except Exception as e:
        return {"goals": [], "error": str(e)}


# IMPORTANT: /goals/next must be registered BEFORE /goals/{goal_id}
# Otherwise FastAPI will match "next" as a goal_id and fail with type validation error
@router.get("/goals/next")
async def get_next_goal(request: Request):
    try:
        loop = asyncio.get_running_loop()
        goal = await loop.run_in_executor(memory_store.executor, memory_store.get_next_goal)
        return {"goal": goal}
    except Exception as e:
        return {"goal": None, "error": str(e)}


@router.get("/goals/{goal_id}")
async def get_goal(request: Request, goal_id: int):
    try:
        loop = asyncio.get_running_loop()
        goal = await loop.run_in_executor(memory_store.executor, lambda: memory_store.get_goal(goal_id))
        return {"goal": goal} if goal else Response(status_code=404)
    except Exception as e:
        return {"goal": None, "error": str(e)}


@router.post("/goals")
async def create_goal(request: Request, description: str = Form(...), priority: int = Form(0), deadline: int = Form(None)):
    try:
        loop = asyncio.get_running_loop()
        goal_id = await loop.run_in_executor(memory_store.executor, lambda: memory_store.create_goal(description, priority, deadline))
        return {"goal_id": goal_id, "message": "Goal created"}
    except MemoryContentBlocked as e:
        return JSONResponse(status_code=400, content={"error": f"Why: {e}. Fix: {BLOCKED_FIX}"})
    except Exception as e:
        # FIX: Use JSONResponse instead of tuple return (FastAPI doesn't support Flask-style tuple returns)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.put("/goals/{goal_id}")
async def update_goal(request: Request, goal_id: int, description: str = Form(None), priority: int = Form(None), status: str = Form(None), context: str = Form(None), deadline: int = Form(None)):
    try:
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(memory_store.executor, lambda: memory_store.update_goal(goal_id, description, priority, status, context, deadline))
        return {"success": success}
    except MemoryContentBlocked as e:
        return JSONResponse(status_code=400, content={"error": f"Why: {e}. Fix: {BLOCKED_FIX}"})
    except Exception as e:
        # FIX: Use JSONResponse instead of tuple return
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.delete("/goals/{goal_id}")
async def delete_goal(request: Request, goal_id: int):
    try:
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(memory_store.executor, lambda: memory_store.delete_goal(goal_id))
        return {"success": success}
    except Exception as e:
        # FIX: Use JSONResponse instead of tuple return
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/goals/{goal_id}/complete")
async def complete_goal(request: Request, goal_id: int):
    try:
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(memory_store.executor, lambda: memory_store.complete_goal(goal_id))
        return {"success": success}
    except Exception as e:
        # FIX: Use JSONResponse instead of tuple return
        return JSONResponse(status_code=500, content={"error": str(e)})



@router.get("/links/{memory_id}")
async def list_memory_links(memory_id: int, relation: str = None, status: str = None):
    loop = asyncio.get_running_loop()
    links = await loop.run_in_executor(
        memory_store.executor,
        lambda: memory_store.get_links(memory_id, relation=relation, status=status),
    )
    confidence = await loop.run_in_executor(
        memory_store.executor, lambda: memory_store.bayesian_confidence(memory_id)
    )
    return {"memory_id": memory_id, "links": links, "evidence": confidence}


@router.post("/links")
async def add_memory_link(request: Request):
    data = await request.json()
    try:
        link = memory_store.add_link(
            int(data["source_id"]), int(data["target_id"]),
            relation=str(data.get("relation", "supports")),
            evidence=str(data.get("evidence", "")),
            status=str(data.get("status", "accepted")),
            created_by=str(data.get("created_by", "operator")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return link


@router.post("/links/{link_id}/accept")
async def accept_memory_link(link_id: int):
    loop = asyncio.get_running_loop()
    link = await loop.run_in_executor(
        memory_store.executor, lambda: memory_store.accept_link(link_id)
    )
    if link is None:
        return JSONResponse(status_code=404, content={"detail": "Link not found"})
    return link


@router.delete("/links/{link_id}")
async def delete_memory_link(link_id: int):
    loop = asyncio.get_running_loop()
    deleted = await loop.run_in_executor(
        memory_store.executor, lambda: memory_store.delete_link(link_id)
    )
    return {"deleted": bool(deleted)}


@router.get("/contradictions")
async def contradiction_candidates(limit: int = 50):
    loop = asyncio.get_running_loop()
    candidates = await loop.run_in_executor(
        memory_store.executor, lambda: memory_store.find_conflicts(limit=limit)
    )
    return {"candidates": candidates}


@router.post("/contradictions/scan")
async def scan_contradictions(request: Request):
    try:
        data = await request.json()
    except (TypeError, ValueError):
        data = {}
    limit = max(1, min(int((data or {}).get("limit", 20)), 100))
    loop = asyncio.get_running_loop()
    proposals = await loop.run_in_executor(
        memory_store.executor, lambda: memory_store.suggest_contradictions(limit=limit)
    )
    return {"created": len(proposals), "proposals": proposals}