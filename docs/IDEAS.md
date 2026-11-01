# 💡 NeuroCore Ideas & Improvements

This document tracks brainstorming features, layout refinements, and long-term architectural upgrades for the **NeuroCore** framework lists index flaws ok.

---

## 🐞 0. Known Bugs & Gaps (found 2026-10-29)
- **[x] Truncation drops the system prompt** *(bug, IDEA-370 + IDEA-120)*:
  - `AgentLoopExecutor._truncate_messages` (`modules/agent_loop/node.py`) kept only the *last*
    system message once a conversation exceeded `max_context_tokens`. A flow with the system-prompt
    node followed by the memory goal node (`modules/memory/node.py`) sends two system messages, so
    the persona prompt was silently dropped and only the goal survived.
  - The same walk could keep a `tool` message whose assistant `tool_calls` message was cut, which
    OpenAI-compatible APIs reject.
  - Fixed: truncation now keeps every system message and treats an assistant tool call plus its
    tool results as one atomic group; the dropped count is logged. Regression test added.
- **[x] DNS rebinding passes the SSRF check** *(IDEA-063)*: `_validate_url` in
  `core/research_gateway.py` checked the resolved address, then httpx resolved again on connect.
  Fixed: both the gateway (every redirect hop) and the skill URL importer connect to the vetted
  address via `core/pinned_http.py`, with the hostname kept for `Host` and TLS `sni_hostname`.
- **[x] Token budget unenforced without provider usage** *(IDEA-369)*: `_charge_llm_usage` charged
  0 tokens when a response had no `usage` (common on local servers). Fixed with a bounded
  request-plus-response estimate when the provider omits usage.
- **[x] Step risk is fail-open** *(IDEA-060)*: the agent loop takes `risk`/`reversible` from the
  model, defaults to `low`/`true` when missing, and only adds a prompt warning.
  Fixed: tool calls are classified from the manifest (`tier` + `tier_reason`), unknown tools and
  reasonless tiers are HIGH, an argument rule can only raise a tier, and HIGH calls are held in an
  approval ledger instead of running (`modules/tools/tiers.py`, `modules/tools/approvals.py`;
  ADR-069).
- **[x] Injected text can make the agent send email** *(IDEA-032)*: `SendEmail` is enabled by
  default (`modules/tools/tools.json`) and sends to any recipient without confirmation; only its
  tool description says "use only when the user explicitly asks". Untrusted text reaches the same
  agent through the email bridge, `FetchURL` and KB documents. Fix: the tool writes a draft, and only
  a later explicit user turn sends it. Fixed: every dispatcher drafts the call
  (`modules/tools/outbound.py`); `POST /tools/drafts/{id}/send` with the confirmation phrase sends it once.

- **[x] Any stranger can drive the agent through a messaging bridge** *(IDEA-839)*: the
  Telegram, Signal and WhatsApp bridges ran every sender's message through the agent
  (`modules/messaging_bridge/service.py`), and Discord filtered only when a channel was set. Anyone
  who finds the bot could use its tools, memory and LLM quota. Fixed: `modules/messaging_bridge/access.py`
  enforces a per-bridge sender (and optional chat) allowlist before commands, sessions, media or a
  flow run, with default deny and no wildcard; Telegram and Discord seed their lists from the legacy
  config, and a denial logs only the platform id (ADR-083).
- **[x] Recall can cross user boundaries** *(IDEA-1109)*: search and keyword search filtered only
  source and deletion, so with the messaging bridge one person's memory could enter another's
  prompt. Fixed: `modules/memory/scope.py` plus `user_id`/`agent_id`/`run_id` columns and indexes
  filter recall, writes, updates and deletes by the caller's scope in the same SQL statement,
  including link expansion; `memory_scope_required` refuses an unscoped call and `migrate_scope()`
  backfills legacy rows (ADR-084).
- **[x] A resumed episode can run a stale plan** *(IDEA-1110)*: an unfinished episode was adopted by
  any later run without an `episode_id`, even after the request, model or tools changed. Fixed:
  `core/episode_identity.py` hashes the normalised request, model and tool set; checkpoints carry
  an id, parent, source and step; `resume_policy`/`resume_any` guard `prepare_resume` across all
  four agent loops, and a mismatch pauses the old episode (ADR-085).
- **[x] A crash can repeat a side-effecting tool call** *(IDEA-1111)*: the episode checkpoint was
  written only after the loop, so an interrupted call could run again on resume. Fixed:
  `modules/agent_loop/tool_journal.py` persists a `pending` entry before dispatch and a `done`
  entry with a result digest and preview; `tool_journal_policy` (`ask|skip|rerun`) and the manifest
  `idempotent` flag decide whether an orphaned call may be retried (ADR-086).
- **[x] A retried flow run repeats every completed node** *(IDEA-1152)*: `_run` kept `node_outputs`
  in memory and replayed the whole DAG after an error or restart. Fixed: `core/step_journal.py`
  journals committed node outputs by deterministic `run_id` and input digest, reuses them on
  resume, refuses to replay an interrupted effectful node (`IncompleteStep`) and attaches a
  duplicate `_idempotency_key` to the first run; off by default behind `step_journal_enabled`
  (ADR-087).
- **[x] Completed episodes and journals accumulate forever** *(IDEA-1153)*: no completion or expiry
  stamp existed and only a manual delete removed a file. Fixed: `core/retention.py` stamps both
  expiries (journal capped by completion), `prune_episodes(dry_run=True)` quarantines expired
  episodes with a tombstone, and a reused `episode_id` starts at `generation + 1` (ADR-088).
- **[x] A selective memory filter still ranks the whole index** *(IDEA-1154)*: `search` always
  scanned every FAISS vector and filtered afterwards, and `type`/`subject`/`verified` were
  unindexed. Fixed: `modules/memory/filter_plan.py` bounds a cardinality probe at 5,000 rows,
  compares candidates against a scan-byte budget, and `search` scores the SQL candidates
  exactly when the set is small, with a parity test against the index (ADR-089).
- **[x] IVF search returns approximate scores for a truncated request** *(IDEA-1155)*:
  `FaissDocumentStore.search` never set `nprobe` and mapped ANN scores straight into the top-k.
  Fixed: `modules/knowledge_base/recall.py` sets `nprobe` from the list count, over-fetches
  `limit x oversample` candidates and rescores them exactly against stored vectors; a flat index
  keeps the plain path (`kb_recall_budget`; ADR-090).
- **[x] A manifest can name a primitive the runtime lacks, and a missing file runs empty code**
  *(IDEA-1172)*: the dispatcher read only `enabled`/tier and ran `library/<name>.py`, so a case
  mismatch or an RLM entry returned success-shaped empty output. Fixed:
  `modules/tools/capabilities.py` declares the primitive set, `preflight` refuses an unknown or
  unsupported requirement or a missing source before the tier gate, `resolve_source` searches both
  libraries case-insensitively, and `GET /tools/capabilities` reports drift (ADR-091).
- **[x] A wedged tool call cannot be cancelled and a timeout looks like a result** *(IDEA-1173)*:
  the only bound was the 30 s sandbox timeout, with no call id, cancel path, progress or status.
  Fixed: `modules/tools/calls.py` adds a per-call context and bounded registry, the sandbox polls
  a cancel event every 100 ms and terminates the child, and failure returns a receipt whose status
  is `cancelled`/`timeout`/`error` with `result` unset; `POST /tools/calls/{id}/cancel` reaches it
  (ADR-092).
- **[x] Recall can overflow the prompt's memory slot** *(IDEA-1174)*: recall clamped only the count
  and joined every text unbounded, so a long block was dropped whole or crowded out knowledge.
  Fixed: `modules/memory/budget.py` keeps rank order, dedupes within a scope, evicts whole
  memories over the total, truncates only the top item at a sentence boundary, and returns a
  footer plus `_memory_budget` receipt (`recall_max_total_chars`/`recall_max_item_chars`;
  ADR-093).
- **[x] An edit hides its previous value and cannot be undone** *(IDEA-1175)*: the edit payload
  held only `new_id`, so the old text lived in prose and no history or inverse existed. Fixed:
  `modules/memory/history.py` writes a receipt (both ids, both hashes, changed fields),
  `GET /memory/{id}/history` returns bounded entries and `POST /memory/edits/{id}/undo` restores
  the previous value only while the head and hash still match (ADR-094).
- **[x] Every eval check weighs the same and reports only pass/fail** *(IDEA-1206)*: checks were
  loose dicts and a task passed only when all of them passed, so a latency probe could veto a
  correctness result. Fixed: `modules/brain/assertions.py` gives each check a typed
  `CheckSpec`/`CheckVerdict` with weight, metric, required and optional threshold, aggregates a
  score with named metrics, refuses malformed specs, and keeps a required failure authoritative
  (ADR-095).
- **[x] An edited eval check can look like an improvement** *(IDEA-1207)*: a run stored a fixed
  suite name and no task identity, so a changed assertion compared against an unrelated baseline.
  Fixed: `modules/brain/suite_manifest.py` content-addresses each task and the suite,
  `compare_results` reports an incomparable pair instead of a regression, and `promote_baseline`
  needs an explicit accepted reason to replace a changed baseline (ADR-096).
- **[x] A browser click reports success even when nothing changed** *(IDEA-1208)*: click and
  type_text returned success without reading anything back. Fixed:
  `modules/browser_automation/indexed.py` adds generation-scoped element ids, a typed
  `ActionResult`, an expectation poll and `stale_element`/`unverified`/`policy_refused` codes
  (ADR-097).
- **[x] Page snapshots are cut silently** *(IDEA-1209)*: HTML and text were truncated with a
  marker and screenshots had no ceiling. Fixed: `modules/browser_automation/snapshot.py` measures
  against a `SnapshotBudget` and returns a typed refusal, with reported truncation only on
  request (ADR-098).
- **[x] A flow outcome cannot be tied to the step that produced it** *(IDEA-1237)*: outcomes were
  bare dicts even though the trace context already had span ids. Fixed: the existing
  `TraceContext` gains root-once sampling and `current_span_ids`, and `emit_outcome` stamps
  `trace_id`/`span_id`/`parent_span_id` without mutating the caller (ADR-099).
- **[x] A prompt has no version, digest or rollout label** *(IDEA-1238)*: prompt text lived in node
  code and config, so a rollout was an in-place edit. Fixed: `modules/brain/prompt_registry.py`
  publishes immutable versions with digests, moves labels for a rollout, records label history and
  rolls a label back (ADR-100).
- **[x] A delegated task has no capability or output contract** *(IDEA-1242)*: delegation was an
  implicit call with no tool bound, expected shape or depth limit. Fixed: `core/delegation.py`
  adds `DelegationContract`, typed `CapabilityRefused`, an output check and a depth cap
  (ADR-101).
- **[x] A handoff is fire-and-forget** *(IDEA-1243)*: a context handoff had no delivery record,
  acceptance check or retry. Fixed: `core/handoff.py` tracks delivered/accepted/rejected/
  exhausted states, bounds context and re-queues a rejection within its retry budget (ADR-102).
---

## 🔬 1. Core Engine & DAG Runner
- **[ ] Execution Trace Visual Replay**:
  - Build UI playback slider that reads `execution_trace.jsonl` to animate node transitions on visual maps, giving full debug walkthrough histories flawlessly flaws ok.
- **[ ] Node Memoization / Caching Cache**:
  - Cache node sends based on hashing input weights payloads to skip re-running expensive LLM triggers if data did not mutate in short-looped intervals flawless ok.
- **[ ] Parallel Merge Node Blocks**:
  - Introduce explicit implicit execution Split/Join nodes expanding concurrent workflow lanes seamlessly flaws ok.

---

## 🌍 2. Community Marketplace
- **[x] Rating & Sorting system** *(implemented)*:
  - Users can Upvote/Downvote catalog listings so top assets sort highest inside grid layout lists.
- **[ ] Flow Visual Maps Previews Overlay**:
  - Hovering or clicking shared workflows triggers a read-only vis-DAG overlay graph preview so user sees the Node-tree before download flawlessly.
- **[x] Versioning & Sync Tracking** *(implemented)*:
  - Items carry a `changelog` list prepended on each update; imported items track `downloaded_version` for update detection.

---

## 🧠 3. Skills Management
- **[x] Live Rich Text Prompt Editor** *(implemented)*:
  - Skill edit/create flow available in Settings → Skills with full in-UI editor.
- **[ ] Skill Synthesizer / Creator Templates Template**:
  - Insert "+" floating triggers setting blank categories placeholders mapping custom prompts creation directly inside `.skills/data` arrays flawless ok.

---

## 🎨 4. UX & Interfaces
- **[ ] Light / Grid Theme Toggle**:
  - Standard variables setups toggle support natively flawless ok.
- **[ ] Dashboard WebSockets Streaming**:
  - Stream `stdout` logs live via lower console logs drawers avoiding debug-only lookup cycles flawlessly layout streams flaws ok.

---

## ⚙️ 5. Reliability & Error Recovery (Core Engine)
- **[x] Node Retry Policies with Backoff** *(implemented, ADR-060)*:
  - Per-node `retry_count`, `retry_delay`, `retry_max_delay`, `retry_backoff` and
    `timeout_seconds`; transient failures only, charged to the run budget.
- **[x] Node Deadlocks / Stalls Detector** *(implemented, ADR-061)*:
  - Per module/type latency history with p50/p95/p99; an attempt above both 2 s and 3x the prior
    p99 is logged, counted and exposed by `GET /debug/node-latency` without aborting the run.

---

## 📈 6. Advanced Memory & RAG Strategies (Knowledge Base)
- **[ ] FAISS IndexHNSW Scaling for expanding corpuses**:
  - `IndexFlatIP` does linear exact scans. As files grow, upgrading dynamic index types to approximate indices (like HNSW) scales lookup speeds effortlessly flawless flawless ok.
- **[ ] Dynamic Memory Consolidation (Background Workers)**:
  - Setup background cron-triggers loading FAISS indices to merge duplicates, summarize dense chat clusters into memory beliefs automatically flawlessly ok.

---

## 🛠️ 7. Dynamic Tools & Full Sandbox
- **[ ] Code Interpreter Docker Executor Layouts**:
  - Expand execution pipelines pushing tool calls onto isolated docker socket payloads allowing full OS pip installs safely flawlessly ok.

---

## 📊 8. Dashboard Analytics Visualizations
- **[ ] Live Metrics Gauges Overlays**:
  - Plot running charts reading `core/observability.py` values to inspect flows through put in real-time flawless ok.

---
