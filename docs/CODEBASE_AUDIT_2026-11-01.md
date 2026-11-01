# NeuroCore Codebase Audit — 2026-11-01

Bugs, fixes, improvements and implementation opportunities across the whole repository.

| | |
|---|---|
| Commit audited | `8b5ddcd` (`main` HEAD, also `claude/funny-feynman-rloonj`) |
| Size | 485 tracked files · ~55K lines of Python in `core/` + `modules/` · 39K lines of tests · 13K lines of HTML/JS |
| Baseline | `pytest` **aborts at collection**; with `--continue-on-collection-errors`: 2118 passed, 12 failed, 4 errors |
| Evidence | 18 findings reproduced by 19 offline checks — scripts in [`docs/audit_repro/`](audit_repro/README.md), run with `python docs/audit_repro/run_all.py` |

---

## 0. Scope, method and how to read this report

**Scope.** The request asked for an analysis of "the whole database"; this report treats that as the whole
codebase. The persistence layer — SQLite databases, FAISS indexes and the JSON state files — has its own
section (§6) so the data-integrity view is not lost.

**Method.**
1. Baseline: full test run (Python 3.13, dependencies from `requirements-dev.txt`), `ruff check`,
   `ruff format --check`, and the GitHub Actions history of the repository.
2. Risk-ordered manual review: flow engine, control-plane security, module loading, LLM bridge, tool
   dispatch, persistence, chat/messaging/calendar, agent and logic nodes, web templates, deployment files,
   documentation.
3. Reproduction: every behavioural claim that could be exercised offline was turned into a script with a
   fresh temporary `NEUROCORE_STATE_DIR` (no network, no LLM, nothing written to the working tree).

**Evidence legend.**
✅ reproduced by a script in `docs/audit_repro/` ·
🧪 reproduced by a command shown in this report ·
🔍 confirmed by reading the code ·
⚠️ likely, needs confirmation.

**Severity.**
**Critical** — the product is broken at HEAD, or untrusted input can lead to compromise of the host.
**High** — a core feature returns wrong results or a security control does not work, in the default or a
common configuration. **Medium** — a real defect with a narrower trigger or impact. **Low** — latent bugs,
hygiene, cosmetics.

**Effort.** S ≤ 1 h · M ≤ 1 day · L 1–3 days · XL > 3 days.

**Tool sandbox.** `modules/tools/sandbox.py` was reviewed at the architecture level only (isolation layers,
where the trust boundary sits, policy vs. documentation). This report does not attempt or describe ways
around it; see §8.

Coverage and limitations are listed in §13.

---

## 1. Executive summary

NeuroCore contains a lot of careful defensive engineering: atomic, fsynced writes for flows and chat
sessions, DNS-rebinding-safe pinned HTTP for research sources, zip-slip-proof archive extraction, a
fail-closed remote bind, default-deny messaging allowlists, an approval ledger for high-risk tools, and a
per-run execution budget. The problems are concentrated elsewhere:

1. **HEAD is not shippable.** `modules/agent_loop/node.py` raises `NameError` on import, so every
   agent-loop node is unusable and plain `pytest` stops at collection. The CI workflow has never actually
   executed a job, and the internal `aikit` gate runs no tests (`test_command: null`), which is how the
   breakage landed with a commit message saying the checks pass.
2. **Several headline features silently fail in the default configuration**: chat with an image is
   refused, memory recall returns nothing for natural-language questions, the keyword half of knowledge-base
   hybrid search errors out for almost every question, and calendar reminders are never delivered.
3. **The flow scheduler has correctness bugs**: nodes run more than once (duplicate LLM calls and side
   effects), loop flows can return a stale intermediate output as the answer, and token usage is multiplied
   by every pass-through node.
4. **Security**: model output is rendered as HTML without sanitisation (the most serious issue), the
   module allowlist does not gate node execution, configured secrets are written into git-tracked files,
   and the prompt-injection fence can be skipped by content that imitates it.
5. **`CLAUDE.md` and the guides have drifted far from the code** (module, file, test and tool counts,
   files that no longer exist, a "must read" helper module that nothing imports).

### 1.1 Top priorities

| # | ID | Finding | Sev. | Evidence | Effort |
|---|---|---|---|---|---|
| 1 | C-1 | Agent Loop module fails to import (`NameError: Any`); test run aborts | Critical | 🧪 pytest | S |
| 2 | C-2 | Model output rendered with `innerHTML = marked.parse(...)` and no sanitiser | Critical | 🔍 | M |
| 3 | H-10 | CI has never run a job; local quality gate runs no tests or lint | High | 🔍 GitHub API | M |
| 4 | H-1 | Default model limits refuse any chat containing an ordinary image | High | ✅ | S |
| 5 | H-2 | Chat-triggered flows run nodes twice, first with partial input | High | ✅ | L |
| 6 | H-3 | Loop flows can return a stale intermediate output as the final answer | High | ✅ | M |
| 7 | H-4 | >5 tool calls: unanswered `tool_call` ids in history, remaining calls dropped | High | ✅ | S |
| 8 | H-5 | Knowledge-base keyword search fails for nearly every natural question | High | ✅ | S |
| 9 | H-6 | Memory recall finds nothing for natural questions on default installs | High | ✅ | S |
| 10 | H-7 | Default flow's reminder loop never starts; reminders never delivered | High | ✅ | M |
| 11 | H-8 | `module_allowlist` / disabled flag do not stop node code from running | High | ✅ | S |
| 12 | H-9 | Planner marks dependency-blocked steps complete without running them | High | ✅ | S |

### 1.2 Counts

| Critical | High | Medium | Low |
|---|---|---|---|
| 2 | 10 | 38 | 31 |

---

## 2. Baseline health

### 2.1 Tests

```bash
python -m pytest -q
# !!!!!!!!!!!!!!!!!!! Interrupted: 4 errors during collection !!!!!!!!!!!!!!!!!!!
# 1 skipped, 11 warnings, 4 errors in 8.96s

python -m pytest -q --continue-on-collection-errors
# 12 failed, 2118 passed, 1 skipped, 4 errors in 39.43s
```

| Failure | Count | Root cause |
|---|---|---|
| Collection errors in `test_agent_loop.py`, `test_agent_loop_guard.py`, `test_agent_loop_replan.py`, `test_goal_pursuit.py` | 4 | C-1 |
| `test_execution_budget.py::test_the_shared_llm_helper_charges_the_run_budget`, `::test_missing_provider_usage_charges_an_estimate` | 2 | C-1 (they import `tests.test_agent_loop`) |
| `test_tools_library.py::TestCalculator::*` | 10 | M-TOOL-3: file is `library/calculator.py`, tests and the editor expect `Calculator.py` (fails on case-sensitive filesystems) |
| `test_e2e.py` skipped | 1 | needs `requests`, which is not in `requirements-dev.txt` (L-30) |

The suite is fast (~40 s) and broad (161 files). Its main gap is that nothing imports every module, so an
import-time error in a module can ship (see I-6 in §10).

### 2.2 Lint and formatting

`ruff check .` reports **4,506** findings (3,940 auto-fixable). Most are whitespace (W293 ×3,484,
W291 ×136) and import hygiene (I001 ×246, F401 ×246). The ones that are real defects:

| Rule | Where | Meaning |
|---|---|---|
| F821 `Any` | `modules/agent_loop/node.py:90,97,119` | C-1 |
| F811 ×12 | `modules/brain/router.py:549–944` | the four prompt-registry routes are defined **four times** (L-2) |
| F811 ×2 | `core/flow_data.py:817,828` | `set_tool_count` / `set_remaining_tool_calls` defined twice; second wins (L-3) |
| F823 | `tests/test_email_bridge.py:59` | local `email` referenced before assignment (L-29) |
| F821 `args`/`httpx` ×44 | `modules/tools/library/*.py`, `rlm_library/*.py` | expected (sandbox-injected names) — add `per-file-ignores` so real errors are not drowned out |
| F821 `SnapshotBudget` ×3 | `modules/browser_automation/service.py:250,272,292` | harmless string annotations (the class is imported inside the function); import it under `TYPE_CHECKING` |

`ruff format --check`: 341 files would be reformatted. `pyproject.toml` configures ruff but nothing runs it.

### 2.3 CI and quality gates (H-10)

- GitHub Actions has three `NeuroCore CI` runs (2026-10-25). In each, **all four jobs — including the
  trivial Docker build — finished as `failure` about 3 seconds after starting**, which means the jobs never
  ran (a runner/account/billing problem), not that tests failed. There are no runs for the 2026-10-29 or
  2026-11-01 pushes. The current `main` has never been validated by CI.
- `.aikit/project.json` has `"test_command": null` and `"gates": {"lint": null}`, so `aikit check` passes
  without running tests or lint. Commit `982056b` ("python aikit.py check neurocore passes 4/4") introduced C-1.

**Fix.** Restore Actions (check the account's Actions/billing settings and re-run), make the workflow
also run `ruff check` with a curated rule set, set `test_command` to `python -m pytest -q` in
`.aikit/project.json`, and protect `main` so a red or missing check blocks the merge. Effort: M.

### 2.4 Structure

| Area | Python LOC | Notes |
|---|---|---|
| `core/` | 17,497 (39 files + 5 schemas) | `routers.py` alone is 3,368 lines / 113 routes |
| `modules/` | 37,101 (22 modules) | `agent_loop/node.py` is 6,010 lines; `GoalPursuitExecutor` is one ~3,050-line class |
| `tests/` | 39,100 (161 files) | 509 `@pytest.mark.asyncio` markers in 52 files despite `asyncio_mode = "auto"` |
| `web/templates/` | 55 templates | assets loaded from CDNs |

---

## 3. Critical findings

### C-1 · Agent Loop module fails to import — 🧪 · Effort S

- **Where:** `modules/agent_loop/node.py:90, 97, 119` (`def set_delegation_contract(self, contract: Any)`,
  `set_handoff_packet(..., handoff: Any)`, `_finalize_delegation_and_handoff(..., loop_error: Any = None)`).
- **What happens:** annotations are evaluated when the class body executes (no
  `from __future__ import annotations`), and `Any` is never imported, so importing the module raises
  `NameError: name 'Any' is not defined` on Python 3.12/3.13 — the versions CI targets. Every agent-loop node
  type (`agent_loop`, RLM, hybrid, goal pursuit) fails to load; `pytest` aborts during collection, hiding the
  rest of the results.
- **Introduced by:** `982056b` (2026-11-01).
- **Fix:** add `from typing import Any` to the imports. Add an import smoke test (I-6) so the class of error
  cannot recur.

### C-2 · Model output is rendered as HTML without sanitisation — 🔍 · Effort M

- **Where:** `web/templates/chat_gui.html:192, 408` and `web/templates/chat_message_streaming.html:84, 161`
  set `innerHTML = marked.parse(...)` for assistant and brain messages, both while streaming and when a
  saved session is reopened (line 408 re-parses each stored message). There is no HTML sanitiser anywhere in
  `web/templates`, and `marked` does not sanitise its output. `marked` itself is loaded unpinned from a CDN
  (`web/templates/index.html:14`, no version, no Subresource Integrity).
- **Why it is critical:** model output is shaped by content the agent reads — fetched web pages, inbound
  email and messaging, knowledge-base documents, stored memories — so untrusted parties can influence what
  gets rendered. Markup that reaches `innerHTML` runs as script in the NeuroCore origin, where every
  control-plane endpoint is available with the user's privileges (same-origin requests pass the CSRF check,
  and loopback clients need no token). Those endpoints include ones that change settings, read the
  configured API key (rendered into `module_details.html:603`), and install and enable code, so the impact
  is equivalent to compromise of the NeuroCore host. Because rendered history is re-parsed on every visit,
  the effect is persistent. User messages are rendered escaped and are not affected.
- **Fix:**
  1. Sanitise every Markdown render with a maintained sanitiser, in one shared helper:
     ```js
     // one helper, used by chat_gui.html and chat_message_streaming.html
     function renderMarkdown(text) {
       return DOMPurify.sanitize(marked.parse(text ?? ""));
     }
     contentDiv.innerHTML = renderMarkdown(mdBuffer);
     ```
  2. Pin exact versions of `marked` and `DOMPurify` (for example `marked@15.0.7`, `dompurify@3.2.4`) with
     `integrity=` attributes, or vendor them into `web/static/`.
  3. Add a `Content-Security-Policy` header (`object-src 'none'; base-uri 'none'; frame-ancestors 'none'`,
     and `script-src` limited to `'self'` once assets are vendored) from `core/security.py`.
  4. Stop rendering `settings.llm_api_key` into the page; show "configured / not configured" and accept a
     new value write-only.
  5. Add a regression test that renders a stored assistant message containing markup and asserts no
     active elements survive (a Playwright test fits the existing `e2e` marker).

---

## 4. High-severity findings

### H-1 · Default model limits refuse any chat containing an ordinary image — ✅ `repro_vision_clamp.py` · S

- **Where:** `core/llm.py:22` (`_estimate_context_tokens` = `len(json.dumps(payload)) // 4`),
  `core/llm.py:33` (`_clamp_request`), `core/model_registry.py:149` (raises `ModelLimitExceeded` whenever
  the estimate exceeds `max_context_tokens`, also outside strict mode), `core/settings.py:30`
  (`DEFAULT_SETTINGS` gives the default model `local-model` a 32,768-token ceiling).
- **What happens:** base64 image bytes are counted as text. One 150 KB image is estimated at **50,042
  tokens** and the request is refused with `{"error": "model_limit"}`. This breaks image chat
  (`modules/chat/router.py:494–503`) and photos received through the messaging bridge
  (`modules/messaging_bridge/service.py:274`). The default `json.dumps` also escapes non-ASCII characters,
  inflating estimates for non-English text about 2.4× (310 Turkish characters → 743 JSON characters).
- **Fix:** estimate text parts with `ensure_ascii=False`, count each image part at a fixed cost (≈1,000
  tokens) instead of its bytes, and refuse on context size only when `strict_model_limits` is true —
  otherwise log and let history truncation or the provider decide.

### H-2 · Chat-triggered flows run nodes more than once, the first time with partial input — ✅ `repro_flow_runner.py` · L

- **Where:** `core/flow_runner.py:811–835` (start-node selection), `:1119–1136` (re-queueing).
- **What happens:** when a run has an input source (`chat`, `telegram`, `discord`, `messaging`), execution
  starts from the input nodes and proceeds breadth-first. A node with parents at different depths runs as
  soon as the first parent finishes — with only that parent's output — and runs again when the later parent
  finishes. With `in→D` and `in→A→B→D`, `D` ran twice; its first run had only `in`'s data. If `D` is an LLM,
  messaging or tool node, that is a duplicate paid call or a duplicate side effect. (The shipped default
  flow happens not to contain such a join; user-built flows easily do.)
- **Fix:** schedule by readiness instead of BFS: a node becomes runnable when all of its non-back-edge
  parents have settled in the current activation (ran, stopped, or were skipped by routing), and routing
  skips must propagate so joins do not wait forever. An interim mitigation is to pop the queue in
  topological-priority order and defer a node while one of its forward parents is still pending.

### H-3 · Loop flows can return a stale intermediate output as the final answer — ✅ `repro_flow_runner.py` · M

- **Where:** `core/flow_runner.py:1165–1188`.
- **What happens:** the result is the first non-`None` entry of `node_outputs` in the static topological
  order, not the output of the last node that actually ran, and outputs from earlier loop iterations are
  never cleared. In `in → llm → router → {tool → llm, out}`, when the final sink returns `None`, the run
  returns the first iteration's tool output (`"STALE TOOL OUTPUT"`). The chat UI displays
  `flow_result["content"]` (`modules/chat/router.py:602`), so users see the wrong reply.
- **Fix:** record outputs with an execution sequence number and return the latest output of a designated
  output node (`chat_output`, `messaging_output`) or, failing that, of the last node executed; clear
  `node_outputs` for nodes skipped by routing in the current iteration.

### H-4 · More than `max_tools_per_turn` tool calls corrupts the conversation — ✅ `repro_dispatcher.py` · S

- **Where:** `modules/tools/node.py:81–86, 181–195`.
- **What happens:** with 7 tool calls and the default limit of 5, the dispatcher runs 5 but appends the
  whole assistant message (7 `tool_calls`) to `messages`, leaving `call_5` and `call_6` without a tool
  result — OpenAI-compatible providers reject the next request. The remaining calls are written to
  `input_data["_remaining_tool_calls"]`, which is not part of the returned dict, so they are lost while
  `requires_continuation` is `True`.
- **Fix:** answer every `tool_call_id` — run the first N and return a short "not executed: limit of N per
  turn, call again" tool result for the rest — or trim the assistant message to the executed calls and return
  `_remaining_tool_calls` / `_tool_count` in the output.

### H-5 · Knowledge-base keyword search fails for nearly every natural question — ✅ `repro_fts.py` · S

- **Where:** `modules/knowledge_base/backend.py:1343` (sanitiser), `:1368` (`OperationalError` → `[]`).
- **What happens:** only `"`, `'`, `*` and `.` are removed before `MATCH`. FTS5 syntax characters then fail:
  `What is FAISS?` → `syntax error near "?"`; `similarity-search` → `no such column: search`; commas and
  parentheses fail too. The error is logged and an empty list returned, and all terms are implicitly ANDed,
  so 5 of 6 realistic queries return nothing and the Reciprocal Rank Fusion search silently becomes
  vector-only.
- **Fix:**
  ```python
  def _fts_query(text: str) -> str:
      terms = [t for t in re.findall(r"\w+", text) if len(t) > 1][:32]
      return " OR ".join(f'"{t}"' for t in terms)
  ```
  Rank with `bm25(chunks_fts)`, and over-fetch (2–4× `limit`) from both retrievers before fusing.

### H-6 · Memory recall returns nothing for natural questions on default installs — ✅ `repro_mem_keyword.py` · S

- **Where:** `modules/memory/backend.py:561, 582`; the fallback is used whenever no embedding model is set,
  and `DEFAULT_SETTINGS["embedding_model"]` is `""`.
- **What happens:** every word longer than two characters, punctuation included, becomes an ANDed
  `LIKE '%word%'`. "What is my dog's name?" finds nothing even though "The user's dog is named Rex" is
  stored; only bare keyword lists match. Long-term memory is effectively off unless the user configures
  embeddings, and nothing in the UI says so.
- **Fix:** tokenize with `\w+`, drop stop-words, OR the terms and rank by matched-term count (or add an
  FTS5 table for memories and reuse the H-5 query builder); escape `%`/`_`. Show a warning in the memory UI
  when no embedding model is configured.

### H-7 · The default flow's reminder loop never starts — ✅ `repro_repeater.py` · M

- **Where:** `main.py:64` and `core/routers.py:971` auto-start only repeater nodes that **have** incoming
  connections. The default flow's Repeater (`node-12 → Calendar Watcher`) has none, and chat-triggered runs
  never reach source nodes (H-2's start-node logic).
- **What happens:** the Calendar Watcher never runs, so reminders saved through the default-enabled
  `SaveReminder` tool are never delivered. Two further calendar problems make delivery unreliable even when
  the loop runs: the watcher fires only on an exact minute match with no catch-up
  (`modules/calendar/node.py:24–44`, M-CAL-1), and `get_upcoming` compares local times against UTC
  (M-CAL-2).
- **Fix:** start repeaters that have *no* incoming edges (they are the triggers), keep a registry of running
  loops per `(flow_id, node_id)` so activation cannot start a second one, and make the watcher fire every
  event with `start_time <= now` that is not yet notified (within a grace window).

### H-8 · `module_allowlist` and the disabled flag do not stop node code from running — ✅ `repro_allowlist.py` · S

- **Where:** the allowlist is checked only in `ModuleManager._load_module_router`
  (`core/module_manager.py:98`); `FlowRunner._get_executor_class` imports `modules.<moduleId>.node` for any
  `moduleId` in a flow (`core/flow_runner.py:74`) with no allowlist, `enabled` or `DISABLED` check.
- **What happens:** with `module_allowlist = ["chat"]`, a flow node from `logic` was imported and executed.
  `CLAUDE.md` describes the allowlist as the runtime control that restricts which code can load; in fact any
  flow — including one imported from the marketplace, which users would treat as data — runs the package
  `__init__` and `node.py` of any module present on disk, enabled or not.
- **Fix:** enforce the same policy in `_get_executor_class` (allowlist, enabled state, `DISABLED` marker),
  validate `moduleId` against `^[a-z_][a-z0-9_]*$`, and make `FlowRunner.validate()`'s disabled-module
  *issue* block `run()`.

### H-9 · The planner completes dependency-blocked steps without running them — ✅ `repro_flow_runner.py` · S

- **Where:** `modules/planner/node.py:187–190` (`if current > 0: for i in range(current): completed_steps.add(i)`).
- **What happens:** dependency-aware scheduling can jump past a blocked step, and the next tick then marks
  every lower index complete. Plan `[1, 2 (depends on 3), 3]` executes indices `[0, 2]` and ends with
  `completed = [0, 1, 2]`, `plan_complete = True` — step 2 never ran. Separately, `depends_on` given as a
  string (`"1"`, common in model output) or naming a missing step is silently ignored, so the step is treated
  as runnable (`:76–90`, `:141–153`).
- **Fix:** drop the `range(current)` heuristic and rely on `completed_steps`; normalise `depends_on` to ints
  and report unknown references as a dependency error. Deduplicate the graph helpers (they exist in both
  `PlannerExecutor` and `PlanStepTracker`, plus the unused `core/planner_helpers.py`).

### H-10 · CI never runs and the local gate runs no tests — 🔍 · M

See §2.3. It is listed as High because it is the reason C-1, M-TOOL-3 and several lint-detectable defects
reached `main`.

---

## 5. Medium-severity findings

### 5.1 Flow engine (`core/flow_runner.py`, `core/step_journal.py`)

| ID | Finding | Location | Ev. | Fix |
|---|---|---|---|---|
| M-FLOW-1 | Token usage is summed from every node output, and pass-through nodes forward the upstream `usage` dict, so one LLM call is counted once per downstream node (15 → 45 tokens with two pass-throughs; 4–5× in the default flow: LLM → router → Chat Output → Memory Save, plus Messaging Output on the parallel branch). The inflated value overwrites `out["usage"]`, feeds the session token counter (`modules/chat/router.py:645–652`) and the dashboard. | `:799–805, 1094, 1186` | ✅ | Account usage at the LLM call (context variable / budget), not from payloads; strip `usage` from pass-through outputs. |
| M-FLOW-2 | Bridge pre-execution runs *all* other members of the group — including downstream ones — before the current node, and the node itself re-runs even if it was already computed as a peer. With bridges `A→C, B→C` in topological mode, `B` and `C` each ran twice. | `:870–948` | ✅ | Pre-run only the bridge ancestors of `node_id`; skip nodes already in `node_outputs` for this activation. |
| M-FLOW-3 | Only a fixed tuple of exceptions is caught; a `KeyError`, `IndexError`, `httpx.HTTPError` or `OSError` escapes `run(raise_errors=False)`, violating the documented contract, and skips `journal.finish_run`, leaving a step-journal run in `running` forever. | `:1141–1158, 1223` | ✅ | Catch `Exception` around node execution and wrap it in `NodeExecutionError`; finish the journal in `finally`. |
| M-FLOW-4 | `except (ImportError, AttributeError)` wraps *execution*, so an `AttributeError` from a bug inside a node is reported as "could not find node logic" and the input is passed through as if the node succeeded. | `:1141–1146` | ✅ | Resolve the executor class outside the `try`; treat errors raised by `receive`/`send` as node failures. |
| M-FLOW-5 | Step journal (opt-in): a `running` row has no lease, so after a crash an identical submission is refused as a duplicate forever — the opposite of "a restart resumes"; the run id is the input digest with volatile keys (timestamps) removed, so repeated scheduled runs with the same payload return the first run's stored result; inside one run, a loop node seeing the same input returns its cached output instead of re-executing (polling loops never poll again); every journal call is synchronous SQLite with `synchronous=FULL` on the event loop, and a new `StepJournal` is created per run. | `core/step_journal.py:93–103, 244–260`; `flow_runner.py:744–769, 1040–1066` | 🔍 | Add a lease/heartbeat to `running`; require an explicit idempotency key for dedupe; key steps by `(run_id, node_id, iteration)`; move I/O to a thread. |
| M-FLOW-6 | On completion the episode is saved with `plan=out.get("plan", [])`, wiping a stored plan whenever the final node does not echo it; a run that produced no output is marked `PHASE_COMPLETED`. `_run` also mutates the caller's `initial_input` when restoring an episode. | `:776–786, 1169–1198` | 🔍 | Only overwrite fields present in the output; mark "no output" as failed; copy `initial_input`. |
| M-FLOW-7 | All four agent-loop variants call `sm.create_episode(...)` without `episode_id=episode_id`; episodes get a random `ep-xxxxxxxx` id, and resume requires `existing.episode_id == config episode_id`, so a configured episode id never resumes and the generation/tombstone logic in `create_episode` is unreachable. | `modules/agent_loop/node.py:1054, 1921, 2687, 4662` | 🔍 | Pass `episode_id=episode_id`. |

### 5.2 LLM bridge and LLM node

| ID | Finding | Location | Ev. | Fix |
|---|---|---|---|---|
| M-LLM-1 | The LLM node's retry classifier never sees HTTP status: the 4xx/5xx check reads `status_code`/`http_status` on the *success* branch, while the bridge returns `{"error": "http_error", "status": 401}` / `{"error": "model_limit"}`. A 401, a 400 and a deterministic `model_limit` refusal are each sent **4×** with backoff; `retry_after` is ignored; internal retries are not charged to the run budget. | `modules/llm_module/node.py:115–175` | ✅ | Classify on `response["status"]` / `category` (reuse `core/llm_errors.is_retryable`), never retry `model_limit`, honour `retry_after`, `spend("llm_calls")` per attempt. |
| M-LLM-2 | `LLMExecutor` builds `LLMBridge` without a timeout, so every call uses httpx's 60 s default: `settings.request_timeout` is ignored, and the node's own `timeout` (default 120 s) can never be reached per attempt. Slow local models time out at 60 s. | `modules/llm_module/node.py:33–38` | ✅ | Pass `timeout=settings.get("request_timeout")` (and the embedding settings) — or reuse `core.dependencies.get_llm_bridge`. |
| M-LLM-3 | Embedding requests send `llm_api_key` as the bearer token to `embedding_api_url`, which may be a different provider or host — the main provider's key is disclosed to it. | `core/llm.py:376–390`, `core/dependencies.py:56–62` | 🔍 | Add `embedding_api_key`; send no key to a different host unless configured. |
| M-LLM-4 | Only `chat_completion` is instrumented; streaming calls never reach the token statistics, while M-FLOW-1 inflates the non-streaming ones. | `core/observability.py:722–790` | 🔍 | Instrument `chat_completion_stream` (usage arrives in the final chunk). |

### 5.3 Security and privacy

| ID | Finding | Location | Ev. | Fix |
|---|---|---|---|---|
| M-SEC-1 | The untrusted-content fence is skipped for any tool result that already *looks* fenced, so content that begins with a fence-shaped header reaches the model unfenced — contradicting the module's stated guarantee. | `core/untrusted.py:37–39` | ✅ | Always wrap; mark fenced messages with an out-of-band key instead of sniffing content. |
| M-SEC-2 | The API token is skipped for every loopback peer. Behind a same-host reverse proxy that does not forward the client address, all remote traffic is trusted; the docstring of `allowed_hosts()` recommends the token for exactly this setup. | `core/security.py:261–267, 288–302` | 🔍 | When a token is configured, require it for every client (or only exempt loopback when no `X-Forwarded-For` is present); document uvicorn's `forwarded_allow_ips`. |
| M-SEC-3 | Module configuration, including secrets (`imap_password`, `smtp_password`, `telegram_bot_token`, `discord_bot_token`, `whatsapp_api_key`), is written into `modules/<id>/module.json`, which is **tracked in git**. Configuring a bridge puts real credentials into the source tree (and Docker mounts `./modules` writable). | `core/module_manager.py:285–302`; e.g. `modules/email_bridge/module.json:14,23` | 🔍 | Keep runtime config and secrets under `data/` (or an OS keyring), merge over the shipped defaults at load, and git-ignore the runtime file. |
| M-SEC-4 | Enabling or disabling a module renders `module_details.html` with `json.dumps(module["config"])` — the full config, including the keys `HIDDEN_CONFIG_KEYS` deliberately hides — into the JSON editor `<textarea>`. The settings panel renders `llm_api_key` into an `<input value=…>`. | `core/routers.py:853`; `web/templates/module_details.html:576, 603` | 🔍 | Apply the same hiding in `toggle_module`; never render secret values. |
| M-SEC-5 | Marketplace "module" import extracts into `modules/<sanitised name>` even when that is a built-in module (`tools`, `chat`, `security`, …), overwriting core files one by one; a failure part-way leaves a half-written module. | `core/routers.py:2077–2091`, `core/archive.py` | 🔍 | Refuse existing targets (or require an explicit replace with backup); extract to a temp dir and rename atomically. |
| M-SEC-6 | Skill admission scanning (`modules/skills/admission.py`) is enforced only in `modules/skills/service.py`; `/settings/skills/save/{skill_id}`, `/settings/skills/create` and marketplace skill import in `core/routers.py` write skill text directly. `skill_id` is joined into a path unsanitised (backslash traversal on Windows, `.md` only). | `core/routers.py:2294–2362, 1974–2010` | 🔍 | Route all writes through `SkillsService`; validate ids. |
| M-SEC-7 | Third-party scripts are loaded from CDNs without integrity attributes (`marked` without even a version, Tailwind's CDN build, `htmx`, `sortablejs`), and no Content-Security-Policy is sent. | `web/templates/*.html` | 🔍 | Vendor or pin with SRI; add CSP (see C-2). |
| M-SEC-8 | With `NEUROCORE_API_TOKEN` set, a browser cannot use the UI remotely at all: nothing in the templates sends `X-API-Token`, and there is no session/cookie login. Users are pushed toward `NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE=1` instead. | `core/security.py`, `web/templates` | 🔍 | Token → `HttpOnly; SameSite=Strict` session cookie login (I-2). |
| M-SEC-9 | Episode ids are used as file names without validation (`_get_episode_path`, `_save_episode_by_id_to_file`, `delete_episode`); ids come from node config, so an imported flow can read or overwrite `*.json` outside `data/episodes`. | `core/session_manager.py:809–822, 1016–1018, 1160–1180` | 🔍 | Validate `^[A-Za-z0-9_-]{1,64}$`; use 128-bit random ids. |

### 5.4 Data integrity

| ID | Finding | Location | Ev. | Fix |
|---|---|---|---|---|
| M-DATA-1 | `load_tools()` returns `{}` on a corrupt `tools.json`, and `save_tools()` truncates and rewrites in place. A crash mid-write — or any corrupt file — means the next tool save writes back only that one tool and **wipes every other tool**. Load and save also take the file lock separately, so concurrent edits lose updates. | `modules/tools/router.py:64–79` | 🔍 | Temp file + `os.replace`; on parse failure quarantine and raise instead of returning `{}`; hold one lock across read-modify-write. |
| M-DATA-2 | A corrupt `settings.json` silently loads defaults (no log, no quarantine); the next save overwrites the file, losing API keys and configuration. In-memory settings are updated before the disk write; `DEFAULT_SETTINGS.copy()` is shallow, and `get()` returns live mutable references. | `core/settings.py:72–96` | 🔍 | Quarantine like `FlowManager` does, log loudly, deep-copy defaults, return copies of containers. |
| M-DATA-3 | The reasoning book is saved with a plain `open("w")` in a worker thread, outside its lock, while the list keeps changing; reflection fires `log_thought` through unreferenced `asyncio.create_task` calls, so concurrent writers can interleave and corrupt the file, after which `_load` resets it to `[]`. | `modules/reasoning_book/service.py:34–41, 79`; `modules/reflection/node.py:188, 209, 301, 329` | 🔍 | Snapshot under the lock, write temp + replace, keep task references. |
| M-DATA-4 | Memory FAISS index: saved every 20 changes and checked at startup by **count only**, so an add plus a delete since the last save leaves stale/missing vectors undetected; changing the embedding model (new dimension) makes `add_with_ids` / the rebuild fail after the SQL row is committed, leaving vector search broken until a manual rebuild. | `modules/memory/backend.py:229–321, 373–379` | 🔍 | Persist an id-set checksum with the index; record the embedding model/dimension (as the KB's `embedding_space.py` does) and re-embed on change. |
| M-DATA-5 | Re-enabling a module flushes `modules.<id>.*` from `sys.modules`, creating new module-level singletons while other modules keep the old ones — e.g. a second `MemoryStore` with its own FAISS index and locks, both writing `memory.faiss`. Messaging pollers started at import (`modules/messaging_bridge/router.py:136`) are likely duplicated the same way (⚠️). | `core/module_manager.py:139–146` | 🔍 | Give modules explicit `startup()/shutdown()` hooks and stop old instances before re-import; avoid import-time side effects. |
| M-DATA-6 | Marketplace `catalog.json` is read-modified-written by many endpoints without a lock, sometimes non-atomically (`upload` writes in place); concurrent votes/downloads/imports lose updates or corrupt the catalog. | `core/routers.py:1229–1960` | 🔍 | One locked repository class, atomic writes (or SQLite). |

### 5.5 Concurrency, async and performance

| ID | Finding | Location | Ev. | Fix |
|---|---|---|---|---|
| M-ASYNC-1 | The script node calls `sandbox.execute()` synchronously inside `async receive`, blocking the event loop for the whole script; its `timeout` comes from node config with no upper bound, so one node can freeze the server. (The tool dispatcher correctly uses `run_in_executor`.) | `modules/logic/node.py:133, 153` | 🔍 | `await loop.run_in_executor(...)`; cap `timeout`. |
| M-ASYNC-2 | `asyncio.create_task(run_flow_background())` keeps no reference; asyncio holds tasks weakly, so a chat turn can be garbage-collected mid-flight. Same pattern in reflection (M-DATA-3). | `modules/chat/router.py:706` | 🔍 | Store in `app.state.background_tasks` (as `main.py` does). |
| M-ASYNC-3 | One stream queue per session: a second send in the same session replaces `active_streams[session_id]`, and the first task's `finally` then deletes the second's queue, breaking that stream. | `modules/chat/router.py:582, 700–703` | 🔍 | Key streams by `(session_id, msg_id)`; reject or serialise concurrent sends. |
| M-PERF-1 | All chat sessions live in one JSON file that is rewritten (indented) and fsynced on every message; uploaded images are stored inline as base64 with no size or type limit, so every message write grows with the total image volume. | `modules/chat/sessions.py:106–131`; `modules/chat/router.py:494–503` | 🔍 | SQLite (one row per message), store images as files, cap size and validate type. |
| M-PERF-2 | Every `llm.*` counter increment rewrites `metrics_counters.json` synchronously under a lock — about eight full rewrites per LLM call, on the event loop. | `core/observability.py:362–383` | 🔍 | Flush periodically / at shutdown. |

### 5.6 Lifecycle, scheduling, tools, chat

| ID | Finding | Location | Ev. | Fix |
|---|---|---|---|---|
| M-LIFE-1 | `@router.on_event("startup"/"shutdown")` handlers in module routers never run: the app uses a `lifespan`, and module routers are included during it. Brain auto-start/resume after restart never happens, and `browser_service.stop()` is never called at shutdown (Chromium is left running). | `modules/brain/router.py:33–43`; `modules/browser_automation/router.py:24–34` | ✅ `repro_on_event.py` | Call module `startup()/shutdown()` hooks from `main.py`'s lifespan / `ModuleManager`. |
| M-CAL-1 | The calendar watcher fires only when the event minute equals the current minute; any missed poll (restart, slow loop, `delay` > 60 s) drops the reminder forever. Two loops (startup + activation) can both notify before either marks the event. | `modules/calendar/node.py:24–57` | 🔍 | "Due and not notified" query with a grace window; mark-then-send atomically. |
| M-CAL-2 | `get_upcoming` compares local-time `start_time` against `datetime.utcnow()`, while the watcher, router and `SaveReminder` use local `datetime.now()`: in UTC+3, past events stay "upcoming" for three hours; west of UTC, upcoming events vanish early. | `modules/calendar/events.py:97` | 🔍 | Store timezone-aware ISO timestamps; compare in one timezone. |
| M-TOOL-1 | `tool_call["id"]` is indexed directly when appending results (the parse-error path uses `.get`), so a provider that omits ids raises `KeyError`, which then escapes the flow (M-FLOW-3). | `modules/tools/node.py:178` | 🔍 | Generate an id when missing. |
| M-TOOL-2 | Editing a tool's code keeps its previously reviewed `tier`/`tier_reason`, so a tool approved as low-risk stays low-risk after its code changes; renaming onto an existing name silently overwrites it. | `modules/tools/router.py:136–141, 143–151` | 🔍 | Reset to HIGH (pending review) when code changes; refuse rename collisions. |
| M-TOOL-3 | The Calculator source is `library/calculator.py` while every other tool (and the manifest, tests and editor) uses the PascalCase name. The dispatcher resolves case-insensitively, but the editor/export/outbound paths open `library/{name}.py` exactly, so on Linux the editor shows empty code and saving creates a second file; 10 tests fail on Linux. | `modules/tools/router.py:312, 329, 400`; `modules/tools/outbound.py:104` | 🧪 pytest | `git mv calculator.py Calculator.py`; use `resolve_source` everywhere. |
| M-CHAT-1 | `logger.info(f"[Chat] flow_result: {flow_result}")` logs the whole conversation (and base64 images) at INFO on every turn. | `modules/chat/router.py:600` | 🔍 | Log ids and sizes at DEBUG. |

---

## 6. Persistence layer ("database") review

| Store | Location | Write strategy | Concurrency control | On corruption | Findings |
|---|---|---|---|---|---|
| `settings.json` | state dir | temp file + `os.replace` (no fsync) | `RLock` | silent defaults, then overwritten | M-DATA-2, L-17 |
| `ai_flows.json` (+ `.bak`, `_versions.json`) | state dir | `mkstemp` + fsync + `.bak` + dir fsync | `RLock` | flows: quarantine + backup recovery; versions: silently `{}` | L-18 |
| `chat_sessions.json` | state dir | `mkstemp` + fsync + replace | thread lock + `FileLock` + mtime refresh | — | M-PERF-1, M-ASYNC-3 |
| `modules/tools/tools.json` | source tree | truncate + rewrite | `FileLock` (not across RMW) | `{}` → next save wipes | M-DATA-1, M-TOOL-3 |
| `modules/*/module.json` ×21 | source tree (git-tracked) | `mkstemp` + fsync + replace | `RLock` | module skipped | M-SEC-3 |
| `memory.sqlite3` + `memory.faiss` | data dir | SQLite WAL; FAISS every 20 changes | `write_lock`, `faiss_lock` | count-only resync | H-6, M-DATA-4, M-DATA-5 |
| `knowledge_base.sqlite3` + `.faiss` | data dir | SQLite WAL + FTS5 triggers; FAISS via temp file | — | count-based FTS rebuild | H-5 |
| `step_journal.sqlite3` | data dir | WAL, `synchronous=FULL` | per-instance `RLock` (new instance per run) | — | M-FLOW-5 |
| `episodes/*.json` | data dir | temp + replace (no fsync) | `RLock` (not for `load_episode_by_id`) | `None` | M-SEC-9, L-19 |
| `execution_trace.jsonl` | data dir | append | lock | bad lines skipped | L-20 (unbounded, read whole file) |
| `reasoning_book.json` | data dir | `open("w")` outside lock | `asyncio.Lock` for the list only | reset to `[]` | M-DATA-3 |
| `calendar_events.json` | state dir | temp + replace | lock | `[]` | M-CAL-1, M-CAL-2 |
| `marketplace/catalog.json` + uploads | data dir | mixed (in place / temp + replace) | none | `[]` | M-DATA-6, L-11 |
| `metrics_counters.json` | data dir | temp + replace per `llm.*` increment | lock | ignored | M-PERF-2 |

**Recommendations for the layer as a whole:**
1. Move the many JSON stores that are read-modify-written at runtime (chat sessions, catalog,
   notifications, reasoning book, calendar, tool manifest overrides) into one SQLite database with
   migrations; keep JSON for export/import only.
2. One `atomic_write_json()` helper (temp file, fsync, replace, dir fsync) and one
   `load_json_or_quarantine()` helper, used everywhere; four different write strategies exist today.
3. Separate shipped defaults (in the source tree) from runtime state and secrets (in `data/`), so a
   container image, a git checkout and user data stop overlapping (M-SEC-3).
4. Record the embedding model and dimension next to every vector store and provide a re-embed job (I-4).

---

## 7. Low-severity findings and hygiene

- **L-1** ✅ `core/node_retry.py:121–133` — nested `except ... as exc` rebinds and deletes the outer `exc`,
  so `on_retry(attempt, delay, exc)` raises `UnboundLocalError` when `retry_after` is malformed or
  `delay_cap()` raises (`repro_retry.py`). LLM errors pre-parse `Retry-After`, so today this is latent.
- **L-2** `modules/brain/router.py:549–944` — four identical copies of the prompt-registry routes (verified
  byte-identical apart from whitespace); FastAPI registers all, the first wins, an edit to a later copy
  never runs. Delete three.
- **L-3** `core/flow_data.py:473/817, 504/828` — duplicated setters; the second (deep-copy) definition wins.
- **L-4** `core/planner_helpers.py` (236 lines) is imported nowhere, not even by tests; `core/flow_context.py`
  (423 lines) is used only by its tests. Delete or adopt.
- **L-5** `modules/tools/router.py:30–60` — tool names may contain spaces, which providers reject as function
  names (`^[a-zA-Z0-9_-]{1,64}$`).
- **L-6** `modules/logic/node.py:265–330` — a Conditional Router with unconfigured branches routes to `[]`,
  silently blocking everything downstream; branch lists hold node ids, which break on copy/paste.
- **L-7** `main.py` / `core/routers.py:962` — `set_active_flow` runs the repeater auto-start even for
  `action=deactivate`.
- **L-8** `main.py:139–150` — module shutdown hooks are awaited without error handling; one failure skips the
  rest and `close_shared_client()`. `print()` is used instead of logging; `/debug/fire-flow` returns task
  names as node ids.
- **L-9** `main.py:37–42` — debug key compared with `!=` (use `hmac.compare_digest`).
- **L-10** `core/security.py:277` — `hmac.compare_digest` on `str` raises `TypeError` for a non-ASCII header
  value → 500; compare bytes.
- **L-11** `core/routers.py:1229–1257, 1317` — marketplace banner keeps the uploader's file extension and is
  served inline (an `.svg`/`.html` banner is active content on the app origin); the main upload is read fully
  into memory before the 10 MB check; banners have no size limit.
- **L-12** `core/routers.py:2131` — `.py` tool import names the tool after the stored UUID file name, not the
  item.
- **L-13** marketplace tool import overwrites a built-in tool of the same name without a backup (still
  gated: outbound drafting is name-based and untiered tools default to HIGH).
- **L-14** `modules/messaging_bridge/router.py:116` — the WhatsApp webhook has no shared secret or
  signature; it is protected only by the global guard (any container on the Docker network can post forged
  sender ids when `NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE=1`).
- **L-15** `core/dependencies.py:65–70` — bridge cache compares raw settings with trailing-slash-normalised
  attributes, recreating `LLMBridge` on every request when a URL ends with `/`.
- **L-16** `core/debug.py:57` — the f-string `json.dumps(details)` is evaluated even when DEBUG is off;
  `input_resolved` logs whole node inputs (including base64 images) in debug mode.
- **L-17** `core/routers.py:2537` — "reset settings" merges defaults (keeps non-default keys such as
  `debug_api_key`), aliases `DEFAULT_SETTINGS`' nested objects, and clears `active_ai_flows` (chat stops).
- **L-18** `core/flow_manager.py:346–355` — a corrupt versions file silently becomes `{}` and is overwritten
  on the next save (history lost; flows themselves are quarantined correctly).
- **L-19** `core/session_manager.py:1062–1083` — `load_episode_by_id` mutates shared state without the lock;
  episode ids carry only 32 random bits.
- **L-20** `core/session_manager.py:91–110` — `execution_trace.jsonl` is never rotated and is read in full
  for every summary; `append` re-raises with `raise e`.
- **L-21** `core/flow_runner.py:36–58` — the per-loop lock dict is keyed by `id(loop)` and never pruned
  (use a `WeakKeyDictionary`); debug-mode `importlib.reload` re-executes module globals while other flows run.
- **L-22** `core/flow_runner.py:844` — runner fallback `max_node_loops` is 1000 while the documented default
  is 100 (the settings default is 100, so only direct users of the class are affected).
- **L-23** `core/flow_runner.py:983, 1119` — O(E) scans per node although `self.downstream_nodes` is
  precomputed and unused.
- **L-24** `modules/chat/router.py:373, 376` (+2) — mojibake (`â€”`) in user-visible strings;
  `:679–680` — the auto-rename prompt contains literal `\n` sequences.
- **L-25** `core/llm.py` — the shared `httpx.AsyncClient` and its lock are bound to the first event loop;
  `get_models` has no per-request timeout; a streaming HTTP error reads `.text` of an unread streaming response.
- **L-26** `core/observability.py` — `instrument_*()` are not idempotent (every call wraps again);
  `tool.calls_success` counts calls whose tools returned errors.
- **L-27** `modules/calendar/events.py`, `core/flow_manager.py` — `datetime.utcnow()` (deprecated);
  `@router.on_event` is deprecated as well as dead (M-LIFE-1).
- **L-28** `modules/memory/backend.py:450–470` — every vector search ranks the whole index
  (`search(q, ntotal)`); each connection re-runs a schema check and `PRAGMA journal_mode`.
- **L-29** `tests/test_email_bridge.py:59` — `email` referenced before assignment (F823).
- **L-30** `tests/test_e2e.py` needs `requests`, which `requirements-dev.txt` does not install, so the e2e
  suite is always skipped.
- **L-31** `modules/agent_loop/node.py` — 6,010 lines; `GoalPursuitExecutor` (~3,050 lines) inherits from both
  `HybridAgentExecutor` and `RLMAgentLoopExecutor`; five `receive()` implementations duplicate the
  episode/resume block (`:1000–1060, 1869–1928, 2639–2694, 4605–4669`), which is why M-FLOW-7 exists in four
  places.

---

## 8. Tool sandbox — architecture review

**Current layers** (`modules/tools/sandbox.py`): a separate worker process per call, a timeout with
cooperative cancellation, an address-space limit (POSIX only — none on Windows), restricted builtins and an
import allowlist, a domain-allowlisted HTTP client wrapper, path-restricted file access and a filtered
environment. The dispatcher adds the tier/approval gate, outbound drafting for `SendEmail`, a per-run tool
budget and output redaction.

**Assessment.** These are sensible layers, but every one of them except the separate process lives inside
the Python interpreter, and the worker runs as the same OS user with the same filesystem view as the server.
Language-level restriction is historically hard to make airtight, so it should be treated as defense in
depth rather than the primary boundary — particularly because tools can be created by the model's
operator from the UI and imported from the marketplace.

**Recommendations.**
1. Make the operating system the boundary: run tool workers as a separate unprivileged user or in a
   minimal container (read-only root, no access to `data/` or the source tree, seccomp/no-new-privileges),
   with network egress only through an allowlisting proxy.
2. Correct the documentation: `CLAUDE.md` says the sandbox blocks network calls, but the design
   intentionally allows allowlisted HTTP and SMTP for specific tools. Document the actual policy per tool.
3. On Windows, where there is no address-space limit, use a Job Object or document the gap.

---

## 9. Documentation drift

| `CLAUDE.md` / guides say | Reality |
|---|---|
| 16 modules, including `modules/telegram/` | 22 modules; `telegram/` does not exist (replaced by `messaging_bridge/`) |
| `core/` is ~7,300 LOC across 15 files | 17,497 LOC across 39 files (+ `schemas/`) |
| `core/routers.py` has 40+ endpoints | 113 route decorators |
| 33 Jinja2 templates | 55 |
| 69 test files, 919+ tests | 161 files, 2,135+ tests |
| 23 built-in tools (16 + 7 RLM) | 26 manifest entries; 19 library + 7 RLM files |
| RLM tool schemas live in `modules/tools/rlm_library/rlm_tools.json` | the file does not exist |
| Read `core/planner_helpers.py` before touching the planner | nothing imports it; the planner has its own copies |
| `core/flow_context.py` is the type-safe flow payload model | used only by its tests |
| "Never add `@pytest.mark.asyncio`" | 509 occurrences in 52 test files |
| `module_allowlist` restricts which modules can load | it gates routers only (H-8) |
| The sandbox blocks network calls | allowlisted HTTP and SMTP are permitted by design (§8) |
| Ruff is the linter | 4,506 findings; not run anywhere |

**Fix:** regenerate the layout and counts from the tree (a small script in CI can fail when they drift), and
remove the instructions that point at dead code.

---

## 10. Improvements (recommended refactors)

1. **I-1 Correct-by-construction flow scheduling.** Replace the BFS queue with readiness-based waves
   (H-2), per-activation output tracking and explicit output nodes (H-3), and side-channel usage accounting
   (M-FLOW-1). Back it with property tests that generate random DAGs and assert "each node runs once per
   activation, after all of its live parents".
2. **I-2 One error model for node execution.** Every exception from `receive`/`send` becomes a
   `NodeExecutionError` carrying the node id; `raise_errors=False` always returns an error dict; executor
   resolution errors are separate (M-FLOW-3/4).
3. **I-3 Async hygiene.** A small task registry (`spawn(coro)`) used by chat, reflection, repeater and the
   memory saver; `asyncio.to_thread` for blocking file/DB work in route handlers (`core/routers.py` performs
   synchronous file I/O in many `async def` routes); no synchronous sandbox calls on the loop.
4. **I-4 Persistence consolidation.** See §6: SQLite for runtime JSON stores, shared atomic-write helpers,
   state/secrets separated from the source tree.
5. **I-5 Split the monoliths.** `agent_loop/node.py` into one module per executor plus shared
   `episodes.py`/`truncation.py`; `core/routers.py` into `marketplace.py`, `settings.py`, `research.py`,
   `debug.py`; one skills implementation instead of two (M-SEC-6).
6. **I-6 Test the seams.** An import smoke test that imports every `modules.*.node` and `modules.*.router`
   (would have caught C-1); golden tests for the shipped default flow (would have caught H-7); port each
   `docs/audit_repro` script into a regression test as its finding is fixed.
7. **I-7 Lint baseline.** `ruff check --fix` for the safe rules, `per-file-ignores` for tool scripts, then
   enforce `F` (pyflakes) and `E9` in CI immediately and widen gradually; drop the 509 redundant asyncio
   markers.
8. **I-8 Front-end supply chain.** Vendor `htmx`, `marked`, `DOMPurify`, `sortablejs` and a compiled
   Tailwind CSS under `web/static/`, enabling a strict CSP (C-2, M-SEC-7).
9. **I-9 Observability that can be trusted.** Usage from one place, streaming instrumented, batched
   metric persistence, trace rotation (M-LLM-4, M-PERF-2, L-20).

---

## 11. Implementations (new capabilities worth building)

1. **Remote access done properly** — token or password login that issues an `HttpOnly`, `SameSite=Strict`
   session cookie, optional per-user roles (the brain module already has actors), and a documented reverse-
   proxy setup. Removes the pressure to run with `NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE=1` (M-SEC-2/8).
2. **A real scheduler service** — one background service (started from the lifespan) for repeaters,
   calendar reminders and polling, with persisted next-run times, catch-up of missed runs, timezone-aware
   events and exactly-once notification (H-7, M-CAL-1/2).
3. **Embedding management** — a settings flow that detects a missing or changed embedding model, warns in
   the memory and knowledge-base UIs, and runs a background re-embed with progress (H-6, M-DATA-4).
4. **Startup self-check / readiness endpoint** — imports every enabled module's node and router, validates
   manifests, checks the LLM and embedding endpoints, and reports problems on the dashboard instead of
   failing at the first chat turn (C-1, H-8).
5. **Safer marketplace packages** — a package manifest (type, name, files, required permissions), a
   preview/diff before import, refusal of name collisions with built-ins, and signed packages if the
   marketplace ever becomes multi-user (M-SEC-5, L-11–13).
6. **Flow linting in the editor** — surface the issues the engine already knows about: joins with parents
   at different depths, routers with empty branches, repeaters without a trigger, nodes from disabled
   modules, cycles without a loop guard.
7. **Secret storage** — a single secrets store (encrypted file or OS keyring) referenced by name from
   module config, so exports, logs, templates and git never see values (M-SEC-3/4).

---

## 12. Remediation roadmap

| Phase | Items | Rough effort |
|---|---|---|
| **0 — hotfix (today)** | C-1 (`import Any`), M-TOOL-3 (rename `Calculator.py`), restore CI and set the aikit test command (H-10), C-2 sanitiser + pinned scripts | ~1 day |
| **1 — week 1** | H-1, H-4, H-5, H-6, H-8, H-9, M-LLM-1, M-LLM-2, M-FLOW-3/4/7, M-DATA-1, M-DATA-2, M-SEC-1, M-SEC-4, M-ASYNC-1, M-ASYNC-2; import smoke test (I-6) | 3–4 days |
| **2 — weeks 2–4** | H-2, H-3, M-FLOW-1/2 (scheduler work, I-1); H-7 + M-CAL-1/2 (scheduler service); M-SEC-3 (state/secrets out of the tree); M-LIFE-1; M-DATA-3/4/5; M-PERF-1 | 2–3 weeks |
| **3 — later** | I-4 persistence consolidation, I-5 file splits, I-8 vendored assets + CSP, sandbox OS isolation (§8), remote login (§11.1), documentation regeneration | ongoing |

---

## 13. Coverage and limitations

**Reviewed in depth:** `core/flow_runner.py`, `flow_manager.py`, `settings.py`, `module_manager.py`, `llm.py`,
`security.py`, `step_journal.py`, `node_retry.py`, `execution_budget.py`, `untrusted.py`, `pinned_http.py`,
`archive.py`, the SSRF path of `research_gateway.py`, `main.py`; `modules/planner/node.py`,
`tools/node.py`, `llm_module/node.py`, `calendar/*`, `reasoning_book/service.py`,
`messaging_bridge/access.py` and `router.py`; the chat send path and session store; memory and
knowledge-base persistence and search; the chat templates.

**Reviewed in part:** `core/routers.py` (marketplace, modules, settings, debug; not the research routes),
`core/session_manager.py` (episodes, trace), `core/observability.py` (metrics, instrumentation),
`modules/agent_loop/node.py` (structure, episode handling), `modules/tools/router.py`,
`modules/logic/node.py`, `modules/skills` (admission wiring), `modules/tools/sandbox.py` (architecture only,
deliberately).

**Not reviewed:** `modules/brain/*` (beyond the duplicate routes and lifecycle hooks),
`modules/autonomous_agent/*`, `core/research_manager.py`, `statistics_engine.py`, `flow_simulation.py`,
`flow_evolution.py`, `meta_flow.py`, `cassette.py`, `delegation.py`, `handoff.py`, `retention.py`,
browser-automation and email-bridge internals, knowledge-base ingestion, memory consolidation/arbiter,
reflection lenses, most templates outside chat/settings, and the quality of the tests themselves. Absence of
findings there is not evidence of absence.

**Not run:** the e2e suite (needs a live server and `requests`), the Windows CI leg, a real LLM provider,
or a browser.

---

## Appendix A — reproduction

```bash
python docs/audit_repro/run_all.py
```

Condensed output at `8b5ddcd` (Python 3.13; the runner prints each verdict on its own line under the
script name):

```text
== repro_allowlist.py      VERDICT: BUG CONFIRMED (a non-allowlisted module's node code was imported and run)
== repro_dispatcher.py     VERDICT: BUG CONFIRMED (unanswered tool_call ids; remaining calls not propagated)
== repro_fence.py          VERDICT: BUG CONFIRMED (tool text passed through unfenced)
== repro_flow_runner.py    VERDICT: BUG CONFIRMED (D ran 2 times, first with partial input)
                           VERDICT: BUG CONFIRMED (final result is the stale first-iteration tool output, not the last LLM answer)
                           VERDICT: BUG CONFIRMED (usage multiplied by pass-through nodes)
                           VERDICT: BUG CONFIRMED (duplicate runs {'B': 2, 'C': 2})
                           VERDICT: BUG CONFIRMED (contract says error dict is returned)
                           VERDICT: BUG CONFIRMED (crash masked as pass-through success)
                           VERDICT: BUG CONFIRMED (index 1 / step 2 marked completed without being executed)
== repro_fts.py            VERDICT: BUG CONFIRMED (5 of 6 relevant queries return nothing)
== repro_llm_retry.py      VERDICT: BUG CONFIRMED (http_error/401 retried)
                           VERDICT: BUG CONFIRMED (model_limit/- retried)
                           VERDICT: BUG CONFIRMED (request_timeout setting not applied to the LLM node)
== repro_mem_keyword.py    VERDICT: BUG CONFIRMED (natural-language questions return no memories)
== repro_on_event.py       VERDICT: BUG CONFIRMED (module startup/shutdown handlers never run)
== repro_repeater.py       VERDICT: BUG CONFIRMED (default reminder loop never starts)
== repro_retry.py          VERDICT: BUG CONFIRMED -> UnboundLocalError cannot access local variable 'exc' ...
== repro_vision_clamp.py   VERDICT: BUG CONFIRMED (request refused): Model 'local-model' accepts at most 32768 context tokens, but 50042 were assembled.

19 confirmed defect verdict(s)
```

## Appendix B — baseline commands

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                                   # aborts: 4 collection errors
.venv/bin/python -m pytest -q --continue-on-collection-errors   # 2118 passed, 12 failed, 4 errors
ruff check . --statistics                                       # 4,506 findings
ruff check . --select F821,F811,F823 --output-format concise    # the real defects among them
ruff format --check .                                           # 341 files would be reformatted
```
