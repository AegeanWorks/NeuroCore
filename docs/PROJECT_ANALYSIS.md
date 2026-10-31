# NeuroCore Project Analysis

## Executive Summary

NeuroCore is a **modular, extensible AI orchestration platform** built with Python/FastAPI. It provides a visual flow-based system for building autonomous AI agents with persistent memory, tools, knowledge bases, and multi-platform messaging integrations. The platform features a sophisticated hybrid concurrency model, comprehensive observability infrastructure, and real-time streaming capabilities.

**Scale (March 2026):**

| Metric | Value |
|--------|-------|
| Active modules | 18 |
| Node executors | 28 |
| HTTP routes (core) | 74 |
| HTTP routes (total) | 167 |
| Test files | 72 |
| Test cases | 1,141+ |
| Web templates | 39 |
| Built-in tools | 23 (16 standard + 7 RLM) |
| Total Python files | 170+ |
| Lines of code | ~45,000 |

---

## 1. Architecture Overview

### 1.1 System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PRESENTATION LAYER                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  Chat UI    │  │ Flow Editor │  │ Memory      │  │  Module Dashboard   │ │
│  │  (HTMX/SSE) │  │ (Canvas)    │  │ Browser     │  │  (Settings)         │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
└─────────┼────────────────┼────────────────┼────────────────────┼────────────┘
          │                │                │                    │
          └────────────────┴────────────────┴────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CORE LAYER                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ FlowRunner  │  │FlowManager  │  │ModuleManager│  │  SettingsManager│  │
│  │ (Execution) │  │(Storage/Ver)│  │ (Lifecycle) │  │  (Config)       │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘  │
│         │                │                │                  │           │
│         └────────────────┴────────────────┴──────────────────┘           │
│                                    │                                      │
│                                    ▼                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │ LLMBridge   │  │Observability│  │ Routers     │  │ Dependencies│   │
│  │ (API Client)│  │(Trace/Metr.)│  │ (74 routes) │  │ (DI)        │   │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MODULE LAYER (17 Modules)                            │
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │llm_module│  │  system  │  │  memory  │  │  tools   │  │  chat    │   │
│  │  (Core)  │  │  _prompt │  │ (Vector) │  │(Sandbox) │  │  (I/O)   │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  logic   │  │ planner  │  │reflection│  │knowledge │  │ agent_   │   │
│  │(Control) │  │(Planning)│  │(Quality) │  │  _base   │  │  loop    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │messaging │  │ calendar │  │  skills  │  │reasoning │  │ browser_ │   │
│  │  _bridge │  │ (Events) │  │(Instruct)│  │  _book   │  │  auto    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                                │
│  │ memory_  │  │annotatio │  │  email_  │                                │
│  │ browser  │  │   ns     │  │  bridge  │                                │
│  └──────────┘  └──────────┘  └──────────┘                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA LAYER                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │
│  │  SQLite     │  │   FAISS     │  │   JSON      │  │   JSONL     │      │
│  │ (Metadata)  │  │ (Vectors)   │  │ (Config)    │  │  (Traces)   │      │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend** | Python 3.12+ | Core runtime |
| **Web Framework** | FastAPI 0.115+ | HTTP API & routing |
| **Frontend** | HTMX + Tailwind CSS | Dynamic UI without JS framework |
| **Templating** | Jinja2 3.1+ | HTML generation (39 templates) |
| **Database** | SQLite (WAL mode, FTS5) | Metadata, memory, knowledge base |
| **Vector DB** | FAISS IndexFlatIP | Similarity search |
| **LLM Client** | httpx 0.28+ | Async API calls with connection pooling |
| **Concurrency** | asyncio + threading | Hybrid async/sync model |
| **Data Validation** | Pydantic 2.10+ | Schema enforcement |
| **WebSocket** | websockets 12.0+ | Discord Gateway, custom protocols |
| **Testing** | pytest + pytest-asyncio | Test framework (1,141+ tests) |
| **Containers** | Docker + docker-compose | Deployment |

### 1.3 Project Structure

```
NeuroCore/
├── main.py                    # FastAPI app entry point, lifespan management
├── core/                      # Core framework (15+ files)
│   ├── flow_runner.py        # DAG execution engine
│   ├── flow_manager.py       # Flow CRUD + version history
│   ├── module_manager.py     # Dynamic module loading
│   ├── llm.py                # LLM API client (OpenAI-compatible, async)
│   ├── settings.py           # Thread-safe config (RLock, atomic write)
│   ├── observability.py      # Tracing, metrics, structured JSON logging
│   ├── structured_output.py  # Pydantic schema enforcement (retry logic)
│   ├── session_manager.py    # Chat session + EpisodeState persistence
│   ├── debug.py              # Structured debug logging
│   ├── routers.py            # 74 HTTP endpoints
│   ├── dependencies.py       # FastAPI dependency injection
│   ├── errors.py             # 14 typed exception classes
│   ├── flow_data.py          # FlowData TypedDict + helper functions
│   ├── flow_context.py       # FlowContext Pydantic model
│   ├── planner_helpers.py    # PlanHelper: dependency graphs, cycle detection
│   └── schemas/              # Domain models for scientific research
├── modules/                  # 18 extensible modules
│   ├── llm_module/          # Core LLM interface (streaming, tool calling, vision)
│   ├── agent_loop/          # Autonomous agent loop (3 nodes)
│   ├── system_prompt/       # Context injection, tool registration
│   ├── logic/               # Control flow nodes (7 nodes)
│   ├── reflection/          # Quality assurance node
│   ├── planner/             # Task planning nodes (2 nodes)
│   ├── tools/               # Tool library, dispatcher, sandbox (23 tools)
│   ├── skills/              # Instruction file management
│   ├── knowledge_base/      # RAG system (hybrid FTS5 + FAISS)
│   ├── memory/              # Vector memory (3 nodes + arbiter + consolidation)
│   ├── calendar/            # Event scheduling
│   ├── chat/                # Chat I/O + session management + compaction
│   ├── messaging_bridge/    # Unified messaging (Telegram/Discord/Signal/WhatsApp)
│   ├── browser_automation/  # Headless browser singleton (Playwright)
│   ├── memory_browser/      # Memory management UI
│   ├── reasoning_book/      # Thought journaling
│   └── annotations/         # Flow documentation nodes
├── web/templates/            # 39 Jinja2 HTML templates
├── data/                     # Runtime data (mutable, excluded from reloader)
│   ├── memory.faiss          # Long-term memory vector index
│   ├── memory.sqlite3        # Long-term memory relational store
│   ├── knowledge_base.faiss  # RAG vector index
│   ├── knowledge_base.sqlite3 # RAG relational store (FTS5)
│   ├── reasoning_book.json   # Thought log
│   ├── execution_trace.jsonl # Per-node execution trace (debug only)
│   ├── session.json          # Session persistence
│   ├── episodes/             # EpisodeState files for long-running tasks
│   ├── marketplace/          # Community marketplace uploads and catalog
│   │   ├── catalog.json      # Index of all published items
│   │   └── uploads/          # Uploaded item files
│   ├── marketplace_profile.json       # Local uploader identity (handle, username, bio)
│   ├── marketplace_notifications.json # In-app notification queue (capped at 200)
│   └── download_history.json          # Import tracking for dedup
├── tests/                    # 72 test files, 1,141+ tests
├── docs/                     # Documentation
├── ai_flows.json             # Saved flow definitions
├── ai_flows_versions.json    # Flow version history (up to 20 versions/flow)
├── chat_sessions.json        # Chat session history
├── settings.json             # Runtime configuration
├── pyproject.toml            # Project metadata, pytest config, ruff rules
├── requirements.txt          # Runtime dependencies
├── Dockerfile
└── docker-compose.yml
```

---

## 2. Core Components Deep Dive

### 2.1 FlowRunner (`core/flow_runner.py`)

**Purpose**: Executes AI flows as directed acyclic graphs (DAGs)

**Key Features**:
- Kahn's topological sort for execution ordering
- BFS-based bridge group detection for shared data
- Per-event-loop executor cache (max 100 entries, FIFO eviction at 20%)
- Deep-copies `messages` list before each node to prevent cross-node mutation
- Debug mode forces `importlib.reload()` on every executor load for hot-reloading

**`run()` Signature**:
```python
async def run(
    self,
    initial_input: dict,
    start_node_id: str = None,
    timeout: float = None,
    raise_errors: bool = False,
    episode_id: str = None,
) -> dict
```

**Execution Flow**:
```
1. [Optional] Load EpisodeState from data/episodes/ → inject into initial_input
2. Load flow definition (nodes, connections, bridges)
3. Build bridge groups using BFS connected-component analysis
4. Compute topological execution order (Kahn's algorithm)
5. For each node in order:
   a. Collect input from upstream nodes (or initial_input for source nodes)
   b. Merge bridge group data
   c. Deep-copy messages list to prevent cross-node mutation
   d. Execute node.receive() → node.send()
   e. Auto-propagate `messages` key if output omits it
   f. Store output for downstream nodes
6. Return final output dict
```

### 2.2 FlowManager (`core/flow_manager.py`)

**Purpose**: CRUD operations for AI flows with version control

**Key Features**:
- Atomic JSON writes (tempfile + os.replace)
- Flow version history in `ai_flows_versions.json` (up to 20 versions per flow)
- Version restore with confirmation UI
- Orphaned node detection during validation
- Auto-creates default flow on first run

**Default Flow Structure**:
- Chat Input → Memory Recall → System Prompt → LLM Core → Conditional Router
- Tool Dispatcher (conditional true branch)
- Chat Output + Messaging Output (parallel terminal nodes)
- Memory Save (side effect terminal)

### 2.3 ModuleManager (`core/module_manager.py`)

**Purpose**: Dynamic module loading and lifecycle management

**Key Features**:
- Hot-swapping: enable/disable modules without restart
- `_loaded_once` set prevents accidental `sys.modules` flush on first import
- `module_allowlist` security control from `settings.json`
- Load errors stored in `_load_errors` dict only — never written to `module.json`
- `DISABLED` file marker in module directory skips discovery entirely

**Module States**:
```
DISABLED (file marker) → DISCOVERED → ENABLED → LOADED → ACTIVE
                              ↑________↓ (hot-swap supported)
```

### 2.4 LLMBridge (`core/llm.py`)

**Purpose**: Unified async interface to OpenAI-compatible LLM APIs

**Key Features**:
- Shared `httpx.AsyncClient` with connection pooling (20 keepalive, 100 total)
- Lazy-initialized singleton with `asyncio.Lock` guard
- Supports streaming responses via SSE
- Separate embedding endpoint support
- Token usage tracking per request
- Configurable timeout (default 60s)

### 2.5 EpisodeState (`core/session_manager.py`)

**Purpose**: Persist long-running agent task state across flow executions

**Key Fields**:
- `episode_id` — unique identifier
- `plan` — list of steps
- `current_step` — current step index
- `completed_steps` — set of completed step indices
- `phase` — lifecycle phase (`PHASE_PLANNING`, `PHASE_EXECUTING`, `PHASE_REPLANNING`, `PHASE_COMPLETED`, `PHASE_FAILED`, `PHASE_PAUSED`)
- `max_context_tokens` — token budget for context window

**Storage**: `data/episodes/{episode_id}.json`

### 2.6 Observability (`core/observability.py`)

**Three Pillars**:

| Component | Description |
|-----------|-------------|
| **Distributed Tracing** | Span-based traces with trace_id/span_id propagation via contextvars |
| **Metrics** | Counters, gauges, histograms with p50/p95/p99 percentiles. Counters are persisted to disk across restarts. |
| **Structured Logging** | JSON-formatted logs with trace context correlation |

**Auto-Instrumentation**: Patches FlowRunner.run(), LLMBridge calls, and ToolDispatcher.

### 2.7 Scientific Schemas (`core/schemas/`)

Domain models for academic research, usable as structured output targets with `core/structured_output.py`:

| Schema | Purpose |
|--------|---------|
| `Hypothesis` | Scientific hypotheses with variables and confidence levels |
| `Article` | Academic articles with bibliographic info and citations |
| `Finding` | Research findings with evidence linking |
| `StudyDesign` | Scientific study designs with methodology |

---

## 3. Module Analysis

### 3.1 Complete Module Registry

| Module | Order | Nodes | Purpose |
|--------|-------|-------|---------|
| `llm_module` | 0 | 1 | Core LLM interface with streaming |
| `agent_loop` | 1 | 3 | Autonomous agent loop |
| `system_prompt` | 2 | 1 | Context injection, tool registration |
| `logic` | 3 | 7 | Control flow (Delay, Repeater, Conditional, Script, Schedule, ContextLengthRouter) |
| `reflection` | 4 | 1 | Quality assurance |
| `planner` | 5 | 2 | Task decomposition |
| `tools` | 6 | 1 | Tool dispatcher + sandbox |
| `skills` | 7 | 0 | Instruction file management |
| `knowledge_base` | 9 | 1 | RAG document query |
| `memory` | 11 | 3 | Vector long-term memory |
| `calendar` | 13 | 1 | Event scheduling |
| `chat` | 14 | 2 | Chat I/O + session compaction |
| `messaging_bridge` | 15 | 2 | Telegram/Discord/Signal/WhatsApp |
| `browser_automation` | 15 | 0 | Headless browser singleton |
| `email_bridge` | 16 | 2 | IMAP receive + SMTP send |
| `memory_browser` | — | 0 | Memory management UI |
| `reasoning_book` | — | 2 | Thought journaling |
| `annotations` | — | 1 | Flow comment nodes |

### 3.2 Key Module Deep Dives

#### Agent Loop (`modules/agent_loop/`)

**3 Nodes**: `agent_loop`, `recursive_lm`, `repl_environment`

The `AgentLoopExecutor` runs an autonomous LLM↔Tool loop until the LLM stops generating tool calls or `max_iterations` is reached. Key behaviour:
- Builds its own messages array with system prompt, memory context, plan context
- Executes tool calls via the sandbox
- Supports `tool_error_strategy`: `"continue"` (default) or `"stop"`
- Emits thinking trace events consumed by the chat UI streaming endpoint

**Configuration**:
```json
{
    "max_iterations": 10,
    "max_llm_retries": 3,
    "retry_delay": 1.0,
    "tool_error_strategy": "continue",
    "timeout": 120,
    "include_plan_in_context": true,
    "include_memory_context": true,
    "include_knowledge_context": true
}
```

#### Memory System (`modules/memory/`)

**3 Nodes**: `memory_recall`, `memory_save`, `check_goal`

**Dual storage**:
- FAISS `IndexFlatIP` + L2 normalization for vector similarity
- SQLite with FTS5 for structured queries

**Memory Types**: BELIEF (30-day TTL), FACT, RULE, EXPERIENCE, PREFERENCE, IDENTITY

**Consolidation Algorithm**:
1. Compute cosine similarity matrix for all active memories
2. For pairs with similarity > 0.92: use LLM to verify equivalence
3. Link older memory as child of newer; remove from FAISS
4. Runs automatically every 24 hours as a background task

**Memory Arbiter**: LLM-based quality control that detects contradictions and suggests updates before saving.

#### Tools System (`modules/tools/`)

**1 Node**: `tool_dispatcher`

**Sandbox Security Layers**:
| Layer | Mechanism |
|-------|-----------|
| Static Analysis | Regex scanning for dangerous patterns before execution |
| Import Restrictions | Blocks 18+ dangerous modules (os, sys, subprocess, socket, shutil, importlib, pickle, ctypes, mmap, multiprocessing, pathlib, ...) |
| Restricted Builtins | Removes eval, exec, open, `__import__`, compile |
| File Access | SafeOpen with directory whitelist |
| Network | SafeHttpxClient with domain allowlist |
| SSRF Protection | Blocks private IP ranges (127.x, 10.x, 192.168.x, 172.16–31.x) |
| Resource Limits | Configurable timeout (default 30s), max output 100 KB |

#### Messaging Bridge (`modules/messaging_bridge/`)

**2 Nodes**: `messaging_input`, `messaging_output`

Unified interface for 4 messaging platforms through a single module. Each platform has its own bridge class and listener strategy:

| Platform | Bridge Class | Strategy | Chunk Limit |
|----------|-------------|----------|-------------|
| Telegram | `TelegramBridge` | HTTP long-polling thread | 3,072 chars |
| Discord | `DiscordBridge` | WebSocket Gateway v10 thread | 1,900 chars |
| Signal | `SignalBridge` | HTTP polling thread (signal-cli REST) | 1,800 chars |
| WhatsApp | `WhatsAppBridge` | Webhook-driven (Evolution API) | 4,000 chars |

`MESSAGING_PLATFORMS` in `node.py` is the single source of truth — adding a new platform requires one list entry + one bridge class.

**`messaging_output` config**:
- `platform`: `"auto"` (reply to sender) or a specific platform ID
- `proactive_recipients`: `["telegram:123456", "discord:987654"]` — used when no reply context exists (e.g., Repeater-triggered flows)

#### Chat Module (`modules/chat/`)

**2 Nodes**: `chat_input`, `chat_output`

**Session Compaction**: When token count exceeds `auto_compact_tokens`, older messages are summarized into a single system message by the LLM; the last `compact_keep_last` turns are preserved verbatim. Prevents context overflow in long conversations.

**Real-time Streaming**: The chat UI uses SSE (Server-Sent Events) to stream LLM responses token-by-token and display agent thinking traces in real time.

#### Logic Module (`modules/logic/`)

**7 Nodes**:

| Node | Purpose |
|------|---------|
| `trigger_node` | Manual pass-through trigger |
| `delay_node` | Configurable sleep (seconds) |
| `script_node` | Custom Python execution (sandboxed) |
| `repeater_node` | Background loop with configurable interval and max count |
| `conditional_router` | Branch on field existence (`tool_calls`, `satisfied`, `requires_continuation`, `max_tools_per_turn`) |
| `schedule_start_node` | Wait until a specific date/time before continuing |
| `context_length_router` | Route to RLM (RecursiveLM) or standard LLM based on estimated context length |

#### Browser Automation (`modules/browser_automation/`)

Provides a lazy-initialized Playwright singleton (`headless=true` by default). Currently no flow nodes — the browser is available as a service object but cannot be used directly in flows. See IDEAS.md for the planned browser node executors.

#### Email Bridge (`modules/email_bridge/`)

**2 Nodes**: `email_input`, `email_output`

Bridges IMAP/SMTP email into NeuroCore flows. `ImapBridge` polls the inbox for new messages; `SmtpBridge` sends outbound emails. Mirrors the `messaging_bridge` pattern and is configured via `module.json` with IMAP host, SMTP host, credentials, and polling interval.

---

### 3.3 Community Marketplace

The marketplace is implemented entirely in `core/routers.py` (no separate module) and uses the data layer under `data/marketplace/`.

**Key features:**
- **Item types**: flows, skills, tools, prompts
- **HMAC uploader identity**: immutable 12-char hex handle per instance, editable display name and bio
- **Comments + @mentions**: comment threads per item; typing `@handle` triggers autocomplete
- **Notifications**: server-side triggers for comments on own items and `@mention`s in any comment
- **Voting**: upvote/downvote per item with score tracking
- **Versioning**: each publish update prepends a `{version, notes, timestamp}` entry to `item["changelog"]`
- **Originality enforcement**: imported items are tagged `"marketplace_import"`; backend rejects re-upload of unmodified imports
- **Import-to-NeuroCore**: type-aware import handler per item type (skill copy, flow import, tool registration with filelock, prompt copy)
- **Block/unblock uploaders**: per-handle block list persisted per instance
- **Community profiles**: public uploader pages with item grid, stats, and optional bio

**New data files:**

| File | Purpose |
|------|---------|
| `data/marketplace/catalog.json` | All published items |
| `data/marketplace/uploads/` | Item files on disk |
| `data/marketplace_profile.json` | Local identity (handle, username, description) |
| `data/marketplace_notifications.json` | Notification queue, capped at 200 entries |
| `data/download_history.json` | Import dedup tracking (item_id → `{version, timestamp}`) |

---

## 4. Data Flow Analysis

### 4.1 Standard Chat Flow

```
User Input
    │
    ▼
[chat_input] → [memory_recall] → [system_prompt] → [llm_core]
                                                        │
                                    ┌───────────────────┘
                                    ▼
                            [conditional_router] → (tool_calls?)
                                    │                    │
                                    │ no                 │ yes
                                    ▼                    ▼
                            [chat_output]         [tool_dispatcher]
                            [memory_save]               │
                                                         └→ [llm_core] (loop)
```

### 4.2 Autonomous Agent Flow

```
[repeater_node] (every N seconds)
    │
    ▼
[memory_recall] → [system_prompt] → [agent_loop]
                                        │
                    ┌───────────────────┘
                    │  (tool loop inside agent_loop)
                    ▼
            [messaging_output]
            (proactive_recipients: ["telegram:123456"])
```

This is the simplest autonomous agent pattern — no new modules required. The `repeater_node` provides autonomous wakeup; `agent_loop` provides the reasoning loop; `messaging_output` with `proactive_recipients` sends the result without needing an incoming message.

### 4.3 RAG + Planning Flow

```
[chat_input] → [query_knowledge] → [memory_recall] → [system_prompt]
                                                           │
                                                           ▼
                                                     [planner]
                                                           │
                                                           ▼
                                                    [agent_loop]
                                                           │
                                                           ▼
                                               [plan_step_tracker]
                                                           │
                                                    (all steps done?)
                                                           │
                                                           ▼
                                                    [chat_output]
```

---

## 5. Security Analysis

### 5.1 Tool Sandbox

The sandbox in `modules/tools/sandbox.py` enforces a principle of least privilege. All tool code (built-in and user-created) executes in this environment.

**Threat Model**:
| Threat | Mitigation |
|--------|-----------|
| Code Injection | Static analysis + restricted builtins (no eval/exec) |
| File Traversal | Path normalization + directory whitelist via SafeOpen |
| Data Exfiltration | Domain allowlist via SafeHttpxClient |
| SSRF | IP range blocking of all private/loopback addresses |
| Resource Exhaustion | Per-execution timeout (30s) + max output size (100 KB) |
| Privilege Escalation | No access to os, sys, subprocess, importlib |

### 5.2 Module Allowlist

`settings.json` `module_allowlist` controls which modules can be hot-loaded at runtime. When non-empty, only listed module IDs are permitted. This is a production security control to restrict the attack surface.

### 5.3 Sensitive Config Protection

`core/routers.py` defines `HIDDEN_CONFIG_KEYS` per module — a dict mapping module IDs to lists of config keys that are redacted from the generic JSON editor in the UI. This prevents API keys, bot tokens, and phone numbers from appearing in plaintext in config forms.

---

## 6. Testing Strategy

### 6.1 Overview

| Category | Files | Tests |
|----------|-------|-------|
| Core flow execution | 6 | 120+ |
| Chat | 6 | 90+ |
| Agent loop | 3 | 60+ |
| Memory | 6+ | 100+ |
| Tools & sandbox | 3 | 80+ |
| Individual modules | 14 | 220+ |
| LLM bridge | 4 | 60+ |
| Concurrency | 2 | 40+ |
| Messaging bridge | 1 | 63 |
| E2E (live server) | 1 | gated |
| **Total** | **72** | **1,141+** |

### 6.2 Testing Infrastructure

- `asyncio_mode = "auto"` in `pyproject.toml` — **never add `@pytest.mark.asyncio`**
- `conftest.py` auto-backs up and restores `module.json` files during test sessions
- `pytest-httpx` for mocking LLM API calls
- E2E tests (`test_e2e.py`) require `NEUROCORE_RUN_E2E=1` and a live server at `localhost:8000`
- Run tests: `py tests/run_tests.py` or `pytest`

### 6.3 Known Test Issues

| File | Issue |
|------|-------|
| `test_chat_router.py` | 404 errors — router not registering in TestClient lifecycle (module loading requires full FastAPI lifespan) |
| `test_core_robustness.py` | Thread-safety test failures under concurrent load |
| `test_e2e.py` | Requires live server — skipped in normal CI |

---

## 7. Configuration System

### 7.1 Settings Hierarchy

```
DEFAULT_SETTINGS (hardcoded)
    ↓
settings.json (user config)
    ↓
module.json (module defaults)
    ↓
Node config (set in flow editor)
    ↓
Input data (runtime)       ← highest priority
```

### 7.2 Key Configuration Files

| File | Purpose |
|------|---------|
| `settings.json` | Global application settings |
| `ai_flows.json` | Flow definitions |
| `ai_flows_versions.json` | Flow version history (up to 20 per flow) |
| `chat_sessions.json` | Chat session history |
| `modules/*/module.json` | Module metadata and default config |
| `modules/tools/tools.json` | Tool definitions (JSON schema + code) |
| `data/memory.sqlite3` | Long-term memory relational store |
| `data/memory.faiss` | Long-term memory vector index |
| `data/knowledge_base.sqlite3` | RAG relational store (FTS5) |
| `data/knowledge_base.faiss` | RAG vector index |
| `data/execution_trace.jsonl` | Per-node execution traces (debug_mode only) |
| `data/episodes/` | EpisodeState files for long-running tasks |

---

## 8. Concurrency Model

See [CONCURRENCY.md](./CONCURRENCY.md) for the full reference.

### 8.1 Lock Types Summary

| Lock | Owner | Purpose |
|------|-------|---------|
| `threading.RLock` | FlowManager, ModuleManager, SettingsManager, SessionPersistenceManager | Synchronous shared state |
| `threading.Lock` | Metrics, SessionManager (instance), `_init_lock` (singleton guard) | Single-level guards |
| `asyncio.Lock` | LLMBridge, FlowRunner cache (per loop), ChatSessions (via to_thread), ReasoningBook | Async resources |

### 8.2 Lock Ordering (deadlock prevention)

1. `module_manager._lock`
2. `flow_manager._lock`
3. `settings._lock`

Never `await` while holding a `threading.Lock`.

---

## 9. Performance Characteristics

### 9.1 Bottlenecks

| Component | Bottleneck | Mitigation |
|-----------|-----------|------------|
| LLM Calls | Network latency | Async execution, streaming |
| Vector Search | FAISS index size | IndexFlatIP with L2 normalization; periodic compaction |
| Memory Consolidation | LLM verification calls | 24h background interval |
| Tool Execution | Python sandbox overhead | Timeout limits; executor cache |
| Chat Sessions | Session JSON file growth | Periodic compaction; `compact_keep_last` setting |

### 9.2 Optimizations

- **Executor Caching**: FIFO cache (max 100) avoids re-importing node classes
- **Connection Pooling**: Shared `httpx.AsyncClient` across all LLM calls
- **Async I/O**: Non-blocking LLM calls; `asyncio.to_thread()` for blocking file/DB ops
- **Incremental KB Indexing**: Knowledge base tracks last-indexed timestamp and re-indexes only changed documents (provenance tracking)
- **Lazy Loading**: Modules loaded only when enabled

---

## 10. Recent Development History

| Feature | Description |
|---------|-------------|
| Messaging Bridge | Unified Telegram/Discord/Signal/WhatsApp module replacing separate platform modules |
| Real-time Streaming | LLM token streaming + thinking trace in chat UI via SSE |
| Flow Versioning | Up to 20 versions per flow with restore and confirmation |
| LLM Token Tracking | Per-request token usage tracked and displayed in debug UI |
| Metrics Persistence | Observability counters persist across server restarts |
| Context Length Router | New logic node to route between standard LLM and RLM based on token estimate |
| Incremental KB Indexing | Knowledge base re-indexes only changed documents |
| Sandbox Domain Expansion | Configurable allowed domains for sandboxed tool HTTP calls |
| Agent Thinking Trace | Agent loop emits real-time thinking events visible in chat UI |
| Community Marketplace | Full marketplace for sharing/importing flows, skills, tools, and prompts with identity, comments, @mentions, notifications, voting, and versioning |
| Email Bridge | New `email_bridge` module with IMAP polling and SMTP sending, following the messaging_bridge pattern |
| Marketplace Tool Import | Tools imported from marketplace are fully registered in `tools.json` and written to `library/` with filelock safety |
| Uploader Profiles | Public uploader pages with immutable HMAC handle, editable display name and bio, item grid, and stats |
| Comment System | Per-item comment threads with @mention autocomplete, display names, delete-own-comment, and custom confirm modal |
| Changelog System | Publisher update notes per version, prepended to item changelog and displayed on item detail page |

---

## 11. Conclusion

NeuroCore is a **well-architected, production-ready AI orchestration platform** with strong foundations:

✅ Clean layered architecture (presentation → core → modules → data)
✅ Comprehensive error hierarchy with typed exceptions
✅ Thread-safe singleton patterns with correct lock ordering
✅ Hot-module loading with smart reload safety
✅ Extensive test coverage (1,141+ tests across 72 files)
✅ Async-first design with proper event loop handling
✅ Security-conscious tool sandbox (SSRF, import, file, resource limits)
✅ Rich observability (distributed tracing, metrics, debug logs)
✅ Real-time streaming (SSE for chat, thinking trace)
✅ Unified multi-platform messaging (4 platforms, single module)
✅ Flow version history with rollback

See [IDEAS.md](./IDEAS.md) for known bugs, planned improvements, and future feature ideas.

---

*Last updated: 2026-03-20*
*Based on exhaustive codebase review: 170+ Python files, 1,141+ tests, 18 modules*
