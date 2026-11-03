import asyncio
import os
import sys

# Runtime state is stored with relative paths throughout the app. Anchor every launch to
# the project folder so starting the app from a parent directory cannot scatter files there.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from core.debug import debug_logger
from core.flow_manager import flow_manager
from core.flow_runner import FlowRunner
from core.llm import close_shared_client
from core.module_manager import ModuleManager
from core.observability import (
    instrument_flow_runner,
    instrument_llm_calls,
    instrument_tools,
)
from core.routers import router as core_router
from core.security import (
    bind_host,
    bind_port,
    install_security_middleware,
    reload_enabled,
    validate_deployment_security,
)
from core.settings import settings


async def verify_debug_key(x_debug_key: str = Header(...)):
    """Verify debug API key for protected debug endpoints."""
    import hmac as _hmac

    debug_key = settings.get('debug_api_key')
    # L-9: constant-time comparison so the key check does not leak prefix length.
    if not debug_key or not _hmac.compare_digest(str(x_debug_key), str(debug_key)):
        raise HTTPException(status_code=403, detail="Invalid or missing debug API key")
    return x_debug_key


def _fire_repeater_nodes(app, flow_id: str, flow: dict, is_auto_start: bool = False):
    """
    Fire all repeater nodes in a flow.
    
    Args:
        app: FastAPI app instancef
        flow_id: The flow ID
        flow: The flow data dictionary
        is_auto_start: If True, this is an auto-start from lifespan (don't await)
    
    Returns:
        List of created tasks
    """
    background_node_types = ["repeater_node"]
    nodes = flow.get("nodes", [])
    connections = flow.get("connections", [])
    created_tasks = []
    
    # One registry of running loops per flow+node so two triggers cannot
    # run the same repeater twice (H-7 follow-up: duplicate loops double-notify).
    registry = getattr(app.state, "repeater_loops", None)
    if registry is None:
        registry = {}
        app.state.repeater_loops = registry

    for node in nodes:
        if node.get("nodeTypeId") in background_node_types:
            # H-7: repeaters with no incoming edges are the triggers (sources).
            # The default flow's Repeater (node-12, feeding the Calendar
            # Watcher) has only outgoing edges and was never started.
            # Repeaters mid-flow keep their previous auto-start behaviour.
            key = (flow_id, node.get("id"))
            existing = registry.get(key)
            if existing is not None and not existing.done():
                continue
            event_type = "auto_start" if is_auto_start else "manual_trigger"
            print(f"[{'System' if is_auto_start else 'Debug'}] {'Auto-starting' if is_auto_start else 'Manually firing'} flow '{flow.get('name')}' from {node['nodeTypeId']} '{node['id']}'.")
            if settings.get("debug_mode"):
                debug_logger.log(flow_id, node['id'], node.get('name'), event_type, {})
            runner = FlowRunner(flow_id)
            task = asyncio.create_task(runner.run({"_repeat_count": 1}, start_node_id=node['id']))

            registry[key] = task

            if hasattr(app.state, "background_tasks"):
                app.state.background_tasks.add(task)
                def _cleanup(t, fid=flow_id, nid=node['id'], _app=app, _key=key):
                    background_task_callback(t, fid, nid, _app)
                    try:
                        if _app.state.repeater_loops.get(_key) is t:
                            del _app.state.repeater_loops[_key]
                    except Exception:  # hygiene: allow best-effort done-callback cleanup; entry may already be gone
                        pass
                task.add_done_callback(_cleanup)
            else:
                def _cleanup_registry(t, _app=app, _key=key):
                    try:
                        if _app.state.repeater_loops.get(_key) is t:
                            del _app.state.repeater_loops[_key]
                    except Exception:  # hygiene: allow best-effort done-callback cleanup; entry may already be gone
                        pass
                task.add_done_callback(_cleanup_registry)
            created_tasks.append(task)

    return created_tasks

def background_task_callback(task, flow_id, node_id, app):
    try:
        app.state.background_tasks.discard(task)
        task.result() # This will raise exception if task failed
        if settings.get("debug_mode"):
             debug_logger.log(flow_id, node_id, "System", "task_finished", {})
    except asyncio.CancelledError:
        print(f"[System] Task for flow {flow_id} cancelled")
    except Exception as e:
        print(f"[System] Task for flow {flow_id} failed: {e}")
        if settings.get("debug_mode"):
             debug_logger.log(flow_id, node_id, "System", "task_failed", {"error": str(e)})

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events for the application."""
    # Initialize the module manager and load enabled modules
    module_manager = ModuleManager(app=app)
    app.state.module_manager = module_manager
    module_manager.load_enabled_modules()

    # Initialize background tasks set to prevent GC of fire-and-forget tasks
    app.state.background_tasks = set()

    # M-LIFE-1: @router.on_event("startup") handlers never run when the
    # app uses a lifespan (this app does). Call module startup hooks
    # explicitly so brain auto-start and similar init actually happen.
    for mod_meta in module_manager.get_all_modules():
        if not mod_meta.get("enabled"):
            continue
        mid = mod_meta.get("id")
        try:
            if mid == "brain":
                from modules.brain.router import brain_startup
                await brain_startup()
            elif mid == "browser_automation":
                from modules.browser_automation.router import startup_event
                await startup_event()
        except Exception as exc:
            print(f"[System] Module startup hook failed for {mid}: {exc}")

    # Auto-start all active flows that contain Repeater nodes
    active_flow_ids = settings.get("active_ai_flows", [])
    for active_flow_id in active_flow_ids:
        flow = flow_manager.get_flow(active_flow_id)
        if flow:
            _fire_repeater_nodes(app, active_flow_id, flow, is_auto_start=True)

    # Initialize observability instrumentation
    instrument_flow_runner()
    instrument_llm_calls()
    instrument_tools()

    yield
    
    # Graceful shutdown: Cancel all background tasks
    print("[System] Shutting down, cancelling background tasks...")
    if hasattr(app.state, "background_tasks"):
        # Iterate over a copy to avoid mutation during iteration
        for task in list(app.state.background_tasks):
            if not task.done():
                task.cancel()
                print(f"[System] Cancelled task for flow")
        
        # Wait for tasks to complete cancellation (with timeout)
        if app.state.background_tasks:
            done, pending = await asyncio.wait(
                app.state.background_tasks, 
                timeout=5.0,
                return_when=asyncio.ALL_COMPLETED
            )
            if pending:
                print(f"[System] {len(pending)} tasks did not complete in time")
    
    # Shutdown module managers if they have cleanup methods
    # M-LIFE-1: router on_event("shutdown") handlers never run under a
    # lifespan either; call them explicitly (e.g. browser service stop).
    if hasattr(app.state, "module_manager"):
        module_manager = app.state.module_manager
        for module in module_manager.get_all_modules():
            if module.get("enabled"):
                mid = module.get("id")
                try:
                    if mid == "brain":
                        from modules.brain.router import brain_shutdown
                        await brain_shutdown()
                    elif mid == "browser_automation":
                        from modules.browser_automation.router import shutdown_event
                        await shutdown_event()
                    else:
                        mod = sys.modules.get(f"modules.{mid}")
                        if mod and hasattr(mod, 'shutdown'):
                            await mod.shutdown()
                except Exception as exc:
                    print(f"[System] Module shutdown hook failed for {mid}: {exc}")
                print(f"[System] Stopping module: {module.get('name', 'unknown')}")
    
    # Close the shared LLM client
    await close_shared_client()

    # M-PERF-2: persist batched metrics counters on shutdown.
    try:
        from core.observability import metrics as _metrics

        _metrics.flush()
    except Exception as exc:
        print(f"[System] Metrics flush failed: {exc}")

# Fail closed: a non-loopback bind requires a token or an explicit, documented opt-out.
validate_deployment_security()

app = FastAPI(title="NeuroCore", description="Modular LLM API Core", lifespan=lifespan)

# Ensure static directory exists to prevent startup errors
static_dir = os.path.join(PROJECT_ROOT, "web", "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(core_router)

# Trusted-host guard: only loopback hosts are accepted unless NEUROCORE_ALLOWED_HOSTS opts in.
install_security_middleware(app)

# Debug Endpoint to manually fire all active flows
@app.post("/debug/fire-flow", dependencies=[Depends(verify_debug_key)])
async def debug_fire_flow():
    active_flow_ids = settings.get("active_ai_flows", [])
    fired = []
    for active_flow_id in active_flow_ids:
        flow = flow_manager.get_flow(active_flow_id)
        if not flow:
            continue
        for task in _fire_repeater_nodes(app, active_flow_id, flow, is_auto_start=False):
            fired.append({
                "flow_id": active_flow_id,
                "node_id": task.get_name() if hasattr(task, 'get_name') else f"task_{id(task)}",
            })
    if fired:
        return {"status": "fired", "count": len(fired), "fired": fired}
    return {"status": "failed", "fired": []}

# --- Server Startup ---
if __name__ == "__main__":
    import uvicorn
    # Loopback by default and no auto-reload unless explicitly opted in; a remote bind or the
    # reloader must never be a silent default (see core/security.py).
    # Exclude all .json files from the reloader. This prevents the server from
    # restarting when state files (settings, flows, sessions, module configs)
    # are updated by the application itself, which fixes the module reordering issue.
    uvicorn.run("main:app", host=bind_host(), port=bind_port(), reload=reload_enabled(), reload_excludes=["*.json", "data/*.json", "data/execution_trace.jsonl", "data/*.sqlite3*", "modules/*/*.json"], loop="asyncio")

