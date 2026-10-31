# CLAUDE.md — NeuroCore AI Assistant Guide

> This file provides context for AI assistants (e.g., Claude Code) working in this repository.

---

## Required Workflow for All Code Tasks

Every code change — no matter how small — must follow this sequence:

1. **Proposed Solution** — Describe the approach before writing any code. Identify affected files, design decisions, and trade-offs.
2. **Implementation** — Write the code changes.
3. **Implementation Review** — Re-read every changed file. Check for correctness, security issues, thread safety, adherence to conventions in this document, and unintended side effects.
4. **Test File Creation** — Write or update tests that cover the new/changed behaviour. Tests live in `tests/test_<area>.py`.
5. **Test File Running** — Execute the tests and confirm they pass.

### Execution Cadence (Important)

Do **not** attempt to complete large or multi-step tasks all at once. Work in clear, incremental chunks:

- Break work into small, logically grouped steps.
- Update todo/progress tracking regularly as each chunk is finished (not only at the very end).

> This workflow applies to all changes: new features, bug fixes, refactors, and configuration changes. The only exception is pure documentation edits (e.g., updating this file) where no executable logic is involved.

### Todo Tracking

Use the `TodoWrite` tool to track progress for **every non-trivial task** — bugs, fixes, new features, refactors, and investigations. Create todos at the start of the task, mark each item `in_progress` before working on it, and mark it `completed` immediately when done. Never batch completions.

**Always use todos for:**
- Bug investigations and fixes
- New feature implementations
- Refactoring existing code
- Running and interpreting test results
- Multi-step investigations (e.g., tracing an error across files)

**Skip todos only for:** single-step trivial edits (e.g., fixing a typo, adding one import).

---

## Project Overview

**NeuroCore** is a production-quality, modular AI orchestration framework built in Python. It provides a visual flow-based execution engine for chaining AI nodes (LLM calls, memory retrieval, tool execution, etc.) into complex autonomous workflows. The system exposes a web UI (HTMX + TailwindCSS), a REST API (FastAPI), and integrations with Telegram, calendar scheduling, and long-term memory.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI 0.115+, Uvicorn 0.32+ |
| Frontend | HTMX, Vanilla JS, TailwindCSS (CDN), Jinja2 3.1+ |
| Vector DB | FAISS (IndexFlatIP + L2 normalization) |
| Relational DB | SQLite (WAL mode, FTS5 full-text search) |
| HTTP Client | HTTPX 0.28+ (async, connection pooling) |
| Data Validation | Pydantic 2.10+ |
| Testing | pytest, pytest-asyncio, pytest-httpx, pytest-cov |
| Containers | Docker, docker-compose |
| Linting | Ruff (configured in pyproject.toml) |

---

## Repository Layout

```
NeuroCore/
├── main.py                    # FastAPI app entry point, lifespan management
├── core/                      # Core engine (~7,300 LOC across 15 files)
│   ├── flow_runner.py         # DAG execution engine (Kahn's sort, bridges)
│   ├── flow_manager.py        # Flow CRUD, JSON persistence
│   ├── routers.py             # 40+ HTTP API endpoints
│   ├── session_manager.py     # Chat session persistence and tracing
│   ├── observability.py       # Distributed tracing, metrics, structured logging
│   ├── flow_context.py        # Pydantic-based type-safe flow payload model
│   ├── flow_data.py           # TypedDict schema, helper functions, migration utils
│   ├── llm.py                 # OpenAI-compatible HTTP client
│   ├── settings.py            # Thread-safe settings manager
│   ├── module_manager.py      # Dynamic module discovery and hot-loading
│   ├── structured_output.py   # Pydantic-based structured output validation
│   ├── planner_helpers.py     # Plan dependency resolution, cycle detection
│   ├── debug.py               # Structured debug logging
│   ├── dependencies.py        # FastAPI dependency injection utilities
│   ├── errors.py              # Exception hierarchy
│   └── schemas/               # Scientific domain models (Hypothesis, Article, Finding, StudyDesign)
├── modules/                   # 16 self-contained feature modules
│   ├── chat/                  # Chat UI, sessions, multimodal
│   ├── memory/                # FAISS+SQLite long-term memory, arbiter
│   ├── knowledge_base/        # RAG ingestion, hybrid vector+keyword search
│   ├── llm_module/            # Core LLM node, streaming, tool calling, vision
│   ├── tools/                 # Tool library, dispatcher, sandbox, 23 built-in tools
│   │   └── rlm_library/       # 7 Recursive Language Model tools
│   ├── system_prompt/         # System prompt injection, tool registration
│   ├── telegram/              # Telegram bot bridge
│   ├── calendar/              # Event management and scheduling
│   ├── memory_browser/        # UI for memory search/edit/merge/delete
│   ├── logic/                 # Delay, Repeater, Conditional, PythonScript nodes
│   ├── annotations/           # Flow comment/annotation nodes
│   ├── agent_loop/            # Autonomous agent loop execution
│   ├── planner/               # Goal-based planning nodes
│   ├── reflection/            # Agent reflection/introspection node
│   ├── reasoning_book/        # Reasoning journal for thought recording
│   └── skills/                # Skill/instruction file management
├── web/templates/             # 33 Jinja2 HTML templates for the web UI
├── tests/                     # 69 test files, 919+ tests
│   └── run_tests.py           # Test runner with coverage support
├── data/                      # Runtime data (SQLite DBs, FAISS indexes)
│   ├── memory.sqlite3         # Long-term memory database
│   ├── memory.faiss           # Memory vector index
│   ├── knowledge_base.sqlite3 # RAG knowledge base
│   ├── knowledge_base.faiss   # Knowledge base vector index
│   ├── episodes/              # Serialized EpisodeState objects for long-running agent tasks
│   └── execution_trace.jsonl  # Per-node execution traces (written when debug_mode=true)
├── docs/
│   ├── CONCURRENCY.md         # Lock ordering rules and deadlock prevention
│   ├── SYSTEM_ARCHITECTURE.md # High-level architecture overview
│   ├── MODULE_GUIDE.md        # Module development guide
│   ├── TOOL_GUIDE.md          # Tool creation and sandbox reference
│   └── PROJECT_ANALYSIS.md    # Deep-dive project analysis
├── settings.json              # Runtime configuration (LLM URLs, models, flags)
├── ai_flows.json              # Saved flow definitions
├── pyproject.toml             # Project metadata, pytest config, ruff rules
├── requirements.txt           # Runtime dependencies
├── Dockerfile                 # Container image definition
└── docker-compose.yml         # Container orchestration
```

---

## Development Commands

### Running the Server

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server (development with auto-reload)
py main.py

# Or explicitly via uvicorn
uvicorn main:app --reload --port 8000

# Or via Docker
docker-compose up --build
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=term-missing

# Run a specific test file
pytest tests/test_flow_runner.py -v

# Run via the test runner script
py tests/run_tests.py

# Run e2e tests (requires a live server at localhost:8000)
NEUROCORE_RUN_E2E=1 pytest tests/test_e2e.py -v
```

### Linting

```bash
# Ruff is configured in pyproject.toml
ruff check .
ruff format .
```

---

## Configuration

All runtime configuration lives in **`settings.json`** (no `.env` files). Key fields:

```json
{
  "llm_api_url": "http://localhost:1234/v1",
  "llm_api_key": "",
  "default_model": "local-model",
  "embedding_api_url": "",
  "embedding_model": "",
  "active_ai_flows": [],
  "temperature": 0.7,
  "max_tokens": 2048,
  "debug_mode": false,
  "ui_wide_mode": false,
  "ui_show_footer": true,
  "request_timeout": 60.0,
  "max_node_loops": 100,
  "module_allowlist": []
}
```

- Settings are managed by `core/settings.py` using a thread-safe `threading.RLock`.
- Writes use atomic tempfile + rename to prevent corruption.
- `module_allowlist` is a **runtime security control**: when non-empty, only listed module IDs can be hot-loaded. Set this in production to restrict the attack surface. Empty = allow all modules.
- `debug_mode: true` enables per-node execution tracing to `data/execution_trace.jsonl` and triggers `importlib.reload()` on every executor load.

---

## Core Architecture Concepts

### Flow Engine (DAG Execution)

All workflows are **Directed Acyclic Graphs (DAGs)** executed by `core/flow_runner.py`:

- Nodes are ordered using **Kahn's topological sort algorithm**.
- **Bridge groups** enable implicit parallelism via BFS component grouping.
- **Conditional routing** is driven by the `_route_targets` key in node output.
- **Loop guard:** `max_node_loops` counter (default 100, max 1000) prevents infinite loops.
- **Executor cache:** Class-level FIFO cache (max 100 entries) avoids re-instantiation.

**`FlowRunner.run()` key parameters:**

| Parameter | Type | Purpose |
|-----------|------|---------|
| `timeout` | `float \| None` | Wraps execution in `asyncio.wait_for`; raises `asyncio.TimeoutError` |
| `raise_errors` | `bool` | `True` propagates node exceptions; `False` returns error dicts |
| `episode_id` | `str \| None` | Restores `EpisodeState` from `data/episodes/` before execution |

### Episode System

Long-running agent tasks persist state via `EpisodeState` (stored in `data/episodes/`):

- Contains: `plan`, `current_step`, `completed_steps`, `phase`
- Valid phases: `PHASE_PLANNING`, `PHASE_EXECUTING`, `PHASE_REPLANNING`, `PHASE_COMPLETED`, `PHASE_FAILED`, `PHASE_PAUSED`
- Pass `episode_id` to `FlowRunner.run()` to resume a paused or in-progress episode.

### Node Executor Contract

Every flow node implements this async interface:

```python
class NodeExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict | None:
        # Process input.
        # Return None to stop this branch (conditional logic).

    async def send(self, processed_data: dict) -> dict:
        # Finalize output for downstream nodes.
```

### Reserved Data Keys

These keys are used across nodes and must not be repurposed:

| Key | Owner | Purpose |
|-----|-------|---------|
| `messages` | All nodes | Conversation history — preserved across all nodes |
| `_memory_context` | Memory Recall | Injected retrieved memories |
| `_kb_context` | Knowledge Query | Knowledge base retrieval results |
| `_route_targets` | Conditional node | Dynamic branching targets |
| `tool_calls` | LLM Core | LLM-requested tool invocations |
| `tool_results` | Tool Dispatcher | Tool execution results |
| `requires_continuation` | Agent Loop | Multi-turn tool loop flag |
| `_input_source` | Flow Runner | Tracks the originating source node |
| `_strip_messages` | Internal | Signals that messages should be stripped before output |
| `_is_error` | Internal | Marks the payload as an error response |

### Module System

Modules are self-contained directories under `modules/`. Each has:
- `module.json` — Metadata, enabled flag, node definitions, allowed config keys
- A Python router (`router.py`) and/or node executors

Modules can be hot-loaded and hot-unloaded at runtime without restarting the server. See `docs/MODULE_GUIDE.md` for the full development guide.

**Hot-reload safety:** `ModuleManager` uses a `_loaded_once` set to distinguish first load from subsequent reloads. On first load, it does **not** flush `sys.modules` (prevents breaking already-imported submodules in tests). On re-load after explicit unload, it flushes to pick up code changes. Never clear `_loaded_once` directly.

### Memory System

Two memory layers backed by FAISS + SQLite:

1. **Long-term Memory** (`modules/memory/`) — Stores facts, beliefs, preferences, goals with TTL. Uses FAISS `IndexFlatIP` with L2 normalization for vector search + SQLite for structured queries.
2. **Knowledge Base** (`modules/knowledge_base/`) — RAG document ingestion. Hybrid search with **Reciprocal Rank Fusion (RRF, k=60)** combining FAISS vector search and SQLite FTS5 keyword search.

### Session Compaction

When a chat session's token count exceeds `auto_compact_tokens`, the LLM summarizes older messages into a single system message, keeping the most recent `compact_keep_last` turns verbatim. This prevents context overflow in long conversations. Both thresholds are configurable per chat module config.

### Tool Sandbox

All custom Python tools (`modules/tools/`) execute in a **restricted sandbox** that blocks:
- Dangerous imports: `os`, `sys`, `subprocess`, `socket`, `shutil`, `importlib`, and 15+ more
- Network calls, file system writes, and process spawning
- Violations raise `SandboxSecurityError`

See `docs/TOOL_GUIDE.md` for the sandbox architecture and the 23 built-in tools reference (16 standard + 7 RLM tools in `modules/tools/rlm_library/`).

---

## Thread Safety Rules

> Critical — read `docs/CONCURRENCY.md` before modifying any shared state.

| Lock Type | Used For | Rule |
|-----------|----------|------|
| `threading.RLock` | FlowManager, ModuleManager, SettingsManager, SessionPersistenceManager | Safe for nested (reentrant) access |
| `threading.Lock` | Metrics, SessionManager, ChatSessions (bridged via `asyncio.to_thread`), singleton guards | Non-reentrant; never re-acquire in same thread |
| `asyncio.Lock` | LLM client, FlowRunner cache (per event-loop), ReasoningBook | Must be `await`ed inside `async` functions |

**Never** `await` while holding a `threading.Lock` — this causes deadlocks.

**Lock ordering (to prevent deadlocks):**
1. `module_manager._lock`
2. `flow_manager._lock`
3. `settings._lock`

---

## Adding a New Module

1. Create `modules/<name>/` directory.
2. Add `module.json` with `name`, `enabled`, `order`, `description`, and optionally `nodes` and `config`.
3. Add `__init__.py`.
4. Implement node executors (inherit `NodeExecutor` interface) and/or a FastAPI `router.py`.
5. Register the router in `module.json` → `"router": "modules.<name>.router"`.
6. Write tests in `tests/test_<name>_module.py`.

Refer to `docs/MODULE_GUIDE.md` for detailed examples and patterns.

---

## Adding a New Tool

**Standard tool (in `tools_library.py`):**
1. Edit `modules/tools/tools.json` to add the tool's JSON schema definition.
2. Implement the handler in `modules/tools/tools_library.py`.
3. Write tests covering both success and sandboxed failure paths.

**RLM tool (in `rlm_library/`):**
1. Add tool schema to `modules/tools/rlm_library/rlm_tools.json`.
2. Implement the handler in `modules/tools/rlm_library/rlm_library.py`.
3. Write tests as above.

Refer to `docs/TOOL_GUIDE.md` for the full API.

---

## API Conventions

- **HTMX partials** — Most endpoints return rendered HTML fragments (not JSON).
- **JSON API** — Data manipulation endpoints return JSON.
- **File uploads** — Use `multipart/form-data`.
- **Debug endpoints** — Protected by optional API key (set in settings).

**Key route groups:**
- `/` — Dashboard
- `/modules/*` — Module management (list, enable, disable, configure)
- `/ai-flow/*` — Flow CRUD (save, validate, run, delete)
- `/settings/*` — Configuration (get, save, reset, export, import)
- `/debug/*` — Debug logging and event inspection

---

## Testing Conventions

- All test files live in `tests/` and are named `test_<area>.py`.
- Use `pytest-asyncio` for async tests — `asyncio_mode = "auto"` is set globally in `pyproject.toml`. **Never add `@pytest.mark.asyncio`** — it is redundant and can cause conflicts.
- Use `pytest-httpx` (`httpx.MockTransport` / `respx`) to mock LLM API calls.
- Security tests (especially sandbox enforcement) live in `tests/test_tool_sandbox.py`.
- Concurrency/stress tests live in `tests/test_core_concurrency.py`.
- **e2e tests** (`tests/test_e2e.py`) require a live server and are gated behind `NEUROCORE_RUN_E2E=1`. They are intentionally skipped in normal CI — do not remove these skip marks.
- `conftest.py` automatically backs up and restores `module.json` files around each test session to prevent test pollution of module state.

---

## Observability

`core/observability.py` provides:
- **Distributed tracing** — Span-based trace context
- **Structured JSON logging** — Machine-readable log output
- **Metrics collection** — Counters, gauges, histograms
- **Debug events** — Inspectable via `/debug/*` endpoints

Enable verbose output by setting `debug_mode: true` in `settings.json`. This also writes per-node execution traces to `data/execution_trace.jsonl`.

---

## Common Pitfalls

1. **Mixing sync and async locks** — Always use `asyncio.Lock` inside `async` functions; use `threading.RLock` in synchronous code only.
2. **Mutating `messages` in place** — Always copy the list before modifying to avoid cross-node state corruption.
3. **Blocking I/O in async handlers** — Use `asyncio.to_thread()` for any blocking file/DB operations in async context.
4. **Skipping `module.json` validation** — The module loader validates `module.json` strictly; missing required fields will prevent hot-load.
5. **Hardcoding `settings.json` paths** — Always use `core/settings.py` API; never read the file directly.
6. **Returning `None` unintentionally from `receive()`** — A `None` return stops the execution branch. Return `{}` if you want to continue with empty data.
7. **Adding `@pytest.mark.skip` to silence flaky tests** — Don't paper over flakiness with skip marks. Investigate the root cause and fix it. Stale skip marks accumulate and hide real coverage gaps.

---

## Key Files to Read First

When starting work on a new feature, read these files to understand the system:

| File | Why |
|------|-----|
| `core/flow_runner.py` | Execution engine — understand before changing any node behavior |
| `core/errors.py` | Exception hierarchy — raise the right error type |
| `core/settings.py` | Config access patterns |
| `core/planner_helpers.py` | Plan dependency graph logic — read before touching agent loop or planner |
| `docs/CONCURRENCY.md` | Lock rules — mandatory before touching shared state |
| `docs/MODULE_GUIDE.md` | Module development patterns |
| `docs/TOOL_GUIDE.md` | Tool/sandbox patterns |
