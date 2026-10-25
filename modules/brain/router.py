"""HTTP surface for the AI-Brain substrate and evaluation harness."""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.dependencies import get_module_manager

from .auth import BrainActor, get_actor_registry, get_brain_actor, require_role
from .configuration import (
    ConfigurationPolicyError,
    ConfigurationQuotaError,
)
from .improvement import QuotaExceededError
from .kernel import KernelBlockedError
from .service import (
    brain_auto_start_enabled,
    get_brain_service,
    get_existing_brain_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_brain_actor)])


@router.on_event("startup")
async def brain_startup():
    if brain_auto_start_enabled():
        await get_brain_service().start()


@router.on_event("shutdown")
async def brain_shutdown():
    service = get_existing_brain_service()
    if service is not None:
        await service.stop()


@router.get("/ui", response_class=HTMLResponse)
async def brain_ui(request: Request):
    from core.routers import templates
    from core.settings import settings

    module_manager = getattr(request.app.state, "module_manager", None)
    modules = module_manager.get_all_modules() if module_manager else []
    return templates.TemplateResponse(request, "brain.html", {
        "modules": modules,
        "settings": settings.settings,
        "active_module": "brain",
    })


@router.get("/actor")
async def brain_actor(actor: BrainActor = Depends(get_brain_actor)):
    return {
        "name": actor.name,
        "role": actor.role,
        "authenticated": actor.authenticated,
    }


@router.get("/actors")
async def brain_actors(_actor: BrainActor = Depends(require_role("admin"))):
    return {"actors": get_actor_registry().list()}


@router.post("/actors")
async def brain_actor_create(body: dict = Body(...),
                             _actor: BrainActor = Depends(require_role("admin"))):
    name = str(body.get("name", ""))
    role = str(body.get("role", "operator"))
    token = body.get("token")
    try:
        return get_actor_registry().create(
            name, role=role, token=str(token) if token else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/actors/{name}/rotate")
async def brain_actor_rotate(name: str,
                             _actor: BrainActor = Depends(require_role("admin"))):
    try:
        return get_actor_registry().rotate(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/actors/{name}/disable")
async def brain_actor_disable(name: str,
                              _actor: BrainActor = Depends(require_role("admin"))):
    try:
        return get_actor_registry().disable(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/actors/{name}")
async def brain_actor_delete(name: str,
                             _actor: BrainActor = Depends(require_role("admin"))):
    return {"deleted": get_actor_registry().delete(name), "name": name}


@router.get("/state")
async def brain_state():
    state = await get_brain_service().snapshot()
    return {"state": state, "self": state.get("last_snapshot", {})}


@router.get("/events")
async def brain_events(limit: int = 50):
    return {"events": get_brain_service().events(limit=max(0, min(limit, 200)))}


@router.post("/events")
async def brain_publish_event(body: dict = Body(default_factory=dict),
                              actor: BrainActor = Depends(require_role("operator"))):
    service = get_brain_service()
    event = await service.publish_event(
        event_type=str(body.get("type") or "manual"),
        source=str(actor.name),
        payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        salience=float(body.get("salience", 0.5)),
        dedupe_key=body.get("dedupe_key"),
    )
    if event is None:
        return {"deduplicated": True}
    return event


@router.post("/start")
async def brain_start(_actor: BrainActor = Depends(require_role("admin"))):
    return await get_brain_service().start()


@router.post("/stop")
async def brain_stop(_actor: BrainActor = Depends(require_role("admin"))):
    return await get_brain_service().stop()


@router.post("/cycle")
async def brain_cycle(_actor: BrainActor = Depends(require_role("operator"))):
    try:
        return await get_brain_service().run_cycle(trigger="api")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/mode")
async def brain_mode(body: dict = Body(...),
                     _actor: BrainActor = Depends(require_role("admin"))):
    try:
        return await get_brain_service().set_mode(str(body.get("mode", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/proposals")
async def brain_proposals():
    state = await get_brain_service().snapshot()
    return {"proposals": state.get("proposals", [])}


@router.get("/eval/suite")
async def brain_eval_suite():
    from .evaluation import DEFAULT_TASKS, SUITE_NAME

    return {
        "suite": SUITE_NAME,
        "tasks": [
            {"id": task.id, "description": task.description, "tags": task.tags}
            for task in DEFAULT_TASKS
        ],
    }


@router.post("/eval/run")
async def brain_eval_run(body: dict = Body(default_factory=dict),
                         _actor: BrainActor = Depends(require_role("operator"))):
    flow_id: Optional[str] = body.get("flow_id") or None
    try:
        return await get_brain_service().run_evaluation(flow_id=flow_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/eval/baseline")
async def brain_eval_baseline(actor: BrainActor = Depends(require_role("admin"))):
    try:
        return await get_brain_service().promote_baseline(approved_by=actor.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/eval/status")
async def brain_eval_status():
    return get_brain_service().evaluation_status()


@router.get("/budget/status")
async def brain_budget_status():
    return get_brain_service().self_budget_status()


@router.get("/learn/status")
async def brain_learn_status():
    return get_brain_service().learning_status()


@router.get("/learn/experiences")
async def brain_learn_experiences(limit: int = 50):
    service = get_brain_service()
    return {"experiences": service.learning.experiences_page(limit=limit)}


@router.post("/learn")
async def brain_learn(body: dict = Body(...),
                      actor: BrainActor = Depends(require_role("operator"))):
    service = get_brain_service()
    if not body.get("action"):
        raise HTTPException(status_code=400, detail="action is required")
    try:
        return await service.record_outcome(
            action=str(body["action"]),
            input_data=body.get("input", {}),
            output=body.get("output", ""),
            success=bool(body.get("success", False)),
            error=body.get("error"),
            latency_ms=float(body.get("latency_ms", 0.0)),
            tags=(body.get("tags") if isinstance(body.get("tags"), list) else [])
            + [f"actor:{actor.name}"],
            trace_id=body.get("trace_id"),
        )
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except SelfBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/learn/consolidate")
async def brain_learn_consolidate(_actor: BrainActor = Depends(require_role("operator"))):
    try:
        return await get_brain_service().consolidate_learning()
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


@router.post("/learn/recall")
async def brain_learn_recall(body: dict = Body(default_factory=dict)):
    return await get_brain_service().evaluate_learning_recall(
        top_k=max(1, min(int(body.get("top_k", 3)), 20))
    )


@router.post("/learn/suggest")
async def brain_learn_suggest(body: dict = Body(...)):
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    return get_brain_service().suggest_learning(
        query, top_k=max(1, min(int(body.get("top_k", 3)), 20))
    )


@router.get("/improve/status")
async def brain_improve_status():
    return get_brain_service().improvement_status()


@router.post("/improve/candidates")
async def brain_improve_candidate(body: dict = Body(...),
                                  actor: BrainActor = Depends(require_role("admin"))):
    flow_id = body.get("flow_id")
    operations = body.get("operations")
    if not flow_id or not isinstance(operations, list):
        raise HTTPException(
            status_code=400, detail="flow_id and a non-empty operations list are required"
        )
    try:
        return await get_brain_service().propose_improvement(
            str(flow_id), operations,
            description=str(body.get("description", "")),
            source=f"actor:{actor.name}",
            goal_id=body.get("goal_id"),
            goal_kind=str(body.get("goal_kind", "goal")),
        )
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except SelfBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/improve/generate")
async def brain_improve_generate(body: dict = Body(...),
                                 _actor: BrainActor = Depends(require_role("admin"))):
    flow_id = body.get("flow_id")
    instruction = body.get("instruction")
    if not flow_id or not instruction:
        raise HTTPException(status_code=400, detail="flow_id and instruction are required")
    try:
        return await get_brain_service().generate_improvement(
            str(flow_id), str(instruction),
            goal_id=body.get("goal_id"),
            goal_kind=str(body.get("goal_kind", "goal")),
        )
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except SelfBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/improve/evaluate")
async def brain_improve_evaluate(body: dict = Body(...),
                                 actor: BrainActor = Depends(require_role("admin"))):
    candidate_id = body.get("candidate_id")
    if not candidate_id:
        raise HTTPException(status_code=400, detail="candidate_id is required")
    try:
        return await get_brain_service().evaluate_improvement(
            str(candidate_id), approve=bool(body.get("approve", False)),
            approved_by=actor.name,
        )
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except SelfBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/improve/rollback")
async def brain_improve_rollback(body: dict = Body(...),
                                 _actor: BrainActor = Depends(require_role("admin"))):
    candidate_id = body.get("candidate_id")
    if not candidate_id:
        raise HTTPException(status_code=400, detail="candidate_id is required")
    try:
        return await get_brain_service().rollback_improvement(str(candidate_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/config/status")
async def brain_config_status():
    return get_brain_service().configuration_status()


@router.get("/kernel")
async def brain_kernel_status():
    return get_brain_service().kernel_status()


@router.post("/kernel/kill")
async def brain_kernel_kill(body: dict = Body(...),
                            actor: BrainActor = Depends(require_role("admin"))):
    try:
        return get_brain_service().engage_kill_switch(
            reason=str(body.get("reason", "manual")),
            approved_by=actor.name,
        )
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


@router.post("/kernel/reset")
async def brain_kernel_reset(actor: BrainActor = Depends(require_role("admin"))):
    try:
        return get_brain_service().reset_kernel(actor.name)
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


@router.get("/guardrails/status")
async def brain_guardrails_status():
    from .guardrails import guardrail_stats

    return guardrail_stats().snapshot()


@router.post("/guardrails/scan")
async def brain_guardrails_scan(body: dict = Body(...),
                                _actor: BrainActor = Depends(require_role("operator"))):
    from .guardrails import MODE_ENFORCE, scan_text

    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text is required")
    surface = str(body.get("surface") or "input")
    if surface not in {"input", "output"}:
        raise HTTPException(status_code=400, detail="surface must be input or output")
    mode = MODE_ENFORCE if body.get("enforce") is True else None
    verdict = scan_text(text, surface, mode=mode,
                        system_prompt=body.get("system_prompt") if surface == "output" else None)
    return verdict.to_dict()


@router.post("/drift/baseline")
async def brain_drift_baseline(actor: BrainActor = Depends(require_role("admin"))):
    try:
        return await get_brain_service().set_drift_baseline(actor.name)
    except (KernelBlockedError, ValueError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


@router.get("/reactions")
async def brain_reactions():
    return get_brain_service().reactivity_status()


@router.post("/reactions/dismiss")
async def brain_reaction_dismiss(body: dict = Body(...),
                                 _actor: BrainActor = Depends(require_role("operator"))):
    reaction_id = body.get("reaction_id")
    if not reaction_id:
        raise HTTPException(status_code=400, detail="reaction_id is required")
    try:
        return get_brain_service().dismiss_reaction(str(reaction_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reactions/mute")
async def brain_reaction_mute(body: dict = Body(...),
                              _actor: BrainActor = Depends(require_role("operator"))):
    key = body.get("key")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    try:
        return get_brain_service().mute_reaction(
            str(key), minutes=int(body.get("minutes", 60))
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reactions/unmute")
async def brain_reaction_unmute(body: dict = Body(...),
                                _actor: BrainActor = Depends(require_role("operator"))):
    key = body.get("key")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    return get_brain_service().unmute_reaction(str(key))


@router.post("/react/start")
async def brain_react_start(_actor: BrainActor = Depends(require_role("operator"))):
    return await get_brain_service().start_reactivity()


@router.post("/react/stop")
async def brain_react_stop(_actor: BrainActor = Depends(require_role("operator"))):
    return await get_brain_service().stop_reactivity()


@router.post("/react/poll")
async def brain_react_poll(_actor: BrainActor = Depends(require_role("operator"))):
    return await get_brain_service().poll_reactivity()


@router.post("/config/preview")
async def brain_config_preview(body: dict = Body(...),
                               module_manager=Depends(get_module_manager)):
    try:
        return get_brain_service().preview_configuration(
            body, module_manager=module_manager
        )
    except ConfigurationPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/config/apply")
async def brain_config_apply(body: dict = Body(...),
                             module_manager=Depends(get_module_manager),
                             actor: BrainActor = Depends(require_role("admin"))):
    operation = body.get("operation")
    if not isinstance(operation, dict):
        raise HTTPException(status_code=400, detail="operation object is required")
    operation = dict(operation)
    for key in ("goal_id", "goal_kind", "reason"):
        if body.get(key) is not None and key not in operation:
            operation[key] = body[key]
    try:
        return get_brain_service().apply_configuration(
            operation,
            approved_by=actor.name,
            module_manager=module_manager,
        )
    except KernelBlockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except SelfBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ConfigurationQuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ConfigurationPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/config/rollback")
async def brain_config_rollback(body: dict = Body(...),
                                module_manager=Depends(get_module_manager),
                                actor: BrainActor = Depends(require_role("admin"))):
    change_id = body.get("change_id")
    if not change_id:
        raise HTTPException(status_code=400, detail="change_id is required")
    try:
        return get_brain_service().rollback_configuration(
            str(change_id),
            approved_by=actor.name,
            module_manager=module_manager,
        )
    except ConfigurationPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
