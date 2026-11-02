from fastapi import APIRouter, Request, Form, Depends, Query, UploadFile, File, Body, HTTPException
from fastapi.responses import HTMLResponse, Response
from modules.brain.auth import BrainActor, require_role
import base64
import time
from core.settings import settings
from core.dependencies import get_llm_bridge
from core.llm import LLMBridge
from modules.chat.sessions import session_manager, _estimate_tokens
from modules.brain.guardrails import scan_input, scan_output
from fastapi.templating import Jinja2Templates
from core.flow_runner import FlowRunner
from core.flow_manager import flow_manager
import json
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

# A chat message starting with this word is answered by the brain conversation instead of the flow.
BRAIN_PREFIX = "/brain"

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

# Global dict of active streaming queues, keyed by (session_id, msg_id).
# M-ASYNC-3: one queue per session meant a second send replaced the first
# turn's queue and the first task's cleanup deleted the second one's.
active_streams = {}
# Last background failure per (session, message), so a late reader still sees it
last_stream_errors = {}


def _stream_key(session_id: str, msg_id) -> tuple:
    return (session_id, msg_id)


def _latest_stream_for(session_id: str):
    """Newest queue for a session (back-compat when no msg_id is given)."""
    best = None
    for (sid, mid), queue in active_streams.items():
        try:
            newer = best is None or mid > best[0]
        except TypeError:
            newer = best is None
        if sid == session_id and newer:
            best = (mid, queue)
    return best[1] if best else None


def _session_has_stream(session_id: str) -> bool:
    return any(sid == session_id for sid, _ in active_streams)


def _register_stream(key, queue, cap: int = 50) -> None:
    """Register a per-message stream queue with bounded retention.

    Queues are removed when their stream response closes (see
    ``event_generator``'s finally block). Producer completion must NOT pop
    the queue: items stay queued until a (possibly late) reader consumes
    them. The cap evicts the oldest entries so never-opened streams cannot
    grow without bound.
    """
    active_streams[key] = queue
    while len(active_streams) > cap:
        try:
            oldest = next(iter(active_streams))
        except StopIteration:
            break
        if oldest == key:
            break
        active_streams.pop(oldest, None)
        last_stream_errors.pop(oldest, None)
# M-ASYNC-2: keep strong refs to background chat tasks; asyncio holds tasks
# weakly, so an unreferenced turn can be GC'd mid-flight.
background_chat_tasks: set = set()


def _module_meta(request: Request, module_id: str) -> dict:
    """One module manifest without importing the module (the rail works while it is off)."""
    manager = getattr(request.app.state, "module_manager", None)
    modules = getattr(manager, "modules", None) or {}
    meta = modules.get(module_id)
    return meta if isinstance(meta, dict) else {}


def _brain_prompt(message) -> Optional[str]:
    """The text after a leading /brain, or None when the message is ordinary chat."""
    if not isinstance(message, str):
        return None
    text = message.strip()
    if text.lower() == BRAIN_PREFIX:
        return ""
    if text.lower().startswith(BRAIN_PREFIX + " "):
        return text[len(BRAIN_PREFIX):].strip()
    return None


def _without_current_turn(history: list, message: str) -> list:
    """The session history before the user message the caller just appended."""
    if history and history[-1].get("role") == "user" and history[-1].get("content") == message:
        return history[:-1]
    return history


async def _brain_flow_context(request: Request) -> str:
    """A compact brain-state block for an ordinary flow turn, when the brain is enabled."""
    brain = _module_meta(request, "brain")
    if not brain or not brain.get("enabled"):
        return ""
    config = brain.get("config") if isinstance(brain.get("config"), dict) else {}
    if not config.get("inject_flow_context", True):
        return ""
    try:
        from modules.brain import conversation
        from modules.brain.service import get_brain_service

        service = get_brain_service()
        return conversation.state_context(await service.snapshot())
    except Exception as exc:
        logger.warning("Could not build the brain flow context: %s", exc)
        return ""


async def _stream_brain_notice(session_id: str, text: str, queue: asyncio.Queue) -> None:
    """Persist and stream a brain notice that needs no model call."""
    try:
        await asyncio.to_thread(
            session_manager.add_message, session_id, "assistant", text, source="brain"
        )
        await queue.put({"type": "replace", "content": text})
    finally:
        await queue.put(None)


async def _run_brain_turn(session_id: str, message: str, prompt: str, llm: LLMBridge,
                          queue: asyncio.Queue) -> None:
    """Answer one /brain turn in the chat session and stream its result."""
    from modules.brain import conversation as brain_conversation
    from modules.brain.service import get_brain_service

    try:
        service = get_brain_service()
        session = await asyncio.to_thread(session_manager.get_session, session_id)
        history = _without_current_turn((session or {}).get("history", []), message)
        turns = brain_conversation.turns_from_messages(history)
        result = await brain_conversation.answer(service, llm, prompt, turns=turns)
        reply = str(result.get("reply") or "")
        commands = result.get("commands") if isinstance(result.get("commands"), list) else []
        guard = scan_output(reply)
        if guard.blocked:
            logger.warning("Guardrails replaced a brain output: %s",
                           guard.to_dict()["findings"])
            reply = ("I can't return that response because it would repeat "
                     "internal instructions.")
            commands = []
        elif guard.redactions:
            reply = guard.text
        await asyncio.to_thread(
            session_manager.add_message, session_id, "assistant", reply,
            source="brain", commands=commands,
        )
        message_index = None
        if commands:
            session_after = await asyncio.to_thread(session_manager.get_session, session_id)
            if session_after:
                message_index = len(session_after.get("history", [])) - 1
        await queue.put({"type": "replace", "content": reply})
        if commands:
            await queue.put({"type": "commands", "content": {
                "commands": commands, "message_index": message_index,
            }})
        usage = result.get("usage")
        if isinstance(usage, dict) and usage.get("total_tokens"):
            await asyncio.to_thread(session_manager.add_tokens, session_id, usage)
            await queue.put({"type": "usage", "content": usage})
    except Exception as exc:
        logger.exception("Brain chat turn failed: %s", exc)
        text = f"Error: {exc}"
        try:
            await asyncio.to_thread(
                session_manager.add_message, session_id, "assistant", text, source="brain"
            )
        except Exception:
            logger.exception("Could not persist the brain chat error")
        await queue.put({"type": "error", "content": str(exc)})
    finally:
        await queue.put(None)


def _flow_system_prompt(flow: dict) -> str:
    """Concatenated system prompts configured in a flow, bounded for leak detection."""
    parts = []
    for node in (flow or {}).get("nodes", []) or []:
        config = node.get("config") if isinstance(node, dict) else None
        if isinstance(config, dict) and isinstance(config.get("system_prompt"), str):
            parts.append(config["system_prompt"])
    return "\n".join(parts)[:8000]


def _extract_thinking_steps(flow_result: dict) -> list:
    """Extract intermediate agent thinking steps from a flow result.

    Returns a list of step dicts with keys:
        type: 'tool_call' | 'tool_result' | 'assistant' | 'reflection'
        content: str  (display text)
        name: str | None  (tool name for tool events)
    """
    steps = []
    messages = flow_result.get("messages", [])
    agent_loop_trace = flow_result.get("agent_loop_trace", [])

    # GoalPursuit produces a goal_pursuit_trace instead of the usual message-level trace.
    # Synthesize thinking steps directly from it so planning/evaluation are visible.
    gp_trace = flow_result.get("goal_pursuit_trace", [])
    if gp_trace:
        plan = flow_result.get("plan", [])
        completed = flow_result.get("completed_steps", [])
        total_steps = len(completed) + len(flow_result.get("remaining_steps", []))
        if plan or completed:
            all_steps = completed + flow_result.get("remaining_steps", [])
            step_lines = "\n".join(
                f"  {i + 1}. {s.get('action', '?')}: {s.get('target', '')}"
                for i, s in enumerate(all_steps[:8])
            )
            steps.append({
                "type": "tool_call",
                "name": "Plan",
                "content": f"{total_steps} steps:\n{step_lines}",
            })
        for entry in gp_trace:
            step_num = entry.get("step", "?")
            action = entry.get("action", "")
            target = entry.get("target", "")
            steps.append({
                "type": "tool_call",
                "name": f"Step {step_num}",
                "content": f"{action}: {target}",
            })
            evaluation = entry.get("evaluation", {})
            if evaluation:
                steps.append({
                    "type": "tool_result",
                    "name": f"Step {step_num} eval",
                    "content": evaluation.get("reason", "")[:250],
                    "success": evaluation.get("success", False),
                })
            if entry.get("replanned"):
                steps.append({
                    "type": "tool_call",
                    "name": "Replan",
                    "content": f"New plan: {entry.get('new_steps_count', 0)} steps",
                })
            elif entry.get("skipped"):
                steps.append({
                    "type": "tool_result",
                    "name": f"Step {step_num}",
                    "content": entry.get("skip_reason", "Skipped")[:200],
                    "success": False,
                })
        return steps

    # Only emit thinking trace when there was actual agent loop activity
    total_tool_calls = sum(len(t.get("tool_calls", [])) for t in agent_loop_trace)
    has_reflections = any(
        msg.get("role") == "system" and "REFLECTION FEEDBACK" in (msg.get("content") or "")
        for msg in messages
    )
    has_intermediate_assistant = sum(
        1 for msg in messages
        if msg.get("role") == "assistant" and (msg.get("tool_calls") or msg.get("content", "").strip())
    ) > 1  # more than just the final answer

    if not (total_tool_calls or has_reflections or has_intermediate_assistant):
        return []  # Simple single-turn â€” no thinking to show

    # Identify the final assistant message (last non-empty assistant turn)
    final_assistant_idx = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "assistant" and (
            m.get("content", "").strip() or not m.get("tool_calls")
        ):
            final_assistant_idx = i
            break

    for idx, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args)
                        args_display = ", ".join(f"{k}={repr(v)[:80]}" for k, v in args.items())
                    except Exception:
                        args_display = raw_args[:160]
                    steps.append({
                        "type": "tool_call",
                        "name": name,
                        "content": f"{name}({args_display})"
                    })
            elif content.strip() and idx != final_assistant_idx:
                # Intermediate text response (not the final answer)
                steps.append({
                    "type": "assistant",
                    "name": None,
                    "content": content.strip()[:500]
                })

        elif role == "tool":
            tool_name = msg.get("name", "tool")
            success = msg.get("success", True)
            display = (content or "").strip()[:400]
            steps.append({
                "type": "tool_result",
                "name": tool_name,
                "content": display,
                "success": success
            })

        elif role == "system" and "REFLECTION FEEDBACK" in content:
            # Extract just the feedback line
            lines = content.strip().splitlines()
            feedback_lines = []
            capture = False
            for line in lines:
                if "REFLECTION FEEDBACK:" in line:
                    capture = True
                    feedback_lines.append(line.replace("REFLECTION FEEDBACK:", "").strip())
                elif capture and line.startswith("ORIGINAL USER"):
                    break
                elif capture:
                    feedback_lines.append(line)
            steps.append({
                "type": "reflection",
                "name": None,
                "content": " ".join(feedback_lines).strip()[:300]
            })

    return steps

@router.get("", response_class=HTMLResponse)
async def chat_page(request: Request):
    # Access module manager from app state to render the sidebar correctly
    module_manager = request.app.state.module_manager
    enabled_modules = [m for m in module_manager.get_all_modules() if m.get("enabled")]
    brain = _module_meta(request, "brain")

    return templates.TemplateResponse(request, "index.html", {
        "modules": enabled_modules,
        "active_module": "chat",
        "sidebar_template": "chat_sidebar.html",
        "sessions": session_manager.list_sessions(),
        "settings": settings.settings,
        "brain_available": bool(brain),
        "brain_enabled": bool(brain.get("enabled")),
    })

@router.get("/gui", response_class=HTMLResponse)
async def chat_gui(request: Request, session_id: str = Query(None)):
    active_session = None
    if session_id:
        active_session = session_manager.get_session(session_id)
    
    if not active_session:
        # If no session specified or found, try to use the latest one or create one
        sessions = session_manager.list_sessions()
        if sessions:
            active_session = sessions[0]

    estimated_tokens = _estimate_tokens(active_session["history"]) if active_session else 0
    brain = _module_meta(request, "brain")

    return templates.TemplateResponse(request, "chat_gui.html", {
        "session": active_session,
        "estimated_tokens": estimated_tokens,
        "active_stream": bool(active_session and _session_has_stream(active_session["id"])),
        "brain_available": bool(brain),
        "brain_enabled": bool(brain.get("enabled")),
    })

@router.get("/sessions", response_class=HTMLResponse)
async def get_chat_sessions(request: Request):
    sessions = session_manager.list_sessions()
    return templates.TemplateResponse(request, "chat_session_list.html", {"sessions": sessions})

@router.post("/sessions/new")
async def create_new_session():
    new_session = session_manager.create_session()
    trigger_data = {
        "sessionsChanged": None,
        "newSessionCreated": {"id": new_session['id']}
    }
    return HTMLResponse(content="", headers={"HX-Trigger": json.dumps(trigger_data)})

@router.post("/sessions/{session_id}/compact")
async def compact_session_route(
    request: Request,
    session_id: str,
    llm: LLMBridge = Depends(get_llm_bridge)
):
    """Manual compact: summarize old messages, keep last N verbatim."""
    module_manager = request.app.state.module_manager
    chat_module = module_manager.modules.get("chat")
    config = chat_module.get("config", {}) if chat_module else {}
    keep_last = int(config.get("compact_keep_last", 10))

    compacted, tokens_before = await session_manager.compact_session(session_id, llm, keep_last=keep_last)

    response = await chat_gui(request, session_id=session_id)

    if compacted:
        msg = f"Session compacted â€” was ~{tokens_before:,} tokens"
        response.headers["HX-Trigger"] = json.dumps({"showMessage": {"level": "success", "message": msg}})
    else:
        response.headers["HX-Trigger"] = json.dumps({"showMessage": {"level": "info", "message": "Nothing to compact â€” session is short enough"}})

    return response

@router.post("/sessions/{session_id}/delete")
async def delete_session(request: Request, session_id: str, confirm: str = None):
    from core.confirmations import require_confirmation

    require_confirmation(confirm, "delete this session", "deleting a chat session")
    session_manager.delete_session(session_id)

    # Get the new chat GUI. By passing session_id=None, chat_gui will pick the latest session,
    # or create a new one if none are left.
    response = await chat_gui(request, session_id=None)

    # Add a trigger to the response to tell the session list to update itself.
    response.headers["HX-Trigger"] = "sessionsChanged"
    return response

@router.post("/sessions/{session_id}/rename")
async def rename_session(session_id: str, name: str = Form(...)):
    session_manager.rename_session(session_id, name)
    return HTMLResponse(content="", headers={"HX-Trigger": "sessionsChanged"})


@router.post("/sessions/{session_id}/brain/commands")
async def run_brain_command(
    request: Request,
    session_id: str,
    body: dict = Body(...),
    actor: BrainActor = Depends(require_role("operator")),
):
    """Run one brain suggestion and mark it done in the stored chat message."""
    from modules.brain import conversation
    from modules.brain.service import get_brain_service

    brain = _module_meta(request, "brain")
    if not brain or not brain.get("enabled"):
        raise HTTPException(
            status_code=409,
            detail="Why: the brain is off. Fix: enable it from the chat rail or the Modules page.",
        )

    name = str(body.get("name") or "")
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    index = body.get("index")
    commands = None
    if isinstance(index, int) and not isinstance(index, bool):
        session = await asyncio.to_thread(session_manager.get_session, session_id)
        history = (session or {}).get("history", [])
        if 0 <= index < len(history):
            stored = history[index].get("commands")
            if isinstance(stored, list):
                commands = stored
                for command in commands:
                    if (command.get("name") == name
                            and command.get("status") in ("done", "failed")):
                        return {**command, "already": True}
    service = get_brain_service()
    entry = await conversation.execute_command(service, name, args, actor=actor.name)
    await service.journal([entry])

    if commands is not None:
        for command in commands:
            if command.get("name") == name:
                command["status"] = entry.get("status")
                command["result"] = entry.get("result")
                break
        await asyncio.to_thread(
            session_manager.update_message, session_id, index, commands=commands
        )
    return entry

@router.post("/settings/save")
async def save_chat_settings(
    request: Request,
    auto_rename_turns: int = Form(...),
    auto_compact_tokens: int = Form(0),
    compact_keep_last: int = Form(10)
):
    module_manager = request.app.state.module_manager
    chat_module = module_manager.modules.get("chat")
    if not chat_module:
        return Response(status_code=404)
        
    config = chat_module.get("config", {}).copy()
    config["auto_rename_turns"] = auto_rename_turns
    config["auto_compact_tokens"] = auto_compact_tokens
    config["compact_keep_last"] = compact_keep_last
    
    module_manager.update_module_config("chat", config)
    
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Chat settings saved"}})})

@router.post("/send", response_class=HTMLResponse)
async def send_message(
    request: Request,
    message: str = Form(...),
    session_id: str = Query(None),
    image: UploadFile = File(None),
    goal_id: str = Form(None),
    llm: LLMBridge = Depends(get_llm_bridge)
):
    if not session_id:
        return HTMLResponse("Error: No session selected", status_code=400)

    active_session = session_manager.get_session(session_id)
    if not active_session:
        return HTMLResponse("Error: Session not found", status_code=404)

    # Prepare user content (Text or Multimodal)
    user_content = message
    if isinstance(message, str) and message:
        guard = scan_input(message)
        if guard.blocked:
            logger.warning("Guardrails blocked a chat input: %s", guard.to_dict()["findings"])
            return HTMLResponse("Error: this message was blocked by the input guardrail.",
                                status_code=400)
        # Detection never rewrites the user's message; only model output is redacted.
    if image and image.filename:
        # M-PERF-1: bound inline base64 images at the boundary. The session
        # store keeps every message in one JSON file, so an uncapped upload
        # (any type/size) bloats every subsequent rewrite. SVG is rejected
        # outright (scriptable); raster types only, 5 MB max.
        _ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
        _MAX_IMAGE_BYTES = 5 * 1024 * 1024
        mime_type = (image.content_type or "").lower().split(";")[0].strip()
        if mime_type not in _ALLOWED_IMAGE_MIMES:
            return HTMLResponse(
                "Error: unsupported image type. Use PNG, JPEG, WEBP or GIF.",
                status_code=400,
            )
        contents = await image.read()
        if len(contents) > _MAX_IMAGE_BYTES:
            return HTMLResponse(
                "Error: image too large (5 MB maximum).", status_code=400
            )
        encoded = base64.b64encode(contents).decode("utf-8")
        image_url = f"data:{mime_type};base64,{encoded}"

        user_content = [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]

    # Add user message to history without blocking the event loop on disk I/O
    await asyncio.to_thread(session_manager.add_message, session_id, "user", user_content)
    active_session = session_manager.get_session(session_id)

    # Get module config early â€” used for both auto-compact and auto-rename below
    module_manager = request.app.state.module_manager
    chat_module = module_manager.modules.get("chat")
    config = chat_module.get("config", {}) if chat_module else {}

    # Auto-compact if estimated token count exceeds the configured threshold
    auto_compact_tokens = int(config.get("auto_compact_tokens", 0))
    compact_keep_last = int(config.get("compact_keep_last", 10))
    if auto_compact_tokens > 0:
        estimated = _estimate_tokens(active_session["history"])
        if estimated > auto_compact_tokens:
            compacted, _ = await session_manager.compact_session(session_id, llm, keep_last=compact_keep_last)
            if compacted:
                active_session = session_manager.get_session(session_id)
                logger.info(f"[Chat] Auto-compacted session {session_id} (was ~{estimated:,} tokens)")

    # /brain turns are answered by the brain conversation and do not need an active flow.
    brain_prompt = _brain_prompt(message)
    if brain_prompt is not None:
        msg_id = int(time.time() * 1000)
        queue = asyncio.Queue()
        brain_stream_key = _stream_key(session_id, msg_id)
        _register_stream(brain_stream_key, queue)
        brain = _module_meta(request, "brain")
        def _retain(coro):
            # M-ASYNC-2: keep a strong ref so the turn cannot be GC'd
            # mid-flight. Queue lifetime belongs to the stream response
            # (removed on close), not to producer completion: a late GET
            # must still find the queued events.
            task = asyncio.create_task(coro)
            background_chat_tasks.add(task)
            task.add_done_callback(background_chat_tasks.discard)
            return task
        if not brain or not brain.get("enabled"):
            text = ("The brain is off. Enable it from the chat rail or the Modules page, "
                    "then try /brain again.")
            _retain(_stream_brain_notice(session_id, text, queue))
        elif image and image.filename:
            text = "Brain turns are text-only; send the image with an ordinary chat message."
            _retain(_stream_brain_notice(session_id, text, queue))
        elif not brain_prompt:
            text = "Type a question after /brain, for example: /brain what are you working on?"
            _retain(_stream_brain_notice(session_id, text, queue))
        else:
            _retain(
                _run_brain_turn(session_id, message, brain_prompt, llm, queue)
            )
        return templates.TemplateResponse(
            request,
            "chat_message_streaming.html",
            {
                "user_message": user_content,
                "session_id": session_id,
                "msg_id": msg_id,
                "brain_turn": True,
            },
            headers={"HX-Trigger": "sessionsChanged"},
        )

    active_flow_ids = settings.get("active_ai_flows", [])
    active_flow = flow_manager.get_flow(active_flow_ids[0]) if active_flow_ids else None
    flow_error = None  # Track errors in a structured way

    if not active_flow:
        ai_response = "Error: No active AI Flow is set. Please go to the AI Flow page to create and activate a flow."
        elapsed_time = 0
        return templates.TemplateResponse(
            request, 
            "chat_message_pair.html", 
            {
                "user_message": user_content,
                "ai_response": ai_response,
                "elapsed_time": elapsed_time,
                "error": "no_active_flow"
            },
            headers={"HX-Trigger": "sessionsChanged"}
        )
    else:
        # Check if streaming is enabled via some parameter or globally, for now we will assume streaming is always preferred for chat if tools aren't blocking it.
        # However, to be safe, we'll try to stream.
        msg_id = int(time.time() * 1000)
        queue = asyncio.Queue()
        stream_key = _stream_key(session_id, msg_id)
        _register_stream(stream_key, queue)

        async def run_flow_background():
            from modules.chat.sessions import session_manager
            
            start_time = time.time()
            try:
                runner = FlowRunner(flow_id=active_flow['id'])
                initial_data = {"messages": active_session["history"], "_input_source": "chat"}
                brain_context = await _brain_flow_context(request)
                if brain_context:
                    initial_data["_brain_context"] = brain_context
                if goal_id:
                    try:
                        initial_data["goal_id"] = int(goal_id)
                    except (ValueError, TypeError) as _exc:
                        logger.debug("ignored exception: %s", _exc)
                flow_result = await runner.run(initial_data, stream_queue=queue)
                # M-CHAT-1: never log full conversations / base64 images at INFO.
                # Sizes at DEBUG only; content stays out of the log.
                try:
                    _fr = flow_result if isinstance(flow_result, dict) else {}
                    logger.debug(
                        "[Chat] flow done keys=%s content_len=%s msgs=%s",
                        sorted(_fr.keys()),
                        len(str(_fr.get("content", ""))),
                        len(_fr.get("messages", []) or []),
                    )
                except Exception:
                    pass
                
                ai_response = flow_result.get("content", "")
                if not ai_response:
                    # Fallback: look for the last non-empty assistant turn in messages.
                    # Agent loops often write results there even when "content" is empty.
                    messages = flow_result.get("messages", [])
                    for msg in reversed(messages):
                        if msg.get("role") == "assistant" and msg.get("content", "").strip():
                            ai_response = msg["content"].strip()
                            break

                # --- Emit thinking trace (intermediate agent steps) ---
                thinking_steps = _extract_thinking_steps(flow_result)
                if thinking_steps:
                    await queue.put({"type": "thinking", "content": thinking_steps})

                # Guardrails: never persist or stream a leaked prompt or a live credential.
                if ai_response:
                    guard = scan_output(ai_response, system_prompt=_flow_system_prompt(active_flow))
                    if guard.blocked:
                        logger.warning("Guardrails replaced a chat output: %s",
                                       guard.to_dict()["findings"])
                        ai_response = ("I can't return that response because it would repeat "
                                       "internal instructions.")
                    elif guard.redactions:
                        ai_response = guard.text

                if ai_response:
                    await asyncio.to_thread(
                        session_manager.add_message, session_id, "assistant",
                        ai_response, thinking=thinking_steps,
                    )
                    await queue.put({"type": "replace", "content": ai_response})
                elif flow_result.get("error"):
                    await asyncio.to_thread(
                        session_manager.add_message, session_id, "assistant",
                        f"Error: {flow_result['error']}",
                    )
                    await queue.put({"type": "error", "content": flow_result["error"]})
                else:
                    await queue.put({"type": "error", "content": "Flow produced no response."})

                # --- Emit actual token usage from the LLM API response ---
                # Check multiple paths: bare LLM output has usage at top level;
                # Memory Save / agent loop wraps it under response.usage.
                usage = (
                    flow_result.get("usage")
                    or (flow_result.get("response") or {}).get("usage")
                )
                if isinstance(usage, dict) and usage.get("total_tokens"):
                    logger.info(f"[Chat] Adding tokens to session {session_id}: {usage}")
                    await asyncio.to_thread(session_manager.add_tokens, session_id, usage)
                    await queue.put({"type": "usage", "content": usage})
                    
                # --- Auto-Renaming Logic ---
                module_manager = request.app.state.module_manager
                chat_module = module_manager.modules.get("chat")
                config = chat_module.get("config", {}) if chat_module else {}
                auto_rename_turns = int(config.get("auto_rename_turns", 3))

                # Reload session to ensure latest state
                current_session = session_manager.get_session(session_id)
                if current_session and len(current_session["history"]) >= auto_rename_turns * 2 and current_session["name"].startswith("Session "):
                    try:
                        user_text = None
                        ai_text = None
                        for msg in current_session["history"]:
                            content = msg.get("content", "")
                            if msg.get("role") == "user" and user_text is None:
                                user_text = content if isinstance(content, str) else "Image/Multimodal Content"
                            elif msg.get("role") == "assistant" and ai_text is None:
                                ai_text = content if isinstance(content, str) else "Image/Multimodal Content"
                            if user_text and ai_text:
                                break
                        
                        if user_text is None: user_text = "Image/Multimodal Content"
                        if ai_text is None: ai_text = "Response"
                        
                        summary_context = f"User: {user_text[:500]}\nAI: {ai_text[:500]}"
                        prompt = (
                            "Generate a short, concise title (3-5 words) for this "
                            f"conversation based on the start:\n\n{summary_context}\n\nTitle:"
                        )
                        
                        title_response = await llm.chat_completion(
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.7,
                            max_tokens=15
                        )
                        
                        if "choices" in title_response:
                            new_title = title_response["choices"][0]["message"]["content"].strip().strip('"')
                            if new_title and len(new_title) < 50:
                                session_manager.rename_session(session_id, new_title)
                                await queue.put({"type": "rename", "content": new_title})
                    except Exception as err:
                        logger.warning(f"Auto-rename failed: {err}")
                
            except Exception as e:
                logger.exception(f"Stream generation error: {e}")
                last_stream_errors[stream_key] = str(e)
                await queue.put({"type": "error", "content": str(e)})
            finally:
                await queue.put(None)
                # M-ASYNC-3: the queue stays registered until its stream
                # response closes (event_generator finally removes it), so
                # late readers still find it. Only the task ref is dropped.
                # Identity-guarded removal lives in event_generator.
        
        # Start the background execution (M-ASYNC-2: retained, self-cleaning).
        _bg_task = asyncio.create_task(run_flow_background())
        background_chat_tasks.add(_bg_task)
        _bg_task.add_done_callback(background_chat_tasks.discard)
        
        return templates.TemplateResponse(
            request, 
            "chat_message_streaming.html", 
            {
                "user_message": user_content,
                "session_id": session_id,
                "msg_id": msg_id
            },
            headers={"HX-Trigger": "sessionsChanged"}
        )

@router.get("/stream/{session_id}")
async def stream_chat(session_id: str, msg_id: int = None):
    from fastapi.responses import StreamingResponse
    import json

    # M-ASYNC-3: each message has its own queue; without a msg_id fall back
    # to the newest queue for the session (back-compat).
    if msg_id is not None:
        queue = active_streams.get(_stream_key(session_id, msg_id))
        lookup_key = _stream_key(session_id, msg_id)
    else:
        queue = _latest_stream_for(session_id)
        lookup_key = None

    async def event_generator():
        if not queue:
            # The flow can finish before the browser opens the stream. Surface the
            # failure reason or the persisted assistant reply instead of a missing stream.
            failure = last_stream_errors.pop(lookup_key, None) if lookup_key else None
            if failure is None:
                # Back-compat: errors recorded without a message key, or a
                # late reader without a msg_id asking for the newest failure.
                failure = last_stream_errors.pop(session_id, None)
            if failure is None and msg_id is None:
                newest = None
                for key in list(last_stream_errors):
                    if isinstance(key, tuple) and len(key) == 2 and key[0] == session_id:
                        if newest is None or key[1] > newest[1]:
                            newest = key
                if newest is not None:
                    failure = last_stream_errors.pop(newest, None)
            if failure:
                payload = json.dumps({"error": failure})
                yield f"event: error\ndata: {payload}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            # NOTE: module-global session_manager (patchable in tests), not a
            # fresh import which would bypass monkeypatching.
            session = session_manager.get_session(session_id)
            last_reply = None
            if session:
                for message in reversed(session.get("history", [])):
                    if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                        last_reply = message["content"]
                        break
            if last_reply:
                payload = json.dumps({"text": last_reply})
                yield f"event: replace\ndata: {payload}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            yield "event: error\ndata: No active stream\n\n"
            return
            
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                    
                if isinstance(chunk, dict) and chunk.get("type") == "token":
                    payload = json.dumps({"text": chunk.get("content", "")})
                    yield f"data: {payload}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "error":
                    payload = json.dumps({"error": chunk.get("content", "")})
                    yield f"event: error\ndata: {payload}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "replace":
                    payload = json.dumps({"text": chunk.get("content", "")})
                    yield f"event: replace\ndata: {payload}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "rename":
                    payload = json.dumps({"title": chunk.get("content", "")})
                    yield f"event: rename\ndata: {payload}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "usage":
                    payload = json.dumps(chunk.get("content", {}))
                    yield f"event: usage\ndata: {payload}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "thinking":
                    payload = json.dumps(chunk.get("content", []))
                    yield f"event: thinking\ndata: {payload}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "commands":
                    payload = json.dumps(chunk.get("content", []))
                    yield f"event: commands\ndata: {payload}\n\n"
        finally:
            # Identity-guarded per-message cleanup (M-ASYNC-3): a finished
            # stream only removes its own queue. Fall back to the legacy
            # session key for queues registered before this change.
            removed = False
            if lookup_key is not None and active_streams.get(lookup_key) is queue:
                active_streams.pop(lookup_key, None)
                removed = True
            if not removed and active_streams.get(session_id) is queue:
                active_streams.pop(session_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


