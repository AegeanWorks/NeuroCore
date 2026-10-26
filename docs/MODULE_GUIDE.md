# NeuroCore Module Development Guide

> **Build powerful extensions for NeuroCore with custom modules, nodes, and APIs**

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Module Metadata](#4-module-metadata)
5. [Adding API Routes](#5-adding-api-routes)
6. [Adding AI Flow Nodes](#6-adding-ai-flow-nodes)
7. [Core Modules Reference](#7-core-modules-reference)
8. [Node Implementation Patterns](#8-node-implementation-patterns)
9. [Advanced Topics](#9-advanced-topics)
10. [Hot-Swapping & Development](#10-hot-swapping--development)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Quick Start

Create a new module in 3 simple steps:

```bash
# 1. Create module directory
mkdir modules/my_module

# 2. Create metadata file
cat > modules/my_module/module.json << 'EOF'
{
    "name": "My Module",
    "description": "What this module does",
    "enabled": true,
    "id": "my_module",
    "order": 10
}
EOF

# 3. Create empty init file
touch modules/my_module/__init__.py
```

Your module now appears in the NeuroCore dashboard.

---

## 2. Architecture Overview

NeuroCore's modular architecture separates concerns into three layers:

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Flow Layer                            │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  Nodes  │──│  Nodes  │──│  Nodes  │──│  Nodes  │        │
│  │(Logic)  │  │(Memory) │  │(Tools)  │  │(Chat)   │        │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘        │
└───────┼────────────┼────────────┼────────────┼─────────────┘
        │            │            │            │
        └────────────┴────────────┴────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   Module Manager Layer                      │
│         (Loads modules, manages lifecycle)                  │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Router Layer                     │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  GUI    │  │  API    │  │  WebSocket│  │  Static │        │
│  │ Routes  │  │ Endpoints│  │  Handlers│  │  Files  │        │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### Module Types

| Type | Purpose | Example |
|------|---------|---------|
| **Flow Node** | Single node for AI Flows | LLM Core, System Prompt |
| **Node Provider** | Multiple nodes for flows | Logic & Flow, Memory |
| **Service Module** | Backend services + UI | Knowledge Base, Calendar |
| **Bridge Module** | External integrations | Messaging Bridge |

### Module Discovery & Hot-Reload Safety

`ModuleManager` scans `modules/` on startup. Key implementation details:

- **`DISABLED` file marker**: If a `DISABLED` file exists in a module directory, the module is skipped entirely during discovery.
- **`_loaded_once` set**: Tracks which module IDs have been imported at least once. On the **first** load, `sys.modules` entries are NOT flushed — preserving any submodules already imported by tests or other code. On a **re-load** (after an explicit unload), entries are flushed so code changes are picked up.
- **`module_allowlist`**: When non-empty in `settings.json`, only listed module IDs may be loaded. Modules not in the allowlist are skipped silently with a warning.

---

## 3. Directory Structure

```
modules/
└── my_new_module/
    ├── __init__.py          # Exposes the router (required)
    ├── module.json          # Metadata (required)
    ├── router.py            # FastAPI routes (optional)
    ├── node.py              # AI Flow logic (optional)
    ├── service.py           # Business logic (optional)
    └── backend.py           # Data layer (optional)
```

### File Purposes

| File | Required | Purpose |
|------|----------|---------|
| `module.json` | Yes | Module metadata and configuration |
| `__init__.py` | Yes | Package initialization, router exposure |
| `router.py` | No | FastAPI routes for UI and API endpoints |
| `node.py` | No | AI Flow node implementations |
| `service.py` | No | Business logic layer |
| `backend.py` | No | Data persistence layer |

---

## 4. Module Metadata

The `module.json` file defines how NeuroCore loads and displays your module.

### Complete Schema

```json
{
    "name": "My New Module",
    "description": "Description of what this module does.",
    "enabled": true,
    "id": "my_new_module",
    "icon": "M12 2L2 7l10 5 10-5-10-5z...",

    "is_flow_node": false,
    "singleton": false,
    "order": 10,

    "config": {
        "my_setting": "default_value",
        "api_key": "",
        "timeout": 30
    },

    "provides_nodes": [
        {
            "id": "my_custom_node",
            "name": "Custom Processor",
            "description": "Processes text in a specific way.",
            "singleton": false,
            "configurable": true,
            "config": {
                "param1": "value1"
            }
        }
    ]
}
```

> **Note:** `load_error` is a **runtime-only** field tracked in `ModuleManager._load_errors`. It is never written to `module.json`. If you see it in an older file, remove it.

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name in UI |
| `description` | string | Short description for tooltips |
| `enabled` | boolean | Whether module is active |
| `id` | string | Unique identifier (snake_case) |
| `icon` | string | SVG path data for sidebar icon |
| `is_flow_node` | boolean | Module acts as a single flow node |
| `singleton` | boolean | Only one instance allowed |
| `order` | number | Sort order in UI (lower = first) |
| `config` | object | Default configuration values |
| `provides_nodes` | array | Nodes provided for AI Flows |

> The `config` field inside `provides_nodes` entries is for documentation/display purposes only. Per-node runtime configuration is handled in the executor class in `node.py`.

---

## 5. Adding API Routes

Create `router.py` for backend APIs and UI pages.

### Basic Router Structure

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

@router.get("/gui", response_class=HTMLResponse)
async def module_gui(request: Request):
    return templates.TemplateResponse(
        request,
        "my_module_gui.html",
        {"data": "value"}
    )

@router.get("/api/data")
async def get_data():
    return {"status": "success", "data": []}

@router.post("/api/action")
async def perform_action(request: Request):
    data = await request.json()
    return {"result": "done"}
```

### Exposing the Router

**Required** in `__init__.py`:

```python
# modules/my_new_module/__init__.py
from .router import router
```

### Router Patterns

| Pattern | Use Case | Example |
|---------|----------|---------|
| `/gui` | HTML fragment for UI | Knowledge base interface |
| `/api/*` | REST API endpoints | CRUD operations |
| `/ws` | WebSocket handlers | Real-time updates |
| `/webhook` | External callbacks | WhatsApp webhook |

---

## 6. Adding AI Flow Nodes

Create `node.py` to provide nodes for the AI Flow editor.

### Node Executor Pattern

```python
class MyNodeExecutor:
    """Executor class for a flow node."""

    async def receive(self, input_data: dict, config: dict = None) -> dict | None:
        """
        Process incoming data from previous node.

        Returns:
            Processed data dict, or None to stop this branch.
        """
        if input_data is None:
            return None

        config = config or {}

        result = input_data.copy()
        result['processed'] = True
        result['my_field'] = config.get('setting', 'default')

        return result

    async def send(self, processed_data: dict) -> dict:
        """Finalize output for downstream nodes."""
        return processed_data


async def get_executor_class(node_type_id: str):
    """Dispatcher called by FlowRunner."""
    if node_type_id == 'my_custom_node':
        return MyNodeExecutor
    return None
```

### Reserved Flow Keys

These keys are managed by the framework and must not be repurposed:

| Key | Owner | Purpose |
|-----|-------|---------|
| `messages` | All nodes | Conversation history — preserved across all nodes |
| `content` | LLM Core | Final LLM response text |
| `_memory_context` | Memory Recall | Retrieved memory context |
| `_kb_context` | Knowledge Query | Knowledge base retrieval results |
| `reasoning_context` | Reasoning Book | Injected reasoning context |
| `plan_context` | Planner | Formatted plan string |
| `_route_targets` | Conditional Router | Dynamic routing targets (consumed by FlowRunner) |
| `tool_calls` | LLM Core | LLM-requested tool invocations |
| `tool_results` | Tool Dispatcher | Tool execution results |
| `requires_continuation` | Tool Dispatcher | Multi-turn tool loop flag |
| `_input_source` | Flow Runner | Origin (e.g., `"chat"`, `"messaging"`) |
| `_strip_messages` | Internal | Prevent automatic messages propagation |
| `_is_error` | Internal | Mark output as a flow error |
| `_flow_id` | Config | Current flow ID (in config, not input) |
| `_node_id` | Config | Current node ID (in config, not input) |
| `_repeat_count` | Repeater | Loop iteration counter |
| `_messaging_platform` | Messaging Bridge | Platform that originated the message |
| `_messaging_reply_to` | Messaging Bridge | Sender address for replies |
| `plan` | Planner | List of plan steps |
| `current_step` | Planner | Current step index |
| `completed_steps` | Plan Tracker | Set of completed step indices |
| `satisfied` | Reflection | Boolean for conditional routing |

### Node Retry and Timeout Config

Any node config may opt into retries and a per-node time ceiling. Retries are off by default
because nodes with external effects must not run twice silently.

| Key | Default | Range | Meaning |
|-----|---------|-------|---------|
| `retry_count` | `0` | 0-4 | Extra attempts after the first |
| `retry_delay` | `0.5` | 0-30 s | Base delay before the first retry |
| `retry_max_delay` | `10` | 0-30 s | Cap on any single delay |
| `retry_backoff` | `2.0` | 1-10 | Multiplier per retry |
| `timeout_seconds` | `0` | 0-3600 s | Per-attempt ceiling; `0` disables |

Only transient failures retry: `TimeoutError`, `ConnectionError`, `LLMTimeoutError`,
`ToolTimeoutError` and `LLMHTTPError` with 408/425/429/5xx. `ValueError`, `TypeError`,
`KeyError` and cancellation fail immediately. Each attempt spends one node from the run budget.

Every attempt is also timed by `core/node_stall.py`. After 20 samples for a module/type, a duration
above both 2,000 ms and 3x the prior p99 is a stall: it increments `node.stall`, logs a structured
warning and appears in `GET /debug/node-latency` (debug mode). Detection never aborts a run.

---

## 7. Core Modules Reference

NeuroCore includes **18 built-in modules** with 28 node executors.

### Core AI Modules

#### LLM Core (`llm_module`)
Direct interface to Large Language Models.

| Property | Value |
|----------|-------|
| **Node** | `llm_module` |
| **Singleton** | Yes |
| **Order** | 0 |

**Configuration:**
```json
{
    "temperature": 0.7,
    "max_tokens": 8192
}
```

**Features:** Streaming, tool calling, vision (multimodal), configurable model/temperature/max_tokens per node.

---

#### System Prompt (`system_prompt`)
Injects system instructions and manages tool context.

| Property | Value |
|----------|-------|
| **Node** | `system_prompt` |
| **Singleton** | No |
| **Order** | 2 |

**Configuration:**
```json
{
    "system_prompt": "You are NeuroCore, a helpful AI assistant.",
    "enabled_tools": [],
    "max_token_budget": 4000
}
```

**Context injection sources:** `_memory_context`, `_kb_context`, `reasoning_context`, `plan_context`, enabled skills.

---

#### Agent Loop (`agent_loop`)
Autonomous agent with tool execution looping.

| Property | Value |
|----------|-------|
| **Nodes** | `agent_loop`, `recursive_lm`, `repl_environment` |
| **Order** | 1 |

**Configuration:**
```json
{
    "max_iterations": 10,
    "max_tokens": 2048,
    "temperature": 0.7,
    "max_llm_retries": 3,
    "retry_delay": 1.0,
    "tool_error_strategy": "continue",
    "timeout": 120,
    "include_plan_in_context": true,
    "include_memory_context": true,
    "include_knowledge_context": true,
    "include_reasoning_context": true
}
```

**Features:** Exponential backoff retry, tool error handling (continue/stop), timeout protection, real-time thinking trace streaming.

---

#### Task Planner (`planner`)
Breaks down complex requests into actionable steps.

| Property | Value |
|----------|-------|
| **Nodes** | `planner`, `plan_step_tracker` |
| **Order** | 5 |

**Configuration:**
```json
{
    "max_steps": 20,
    "enabled": true
}
```

**Output:** `plan` (list of steps), `current_step`, `plan_context`, `plan_needed`.

---

#### Reflection (`reflection`)
Evaluates agent responses for quality and completeness.

| Property | Value |
|----------|-------|
| **Node** | `reflection` |
| **Order** | 4 |

**Output:** `satisfied` (bool for Conditional Router), `reflection` (evaluation object), improved `messages` if not satisfied.

---

### Control Flow Modules

#### Logic & Flow (`logic`)
Control flow nodes for branching, transformation, and scheduling.

| Property | Value |
|----------|-------|
| **Nodes** | 7 |
| **Order** | 3 |

| Node ID | Name | Description | Configurable |
|---------|------|-------------|--------------|
| `trigger_node` | Trigger | Pass-through manual trigger | No |
| `delay_node` | Delay | Pause execution (seconds) | Yes |
| `script_node` | Python Script | Custom Python execution (sandboxed) | Yes |
| `repeater_node` | Repeater | Re-trigger flow in background with delay | Yes |
| `conditional_router` | Conditional Router | Branch based on field existence | Yes |
| `schedule_start_node` | Scheduled Start | Wait until specific date/time | Yes |
| `context_length_router` | Context Length Router | Route to RLM or standard LLM by token count | Yes |

**Conditional Router fields:**
- `tool_calls` — LLM generated tool calls
- `requires_continuation` — more tools pending
- `satisfied` — reflection evaluation result
- `max_tools_per_turn` — tool limit reached

---

### Memory & Knowledge Modules

#### Long-Term Memory (`memory`)
Vector-based memory with FAISS and SQLite.

| Property | Value |
|----------|-------|
| **Nodes** | `memory_recall`, `memory_save`, `check_goal` |
| **Order** | 11 |

**Configuration:**
```json
{
    "recall_limit": 3,
    "recall_min_score": 0.3,
    "save_confidence_threshold": 0.75,
    "save_default_confidence": 1.0,
    "save_delay": 3.0,
    "consolidation_threshold": 0.92,
    "auto_consolidation_hours": 24,
    "belief_ttl_days": 30
}
```

**Memory types:** BELIEF (30-day TTL), FACT, RULE, EXPERIENCE, PREFERENCE, IDENTITY

---

#### Knowledge Base (`knowledge_base`)
RAG system for document upload and querying.

| Property | Value |
|----------|-------|
| **Node** | `query_knowledge` |
| **Order** | 9 |

**Features:** Hybrid search (FAISS vector + SQLite FTS5) with Reciprocal Rank Fusion (RRF, k=60), PDF/TXT/MD support, incremental re-indexing with provenance tracking.

---

#### Reasoning Book (`reasoning_book`)
Logs AI's internal thoughts and reasoning steps.

| Property | Value |
|----------|-------|
| **Nodes** | `reasoning_save`, `reasoning_load` |

**Storage:** `data/reasoning_book.json` (async Lock for thread safety)

---

#### Memory Browser (`memory_browser`)
UI for viewing and managing long-term memories.

| Property | Value |
|----------|-------|
| **Type** | UI module only (no flow nodes) |
| **Order** | 11 |

**Features:** Search and filter memories, view metadata, delete/merge memories, browse by type and source.

---

#### Skills (`skills`)
Manage instruction files (SKILL.md) for AI tasks.

| Property | Value |
|----------|-------|
| **Type** | Service module (no flow nodes) |
| **Order** | 7 |

**Features:** Import/export skill files, inject skills into system prompts, Markdown-based instruction sets.

---

### Tool & Integration Modules

#### Tool Library (`tools`)
Manages custom Python tools for AI agents.

| Property | Value |
|----------|-------|
| **Node** | `tool_dispatcher` |
| **Order** | 6 |

**23 built-in tools** (16 standard + 7 RLM tools). See [TOOL_GUIDE.md](./TOOL_GUIDE.md) for the full reference.

---

#### Chat (`chat`)
Interactive AI assistant interface with streaming.

| Property | Value |
|----------|-------|
| **Nodes** | `chat_input`, `chat_output` |
| **Order** | 14 |

**Configuration:**
```json
{
    "auto_rename_turns": 3,
    "auto_compact_tokens": 0,
    "compact_keep_last": 10
}
```

**Features:** Real-time LLM streaming via SSE, agent thinking trace display, session compaction, auto-rename.

---

#### Messaging Bridge (`messaging_bridge`)
Unified multi-platform messaging for Telegram, Discord, Signal, and WhatsApp.

| Property | Value |
|----------|-------|
| **Nodes** | `messaging_input`, `messaging_output` |
| **Order** | 15 |

**Configuration (platform credentials):**
```json
{
    "telegram_bot_token": "",
    "telegram_chat_id": 0,
    "discord_bot_token": "",
    "discord_channel_id": "",
    "signal_api_url": "",
    "signal_phone_number": "",
    "whatsapp_api_url": "",
    "whatsapp_api_key": "",
    "whatsapp_instance": "",
    "whatsapp_phone_number": ""
}
```

**`messaging_input` node config:**
- `platforms`: `["telegram", "discord"]` — filter to specific platforms (empty = accept all)

**`messaging_output` node config:**
- `platform`: `"auto"` (reply to sender) or a specific platform ID
- `proactive_recipients`: `["telegram:123456", "discord:987654"]` — used when there is no incoming message context (e.g. Repeater-triggered flows)

**Adding a new platform:** Append one entry to `MESSAGING_PLATFORMS` in `node.py`, implement the bridge class, and wire it in `service.py`.

---

#### Calendar (`calendar`)
Event scheduling and reminders.

| Property | Value |
|----------|-------|
| **Node** | `calendar_watcher` |
| **Order** | 13 |

---

#### Browser Automation (`browser_automation`)
Headless browser singleton (Playwright).

| Property | Value |
|----------|-------|
| **Type** | Singleton service (no flow nodes yet) |
| **Order** | 15 |

**Configuration:**
```json
{
    "headless": true,
    "timeout": 30000,
    "viewport_width": 1280,
    "viewport_height": 720
}
```

The Playwright instance is lazily initialized on first use. Currently available as a service object; flow node executors are planned (see `docs/IDEAS.md`).

---

#### Email Bridge (`email_bridge`)
IMAP/SMTP email integration for NeuroCore flows.

| Property | Value |
|----------|-------|
| **Nodes** | `email_input`, `email_output` |
| **Order** | 16 |

**Configuration:**
```json
{
    "imap_host": "",
    "imap_port": 993,
    "smtp_host": "",
    "smtp_port": 587,
    "email": "",
    "password": "",
    "poll_interval_seconds": 30
}
```

`email_input` polls the IMAP inbox for new messages and injects them as flow input. `email_output` sends replies or proactive messages via SMTP. Mirrors the `messaging_bridge` architecture with `ImapBridge` and `SmtpBridge` classes.

---

#### Annotations (`annotations`)
Comment and organization nodes for flows.

| Property | Value |
|----------|-------|
| **Node** | `comment_node` — resizable text box |

---

## 8. Node Implementation Patterns

### Pattern 1: Pass-Through Processor

```python
class ProcessorExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None
        result = input_data.copy()
        result['processed'] = True
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data
```

### Pattern 2: Context Injector

```python
class ContextInjectorExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None

        context = "Additional context here"
        messages = list(input_data.get('messages', []))  # Always copy before modifying
        messages.insert(0, {'role': 'system', 'content': context})

        result = input_data.copy()
        result['messages'] = messages
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data
```

### Pattern 3: Conditional Router

```python
class RouterExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None

        config = config or {}
        condition = config.get('check_field', 'some_field')
        is_true = bool(input_data.get(condition))

        if config.get('invert', False):
            is_true = not is_true

        targets = config.get('true_branches' if is_true else 'false_branches', [])
        result = input_data.copy()
        result['_route_targets'] = targets
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data
```

### Pattern 4: External Service Call

```python
import httpx

class APIExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None

        config = config or {}
        api_url = config.get('api_url', '')

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(api_url, timeout=10.0)
                response.raise_for_status()
                data = response.json()

            result = input_data.copy()
            result['api_result'] = data
            return result
        except Exception as e:
            result = input_data.copy()
            result['error'] = f"API call failed: {e}"
            return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data
```

### Pattern 5: Background Task (Fire-and-Forget)

```python
import asyncio

class BackgroundExecutor:
    async def _background_task(self, data: dict):
        await asyncio.sleep(5)
        # do work

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None

        # Fire and forget — don't await
        asyncio.create_task(self._background_task(input_data.copy()))

        return input_data  # Return immediately without waiting

    async def send(self, processed_data: dict) -> dict:
        return processed_data
```

---

## 9. Advanced Topics

### Accessing Core Services

```python
async def receive(self, input_data: dict, config: dict = None) -> dict:
    config = config or {}

    # Flow metadata (available in config, not input)
    flow_id = config.get('_flow_id')
    node_id = config.get('_node_id')

    # Access settings
    from core.settings import settings
    api_key = settings.get('llm_api_key')

    return input_data
```

### Thread Safety in Nodes

Nodes run in an async context. Rules:
- Use `asyncio.Lock` for async state (e.g., lazy-init a client)
- Use `asyncio.to_thread()` for any blocking I/O
- Never `await` while holding a `threading.Lock`

```python
import asyncio

class StatefulExecutor:
    _lock = asyncio.Lock()
    _client = None

    async def _get_client(self):
        async with self._lock:
            if self._client is None:
                self._client = await create_async_client()
            return self._client

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        client = await self._get_client()
        # use client...
        return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data
```

### Testing Nodes

```python
import pytest
from modules.my_module.node import MyNodeExecutor

# Do NOT add @pytest.mark.asyncio — asyncio_mode = "auto" is set globally
async def test_my_node():
    executor = MyNodeExecutor()

    input_data = {'messages': [{'role': 'user', 'content': 'Hello'}]}
    config = {'setting': 'value'}

    result = await executor.receive(input_data, config)

    assert result is not None
    assert 'processed' in result
```

---

## 10. Hot-Swapping & Development

### Development Workflow

1. Create/edit module files
2. Go to **Settings → Modules** in the UI
3. Toggle **Enabled** off, then on again to reload
4. Or restart NeuroCore for a complete refresh

### Hot-Reload Safety

- **First load**: `sys.modules` is NOT flushed — preserves already-imported submodules
- **Re-load after unload**: `sys.modules` IS flushed to pick up code changes
- **debug_mode=true**: Forces `importlib.reload()` on every executor class load (useful during active node development)

### Module Lifecycle

```
Created → Discovered → Enabled → Loaded → Active
   ↑         ↑           ↑         ↑        │
   └─────────┴───────────┴─────────┴────────┘
              (Hot-swap supported)
```

---

## 11. Troubleshooting

### Common Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| Module not appearing | Missing `module.json` | Create valid metadata file |
| Router not loading | Missing `router` export | Add `from .router import router` to `__init__.py` |
| Node not executing | Wrong `node_type_id` | Match ID in `module.json` and `get_executor_class` |
| `NoneType` errors | Not handling `None` input | Add `if input_data is None: return None` |
| Config not loading | Invalid JSON syntax | Validate `module.json` with a JSON linter |
| Changes not applied | Caching issue | Disable → Enable module or restart |
| Branch not reached | Wrong `_route_targets` | Check node IDs in conditional router config |

### Debugging Tips

1. Enable `debug_mode: true` in settings to get per-node execution traces in `data/execution_trace.jsonl`
2. Check `load_error` state via `GET /modules` API response
3. Test nodes in isolation:
   ```python
   import asyncio
   from modules.my_module.node import MyExecutor

   executor = MyExecutor()
   result = asyncio.run(executor.receive({'test': 'data'}))
   print(result)
   ```
4. Review console output for import errors on module load
5. Look for `load_error` state via the `/modules/` API

### Pro Tip

Start by copying an existing simple module (like `annotations`) as a template, then modify it for your needs.
