# Tif to NeuroCore: Integration Assessment

- **Date:** 2026-10-25
- **Reference analysed:** `references/Tif` (Tiferet): 51 Python files, 20,822 lines, 58 files total
- **Archive status:** the folder was analysed read-only and has been removed; this report is the
  durable record. No NeuroCore code imports, reads or depends on it.
- **Target:** `projects/neurocore` (`modules/brain`, `core` flow engine, memory, knowledge base, tools)
- **Status:** read-only assessment; no Tif code was copied, executed, or installed
- **Constraint:** `references/Tif` contains **no LICENSE file** (and no `pyproject.toml` or
  `requirements.txt`). Under default copyright, all rights are reserved. Treat every Tif file as
  unlicensed reading material: reimplement the ideas in NeuroCore's own style, never copy code or
  prompt text verbatim. If any verbatim reuse is ever wanted, obtain written permission first.

---

## 1. Bottom line

Tif is a single-user, desktop, LLM-driven cognitive loop. NeuroCore is a multi-tenant FastAPI
platform with a flow engine, a persisted brain, RBAC, evaluation and audit gates. Tif is therefore
**not a library to import**; it is a source of designs for capabilities NeuroCore does not have.

NeuroCore's brain already covers most of Tif's governance vocabulary: kernel modes and gates,
kill switch, risk score, drift baseline, deterministic evaluation, eval-gated flow improvement with
quotas and rollback, outcome learning, policy-gated self-configuration, event bus, reactivity and
self-model snapshots. Copying Tif's equivalents would duplicate or weaken working code.

The valuable gap is narrower: **Tif links safety to concrete runtime signals and to goals**, and it
has three capabilities NeuroCore genuinely lacks:

1. deterministic guardrails on untrusted text (prompt injection, system-prompt/secret leakage),
2. a decaying error/violation budget in the kernel instead of a monotonic counter,
3. goal-governed evolution (no mutation without an active goal or an explicit self-repair reason).

Recommended scope is 3 P0 items (about 7-8 engineer-days), 4 P1 items (about 12-14 days) and 4 P2
items (about 11-13 days). Everything else is a non-goal or not worth the risk.

| ID | Capability | Priority | Effort | Risk | Verdict |
|----|------------|----------|--------|------|---------|
| P0-1 | Guardrail gateway for untrusted text | P0 | 2-3 d | Medium | Implement |
| P0-2 | Kernel error/violation budget with decay and dwell | P0 | 2 d | Low | Implement |
| P0-3 | Goal-governed improvement and configuration | P0 | 3 d | Low | Implement |
| P1-4 | Per-run execution budget (tokens, tools, nodes, time) | P1 | 4 d | Medium | Implement |
| P1-5 | Deterministic pre-flight flow simulation | P1 | 3 d (+2 LLM) | Low | Implement |
| P1-6 | Versioned prompt/skill registry with rollback | P1 | 3-4 d | Medium | Implement |
| P2-7 | Epistemic links, contradiction scan, Bayesian confidence | P2 | 4-5 d | Medium | Implement after P0/P1 |
| P2-8 | Failure-to-avoidance consolidation | P2 | 2 d | Low | Implement |
| P2-9 | Agent-loop repetition/deadlock guard | P2 | 2 d | Low | Implement |
| P2-10 | Allowlisted research source gateway with provenance | P2 | 3 d | Medium | Implement |
| P2-11 | Unified self-modification budget | P2 | 1-2 d | Low | Implement |
| -- | Drives, persona, phenomenology, narrative, "consciousness" | -- | -- | High | Do not port |
| -- | LLM text-tag tool execution (`[EXECUTE: ...]`) | -- | -- | High | Do not port |
| -- | Tif plugin AST validator | -- | -- | -- | Inferior to existing sandbox |
| -- | Self-rewriting prompts without an eval gate | -- | -- | High | Do not port |

---

## 2. What Tif is

| Layer | Main files | What it does | Portable? |
|-------|------------|--------------|-----------|
| Kernel and safety | `ai_core/cognitive_kernel.py`, `safe_mode_controller.py`, `safety_checks.py`, `core_stability.py` | Modes NORMAL/DEGRADED/SAFE, rogue-risk score, kill switch, error reporting, command gating, mutation approval | Partly: error budget, dwell, goal linkage. Its command-string gate is not applicable (NeuroCore uses structured tools). |
| Values and alignment | `ai_core/value_core.py` | Regex + separator injection detection, output leak check, embedding similarity to harmful anchors, violation pressure | Partly: deterministic injection/leak rules. The embedding layer needs an embedder NeuroCore may not always have. |
| Resource control | `ai_core/crs.py` | Daily cognitive budget, fatigue, task-type resource tiers, mode selection | Partly: budget tiers and hard caps. Fatigue/circadian/skill decay are not meaningful for a server process. |
| Evolution | `ai_core/evolution_manager.py`, `governance.py`, `meta_learner.py` | Prompt/parameter mutations, versioned strategy registry, Bayesian-ish parameter fitting | Partly: versioned artifacts with rollback. Its registry is in-memory, unlocked and unbounded-ish (1000). |
| Goals and decisions | `treeoflife/tiferet_components/goal_manager.py`, `decision_maker.py`, `simulation_engine.py` | Goal creation, decomposition, ranking/pruning, conflict resolution; utility scoring; deadlock detection; what-if simulation | Partly: goal governance, loop detection, simulation concept. Most decision logic is a ~1,000-line LLM prompt chain. |
| Knowledge | `treeoflife/daat.py`, `memory/base_store.py` | Ingest, summarize, RDF triples, spreading activation, contradiction scan, Bayesian confidence, gap detection | Partly: evidence links, confidence formula, contradiction proposals. Ingestion and LLM calls are heavy and untested. |
| Self-model | `ai_core/core_self_model.py`, `core_phenomenology.py`, `meta_narrative.py` | Drives, identity stability, persona integrity, influence budget, narrative | Mostly non-portable. One usable idea: a global daily influence/change budget. |
| Interfaces | `desktop_assistant.py`, `ui/`, `bridges/internet_bridge.py` | Desktop UI, Telegram/Internet bridges, search | Partly: allowlisted sources, URL safety, caching. The desktop/UI layer is irrelevant. |

There is **no test directory** in Tif. Every behaviour in this report was read from source. That is
also why proposals here are specified with tests for NeuroCore rather than "port and trust".

---

## 3. Coverage matrix (avoid duplicating what exists)

| Capability | Tif implementation | NeuroCore today | Verdict |
|------------|--------------------|-----------------|---------|
| Kernel modes and gates | `cognitive_kernel.py` | `modules/brain/kernel.py:118-200` (risk, hysteresis via `safe_exit_threshold`, kill switch, gates) | Keep NeuroCore |
| Kill switch / reset with approver | `cognitive_kernel.py:291-300` | `kernel.py` `kill_switch`/`reset`, `/brain/kernel/kill|reset` | Keep NeuroCore |
| Risk inputs | drift 30, errors 20, failed attempts 20, mode 10, violations 40 | provider error 30, eval 30, drift 30, blocked 20 (`kernel.py:118-124`) | **Enhance** with P0-2 |
| Error accounting | per-component counters, 1 h decay, recovery (`:308-358`) | only `blocked_actions`, never decays (`kernel.py:156-160`) | **Port concept (P0-2)** |
| Anti-flapping | 60 s cooldown before NORMAL (`:352`) | hysteresis only from SAFE | **Port concept (P0-2)** |
| Mutation approval | requires `goal_id` or "self-repair" (`:380-440`) | only quota, eval, baseline, admin auth | **Port concept (P0-3)** |
| Immutable invariants in prompts | string-presence check on new system prompt | none | **Reject** (brittle NLP) |
| Drift baseline | ai_core/drift_monitor.py | `modules/brain/drift.py` with weighted components and persisted baseline | Keep NeuroCore |
| Deterministic evaluation | none | `modules/brain/evaluation.py` (echo suite, safe node allowlist, pass rate) | Keep NeuroCore |
| Eval-gated improvement | `evolution_manager.py` (LLM metrics) | `improvement.py` (preview, eval on copy, perfect same-flow baseline, quotas, rollback) | Keep NeuroCore |
| Learning from outcomes | `meta_learner.py` (fitness) | `learning.py` (experiences, holdout, skill consolidation, recall) | Keep; add P2-8 |
| Self-configuration | `governance.py` + settings mutation | `configuration.py` (allowlist, ranges, preview, apply, rollback, quotas) | Keep NeuroCore |
| Event bus | `event_bus.py` | `modules/brain/events.py` (bounded, timeouts, dedupe) | Keep NeuroCore |
| Reactivity | scattered | `reactivity.py` (dedupe, mute, rate limits, proposal-only) | Keep NeuroCore |
| Self-model | persistent + simulation | `self_model.py` (live providers, errors surfaced) | Keep NeuroCore |
| Goal storage | memory records | `modules/memory/backend.py:130` table, `:950-1063` CRUD | Reuse for P0-3 |
| Prompt injection | regex + separators (`value_core.py:75-100`) | none | **Port concept (P0-1)** |
| System-prompt / secret leak | output string check (`:102-110`) | none | **Port concept (P0-1)** |
| Per-run resource budget | `crs.py` | node loop cap (`flow_runner.py:729`), per-node timeout | **Port concept (P1-4)** |
| Flow pre-flight simulation | LLM-only `simulation_engine.py` | `validate_flow` (structure only), `FlowEvolution.preview` | **Port concept (P1-5)** |
| Versioned strategy registry | in-memory (`governance.py:15-63`) | flow version history; skills/prompts edited in place | **Port concept (P1-6)** |
| Evidence graph / contradiction | `daat.py:1299-1378` | memory table has no links; no contradiction job | **Port concept (P2-7)** |
| Tool sandbox | AST validator + dynamic plugins | `modules/tools/sandbox.py` (blocked builtins/modules/attributes, subprocess isolation) | NeuroCore is stronger; skip |
| Tool execution protocol | regex over LLM text (`core_actions.py:200-320`) | structured tool calls in nodes | Reject Tif approach |
| Desktop UI / persona | `ui/`, `yesod.py`, `core_phenomenology.py` | web UI, no persona claims | Non-goal |

---

## 4. Recommendations

### P0-1: Guardrail gateway for untrusted text

**Gap.** NeuroCore accepts arbitrary user text (`modules/chat/router.py:285`), documents
(`modules/knowledge_base/router.py:162`), tool output and marketplace content, but has no
conversation-level detection of context-switch injections or system-prompt/secret leakage. The tool
sandbox protects code execution, not context. This is the highest-value missing defence.

**Tif evidence.** `ai_core/value_core.py:75-100` checks a small hard-block regex list plus
`\nsystem:`, `[system]`, `system prompt`, `<|im_start|>` separators. `:102-110` flags a full
system-prompt dump in output. The idea is good; the implementation is narrow and silent
(`0.88` embedding threshold, broad `except` at `:126-131`).

**NeuroCore integration points.**
- Input: `modules/chat/router.py:285` (`send_message`), before `session_manager.add_message`.
- Ingestion: `modules/knowledge_base/router.py:162` (`process_and_save_document`), per chunk.
- Output: LLM node result before it enters the conversation (`modules/llm_module/node.py:27`), and
  flow node output in `core/flow_runner.py` before context merge.
- Memory: `modules/memory/backend.py:269` (`add_entry`) to tag provenance/trust.
- Config/status: `modules/brain/module.json` and `modules/brain/router.py`.

**Design.**
- New `modules/brain/guardrails.py`, dependency-free and deterministic:
  - `GuardVerdict(safe, category, rule, reason, action)`.
  - `scan_input(text, source)` and `scan_output(text, system_prompt)`.
  - Patterns: instruction-override phrases, role/context separators, encoded variants
    (zero-width, homoglyph normalisation, base64 blobs above a size), and a secret-shape scan
    (provider key prefixes, bearer tokens, private keys, `.env` assignments).
  - Hard caps: scan at most 20,000 characters, skip binary; never call the network or an LLM.
- Policy: `enabled`, `mode: observe|enforce`, per-surface `block|flag|allow`, allowlist regex for
  known false positives. Default `observe` on upgrade, `enforce` for new installs.
- On `flag`, attach metadata to the message/memory (`source=untrusted`, `trust=low`) rather than
  dropping silently.
- `POST /brain/guardrails/scan` for diagnostics (admin), plus counters in `/brain/state`
  (`guardrail_blocks`, `guardrail_flags`, top rules).

**Failure behaviour.** The guardrail must fail **closed** when enabled: an internal exception is
logged and the text is flagged, not allowed. It must never mutate user content beyond redaction of
detected secrets.

**Tests.** `tests/test_brain_guardrails.py`: known separators and override phrases; benign
discussion of "system prompt" not blocked in flag mode; zero-width/homoglyph variants; 50k input
truncated safely; secret-shaped strings in output; observe mode never blocks; exception path flags.

**Effort and risk.** 2-3 days. Risk is false positives in legitimate security/prompt-engineering
discussions; mitigate with `observe` default and rule-level telemetry.

**Do not copy.** Tif's embedding similarity path, the broad `except: pass`, or hard-coded
product-specific strings ("You are Tiferet").

---

### P0-2: Kernel error budget with decay, recovery and dwell

**Gap.** `BrainKernel.risk_score` (`modules/brain/kernel.py:118-124`) mixes provider errors,
evaluation, drift and `blocked_actions`. `blocked_actions` increments only while already in SAFE
(`kernel.py:156-160`) and is reset only by a manual `reset`; it never decays. There is no
per-component error budget, so repeated failures in one subsystem (for example an LLM provider or
the evaluation harness) do not accumulate pressure, and a recovered subsystem cannot reduce risk
until a full reset.

**Tif evidence.** `cognitive_kernel.py:63-90` scores drift 30, total errors 20, failed commands 20,
mode 10 and violations 40. `:308-329` keeps per-component counters with a one-hour decay, degrading
at 5 and entering SAFE at 10. `:331-343` decrements on recovery. `:345-358` enforces a 60 s dwell
before returning to NORMAL.

**NeuroCore integration points.** `modules/brain/kernel.py` (`KernelState`, `risk_score`,
`observe`, `record_blocked_action`, `assess`, `_save`), `modules/brain/service.py:252`
(`_observe_self`), outcome listener (`service.py:217-240`), LLM retry exhaustion in
`modules/llm_module/node.py:84`, and `/brain/kernel` (`modules/brain/router.py:336`).

**Design.**
- Replace the monotonic `blocked_actions` contribution with a rolling window:
  `blocked_times: list[float]` persisted, pruned at one hour, contribution
  `min(20, len(blocked_times) * 2)`.
- Add `component_errors: {name: {"count": int, "last_at": float}}` with the same one-hour decay and
  explicit `record_component_error(name)` / `record_component_recovery(name)`. Contribution:
  `min(20, total_error_pressure * 20)` where
  `error_pressure = sum(min(count, 10) for count in components)/20`, capped at 1.
- Optionally add `violation_pressure` (0-1) from P0-1 guardrail flags. Do **not** copy Tif's
  "mode" points: mode is an output of risk, so adding it double-counts.
- Keep existing thresholds (40/70/50) but add a 60-second minimum dwell before NORMAL; SAFE
  transitions bypass it. Re-evaluate on timer even without new snapshots so decay can lower risk.
- Persist counters atomically with the existing `KernelStore`; prune on load and save.
- Expose `components`, `blocked_window`, `dwell_remaining` in `to_dict()`.

**Failure behaviour.** Counter saturation is bounded; persistence failure is logged and does not
silently reset. A factory reset clears counters only through the authenticated `reset` route.

**Tests.** Five errors degrade only when the configured weights cross the threshold; recovery
decrements and eventually returns to NORMAL after dwell; old errors expire; blocked window decays;
SAFE still blocks every gate; corrupted state fails to defaults.

**Effort and risk.** 2 days, low risk. The main risk is tuning: use explicit weights and test them
rather than copying Tif's constants, which cannot reach their own 50-point degrade threshold from
errors alone (20 max).

**Do not copy.** The string command allowlist, the mode contribution to risk, or the 5/10 counts
without mapping them to NeuroCore's documented risk weights.

---

### P0-3: Goal-governed improvement and configuration

**Gap.** NeuroCore can change flows and settings without any link to a user goal. The improvement
engine enforces quotas, a perfect baseline and admin approval (`improvement.py:181-320`), but a
candidate may be pointless or contrary to the active goal. Tif's rule is blunt but effective:
every mutation must name an active goal, or an explicit self-repair reason.

**Tif evidence.** `cognitive_kernel.py:380-440` rejects a mutation without `goal_id` unless the
reason contains "self-repair", and verifies the goal exists. `goal_manager.py:385-454` ranks and
prunes goals, cascading to children; `:455+` resolves conflicts. The ranking is LLM-only and the
cascade is raw SQL, both unsuitable to copy.

**NeuroCore integration points.** Goals already exist:
`modules/memory/backend.py:130` (table), `:950-1063` (CRUD, `get_next_goal` with in-progress
priority). Improvement: `modules/brain/improvement.py:133` (`propose`), `:181` (`evaluate`),
`Candidate` dataclass, `modules/brain/router.py:258-330`, `modules/brain/service.py:308`.
Configuration: `configuration.py:184` (`apply`).

**Design.**
- Add `goal_id: str | None` and `goal_kind: "goal"|"self_repair"` to `Candidate` and to
  configuration change records.
- `propose` validates the goal with `memory_store.get_goal()` and accepts only
  `pending`/`in_progress`; `self_repair` requires a non-empty reason and is allowed only when the
  kernel is not in SAFE (SAFE already blocks promotion).
- `evaluate(approve=True)` re-validates the goal so a candidate cannot be promoted after its goal
  was completed or archived.
- On promotion, set the goal to `in_progress` if still `pending`; do **not** auto-complete it.
  Expose a separate `POST /brain/goals/{id}/complete` through the existing memory API.
- Deterministic goal review: `POST /brain/goals/review` returns proposals for stale, low-value or
  conflicting goals using priority, deadline and evidence counts. No LLM, no automatic archive.
- Configuration changes follow the same linkage (`goal_id` or `self_repair`).
- Surface `goal_id` and goal title in `/brain/state`, `/brain/improve/status` and audit logs.

**Failure behaviour.** Missing or completed goals reject the candidate with `Why:`/`Fix:`; the
ledger is unchanged. Self-repair cannot be used to bypass eval or quotas.

**Tests.** Unknown goal rejected; completed goal rejected; self-repair with reason accepted;
promotion re-check; goal moves to in_progress on promotion; configuration requires the same fields;
review endpoint proposes but never mutates.

**Effort and risk.** 3 days, low risk. The UX risk is friction; mitigate with `goal_kind` and a
clear error message.

**Do not copy.** LLM goal ranking as a gate, raw-SQL cascade deletes, or "goal" data living in
free-text memory records.

---

### P1-4: Per-run execution budget

**Gap.** The flow runner bounds total time (`flow_runner.py:602`) and node re-execution
(`:729`), and the LLM node has per-call defaults (`modules/llm_module/node.py:10-11`). Nothing
bounds the aggregate cost of one run: number of LLM calls, total tokens, number of tool calls, or
sub-flow recursion. A malicious or accidental flow can spend without limit until the outer timeout.

**Tif evidence.** `crs.py:26-68` implements a daily budget (10,000), fatigue growth/decay and daily
reset; `:154-260` maps task type and complexity to reasoning depth (3/5), beam width (1-3),
`max_tokens` (800/1,500), `allow_tools`, and a CONSOLIDATION mode when coherence < 0.4, structural
load > 0.7, violation pressure > 0.5 or fatigue > 0.85. The tiering is useful; the physiology is
not.

**NeuroCore integration points.** `core/flow_runner.py:602` (`run`), node dispatch inside
`_run`, `modules/llm_module/node.py:27`, tool execution in `modules/tools` and
`core/flow_data.py`, settings (`max_node_loops`, per-node timeouts), and `core/observability.py`.

**Design.**
- New `core/execution_budget.py`: `ExecutionBudget` with
  `max_nodes`, `max_llm_calls`, `max_tokens`, `max_tool_calls`, `max_seconds`, and usage counters.
  `checkpoint(kind, units=1)` raises `BudgetExceeded` with a precise `Why:`/`Fix:`.
- Create one budget per `FlowRunner.run`; store it in `FlowData` under a private key; nested/
  bridged runs share the parent budget. Reset per top-level request. Flow metadata may lower, never
  raise, the global cap.
- LLM node reserves `min(requested_max_tokens, remaining)`; tool executor reserves one call; the
  runner reserves one node execution.
- At 80% emit a `budget.pressure` event and force the LLM node to a concise profile; at 100% stop
  with a structured error and emit the outcome as failed.
- Defaults in `settings` (for example 200 nodes, 50 LLM calls, 200k tokens, 50 tool calls,
  settings-driven), documented in README. Expose usage in `execution_trace.jsonl` and metrics.
- Do not implement fatigue, circadian phases, skill discounts or sleeping.

**Failure behaviour.** Exhaustion is a normal, reported failure; partial results are preserved and
the trace identifies the node that hit the limit. Budget creation itself cannot fail open: an
unparseable cap falls back to the safe default and logs.

**Tests.** Each cap independently; nested flow shares the budget; per-flow override cannot exceed
the global cap; 80% pressure changes the LLM profile; exhaustion error names the limit; no budget
leak between requests.

**Effort and risk.** 4 days, medium risk (touches every executor). Mitigate by starting with
LLM/tool counters and adding node/seconds next.

**Do not copy.** The 10,000-unit "cognitive" currency, fatigue, or the 300-line branch tree.

---

### P1-5: Deterministic pre-flight flow simulation

**Gap.** `FlowManager.validate_flow` checks structure and `FlowEvolution.preview` checks operation
legality, but nothing answers "what will this flow do before it runs?" NeuroCore has no dry-run,
no effect classification, no worst-case cost estimate and no orphan/cycle report for the UI. This
is also the missing safety layer before improvement candidates are evaluated.

**Tif evidence.** `simulation_engine.py:16-92` decomposes a scenario into steps and simulates each
with an LLM; `:147-169` scores risk from failure probability plus keyword penalties, aborts at >0.8
and scales total risk by 3.5. The concept (multi-step consequence check) is valuable, but the
implementation is non-deterministic, keyword-driven and double-counts overlapping words.

**NeuroCore integration points.** `core/flow_manager.py` (graph, modules, bridges),
`core/flow_evolution.py` (`preview`), `modules/brain/improvement.py` (`evaluate`), and the flow
editor routes in `core/routers.py`.

**Design.**
- New `core/flow_simulation.py`, deterministic and offline:
  - Reachability, orphan nodes, cycles, missing module/node types, missing required config,
    bridge fan-out, unbounded loops, external-effect classification (read-only, writes memory,
    writes files, network, messaging, tools, LLM), and whether a tool is outside the allowlist.
  - Cost envelope: nodes, maximum loop iterations, LLM calls (from node types), estimated tokens
    from configured limits, tool calls, and whether the flow can exceed the P1-4 budget.
  - Output: `findings[{severity, code, node_id, message, fix}]`, `blocking`, `score`, `summary`.
- `POST /ai-flow/{flow_id}/simulate` and a "Simulate" action in the editor; improvement evaluation
  records the candidate's simulation findings next to the eval result.
- Optional advisory LLM forecast in `modules/brain/simulation.py` (budgeted, timed out, redacted,
  stored as `advisory`); it never blocks or promotes by itself.

**Failure behaviour.** Simulation never executes node code or external effects. An unknown module
degrades to a warning, not a crash.

**Tests.** Synthetic graphs for each finding; no external calls (mock the LLM layer); blocking
result for cycles/unbounded loops; cost envelope arithmetic; route contract; improvement stores
findings; advisory layer cannot change `blocking`.

**Effort and risk.** 3 days deterministic, +2 for advisory LLM. Low risk; keep it out of the hot
path.

**Do not copy.** Keyword risk scoring, LLM-only decomposition, or the 0.8/3.5 constants.

---

### P1-6: Versioned prompt/skill registry with evaluation and rollback

**Gap.** Flow nodes embed system prompts (`modules/system_prompt/module.json`,
`modules/system_prompt/node.py:118`) and skills are edited in place
(`modules/skills/backend.py:143`). There is no content-addressed history, no diff, no eval gate and
no rollback for prompt artifacts. Flow versions do not cover skills or shared prompts.

**Tif evidence.** `governance.py:15-63` defines `VersionedStrategy` and a registry with
`register_new_version`, `get_active_strategy`, `get_version` and `rollback`. Concept: adaptive
artifacts are versioned and can revert. Implementation: in-memory, `_lock = False`, no persistence,
weak `CoreGuard`.

**NeuroCore integration points.** `modules/skills/` (files + metadata), `modules/system_prompt/`,
`core/flow_manager.py` versions, `modules/brain/evaluation.py` and `improvement.py`.

**Design.**
- New `modules/skills/versions.py`: content-addressed versions (`sha256`), atomic metadata JSON
  under the state dir, `create`, `activate`, `rollback`, `diff`, bounded at N versions per skill
  (for example 50), parent pointer and author/approver.
- Every skill update through the API creates a version instead of overwriting silently. Activation
  is explicit; rollback restores the previous content and records an audit entry.
- For flow prompts, keep flow version history as the source of truth and add a `prompt_digest` to
  evaluation results so a baseline is tied to the exact prompt content.
- Read-only endpoints: list versions, diff two versions, status. Mutation requires admin, consistent
  with `/brain/config/*`.
- Optional eval gate: a skill referenced by the evaluated flow is included in the baseline run;
  promotion requires the same perfect baseline as P0-3. Do not invent an LLM judge.

**Failure behaviour.** A missing or corrupt version file quarantines the metadata and keeps the
active content unchanged. Writes use temp + fsync + replace.

**Tests.** Create/activate/rollback round trip; version cap; diff; corrupt file quarantine;
concurrent update does not lose a version; flow baseline binds to the prompt digest.

**Effort and risk.** 3-4 days, medium risk (skill API compatibility). Version the files in place;
do not migrate existing skills destructively.

**Do not copy.** The in-memory registry, the unlocked flag, or `CoreGuard` (it compares a function
with itself and cannot detect tampering).

---

### P2-7: Epistemic links, contradiction scan and Bayesian confidence

**Gap.** NeuroCore memory has facts, confidence, verification, provenance and consolidation, but no
relationships between memories. It cannot answer "what supports this?", "what contradicts it?" or
"how confident should we be given evidence?". `modules/memory/backend.py` has no link table; the
only graph-like structure is consolidation `parent_id`.

**Tif evidence.** `daat.py:1299-1339` scans recent facts for contradictions with an LLM and creates
a goal above 0.85 confidence; `:1341-1362` computes
`confidence = (supports + 1) / (supports + contradictions + 2)`; `:1364-1378` updates all
facts/beliefs. The Laplace-smoothed formula and the evidence graph are sound; the automatic goal
creation and full-table rewrites are not.

**NeuroCore integration points.** `modules/memory/backend.py` schema and CRUD, memory router, brain
self-model memory provider, and the memory browser UI.

**Design.**
- Add `memory_links(id, source_id, target_id, relation, confidence, evidence, created_by,
  created_at)` with `relation in (supports, contradicts, derived_from, supersedes)`, foreign keys
  with cascade, and indexes on both endpoints.
- Methods: `add_link`, `get_links(memory_id, relation=None)`, `delete_link`, and
  `bayesian_confidence(memory_id)` implementing the formula above (0.5 with no evidence).
- Contradiction scan (scheduled or on demand) uses embeddings/prefilter first, then an optional LLM
  only for candidate pairs; it writes **proposals** (pending links) and never edits confidence or
  creates goals automatically. An admin accepts a proposal, which creates a link and recomputes the
  target's confidence once, inside a transaction.
- Memory browser shows links and confidence; `/brain/state` reports counts.
- Do not delete or archive memories automatically.

**Failure behaviour.** LLM unavailable means the scan is skipped with a status, not an exception.
Confidence never rises above its prior without supporting links.

**Tests.** Schema migration idempotent; formula; link cascade; transaction on accept; duplicate
proposal rejected; scan without LLM returns skipped; UI contract.

**Effort and risk.** 4-5 days, medium. The main risk is noisy LLM contradictions; keep proposals
review-gated.

**Do not copy.** Auto-created contradiction goals, full-table confidence rewrites, or the broad
`except` blocks.

---

### P2-8: Failure-to-avoidance consolidation

**Gap.** `LearningLoop.consolidate` (`modules/brain/learning.py:276`) only promotes repeated
**successes** into skills. Failures stay as individual experiences and are never distilled into
"do not do this" knowledge, so the agent can repeat the same mistake and recall will not warn it.

**Tif evidence.** Its prediction-error and negative-feedback paths exist but are diffuse
(`meta_learner.py`, `decision_maker.py:1318`). It does not have a clean negative-consolidation
design; the useful idea is simply making failure patterns first-class, which Tif's `AVOID`/regret
records approximate.

**NeuroCore integration points.** `learning.py` (`experiences`, `skills`, `consolidate`,
`evaluate_recall`), memory bridge, `/brain/learn/*` routes.

**Design.**
- Add `avoidances: {key: {action, signature, error_class, occurrences, last_seen, text}}`,
  bounded (for example 200) and persisted in the same ledger.
- Consolidate failures by `action + signature + normalized error class` after `min_failures`
  (default 2); write an `[avoid] ...` entry to memory (source `brain`, confidence 0.3).
- Recall returns both skills and avoidances; an avoidance match lowers the recommendation to
  "verify before retry" rather than blocking.
- Prune by occurrence/age; delete mirrored memory entries on prune.

**Failure behaviour.** Memory write failure leaves the ledger intact and logs; consolidation is
idempotent.

**Tests.** Repeated failures create one avoidance; a success after failures does not erase it until
explicit resolution; cap and prune; recall surfaces it; idempotent consolidation.

**Effort and risk.** 2 days, low.

---

### P2-9: Agent-loop repetition and deadlock guard

**Gap.** `max_node_loops` stops a graph cycle, but an agent loop can repeat the same reasoning or
tool call with different node IDs and never trip the counter. There is no normalized repetition or
oscillation detector.

**Tif evidence.** `treeoflife/tiferet_components/decision_maker.py:1035-1071` keeps a 20-entry command history, detects
`A-A-A`, topic stagnation (4 repeats), excessive THINK and `A-B-A-B` oscillation. The thresholds
are ad hoc but the shape is right.

**NeuroCore integration points.** `modules/agent_loop/node.py` (decision loop), tool-call dispatch,
and `core/observability.py`.

**Design.**
- New `modules/agent_loop/loop_guard.py`: normalize tool name + arguments by stripping volatile
  values (IDs, timestamps, request tokens), hash, keep a 12-entry history per episode.
- Trigger on 3 identical calls, `A-B-A-B`, or 4 repeats of the same normalized topic. On trigger,
  inject one system message ("loop detected: change approach or stop") and, if repeated, end the
  episode with a structured `LoopDetected` error. Never trigger on `wait`/`reflect`-style actions
  unless they themselves repeat.
- Metrics: `agent_loop_guard_triggers_total{reason}`; state in episode trace.

**Failure behaviour.** In-memory only; a restart clears history (acceptable). The guard cannot
execute or block external actions by itself; it controls the loop.

**Tests.** Identical calls; volatile IDs not treated as different; oscillation; legitimate retry
after an error allowed; one warning then abort; no trigger on alternating distinct tools.

**Effort and risk.** 2 days, low.

---

### P2-10: Allowlisted research source gateway with provenance

**Gap.** NeuroCore has `FetchURL` and a research entity store (`core/research_manager.py`), but no
first-class, allowlisted literature source layer with caching, provenance and URL safety. Research
results can end up in knowledge/memory without a record of where they came from.

**Tif evidence.** `internet_bridge.py:28` defines allowed sources (Wikipedia, PubMed, arXiv, WEB);
`:74-120` rejects literal private IPs and resolves DNS with a timeout; `:159-224` dispatches by
source and caches. It then falls back to search-engine scraping with spoofed user agents
(`:649-800`), which should not be copied.

**NeuroCore integration points.** `core/research_manager.py`, `modules/knowledge_base` ingestion,
`modules/tools/library/FetchURL.py`, and the research UI.

**Design.**
- New `modules/research/sources.py`: explicit adapters for Wikipedia, PubMed and arXiv, each with
  its API endpoint, rate limit, timeout (10 s), max response size (2 MB), and cache TTL (24 h).
- URL safety: HTTPS only, reject loopback/private/link-local/unique-local addresses after DNS
  resolution, validate every redirect hop (max 3), and never follow to a non-allowlisted host.
- Provenance: every stored item records `source`, `source_url`, `retrieved_at`, `license`,
  `query`, `content_hash`; research records and KB documents link to it.
- No search-engine scraping, no user-agent spoofing, no robots bypass.
- Optional brain reaction: new research item can emit a proposal, not an automatic write.

**Failure behaviour.** Source outage returns a typed error and uses cache if fresh; stale cache is
returned with `stale=true`, never silently.

**Tests.** SSRF cases (127.0.0.1, 10/8, 169.254, IPv6 loopback, DNS rebinding-style host),
redirect to disallowed host, size cap, timeout, cache hit/stale, provenance fields, adapter
contract with mocked HTTP.

**Effort and risk.** 3 days, medium (network edge cases).

---

### P2-11: Unified self-modification budget

**Gap.** Improvement has daily quotas (`20/20/5`), configuration has 20/day, but there is no
single ceiling on how much the system may change itself per day, and learning writes are
unbounded. An operator cannot answer "how much did the system change today?" in one place.

**Tif evidence.** `core_self_model.py:244-260` limits external belief updates to 50/day and resets
at 24 h, regardless of subsystem.

**NeuroCore integration points.** `improvement.py` quotas, `configuration.py` quota,
`learning.py` record/consolidate, `service.py` APIs, `/brain/state`.

**Design.**
- `modules/brain/self_budget.py`: per-day counters for `candidate`, `evaluation`, `promotion`,
  `config_change`, `learning_write`, `prompt_activation`; configurable caps with defaults at or
  below today's values. Single persisted ledger under the state dir.
- All mutation entry points reserve before acting and release on definitive failure (quota is
  consumed only by accepted work). `state` exposes usage/caps.
- No fatigue or anthropomorphic language; this is an operator control.

**Failure behaviour.** Denied reservations return 429 with `Why:`/`Fix:` and change nothing.

**Tests.** Per-kind caps; reset at UTC midnight; release on failure; config values cannot exceed
hard maxima; state contract.

**Effort and risk.** 1-2 days, low.

---

## 5. Explicit non-goals

| Tif area | Why not |
|----------|---------|
| `core_phenomenology.py`, `meta_narrative.py`, `yesod.py` persona/emotion/narrative, `verify_consciousness.py` | Anthropomorphic claims with no measurable behaviour; they add prompt surface and unverifiable state. NeuroCore should describe capabilities, not feelings. |
| Drives (curiosity/energy/entropy), circadian phases, fatigue, "metabolism" | Server workloads have no circadian rhythm. Use explicit budgets and operator policy instead. |
| LLM text-tag tool execution (`core_actions.py:200-320`) | Parsing `[EXECUTE: TOOL, args]` from model output is a prompt-injection and injection-to-shell surface. NeuroCore has structured tool calls and a real sandbox. |
| Tif plugin loader and AST validator (`core_actions.py:99-148`, `safety_checks.py`) | Weaker than `modules/tools/sandbox.py:44-110`, which blocks reflection, dangerous modules and interpreter escapes. |
| LLM "double check" as a safety gate (`core_actions.py:155-195`) | Non-deterministic and itself prompt-injectable. Use deterministic policy; LLM opinions may only advise. |
| Self-rewriting system prompts without an eval gate | Tif's `UPDATE_SETTINGS` path can change prompts at runtime. NeuroCore should only promote through P1-6 with baseline and admin approval. |
| `CoreGuard` magic-patch detector (`governance.py:65-81`) | It compares a function to itself; it cannot detect tampering. Real integrity comes from packaging, file permissions and tests. |
| Giant single `decide()` / goal prompts | Untestable, non-deterministic and unbounded. Keep decisions in explicit services with typed inputs. |

---

## 6. Tif defect ledger (what not to inherit)

These defects were verified in source; any port must avoid repeating them.

1. **No tests, no packaging.** No test directory, no `pyproject.toml`, no `requirements.txt`, no
   license. Any reuse is legally and operationally unsafe.
2. **Unlocked in-memory registry.** `StrategyRegistry._lock = False` (`governance.py:24`), no
   persistence, history truncated at 1,000 with no write coordination.
3. **Swallowed failures.** Broad `except`/`except: pass` in safety and knowledge paths, for example
   `value_core.py:126-131`; a semantic check failure silently allows output.
4. **Monotonic counters without decay in some paths.** Tif has decay for component errors and
   violation pressure keeps a one-hour window, but `_failed_command_attempts`
   (`cognitive_kernel.py:53,212-219,431-437`) never decays; NeuroCore's `blocked_actions`
   currently has the same monotonic problem.
5. **Risk double counting.** Tif's rogue score adds mode points (`cognitive_kernel.py:78-82`) even
   though mode is derived from the score, and adds both error totals and per-command failures.
6. **Hard-coded brittle prompt invariants.** `cognitive_kernel.py:407-430` requires literal phrases
   in a system prompt; a legitimate rewrite fails, and a malicious prompt can include the phrases.
7. **Raw SQL cascade in goal pruning.** `goal_manager.py:429-436` archives children outside a
   transaction; a failure leaves a partial tree.
8. **LLM-only gates.** Goal ranking, conflict resolution, contradiction scanning and simulation
   have no deterministic fallback, and the meta-cognitive check is LLM-gated
   (`treeoflife/tiferet_components/decision_maker.py:1159+`); an outage silently changes behaviour.
9. **Unbounded/duplicated text handling.** Simulation concatenates the whole state into prompts
   (`simulation_engine.py:49-60`), with only a 1,500-character slice in one place; keyword scoring
   double-counts overlapping words.
10. **Network policy gaps.** `internet_bridge.py` allows a generic `WEB` source
    (`:28`), spoofs browser user agents (`:444,559,704`), and retries non-idempotent methods.
11. **Command string gating.** `SAFE_COMMANDS` and prefix checks (`cognitive_kernel.py:33`) cannot
    express structured tool policy.
12. **Desktop coupling.** Global singletons, threading and direct UI/log callbacks make the core
    hard to embed in an async server.
13. **Sensitive reference material.** `references/Tif/settings.json` may hold provider keys. The
    root `.gitignore` excludes `references/`, so the tree is not tracked, but do not open or copy
    that file and exclude the tree from any archive or publish step.

---

## 7. Suggested roadmap

Milestones are ordered by risk reduction first. Each is independently shippable and testable.

### M1: Untrusted input and kernel pressure (P0-1, P0-2) - about 5 days

Acceptance:
- Guardrail unit tests pass; observe mode is default; enforce mode blocks the injection corpus and
  flags benign security discussions without false blocks.
- Kernel risk decays; a recovered component lowers risk; NORMAL re-entry respects dwell; no
  monotonic counter remains.
- Full suite and `aikit check neurocore` pass; README documents the new settings.

### M2: Purposeful change (P0-3, P1-6) - about 7 days

Acceptance:
- Every improvement and configuration change carries a goal or a self-repair reason; invalid
  goals are rejected before the ledger changes.
- Skills have version history, diff and rollback; a flow baseline records the prompt digest.
- End-to-end: propose -> simulate (M3) -> evaluate -> approve -> rollback.

### M3: Execution control (P1-4, P1-5, P2-9) - about 9 days

Acceptance:
- A flow cannot exceed its budget; the error names the exhausted dimension.
- Simulation flags cycles, missing modules and external effects without executing anything.
- The agent loop aborts after a confirmed repetition with a structured reason.

### M4: Epistemics and research (P2-7, P2-8, P2-10) - about 10 days

Acceptance:
- Links and Bayesian confidence work with a review gate; contradiction scan degrades gracefully
  without an LLM.
- Failure consolidation creates bounded avoidance knowledge and recall surfaces it.
- Research items carry full provenance; SSRF test suite passes.

### M5: Operator controls (P2-11) - about 2 days

Acceptance:
- One page/state view shows all self-modification usage; caps are enforced at every entry point.

Total: roughly 33-36 engineer-days, including tests and documentation, excluding UI polish.

---

## 8. Metrics to watch

| Area | Metric | Target |
|------|--------|--------|
| Guardrails | blocks/flags per 1k messages; false-positive reports | < 1% false positives in observe |
| Kernel | mode transitions per week; time in SAFE; flapping count | no repeated NORMAL/SAFE flapping |
| Goals | % promotions linked to an active goal | 100% |
| Budget | runs hitting a cap; p95 tokens and tool calls per run | caps hit only on anomalies |
| Simulation | blocking findings caught before execution | increasing, no false blocking |
| Versioning | rollbacks per month; versions without baseline | rollbacks rare, 0 unbaselined promotions |
| Epistemics | contradictions accepted/rejected; confidence calibration on held-out facts | precision reported, not assumed |
| Learning | avoidance hit rate; repeated-failure rate | decreasing repeats |
| Research | items with complete provenance | 100% |

---

## 9. Open questions

1. Should P0-1 guardrails ship in `observe` by default (recommended) or `enforce` for chat?
2. Is goal linkage mandatory for **all** promotions, or only autonomous/LLM-generated candidates?
3. Should the execution budget be global per request, or configurable per flow with a hard ceiling?
4. Which research sources are acceptable by default (Wikipedia/PubMed/arXiv only, or also a general
   web source with stricter controls)?
5. Is there any intent to publish NeuroCore with Tif-derived material? If yes, obtain permission
   for any verbatim text before M2; otherwise implement from the design only.

---

## Appendix A: Tif constants worth borrowing (as concepts, not values)

| Constant | Tif location | Use in NeuroCore |
|----------|--------------|------------------|
| Error window 3,600 s; degrade 5; safe 10 | `cognitive_kernel.py:308-329` | P0-2, remapped to NeuroCore risk weights |
| Recovery decrement | `cognitive_kernel.py:331-343` | P0-2 |
| 60 s dwell before NORMAL | `cognitive_kernel.py:345-358` | P0-2 |
| Separator set and injection patterns | `value_core.py:75-100` | P0-1 seed corpus |
| Output similarity threshold 0.88 | `value_core.py:128` | Only if a local embedder is configured; advisory |
| Daily budget example 10,000 | `crs.py:26` | P1-4 defaults (expressed in tokens/calls, not "cognitive units") |
| Resource tiers 3/5 depth, 800/1,500 tokens | `crs.py:154-200` | P1-4 pressure profile |
| 50 external updates/day | `core_self_model.py:244-260` | P2-11 starting cap |
| `(supports+1)/(supports+contradictions+2)` | `daat.py:1341-1362` | P2-7 formula |
| History 20, 4-topic stagnation, A-B-A-B | `treeoflife/tiferet_components/decision_maker.py:1035-1071` | P2-9 seed thresholds |
| Allowed sources and URL checks | `internet_bridge.py:28,74-120` | P2-10 (without scraping) |

## Appendix B: Where the reference was read (paths removed)

Smallest useful reading set, in order:

1. `ai_core/value_core.py:75-133` - input/output guard ideas.
2. `ai_core/cognitive_kernel.py:63-90, 308-358` - risk and error budget.
3. `ai_core/crs.py:26-68, 154-260` - resource tiers.
4. `ai_core/simulation_engine.py:16-92` - what it tries to do (and why it is not portable as is).
5. `ai_core/governance.py:15-63` - version/rollback concept.
6. `treeoflife/daat.py:1299-1378` - evidence graph and confidence.
7. `treeoflife/tiferet_components/decision_maker.py:1035-1071` - loop detection.
8. `bridges/internet_bridge.py:28, 74-120` - source allowlist and SSRF checks.

Everything else in Tif is either desktop-specific, persona narrative, or a prompt chain with no
deterministic contract.

---

## Appendix C: Archive note and post-implementation status

The reference tree was analysed read-only and is now removed. Nothing in NeuroCore imports,
reads, or depends on it. Do not restore it: it carried no licence and its `settings.json` could
contain provider credentials. The sections below preserve every remaining idea with enough
detail to implement it without the source.

### C.1 Implementation status

| Report item | Where it now lives | ADR |
|-------------|--------------------|-----|
| Deterministic guardrails | `modules/brain/guardrails.py`, chat/KB/tool surfaces | 040, 050, 054 |
| Kernel error budget, recovery, dwell | `modules/brain/kernel.py` | 039, 052 |
| Goal-governed self-modification | `modules/brain/goal_link.py` | 041 |
| Per-run execution budget | `core/execution_budget.py`, flow/agent executors | 042, 050, 054 |
| Deterministic flow simulation | `core/flow_simulation.py` | 043, 053 |
| Versioned skill documents | `modules/skills/versions.py` | 044 |
| Evidence links, Bayesian confidence, contradiction proposals | `modules/memory/backend.py` | 048 |
| Failure consolidation into `[avoid]` knowledge | `modules/brain/learning.py` | 047 |
| Agent-loop repetition guard | `modules/agent_loop/loop_guard.py`, all agent loops | 045, 050, 054 |
| Allowlisted research gateway with provenance | `core/research_gateway.py` | 049 |
| Unified daily self-modification budget | `modules/brain/self_budget.py` | 046 |
| Link-aware recall (post-report addition) | `modules/memory/backend.py` | 055 |

No P0, P1 or P2 report item is open.

### C.2 Remaining ideas, implementable from this note

None is required for the current product. They are ordered by value per unit of risk.

**C.2.1 Metric-drift self-repair proposals (small, deterministic).**
Original idea: compare recent and prior windows of a health metric and emit a repair goal.
NeuroCore records evaluation pass rate, provider errors and drift but has no trend detector.
Design: keep a bounded history (for example the last 100 samples) in brain state; once a day
compare the mean of the last 20 against the previous 20. Trigger when pass rate falls by more
than 0.10 or provider errors rise by more than 0.10; create an improvement candidate with
`goal_kind="self_repair"` and a deterministic reason. Deduplicate by metric and window, cooldown
six hours, at most one per day through the self-modification budget. Proposal only; an admin
still approves. No LLM. Files: `modules/brain/service.py` or a small `trends.py`; tests.
Effort: one to two days.

**C.2.2 Goal review and clarity (small, deterministic).**
Original idea: rank/prune goals and score "objective clarity". NeuroCore stores description,
priority, status, context, deadline and timestamps but exposes no review and has no clarity
signal. Design: `POST /brain/goals/review` returns, without mutating, overdue goals, stale goals
(no update in N days), low-clarity goals and near-duplicates. Clarity is a deterministic score:
description length, context present, deadline or priority set, no duplicate. Promotion already
revalidates the linked goal; optionally warn when clarity is below a threshold. Files:
`modules/brain/goal_link.py` or a new `goal_review.py` plus router; tests. Effort: one day.

**C.2.3 Server-side confirmation for irreversible operations (small).**
Original idea: a pending-confirmation command. NeuroCore destructive routes (reset settings,
clear all documents, wipe memory, delete all sessions) rely on the browser `confirm()` dialog
only, which API callers bypass. Design: require an explicit typed confirmation in the body (for
example `{"confirm": "delete all documents"}`) or a short-lived nonce issued by a GET; reject
with 400 and `Why:`/`Fix:`. Keep the dialogs as UX. Admin auth already covers kernel reset.
Files: `core/routers.py`, `modules/knowledge_base/router.py`, `modules/memory/router.py`,
`modules/chat/router.py`; tests. Effort: one to two days.

**C.2.4 Asymmetric risk smoothing (small, optional).**
Original idea: fast-attack/slow-release smoothing (`alpha` 0.5 / 0.1). NeuroCore uses raw risk,
bounded windows and a dwell. Design: escalate on raw risk so SAFE is never delayed; de-escalate
on `smoothed = 0.1 * raw + 0.9 * smoothed`; persist the smoothed value; reset clears it. Files:
`modules/brain/kernel.py`; tests for a spike and a slow recovery. Effort: one day.

**C.2.5 Optional product ideas.**
- Dialectic critique flow node (thesis, antithesis, synthesis): three budgeted LLM calls,
  advisory only, never a gate. Two to three days; only if high-stakes review is wanted.
- Event-bus priority and load shedding: add only if brain event volume grows; the bus is already
  bounded, deduplicated and handler-timeout guarded.
- Gap-driven research: see C.3; a product decision, not a defect.

### C.3 Gap-driven research proposals (design only)

Goal: turn weak knowledge into reviewable research work without autonomous writes.
Inputs: low-confidence memories without accepted supporting links, unresolved contradiction
proposals, sparse unverified subjects and stale facts. Scanner: deterministic, bounded to the
last N active memories, with a cooldown per subject and deduplication against open proposals.
Output: proposals (not goals) carrying the evidence for the gap. Accepting a proposal creates a
goal; an optional action queries `core/research_gateway.search` for the topic and stores the
provenance. No LLM decides whether a gap exists; an LLM may summarise only after a human accepts.
Files: `modules/brain/gaps.py`, router endpoints, tests. Effort: three to four days. Skip if the
product should stay reactive.

### C.4 Explicitly rejected

- Drives, metabolism, circadian phases, identity-stability metrics, phenomenology, narrative and
  "consciousness" scripts. They add prompt surface and unverifiable state.
- Global workspace/spotlight and thalamus input buffering: anthropomorphic context machinery with
  no measurable advantage over session context and memory recall.
- Dream cycles, policy-weight learning, autonomous goals from value conflicts, and user/creator
  modelling: non-deterministic, privacy-sensitive and not tied to an evaluation gate.
- Plugin AST validator, shell command allowlist and text-tag tool execution: weaker than the
  existing sandbox and structured tool calls.
- Persona/default prompt text and "core value" prompt invariants: brittle, and contrary to the
  eval-gated prompt/skill workflow.
- `internet_bridge`: superseded by the allowlisted research gateway.

### C.5 Deleting the reference

It is safe to delete `references/Tif`: only this report mentioned it, and no script, test,
manifest or runtime path reads it. Do not copy code or prompt text from it; it is unlicensed.
