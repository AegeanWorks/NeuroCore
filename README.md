# NeuroCore

<p align="center">
  <img src="https://github.com/Aegean-E/NeuroCore/blob/main/banner.jpg?raw=true" alt="NeuroCore Banner" width="1200">
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com"><img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="https://htmx.org"><img src="https://img.shields.io/badge/HTMX-333333?style=for-the-badge&logo=htmx&logoColor=white" alt="HTMX"></a>
  <a href="https://tailwindcss.com"><img src="https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white" alt="Tailwind CSS"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=for-the-badge" alt="License"></a>
</p>

<h3 align="center">
  A complete, self-hosted AI agent framework.<br>
  Visual flow editor · Persistent memory · RAG · Tools · Multi-platform messaging · Community marketplace.
</h3>

---

## ⚡ 30-Second Cold-Start Quick Reference

- **Runtime Prerequisites**: Python 3.12+ and git.
- **Canonical Regression Shield Gate**:
  ```bash
  python -m pytest
  ```

### 🛠️ AIKit Commands (run from the AIKit folder)
| Role | Command |
|---|---|
| Status, task and gates | `python aikit.py status neurocore` |
| Phase 4: every gate | `python aikit.py check neurocore` |
| Repair memory and map, then check | `python aikit.py fix neurocore` |
| Resume an interrupted task | `python aikit.py resume neurocore` |

---

## 🚀 Quick Start

```bash
# 1. Clone & install
git clone https://github.com/Aegean-E/NeuroCore
cd NeuroCore
pip install -r requirements.txt

# 2. Point to your LLM (edit settings.json)
# "llm_api_url": "http://localhost:1234/v1"

# 3. Run
python main.py
# Windows convenience: start.bat runs the same command from its own folder.

# 4. Open http://localhost:8000
```

Or with Docker:
```bash
docker-compose up -d --build
```

Docker keeps all mutable state in `./data` (`NEUROCORE_STATE_DIR=/app/data`) and
mounts `./modules` so module configuration survives container recreation. The image holds
runtime dependencies only and runs as an unprivileged user; if your host user is not UID 1000,
start it with `NEUROCORE_UID=$(id -u) NEUROCORE_GID=$(id -g) docker-compose up -d --build`.
Compose publishes on `127.0.0.1` only and therefore sets
`NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE=1`; set `NEUROCORE_API_TOKEN` and drop that flag before
publishing on any other interface.
Browser automation additionally needs its browser binaries:

```bash
playwright install chromium
```

---

## 📦 What's Inside

```text
neurocore/
├── .aikit/                   <- AIKit project memory: ledger, guidelines, decisions
├── .dockerignore
├── .github/
├── .gitignore
├── CLAUDE.md
├── Dockerfile
├── LICENSE
├── README.md
├── start.bat
├── banner.jpg
├── docker-compose.yml
├── main.py                   <- FastAPI application entrypoint
├── pyproject.toml            <- package metadata & dependencies
├── requirements.txt          <- runtime + optional capabilities (mirrors pyproject.toml)
├── requirements-dev.txt      <- adds the test tools
├── core/                     <- engine, flow manager, runner, observability
├── data/                     <- runtime state (ignored; only the directory is tracked)
├── docs/                     <- architectural guides & module docs
├── modules/                  <- hot-swappable functional modules
├── screenshots/              <- UI and feature captures
├── tests/                    <- automated pytest test suite
└── web/                      <- frontend templates and static assets
```

---

## 🏗️ Architecture

NeuroCore is organized into **four clean, decoupled layers:**

1. **Presentation Layer**: HTMX + Jinja2 (39 templates) + TailwindCSS.
2. **Core Layer**: DAG flow engine (`flow_runner.py`), validated flow evolution (`flow_evolution.py`), module hot-swapper (`module_manager.py`), observability and tracing (`observability.py`), and settings.
3. **Module Layer**: 21 self-contained modules (`agent_loop`, `brain`, `memory`, `knowledge_base`, `tools`, `messaging_bridge`, `planner`, etc.).
4. **Data Layer**: SQLite (WAL + FTS5) + FAISS (`IndexFlatIP`) vector search.

### Flow Engine

Workflows are directed acyclic graphs executed deterministically using Kahn's topological sort and component bridge groups for parallel data sharing.

### Meta-Flows

Small flows are ganglia; a meta-flow joins them through pathways. Inline composition namespaces
each member's nodes (`g<index>__<id>`), rewires edges and bridges, adds pathway connections, and
validates the union as one executable flow. Live composition instead creates one `meta:subflow`
ganglion node per member and member-level pathways, so later edits to a member propagate. The
`Subflow (Ganglion)` node runs the referenced flow and guards against cycles and excess depth.
Metadata records `kind: meta_flow`, the mode, ganglia and pathways. Members with multiple
entry/exit nodes need explicit pathways; cycles and unknown endpoints are rejected.
`AgentService.compose_meta_flow` composes flows the agent owns, and the planner emits a
`COMPOSE_FLOW` action from structured `member_flows` metadata or from two or more flow ids in a
combine/compose/merge request; `live` selects reference-based composition.

### Self-Evolution

`core/flow_evolution.py` is the only write path for agent-initiated flow edits. A proposal is
validated for required node keys, unique ids, edge endpoints, self-loops, duplicate edges, graph
acyclicity, size and the agent's node-type and tool allowlists, then saved through the shared
`FlowManager`, which snapshots the previous state. `history` and `rollback` make every edit
auditable and reversible; the agent exposes `evolve_flow` and `rollback_flow` and keeps its flow
cache in sync. The agent can execute an owned flow through `FlowRunner`; actions without a flow id
fall back to the first existing active chat flow, explicit ids must be owned, active or the default
chat flow, and `activate_flow` makes an owned flow the first active flow. Flow lists are ordered by
last edit (`updated_at`) while creation time is preserved. `evolve_and_evaluate` runs an edit
against a test input and auto-reverts when it fails; gateway metadata can submit structured flow
operations with that input. `propose_evolution` asks the configured LLM for a bounded operation
list, and `propose_and_evolve` applies it through the same validation and evaluation path. The
authenticated human gateway remains a later phase.
Ownership metadata and `reverted_nodes` survive save, restart and rollback.
Arbitrary Python, settings or module-code edits stay outside this path. The agent remains disabled
by default until the human gateway and its authentication are defined.

### AI Brain (observe/suggest)

The `brain` module is the control substrate for a neocortex-style loop. It composes a real
self-model from live flows, module manifests, memory, knowledge-base, metrics and evaluation
state; failures are reported as provider errors rather than mock values. An in-process event bus
feeds a bounded cognitive scheduler, and `observe` cycles record state while `suggest` cycles add
pending proposals. Autonomous actuation (`auto`) is intentionally not implemented yet.

`GET /brain/state`, `GET /brain/events`, `POST /brain/start|stop|cycle|mode` and
`POST /brain/events` expose the substrate. `POST /brain/eval/run` runs the deterministic echo
suite (or a supplied flow id), `POST /brain/eval/baseline` promotes the last result, and
`GET /brain/eval/status` compares current against baseline. The module is loaded but
`auto_start` is false by default; enable it in `modules/brain/module.json` or call `/brain/start`.

Outcome learning mirrors experiences into the shared memory store: `POST /brain/learn` records
an action outcome, `POST /brain/learn/consolidate` turns repeated **training** successes into
`RULE` skills, and `POST /brain/learn/recall` reports held-out precision, recall and MRR.
`GET /brain/learn/status` and `GET /brain/learn/experiences` expose the ledger. Evaluation runs
also record their per-task outcomes, so the scorecard feeds the same learning loop.

While the brain service is active, ordinary chat/flow executions emit an outcome through
`core/outcomes.py` and are recorded as `flow:<id>` experiences; evaluation runners opt out to avoid
double counting, and `/brain/stop` disables capture. `capture_flow_outcomes` in the brain module
config can turn this off entirely.

Self-improvement is explicit and eval-gated: `POST /brain/improve/candidates` accepts a flow
operation list (or `/improve/generate` drafts one with the configured LLM), `/improve/evaluate`
runs the candidate against an in-memory copy and only promotes it when `approve` is true, an
`approved_by` identity is supplied, a **perfect same-flow baseline** with provenance exists, the
candidate itself is perfect, and there is no latency regression. `/improve/rollback` restores the
pre-promotion snapshot; `/improve/status` reports candidates and daily quotas. Candidate,
evaluation, promotion and ledger caps are configured in `modules/brain/module.json`.

Evaluation is read-only by construction: flows containing state-mutating or external-effect
nodes (tools, memory writes, messaging, calendar, subflows) are rejected with
`UnsafeEvaluationError` before execution. Promote a baseline with
`POST /brain/eval/baseline {"approved_by": "..."}`; only a perfect run is accepted.

Self-configuration is separately policy-gated: `POST /brain/config/preview` shows a before/after
diff, `POST /brain/config/apply` requires `approved_by`, and `POST /brain/config/rollback` restores
the snapshot (newer changes for the same target must be rolled back first). Only whitelisted
settings, allowlisted modules, and bounded brain options are editable; protected modules and
API keys/secrets are rejected, and `allowed_tools` can only be narrowed. Changes are recorded in the
configuration ledger and broadcast through `GET /brain/config/status`; the daily quota is
configurable in `modules/brain/module.json`.

The governance kernel derives a risk score from provider errors, evaluation pass rate, drift and
blocked actions, then runs in `normal`, `degraded` or `safe` mode. SAFE blocks learning writes,
improvement and configuration; rollback remains available. `GET /brain/kernel` reports the mode,
risk inputs and gates; `POST /brain/kernel/kill` and `/kernel/reset` require `approved_by`.
`POST /brain/drift/baseline` records a measured baseline for drift comparison. Recovery from SAFE
uses hysteresis rather than a single noisy sample.

Reactivity turns events from the calendar, watched files, metric thresholds and the event API into
proposal-only reactions. `GET /brain/reactions`, `/reactions/dismiss`, `/reactions/mute` and
`/reactions/unmute` manage them; `/react/start`, `/react/stop` and `/react/poll` control polling.
Reactions are deduplicated, muted by key, capped per hour and blocked outside normal/degraded
kernel modes, except health alerts. They never execute an action themselves.

Brain ledgers (learning, improvement, configuration, kernel, reactions, actors) are written under a
per-file cross-process lock and reloaded before each mutation, so multiple server workers keep each
other's updates instead of overwriting them.

Brain audit identity comes from the request, not from an `approved_by` JSON field. Loopback and
test clients are trusted as `local` admins (or the optional `X-Brain-Actor` label). Remote clients
must present a token in `X-Brain-Token`: either `NEUROCORE_BRAIN_TOKEN` (shared admin override) or a
token from the actor registry. `GET /brain/actor` reports the resolved actor.

Actor-management endpoints are admin-only: `GET/POST /brain/actors`,
`POST /brain/actors/{name}/rotate|disable`, and `DELETE /brain/actors/{name}`. Tokens are returned
once on create/rotate and stored only as salted PBKDF2 hashes. Roles are `viewer` (read-only),
`operator` (learning, reactions, evaluation runs, cycles) and `admin` (promotions, configuration,
kernel controls, baselines, actors). Promotions, configuration changes, kernel controls and drift
baselines record the authenticated actor name.

`/brain/ui` is the local Brain dashboard: kernel mode/risk, health, cycle status, proposals,
reactions (dismiss/mute), evaluation controls and start/stop/kill/reset controls. It is linked
from the navbar when the `brain` module is enabled and refreshes every five seconds.

---

## 🧩 Available Modules & Capabilities

- **`agent_loop`**: Autonomous goal pursuit with RLM reasoning and tool calls.
- **`brain`**: Observe/suggest self-model, event bus, cognitive scheduler and evaluation scorecard.
- **`memory`**: Semantic memory storage and hybrid vector recall with FAISS.
- **`knowledge_base`**: Document RAG with hybrid search (FAISS + SQLite FTS5) and Reciprocal Rank Fusion.
- **`messaging_bridge`**: Unified multi-platform bridge supporting Telegram, Discord, Signal, and WhatsApp.
- **`tools`**: 23 built-in tools executed in an isolated process sandbox with network policy controls.
- **`planner`**: Multi-step request decomposition and dependency-aware step tracking.
- **`browser_automation`**: Headless browser automation service using Playwright.
- **`calendar`**: Calendar event tracking and proactive deadline reminders.
- **`autonomous_agent`** *(experimental, disabled)*: Standalone runtime with sandboxed flow creation, three-tier memory, reflection and chat buffering. It is not auto-loaded; see `ADR-008` before enabling it.

---

## 🌐 API Reference

Over 70 endpoints provide REST and SSE access:
- **Dashboard & System**: `GET /`, `GET /dashboard/stats`, `GET /llm-status`
- **Flows**: `GET /ai-flow`, `POST /ai-flow/save`, `POST /ai-flow/{id}/run-node/{nid}`
- **Modules**: `GET /modules/list`, `POST /modules/{id}/config`, `POST /modules/{id}/{action}`
- **Marketplace**: `GET /marketplace`, `POST /marketplace/upload`, `POST /marketplace/item/{id}/import`
- **Tracing**: `GET /debug/logs`, `GET /debug/events`, `GET /debug/summary`

---

## 🧪 Testing

```bash
# Install test tools (runtime requirements plus pytest plugins)
pip install -r requirements-dev.txt

# Run test suite
python -m pytest

# Run with coverage
python -m pytest --cov=.
```

---

## 🔒 Security Defaults

NeuroCore is a control plane with no authentication, so it stays local by default. Remote
access, the auto-reloader and secret-bearing exports are each an explicit opt-in:

| Environment variable | Default | Effect when set |
|---|---|---|
| `NEUROCORE_STATE_DIR` | project root | Store settings, flows, chats, calendar events and databases in one directory (Docker uses `/app/data`) |
| `NEUROCORE_HOST` | `127.0.0.1` | Bind another interface (Docker uses `0.0.0.0` inside the container) |
| `NEUROCORE_PORT` | `8000` | Bind another port |
| `NEUROCORE_RELOAD` | off | Enable uvicorn auto-reload |
| `NEUROCORE_ALLOWED_HOSTS` | loopback only | Accept extra `Host` names |
| `NEUROCORE_API_TOKEN` | unset | Token non-loopback clients must send (`X-API-Token` or `Authorization: Bearer`); a non-loopback bind refuses to start without it |
| `NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE` | off | Allow a non-loopback bind without a token, only when an outer layer (e.g. a loopback port publish) already keeps the app local |
| `NEUROCORE_BRAIN_TOKEN` | unset | Required for all remote `/brain/*` access; send `X-Brain-Token`, optional `X-Brain-Actor` |
| `NEUROCORE_EXPORT_SECRETS` | off | Include secrets in the config export |

State-changing requests are rejected when their `Origin` is cross-origin (CSRF). Marketplace
packages are extracted through a path-confined helper, and secrets are stripped from config
exports and masked in debug logs. Tool code runs in a separate worker process (never in-process)
with allowlisted imports, only the environment values listed in the tools module's
`env_allowlist`, and a network allowlist that re-checks every redirect and caps response size.
On NeuroCore's own port a tool may call only `/memory/goals` and `/browser_automation/api/`, so
tool code cannot change settings or install modules through the token-free loopback API.
These are defense-in-depth Python restrictions, not an OS isolation boundary: do not run
untrusted community tools on a machine whose files or credentials you cannot afford to expose.

---

## 📄 License

NeuroCore is licensed under the [GNU Affero General Public License v3](LICENSE).
