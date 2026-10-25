# Decisions

Why this project is built the way it is, oldest first. One entry per decision that changes a
contract, a dependency, a data format or the architecture; IDs are consecutive and never reused.

<!-- Entry format (use the next free ADR-NNN id):
### ADR-NNN: <decision in one line>
- **Date**: YYYY-MM-DD
- **Status**: Accepted
- **Context**: <what forced the decision>
- **Decision**: <what was decided>
- **Consequences**: <what follows, including what it costs>
-->

### ADR-001: Bind the control plane to loopback, opt in to remote/reload, and guard the Host header
- **Date**: 2026-10-20
- **Status**: Accepted
- **Context**: `main.py` started uvicorn on `0.0.0.0` with `reload=True`, so a default launch was
  reachable from any network and restarted on file changes; the Docker publish (`8000:8000`) was
  likewise open to every host interface. The control plane has no authentication, so this exposes
  RCE-capable routes (module hot-load, tool import) to the network.
- **Decision**: `core/security.py` owns the bind policy. The server binds `127.0.0.1` unless
  `NEUROCORE_HOST` opts in; auto-reload is off unless `NEUROCORE_RELOAD` opts in; and a
  Starlette `TrustedHostMiddleware` accepts only loopback hosts plus `testserver` unless
  `NEUROCORE_ALLOWED_HOSTS` opts in. `docker-compose.yml` publishes `127.0.0.1:8000:8000` while
  the container binds `0.0.0.0` internally.
- **Consequences**: A local launch keeps working unchanged. Reaching the app from another machine
  now needs two deliberate acts (a bind opt-in and, when applicable, an allowed-host entry).
  Docker is only reachable through the host loopback address until the publish mapping is edited.

### ADR-002: Extract marketplace archives through a path-confined helper
- **Date**: 2026-10-20
- **Status**: Accepted
- **Context**: `import_marketplace_item_to_neurocore` called `zipfile.extractall` (and a manual
  member loop) on attacker-controlled packages, so a member named `../evil.py`, an absolute path,
  or a symlink could write anywhere the process can reach (zip-slip).
- **Decision**: All module extraction goes through `core/archive.extract_zip_safely`, which rejects
  absolute members, `..` traversal, drive-qualified names, symlink entries, and any resolved path
  outside the destination. An unsafe package returns HTTP 400 and writes nothing.
- **Consequences**: Legitimate single-root packages are still unwrapped into `modules/<name>/`. A
  package that relies on writing outside its own folder is refused, which is the intended
  contract. Read-only member access (file preview) is unchanged because it never writes.

### ADR-003: Allowlist first-party sandbox imports and block dunder traversal
- **Date**: 2026-10-20
- **Status**: Accepted
- **Context**: The tool sandbox passed through every `modules.*` import, so tool code could
  `import modules.tools.sandbox` and read that module's real `os`/`sys` globals; dunder traversal
  (`().__class__.__mro__[1].__subclasses__()`) could also reach the real interpreter.
- **Decision**: Passthrough is an explicit allowlist (`modules.calendar.events`) rather than a
  prefix, a bare `modules` import is refused, and the AST gate rejects the dunder attributes,
  subscript keys and `getattr`/`setattr` names that reach the interpreter object graph.
- **Consequences**: Tools still import the one first-party module they need; the sandbox itself is
  no longer importable. Determined code with computed attribute strings is still bounded by the
  subprocess isolation and static gate, which is best-effort in-process Python.

### ADR-004: Keep secrets out of config exports and debug logs
- **Date**: 2026-10-20
- **Status**: Accepted
- **Context**: `/settings/export/config` returned the whole `settings.json` (including
  `llm_api_key`), and `DebugLogger.log` stored and printed arbitrary `details` dicts verbatim.
- **Decision**: `core/security` gains `redact_secrets` (mask, for logs) and `strip_secrets`
  (drop, for exports). The debug logger redacts at its boundary; the config export strips secrets
  by default and only includes them when `NEUROCORE_EXPORT_SECRETS` opts in.
- **Consequences**: Exports no longer round-trip API keys, so a re-import cannot overwrite a real
  key with a placeholder. Full backups need the explicit opt-in. Sensitivity is by known name or
  `*_key`/`*_token`/`*_password`/`*_secret` suffix so ordinary fields are untouched.

### ADR-005: Escape stored content in server-rendered HTML and innerHTML sinks
- **Date**: 2026-10-20
- **Status**: Accepted
- **Context**: Two module routers built HTML by string interpolation
  (`modules/memory/router.py`, `modules/memory_browser/router.py`), and the marketplace templates
  placed stored other-user text (comment bodies, uploader names, notifications) into `innerHTML`.
- **Decision**: The hand-built HTML interpolations call `html.escape`, and the marketplace
  templates define and use `escapeHtml` for every stored field they insert into `innerHTML`. The
  Jinja environment's autoescape is asserted by a regression test.
- **Consequences**: Stored comment/notification text renders as text, not markup. Field-by-field
  escaping must be maintained when new dynamic sinks are added; the chat view's `marked.parse`
  markdown rendering was left unchanged and is noted as remaining work.

### ADR-006: Require an opt-in API token for remote clients and reject cross-origin writes
- **Date**: 2026-10-20
- **Status**: Accepted
- **Context**: The control plane had no authentication or CSRF protection, so once reachable it
  accepted any state-changing request, including from a browser it did not originate.
- **Decision**: A `ControlPlaneGuard` middleware rejects state-changing requests whose `Origin`
  header is neither same-origin nor an explicitly trusted origin, and requires an
  `X-API-Token`/`Authorization: Bearer` match (constant-time) for non-loopback clients when
  `NEUROCORE_API_TOKEN` is set.
- **Consequences**: The local workflow and the test client are unchanged (no token configured,
  same-origin or absent `Origin`). A remote bind now needs `NEUROCORE_API_TOKEN`; because the host
  guard already blocks non-loopback hosts by default, both must be opted in deliberately. The
  token is checked per request so it can be rotated by restart, not mutation.

### ADR-007: Integrate lattice as the evidence backend for the research module
- **Date**: 2026-10-20
- **Status**: Proposed
- **Context**: NeuroCore's research module (`core/research_manager.py`) manages hypotheses, articles, study designs, and findings in a local SQLite database. Lattice (`projects/lattice/`) is a separate provenance-first research knowledge graph with quote-backed claims, deterministic evidence graphs, MCP server for AI agents, contradiction detection, consensus synthesis, and a Web Research Studio. Maintaining two separate research data stores creates duplication, limits agent access to evidence, and prevents unified synthesis.
- **Decision**: Integrate lattice as the backend for neurocore's research entities via a `LatticeAdapter` that translates neurocore ResearchManager calls to lattice operations. **ZERO DETAIL LOSS**: Every lattice feature, data field, CLI command, MCP tool, web UI component, analytical function, token budget, and validation rule must be preserved and accessible in neurocore. NeuroCore agents will access lattice's complete evidence graph through MCP tools. A bidirectional event-sourced sync engine with field-level conflict resolution will reconcile edits from both sides. The lattice Web Research Studio will be embeddable in neurocore's research tabs with full parity. Lattice must expose a public `lattice/api.py` module for zero-overhead direct access.
- **Consequences**:
  - *Positive*: Single source of truth for research evidence with full provenance; AI agents get token-budgeted provenance-rich context via MCP; contradiction detection and consensus synthesis become available in neurocore; federated literature discovery (arXiv, OpenAlex, Crossref, EuropePMC) flows into neurocore articles; Web Research Studio provides advanced UI without neurocore implementation effort.
  - *Trade-offs*: Adds dependency on lattice project availability; requires adapter layer maintenance; bidirectional sync needs field-level conflict resolution strategy; lattice's stdlib-only constraint means no heavy ML dependencies for embeddings (neurocore's FAISS remains for vector search).
  - *Implementation*: Phase 1 (lattice/api.py + adapter + MCP client), Phase 2 (ResearchManager migration + event-sourced sync), Phase 3 (UI/agent integration + full Research Studio parity), Phase 4 (advanced features), Phase 5 (hardening). Design doc: `docs/lattice_integration_design.md`.

### ADR-008: Ship the autonomous agent as a disabled experimental library
- **Date**: 2026-10-21
- **Status**: Accepted
- **Context**: `modules/autonomous_agent/` arrived as an untracked ~120 KB prototype. It had no router, so `module.json` advertised an enabled module that never loaded; `AgentService.handle_user_message` raised `NameError` for `IncomingMessage`; and six lifecycle methods were stubs. The runtime is not wired into NeuroCore's flow runner, UI or service lifecycle.
- **Decision**: Keep the runtime in-tree as an experimental, opt-in library. Mark the module `enabled: false`, fix the import and node base class, implement perception recording, flow adoption, atomic state persistence, memory serialization and chat buffering, and cover the core lifecycle with `tests/test_autonomous_agent.py`. Do not expose HTTP routes, auto-start it, or register it as a flow node until a follow-up ADR defines service lifecycle, authentication and shutdown.
- **Consequences**:
  - *Positive*: The work is preserved and reviewable; the gates pass; the code no longer claims to be an active module; core behavior is tested.
  - *Trade-offs*: The agent does not appear as an available module in the UI; enabling it requires a router/service lifecycle and a security review for its sandboxed flow actions.
  - *Persistence*: State is stored under `modules/autonomous_agent/data/` (git-ignored) using temp-file + fsync + `os.replace`; corrupt snapshots are quarantined.

### ADR-009: Self-evolve through validated, versioned flow edits
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: The goal is a self-evolving AI brain whose executable behavior lives in flows and
  which humans can talk to through one gateway. The experimental agent could build flow
  prototypes in memory, but nothing wrote them to `ai_flows.json`, `FlowManager.save_flow`
  discarded ownership metadata, and version snapshots lost it, so agent edits could not become
  the running brain or be safely audited and reverted.
- **Decision**:
  1. The editable substrate is the flow graph. Agent-initiated evolution edits flows only; core
     Python, settings, module code and the sandbox remain out of scope.
  2. `core/flow_evolution.FlowEvolution` is the only mutation path. It validates names, required
     node keys, unique ids, edge endpoints, self-loops, duplicate edges, graph acyclicity and
     node/connection bounds, then saves through `FlowManager` under its lock. Failed proposals
     write nothing.
  3. `FlowManager.save_flow` accepts optional metadata, keeps the original `created_at` on update,
     and version snapshots carry metadata and `reverted_nodes`; rollback restores them.
  4. The experimental agent receives an optional `FlowManager` and persists created, updated and
     deleted owned flows, so its flows survive restarts and are adopted on initialize. It exposes
     `evolve_flow(flow_id, operations)` to apply validated edits to owned flows only, and its
     cognitive loop can dispatch `UPDATE_FLOW` actions to that path. The module stays disabled
     until a follow-up ADR defines the human gateway, authentication and lifecycle.
  5. Roadmap: Phase 1 (this) validated versioned evolution core plus the agent apply path; Phase 2
     agent reasoning that proposes edits and evaluates outcomes (including automatic reversion);
     Phase 3 human gateway routes (chat and messaging) with the control-plane guard; Phase 4
     memory-driven evolution metrics and long-horizon self-improvement.
- **Consequences**:
  - *Positive*: Self-editing is real, reversible and testable; ownership metadata survives save,
    restart and rollback; the autonomous agent's flows become executable NeuroCore flows.
  - *Trade-offs*: Every future mutation caller must use `FlowEvolution` or duplicate its checks;
    rollback stays silent and does not add a new version entry, so callers that need a redo point
    must snapshot before rolling back. Gateway exposure is still gated.

### ADR-010: One evolution store, a node-type allowlist and lossless rollback
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Reviewing ADR-009 found that `AgentService` built a second `FlowManager` on the
  singleton's file, so two in-memory copies could overwrite each other's flows. Evolution also
  accepted any `moduleId`/`nodeTypeId`, bypassing the agent's capability limits; `save_flow`
  dropped `reverted_nodes`; rollback reset `created_at` and left the agent cache stale; and the
  create path used an older validator that never checked cycles.
- **Decision**:
  1. `AgentService` uses the shared `core.flow_manager.flow_manager` singleton unless a manager is
     injected. Tests inject managers on temp files. Flow adoption reads the manager snapshot, not
     a second parse of the same file.
  2. Agent-initiated evolution uses the allowlist from `FlowCreator.node_template_types()` plus
     the sandbox `allowed_tools`; `FlowEvolution` rejects node pairs outside the allowlist and
     `tool_dispatcher` configs that name disallowed tools. Core callers may pass their own
     allowlists. Adding a capability is therefore an explicit template or policy change.
  3. `FlowManager.save_flow` preserves `reverted_nodes` and the original `created_at`, and
     deep-copies stored node, connection and bridge lists. Snapshots carry `created_at`;
     rollback restores it.
  4. `AgentService.rollback_flow` restores an owned flow and refreshes `created_flows`.
  5. `FlowCreator.create_flow` and `update_flow` both validate with `FlowEvolution`, so create
     cannot bypass the DAG, endpoint and allowlist gate.
- **Consequences**:
  - *Positive*: One authoritative store removes lost updates; the agent can only wire capabilities
    its templates declare; edits keep their schema and creation time across save and rollback;
    create and evolve share one validation contract.
  - *Trade-offs*: All agent flow writes serialize on the shared manager lock. New node types need
    an allowlist update before an agent can use them. Rollback still adds no version entry, so a
    caller that needs a redo point must snapshot first.

### ADR-011: Execute evolved flows and make the flow store copy-safe
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: The hardening review found that evolved flows were stored but never executed,
  deleting a flow left its version snapshots behind, public FlowManager reads and writes returned
  mutable internals, validation rejected tuple sequences, a whitespace description produced an
  empty name, create's existence check raced save, and the agent-node test used the real shared
  store.
- **Decision**:
  1. `AgentService.execute_flow` runs an owned flow through
     `FlowRunner(flow_id, flow_override=flow).run(...)`; `EXECUTE_FLOW` actions dispatch to it.
     Activation of an evolved flow as the app-wide default remains a later gateway step.
  2. `FlowManager.delete_flow` removes the flow's version history from memory and disk.
  3. `get_flow`, `save_flow`, `list_flows` and `rollback_version` return deep copies, so callers
     cannot mutate the store without saving.
  4. `FlowEvolution.validate` accepts list or tuple sequences, and `create` checks for an existing
     id under the manager lock.
  5. A blank description falls back to `"Agent flow"`. `AgentNodeConfig.flow_store_path` selects an
     isolated store for node runs; empty uses the shared process store.
- **Consequences**:
  - *Positive*: Self-edited flows are executable, deletions leave no orphan state, store access is
    copy-safe, and validation matches its sequence contract.
  - *Trade-offs*: Execution still needs a configured FlowRunner/module graph; automatic activation
    and human-gateway routing remain Phase 3 work. Returning copies adds a deep copy per read.

### ADR-012: Run the active flow and guard optional runtime config
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Wiring `EXECUTE_FLOW` to the real runner exposed a regression: the planner's
  default chat action carries no `flow_id`, so it failed the ownership check. Activations were
  also manual store edits, null `sandbox` configs broke the allowlist lookup, non-dict metadata
  raised a raw `TypeError`, and deleting an active flow left `active_ai_flows` stale.
- **Decision**:
  1. `_execute_flow` resolves a missing id to the first `active_ai_flows` entry, falling back to
     `default-flow-001`, and runs it through `_run_flow`. The public `execute_flow` remains
     ownership-checked.
  2. `activate_flow` makes an owned flow the first active chat flow via
     `settings.save_settings({"active_ai_flows": [...]})`.
  3. Null `sandbox` configs are treated as empty in the allowlist lookup, and `update_flow`
     rejects non-dict metadata with `FlowValidationError`.
  4. `delete_flow` removes the deleted id from `active_ai_flows`.
  5. A real `FlowRunner` integration test drives an agent-owned flow with mocked node executors,
     covering the constructor and result shape.
- **Consequences**:
  - *Positive*: Default chat and explicit run actions both execute, activation is a supported
    operation, optional config no longer crashes the allowlist path, and deletions leave settings
    consistent.
  - *Trade-offs*: Automatic planning of `UPDATE_FLOW` remains Phase 2; activation is explicit.
    The integration test mocks modules, so full module-graph execution is still exercised only by
    the broader flow suite.

### ADR-013: Persist deletion first and make execution ownership explicit
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: `delete_flow` pruned `active_ai_flows` before writing the flow and version files, so a
  settings failure left the flow removed from memory but still on disk. `_execute_flow` used the
  unrestricted `_run_flow` for explicit ids, so action execution and the public API disagreed on
  ownership, and a stale first active id raised instead of falling back.
- **Decision**:
  1. `delete_flow` persists the flow and version stores first, then prunes `active_ai_flows`;
     settings failures are logged and do not roll back the completed deletion.
  2. `_execute_flow` accepts an explicit id only when the agent owns it, it is listed as active, or
     it is `default-flow-001`; everything else raises `SandboxViolation`.
  3. Missing flow ids use `_resolve_active_flow`, which skips stale active ids and falls back to
     the default chat flow, raising only when neither exists.
  4. Activation failure paths (unowned and missing flows) are covered by tests.
- **Consequences**:
  - *Positive*: Deletion reaches a consistent persisted state even when settings cannot be written;
    explicit execution no longer bypasses the ownership boundary; stale active ids degrade to the
    default flow instead of failing the action.
  - *Trade-offs*: A settings failure leaves a deleted id in `active_ai_flows` until the next settings
    write; the deletion itself is complete and the store is authoritative.

### ADR-014: Order flows by last edit and cover the end-to-end path
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Preserving `created_at` on update stopped edited flows from rising in the flow list,
  because `list_flows` sorted only by creation time. The product review also asked for one
  end-to-end path and a guard that the experimental agent stays disabled.
- **Decision**:
  1. `save_flow`, `rename_flow`, `rollback_version` and the default-flow writers set
     `updated_at`; `list_flows` sorts by `updated_at` with `created_at` as the fallback.
  2. `created_at` remains the immutable creation time, and version snapshots keep it so rollback
     preserves creation history while refreshing `updated_at`.
  3. Add an end-to-end test covering create, evolve, activate and execute through the real
     `FlowRunner` with mocked node executors.
  4. Add a test asserting `modules/autonomous_agent/module.json` remains `enabled: false`.
- **Consequences**:
  - *Positive*: Edited flows surface first without losing their creation history; the product path
    from self-edit to execution is covered in one test; accidental agent enablement fails CI.
  - *Trade-offs*: Flows imported from older data without `updated_at` sort by `created_at` until
    they are saved again; the end-to-end test mocks node executors rather than real modules.

### ADR-015: Evaluate evolved flows and auto-revert regressions
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Phase 2 of the self-evolution roadmap needs a try-evaluate-keep-or-revert loop.
  Applying edits blindly can leave the brain worse, and the human gateway needs a structured way
  to submit an edit plus a test input without brittle natural-language parsing.
- **Decision**:
  1. `evolve_and_evaluate` applies an edit, runs the evolved flow with `evaluation_input` through
     `execute_flow`, and reverts to the pre-edit version when the result carries an error,
     `status` is `error`/`failed`, execution raises, or a custom evaluator returns false.
  2. `UPDATE_FLOW` actions forward `evaluation_input` and `auto_revert`; without an evaluation input
     they keep the existing apply-only behavior.
  3. The planner accepts structured `perception.metadata` (`flow_id`, `flow_operations`,
     `evaluation_input`, `auto_revert`) and emits one `UPDATE_FLOW` action, returning early so
     intent keywords cannot add competing actions.
  4. The pre-edit snapshot created by `FlowEvolution.mutate` is the rollback target; a failed
     evaluation refreshes `created_flows` with the restored flow.
- **Consequences**:
  - *Positive*: The agent can keep a successful edit or undo a regression automatically, and the
    gateway can submit a proposed edit with its acceptance test as structured data.
  - *Trade-offs*: Evaluation executes the flow, so it costs a run and needs valid input; the
    default evaluator only treats error-shaped results as failures. Automatic generation of
    proposals (the LLM/planner policy) is still later work.

### ADR-016: Generate flow-edit proposals with an opt-in LLM proposer
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Phase 2 needs the agent to produce its own edit proposals. Parsing natural language
  into graph operations is brittle, and the model must not write directly to the flow store.
- **Decision**:
  1. `FlowProposer` asks the configured `LLMBridge` for one JSON object containing a bounded
     operation list, using a compact flow summary plus the allowed node types and tools.
  2. The proposer validates the response shape, the operation set, node pairs and size, but never
     writes; `FlowEvolution` remains the only graph validator and writer.
  3. `AgentService.propose_evolution` returns operations; `propose_and_evolve` applies them and
     routes through `evolve_and_evaluate` when an `evaluation_input` is supplied.
  4. `UPDATE_FLOW` accepts an `instruction` when no operations are supplied; the planner emits it
     for natural-language evolve/improve/optimize requests that name a flow. The LLM bridge is
     created lazily, and tests inject fakes.
- **Consequences**:
  - *Positive*: The agent can generate a bounded edit from a goal, while the graph gate and
    evaluation/revert loop remain authoritative; no model output reaches the store unvalidated.
  - *Trade-offs*: Proposal quality depends on the configured model and prompt; a model without
    JSON-mode support fails cleanly. Proposals still require an owned flow and the allowlists.

### ADR-017: Meta-flows compose ganglia through pathways
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Small flows are useful units, but the brain needs to combine them. The requested
  model is ganglia (member flows) joined by pathways (cross-member connections) into meta-flows.
- **Decision**:
  1. `core/meta_flow.MetaFlowComposer` namespaces member node ids as `g<index>__<id>`, rewires each
     member's connections and bridges, adds pathway edges, and validates/saves one normal flow
     through `FlowEvolution`.
  2. Persisted metadata records `kind: meta_flow`, the ganglia membership (index, flow id, node ids)
     and the normalized pathways.
  3. Missing members, unknown pathway endpoints, cycles and members with multiple boundaries
     without explicit pathways are rejected. A single member may appear more than once.
  4. `AgentService.compose_meta_flow` composes only flows the agent owns and registers ownership of
     the result.
  5. Composition inlines members, so the existing `FlowRunner` executes meta-flows without a new
     subflow executor. Live subflow nodes (reference, not inline) are future work.
- **Consequences**:
  - *Positive*: Flows compose into a single validated executable graph; membership and pathways are
    explicit, inspectable metadata; the agent can build meta-flows from its own ganglia.
  - *Trade-offs*: Inlining duplicates member nodes and does not track later edits to a member.
    A member with multiple entry/exit nodes requires explicit pathways, and deep recursion is
    bounded only by the existing size limits.

### ADR-018: Compose meta-flows through the agent action loop
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: `MetaFlowComposer` existed only as a direct API. The brain needed a sandboxed action
  and planner intent so a human request or structured gateway message could combine ganglia.
- **Decision**:
  1. Add `ActionType.COMPOSE_FLOW` with an `own_flows_only` policy; the sandbox checks every member
     flow before delegating.
  2. `AgentService._execute_compose_flow` forwards the name, members, pathways and metadata to
     `compose_meta_flow`, which persists and owns the result.
  3. The planner emits `COMPOSE_FLOW` from structured `member_flows` metadata, or from two or more
     flow ids in a combine/compose/merge message.
  4. `_extract_flow_ids` returns unique ids in appearance order; single-id extraction is unchanged.
- **Consequences**:
  - *Positive*: Composition is available through the same sandbox, planner and action path as other
    agent operations; ownership is enforced before any read or write.
  - *Trade-offs*: Text-based composition depends on ids being present in the message, and a
    composition request still needs an explicit name (otherwise it defaults to
    `Combined meta-flow`).

### ADR-019: Live meta-flows reference ganglia through a subflow node
- **Date**: 2026-10-22
- **Status**: Accepted
- **Context**: Inline composition copies member graphs, so later edits to a ganglion do not reach
  the meta-flow. The requested model treats ganglia as stable units and pathways as the links
  between them.
- **Decision**:
  1. Add `core.meta_flow.MetaFlowComposer.compose_live`: one `meta:subflow` node per member
     (`g<index>`, config `flow_id`) and member-level pathways. Metadata records
     `mode: live`, ganglia and pathways.
  2. Add the `meta` module with a `Subflow (Ganglion)` node. The executor runs the referenced flow
     through `FlowRunner`, passes input/output through, and guards against cycles and excess depth
     with a context-local stack (default depth 8). A context-local manager override makes nested
     flows resolve in the executing agent's store; direct runs use the process store.
  3. `AgentService.compose_meta_flow(..., live=True)` and the `COMPOSE_FLOW` action support live
     composition; structured or textual requests can set `live`. Ownership is checked for every
     member.
  4. The evolution allowlist now applies to **new** node types: an edit may keep existing node types
     outside the agent template set, so a live meta-flow remains editable. Newly added types are
     still rejected.
- **Consequences**:
  - *Positive*: Live meta-flows track member edits, execute with the existing runner, and expose an
    explicit ganglion node for the UI. Cycles and runaway recursion fail with clear errors.
  - *Trade-offs*: Live execution nests `FlowRunner` instances and costs more than an inline graph.
    Members must still exist at execution time, and deleting a referenced member breaks the live
    edge.

### ADR-020: Audit hardening for state, escaping, sandbox and memory
- **Date**: 2026-10-23
- **Status**: Accepted
- **Context**: A read-only audit found that one malformed `/ai-flow/save` could make the next start
  discard every flow, the shared `escapeHtml` helper emitted no escapes, computed `getattr` reached
  interpreter internals inside the tool sandbox, upgrades never ran memory migrations, consolidation
  crashed on FAISS ID maps, and Docker did not persist root-level state.
- **Decision**:
  1. `FlowManager.save_flow(..., validate=True)` is used by the HTTP save path; `ai_flows.json` and
     version files are written through fsynced temp files with `.bak`; invalid/corrupt stores are
     quarantined and restored from backup, never silently reset over the only copy.
  2. Restore the entity mappings in `web/templates/_utils.html`. Remove reflection builtins from the
     sandbox, reject references to them, and block `operator.attrgetter`/`methodcaller` plus
     `__dict__`-family attributes.
  3. Run `_execute_init` on every MemoryStore startup, repair consolidation (FAISS import, IDMap
     mapping, linear fallback, background execution, success-only retry clock), re-add edited
     memories to FAISS, persist KB `stored_filename` for delete/integrity checks, and honour
     `NEUROCORE_STATE_DIR` for root-level state files.
  4. Keep the UI editor free to save cyclic flows: `validate=True` is structural, while the stricter
     evolution validator remains the gate for agent-created graphs.
- **Consequences**:
  - *Positive*: Saves fail closed, corrupt state is recoverable, stored marketplace content is
    escaped, reflective sandbox escapes are closed, upgrades and consolidation work, and Docker keeps
    state without baking settings or chats into image layers.
  - *Trade-offs*: Invalid editor payloads now receive 400 instead of being persisted; reflection
    helpers are unavailable to custom tools; `.bak` and quarantined files accumulate until cleaned.

### ADR-021: Composition loops and containment follow the runtime, not a stricter DAG gate
- **Date**: 2026-10-23
- **Status**: Accepted
- **Context**: The default chat flow and most user flows contain bounded loops. The evolution
  validator rejected every cycle, so inline composition and agent edits could not touch real
  flows, and inline composition left router branch ids pointing at pre-namespacing names.
  Separately, archive expansion, small-sample statistics and chat persistence had unguarded
  failure modes.
- **Decision**:
  1. `FlowEvolution.validate/create` take `allow_cycles=False` by default; inline `compose` passes
     `allow_cycles=True` for member graphs but rejects cycles in the ganglion pathway graph. Agent
     creation stays acyclic; mutation preserves an existing cycle in a composed meta-flow but
     rejects introducing a cycle into an acyclic flow.
  2. Inline composition rewrites node ids stored in `true_branches`, `false_branches`,
     `rlm_branch` and `standard_branch` while namespacing.
  3. `tool_agent` uses unique template keys, an acyclic two-pass LLM graph, populated router
     branches and node-id `reverted_nodes`.
  4. ZIP extraction streams with per-member and total caps; statistics reject undersized samples
     and zero-variance division; FAISS startup synchronization repairs any count mismatch; chat
     history writes run in `asyncio.to_thread`.
- **Consequences**:
  - *Positive*: Real flows compose and route correctly, agent tool flows are usable, archives
    cannot expand without bound, and small/constant samples return defined results.
  - *Trade-offs*: Inline meta-flows can contain loops and rely on FlowRunner's loop bound; pathway
    cycles remain forbidden. Archive limits are module constants (50 MB/member, 200 MB total).

### ADR-022: Mutable stores use cross-process locks, retention and reversible history
- **Date**: 2026-10-23
- **Status**: Accepted
- **Context**: Chat history was a single JSON file with only a process-local lock, so two workers
  could overwrite each other, and neither sessions nor messages had a cap. Flow rename, delete and
  import bypassed version history, delete discarded snapshots, and rollback could not be undone.
  Correlation on constant series emitted NaN, and FTS only rebuilt when completely empty.
- **Decision**:
  1. `SessionManager` takes a `FileLock` around every read-modify-write, reloads the file when its
     mtime changed, writes through a fsynced unique temp file, and applies configurable retention
     (`max_sessions=200`, `max_messages=2000`; zero disables each cap). Invalid JSON is quarantined.
  2. `rename_flow`, `delete_flow` and `import_flows` snapshot affected flows first. Delete retains
     history; `rollback_version` restores deleted flows and records a redo snapshot so rollback is
     itself reversible.
  3. Pearson/Spearman return null statistics with a zero-variance explanation instead of NaN, and
     FTS rebuilds whenever its row count differs from the chunks table.
- **Consequences**:
  - *Positive*: Concurrent chat workers stop losing messages, chat/flows cannot grow without bound,
    deleted or renamed flows remain recoverable, and search/statistics self-heal.
  - *Trade-offs*: Chat mutations take a file-lock round trip; very long histories/session lists are
    trimmed; deleted-flow versions remain until their per-flow cap; FTS can rebuild at startup.

### ADR-023: AI-Brain v1 is a real observe/suggest substrate with an evaluation gate
- **Date**: 2026-10-23
- **Status**: Accepted
- **Context**: The autonomous agent's self-model returned mock state, there was no scorecard for
  judging changes, and self-* features were disconnected. Self-improvement without measurement
  would be unverifiable and unsafe.
- **Decision**:
  1. Add the `brain` module as a control plane with a live `BrainSelfModel` reading flows, module
     manifests, memory, knowledge-base, metrics and evaluation files; provider failures are
     surfaced, never replaced with plausible placeholders.
  2. Add a bounded async `EventBus`, a cognitive scheduler with observe/suggest modes, pending
     proposals, hourly cycle throttling, a kill switch and atomic brain-state persistence.
  3. Add a deterministic echo evaluation suite, an atomic last/baseline result store and a
     regression comparison. Later self-improvement must pass this gate before promotion.
  4. Keep autonomous actuation (`auto`) unimplemented and rejected by the API. The module is
     enabled so its API and startup hook exist, but `auto_start` is false by default.
- **Consequences**:
  - *Positive*: The brain now reports measured, inspectable state; proposals are evidence-backed;
    evaluation provides a stable baseline for future learning and self-modification.
  - *Trade-offs*: Brain state is in-process plus a small JSON snapshot; event history is bounded;
    the default suite is deliberately small and deterministic; `auto` remains future work.

### ADR-024: Learning is outcome-driven, train-only consolidation with held-out recall
- **Date**: 2026-10-23
- **Status**: Accepted
- **Context**: The brain could observe and evaluate but did not convert outcomes into durable
  knowledge, and a naive consolidation that included evaluation examples would inflate recall.
- **Decision**:
  1. Record each action/evaluation outcome with action, input signature, output preview, success,
     error, latency, tags, trace id and a deterministic train/holdout split.
  2. Mirror raw experiences and consolidated skills into the shared memory store (`EXPERIENCE`
     and `RULE` entries, source `brain`) so recall is not a separate silo.
  3. Consolidate only successful *training* outcomes, after a configurable minimum occurrence
     count, into structured `[skill]` JSON rules; update existing skill memories instead of
     duplicating them.
  4. Measure held-out recall (precision@k, recall, MRR) from global-memory skill retrieval;
     holdout outcomes never contribute to skills.
- **Consequences**:
  - *Positive*: Learning is measurable, outcomes are auditable, and repeated successful behavior
    becomes reusable knowledge rather than a pile of chat memories.
  - *Trade-offs*: The local ledger is capped at 1000 experiences; consolidation uses lexical
    retrieval rather than embeddings; the first baseline has no learned skills.

### ADR-025: Self-improvement evaluates a copy and promotes only on an explicit gate
- **Date**: 2026-10-23
- **Status**: Accepted
- **Context**: Allowing the brain to edit live flows without a measurable gate would let a
  plausible-looking but worse change persist, and evaluating by mutating the live flow exposes
  other requests to half-finished state.
- **Decision**:
  1. Candidate flow edits are validated with `FlowEvolution.preview`, then evaluated through
     `EvaluationRunner` with an in-memory `flow_override`; the live flow is untouched until an
     approved promotion.
  2. Promotion requires an existing baseline for the same flow, a non-regressing pass rate, no new
     failing tasks and latency within the configured threshold; otherwise the candidate is
     recorded as failed and the flow stays unchanged.
  3. Promotion is explicit (`approve=true`), snapshots a pre-promotion version and can be rolled
     back. Daily candidate/evaluation/promotion quotas are persisted in the improvement ledger.
  4. Candidate generation is available through an injected generator or the configured LLM via
     `FlowProposer`; the generated operations still pass preview, evaluation and approval.
- **Consequences**:
  - *Positive*: Self-improvement is auditable, reversible and cannot silently degrade a live flow.
  - *Trade-offs*: Promotion is human-approved for now; evaluation uses the shared deterministic
    suite; candidate generation requires a configured LLM; quotas may delay experiments.

### ADR-026: Evaluation is read-only and improvement needs perfect, attributed baselines
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: Verification found that a 0% baseline could authorize a 0% candidate, that evaluation
  would execute tool/memory/calendar nodes, that quota exhaustion lost the evaluation record, and
  that promotion had no approver identity.
- **Decision**:
  1. Evaluation rejects any flow containing nodes outside a read-only allowlist (chat input/output,
     LLM, system prompt, conditional router, KB query). Callers cannot opt in through the HTTP API.
  2. A baseline is promotable only when it is perfect (`pass_rate == 1.0`, no failures), has a
     positive task count, names the same flow and records `recorded_by`/`recorded_at`.
  3. Promotion requires `approved_by`, a perfect candidate, no new failures, and latency within the
     threshold. Quota exhaustion marks the candidate `blocked` and persists the evaluation before
     raising.
  4. Brain state snapshots carry compact improvement summaries; learning experiences, skills and
     candidate ledgers are capped and pruned, including their mirrored memory rows.
  5. Self-model sync providers run in `asyncio.to_thread`; event handlers have a timeout; `/state`
     no longer rewrites the state file on every request.
- **Consequences**:
  - *Positive*: Baseline poisoning and unsafe evaluation are closed, approvals are attributable,
    quota failures are auditable, and the event loop no longer blocks on SQLite/FAISS reads.
  - *Trade-offs*: Evaluation cannot test tool-using flows yet; promotions need a named approver;
    ledger locks remain process-local; safe read-only evaluation is the current boundary.

### ADR-027: Self-configuration is bounded, attributed and reversible per target
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: The brain could edit flows but not its own settings or module posture. Allowing
  unrestricted configuration would expose secrets, protected subsystems and its own authority.
- **Decision**:
  1. Configuration operations are limited to whitelisted settings (`temperature`, `max_tokens`,
     UI booleans), a small module allowlist (`annotations`, `planner`, `reflection`,
     `reasoning_book`), and bounded brain options (cycle interval, quotas, learning caps).
  2. Protected modules (brain, tools, memory, KB, messaging, browser, autonomous agent, chat, LLM)
     and secret settings are rejected. Self-configuration can only narrow `allowed_tools`, never
     widen them.
  3. Every apply requires `approved_by`, writes an atomic snapshot/ledger record, and supports
     rollback. Older changes for the same target cannot be rolled back over a newer one.
  4. Module changes go through `ModuleManager.enable_module/disable_module`; brain options update
     `modules/brain/module.json` atomically and apply live runtime limits.
- **Consequences**:
  - *Positive*: The brain can tune safe settings and module posture with audit, attribution and
    rollback, without gaining new authority.
  - *Trade-offs*: Changing a module requires the live module manager; configuration ledgers remain
    process-local; module enable/disable has runtime side effects by design.

### ADR-028: A risk-driven kernel gates the brain before autonomy
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: The brain could observe, learn, improve and configure, but had no global mode or
  risk interlock. A degraded provider or repeated evaluation failures should stop memory and
  configuration writes before autonomous behavior is enabled.
- **Decision**:
  1. Add `BrainKernel` with `NORMAL`, `DEGRADED` and `SAFE` modes. Risk combines provider-error
     rate, evaluation pass rate, drift and blocked actions; SAFE entry is risk >= 70 and exit needs
     risk < 50 (hysteresis). A kill switch requires an explicit attributed reset.
  2. SAFE blocks learning writes, consolidation, improvement proposals/evaluation and
     configuration application; evaluation and rollback remain available for diagnosis/recovery.
  3. Add `DriftMonitor` with an explicit, attributed baseline over provider error rate, evaluation
     pass rate, memory volume and knowledge documents. Drift feeds the kernel.
  4. Persist kernel mode/risk and expose kernel/drift status and control endpoints. The kernel
     never executes actions itself; it only allows or denies them.
- **Consequences**:
  - *Positive*: A single interlock now protects learning, improvement and configuration; risk and
    gate state are inspectable; recovery is deliberate and auditable.
  - *Trade-offs*: Thresholds are heuristic; drift can rise from legitimate memory growth; SAFE
    blocks improvement until reset; kernel state is process-local unless the file is shared.

### ADR-029: Reactivity produces proposals, never actions
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: Event sources existed in separate modules, with no shared salience, dedupe or mute
  policy. Wiring them directly to actuators would create uncontrolled autonomous behavior.
- **Decision**:
  1. A `ReactivityEngine` subscribes to the brain event bus and polls calendar, watched files and
     metric thresholds into pending reactions.
  2. Reactions are deduplicated by key, muted for a bounded time, capped per hour and stored in an
     atomic ledger. Dismiss/mute/unmute are explicit API operations.
  3. The kernel gates reactions: normal reactions are blocked in SAFE mode, while health/metric
     alerts remain visible for diagnosis. Reactions are advisory proposals only.
  4. The engine is off by default (`reactivity_auto_start: false`); `/react/start` enables polling.
- **Consequences**:
  - *Positive*: Calendar/files/metrics can demand attention without granting execution authority;
    duplicate or noisy signals are suppressible; SAFE mode does not hide health problems.
  - *Trade-offs*: Polling is coarse and in-process; file watching sees changes only between polls;
    reaction ledgers are process-local; there is no delivery channel beyond the API/event bus yet.

### ADR-030: Ordinary flow outcomes feed learning; evaluation is excluded
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: Learning only saw manual `/brain/learn` calls and evaluation runs, so everyday chat
  and flow use produced no training signal. Evaluation already records per-task outcomes, so a
  second generic flow record would double-count.
- **Decision**:
  1. Add a dependency-free `core.outcomes` hook. `FlowRunner.run` emits one outcome with flow id,
     success, error, duration and a short output summary; a listener failure never changes the
     flow result.
  2. The active brain service registers as the listener and records `flow:<id>` experiences while
     the kernel permits memory writes; stopping or resetting the service unregisters it.
  3. Evaluation runners construct `FlowRunner(capture_outcome=False)`, and
     `capture_flow_outcomes` in `modules/brain/module.json` can disable capture globally.
  4. Tests clear the global listener after every test so a test brain cannot capture later flows.
- **Consequences**:
  - *Positive*: Normal usage now produces measurable experiences and skills; evaluation remains a
    clean scorecard; flow-run volume is bounded by the experience cap and mirror pruning.
  - *Trade-offs*: Outcome recording is synchronous on flow completion; nested subflows emit their
    own outcomes; flows only produce signal while a brain service exists.

### ADR-031: Brain approvals use authenticated request actors
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: Approval endpoints accepted `approved_by` from the request body, so any caller could
  choose the audit name. Remote access also had no brain-specific credential.
- **Decision**:
  1. All `/brain/*` routes share a `get_brain_actor` dependency. Loopback/test clients are trusted
     as `local` (or an optional `X-Brain-Actor` label); non-local clients must present
     `NEUROCORE_BRAIN_TOKEN` via `X-Brain-Token` (constant-time compare) and receive `401` on a
     missing/wrong token.
  2. Mutating routes use the authenticated actor name for promotion, configuration, kernel and
     drift-baseline audit records; the body `approved_by` field is ignored.
  3. `GET /brain/actor` reports the resolved name, role and whether the token was authenticated.
  4. Local trust remains for the local-first product; exposing the brain remotely requires the
     brain token even when the global API token is unset.
- **Consequences**:
  - *Positive*: Remote brain controls are closed by default; approvals are attributable to a
    request identity rather than a caller-provided string.
  - *Trade-offs*: ADR-033 replaces the single admin role with viewer/operator/admin actor roles; local malware can still act as
    the local actor; the UI/API clients must send the brain token when remote.

### ADR-032: The Brain dashboard is local-first and proposal-oriented
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: Brain state, proposals and reactions were API-only, making them hard to inspect or
  operate without ad-hoc requests.
- **Decision**:
  1. Add `/brain/ui`, a Tailwind/HTMX-free vanilla dashboard included in the navbar when the brain
     module is enabled. It renders kernel mode/risk, health, cycles, proposals, reactions and
     evaluation state, refreshing every five seconds.
  2. Controls cover start/stop/cycle, kill/reset, reactivity start/stop/poll, evaluation run and
     baseline promotion, plus reaction dismiss/mute. No improvement promotion or configuration
     apply is exposed from the dashboard yet.
  3. All user-provided values are passed through the shared `escapeHtml` helper before insertion
     into `innerHTML`; the page is local-first and follows the router's actor dependency.
- **Consequences**:
  - *Positive*: The governed brain is inspectable and operable from the product UI; no build step
    or frontend framework is introduced.
  - *Trade-offs*: Remote dashboards need the brain token at the proxy/API layer; the page polls
    instead of streaming; advanced candidate/config operations still require the API.

### ADR-033: Brain actors use hashed tokens and three roles
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: ADR-031 established a single shared brain token and one admin role. Multi-client use
  needs attributable per-actor credentials and a way to grant read/operator access without admin.
- **Decision**:
  1. Add a `BrainActorRegistry` stored under `NEUROCORE_STATE_DIR/data/brain_actors.json`; tokens are
     generated with `secrets.token_urlsafe`, salted, and stored as PBKDF2-SHA256 hashes. Plaintext
     tokens are returned only on create/rotate.
  2. Roles are `viewer` < `operator` < `admin`. Viewer can read; operator can learn, evaluate,
     react and cycle; admin can promote improvements, change configuration, control the kernel,
     set baselines and manage actors.
  3. Actor management is exposed only to admins. Local/test clients remain local admins; the
     `NEUROCORE_BRAIN_TOKEN` environment value remains a shared admin override for compatibility.
  4. `get_brain_actor` authenticates a supplied registry token before the environment token, then
     falls back to local trust; remote clients without a valid token get 401/403. Role dependencies
     enforce the hierarchy on each mutating route.
- **Consequences**:
  - *Positive*: Per-client revocation/rotation, non-admin operators, and no plaintext token storage;
    every high-impact action has an attributable actor and role.
  - *Trade-offs*: Remote token authentication updates `last_used` on each request; roles are
    global (no per-flow ownership); local clients remain trusted; token loss requires rotation.

### ADR-034: Brain ledgers reload and mutate under one cross-process lock
- **Date**: 2026-10-24
- **Status**: Accepted
- **Context**: Each brain store kept its ledger in process memory and rewrote the whole file through
  a temp-file replace. Two workers or two server processes could each load the same ledger, apply a
  change and overwrite the other's update, losing outcomes, candidates, configuration changes,
  kernel blocks, reactions or actors.
- **Decision**:
  1. Add `modules/brain/persistence.py` with `LedgerLock` (a thread re-entrant guard plus a
     `filelock.FileLock` on `<ledger>.lock`), `atomic_write_json` (fsynced temp file plus
     `os.replace`) and the `engine_transaction`, `engine_transaction_async` and
     `ledger_transaction` decorators.
  2. Every brain store owns a `LedgerLock` and its `load`/`save` run while it is held. Mutating
     engine methods are decorated, so they reload the ledger, apply one change and persist it
     before another writer can interleave. Nested engine calls reuse the outer transaction.
  3. The learning, improvement, configuration, kernel, reactivity and actor ledgers are
     covered. Evaluation last/baseline, drift baseline and brain state files use the same lock
     and atomic write without reloading, because their writes are whole-snapshot replacements.
  4. A lock wait is bounded at 10 seconds and then fails with `filelock.Timeout`; the lock is
     never held across a provider or LLM call.
- **Consequences**:
  - *Positive*: Concurrent workers keep each other's ledger changes; the nine copied temp-file
    write blocks become one tested helper.
  - *Trade-offs*: A `.lock` file sits beside every brain ledger; the brain state file remains
    last-writer-wins because it describes the local runtime, not durable knowledge; a stuck
    holder delays a writer for up to 10 seconds.

### ADR-035: Audit P0 hardening fails closed and states its limits
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: An external audit of `e491655` reported: no token check for remote clients when
  `NEUROCORE_API_TOKEN` was unset; an in-process fallback when the tool worker could not start;
  tools reading the whole process environment; a network policy that accepted mixed DNS answers,
  skipped redirect targets, buffered whole responses and treated an empty allowlist as "default";
  a hostname-only CSRF check; an unused worker pool that could not run; an agent flow pattern
  that raised `KeyError`; memory search returning short pages; and diverging dependency files.
  Verification confirmed these, rejected one claim (the agent's `llm_module` node type is the
  type the LLM dispatcher handles; `llm_core` does not exist) and found more: two agent templates
  (`telegram_output`, `kb_query`) no module dispatches, credentials forwarded on cross-host
  redirects, untested S2/S3/S4 fixes, and `module.json` files truncated to 0 bytes because
  manifests were rewritten in place while concurrent test runs read them.
- **Decision**:
  1. A non-loopback bind refuses to start without a token unless
     `NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE=1`; the middleware rejects tokenless non-loopback
     clients on the same terms. Origins compare scheme, host and effective port.
  2. A tool worker that cannot start raises `SandboxStartupError`; tool code never runs in the
     application process. Tools see only the `env_allowlist` values. The HTTP client rejects any
     blocked DNS answer, re-validates every redirect, drops `Authorization`, `Proxy-Authorization`,
     `Cookie`, `auth` and `cookies` when a redirect changes origin, streams under a byte cap and
     treats `allowed_domains=set()` as no network. README calls this defense in depth, not OS
     isolation.
  3. `modules/tools/sandbox_manager.py` is deleted: nothing imported it and it used
     `queue.Queue` as inter-process transport.
  4. Agent templates dispatch to `messaging_output` (platform pinned to `telegram`) and
     `query_knowledge`; a `scheduled_task` pattern replaces the missing `repeater_node` pattern.
     Tests resolve every template through `FlowRunner._get_executor_class`.
  5. Semantic memory search filters source, parent, deletion and expiry in SQL, walking ranked
     candidates in batches until the page fills; FAISS ranks every vector.
  6. `ModuleManager` writes `module.json` through `_write_manifest` (serialize, fsynced temp file,
     `os.replace`).
  7. `pyproject.toml` is the dependency source of truth: `scipy` becomes core (built-in statistics
     routes import it), `requirements.txt` is runtime plus the browser/documents/messaging extras,
     `requirements-dev.txt` adds the `dev` extra, and `tests/test_packaging.py` enforces it. The
     Docker image installs runtime requirements only, purges `gcc` and runs as UID 1000; compose
     maps `NEUROCORE_UID`/`NEUROCORE_GID` and sets the unauthenticated-remote opt-out its loopback
     publish justifies. `data/.gitkeep` is tracked so the bind mount keeps host ownership. CI runs
     Linux 3.12/3.13, Windows 3.12 and a Docker build that must refuse a tokenless start.
  8. `tests/conftest.py` points `NEUROCORE_STATE_DIR` at a per-run temp directory before any
     app import, and `protect_module_files` snapshots protected files once per session, refuses
     to start (exit 2, with `Why:`/`Fix:`) when one is already invalid JSON, and restores
     atomically only on change. The old per-test snapshot plus truncate-and-write restore let
     two concurrent runs capture an empty manifest and re-impose it after every later test; it
     emptied five `module.json` files (restored from git) and the gitignored root `ai_flows.json`,
     which had no backup and could not be recovered.
  9. Review of the full uncommitted change set added: tools reach NeuroCore's own port on
     loopback only for `/memory/goals` and `/browser_automation/api/` (no `..` or `%`), and a
     name other than `localhost`/`127.0.0.1` resolving to loopback is rejected, because loopback
     clients need no token and `/settings` or module install would let tool code reconfigure
     the app or load code into it; `NEUROCORE_TRUSTED_ORIGINS` entries are origin-normalized;
     chat sessions detect another writer by `(mtime_ns, size, inode)` instead of mtime alone;
     `AgentService.start` raises when initialization fails and unhandled action types are
     rejected instead of reported as successful; loopback brain clients stay local admins
     when `NEUROCORE_BRAIN_TOKEN` is set, as ADR-031 states; `.gitignore` keeps nested `data/`
     directories ignored while tracking only `/data/.gitkeep`.
  10. Test change: `test_os_getenv_works` asserted that tools read the inherited environment, the
     behaviour item 2 removes. It was replaced by `test_os_getenv_hides_the_inherited_environment`
     and `test_os_getenv_reads_permitted_values`.
- **Consequences**:
  - *Positive*: Misconfiguration fails at startup instead of exposing the control plane; sandbox
    isolation never degrades silently; secrets reach a tool only by name; every security fix has
    a regression test; agent flows dispatch; manifests cannot be left truncated.
  - *Trade-offs*: Deployments that bound `0.0.0.0` without a token must now set one or the
    opt-out; tools that read arbitrary environment variables must list them in `env_allowlist`;
    `pip install -r requirements.txt` no longer installs pytest; a Linux host user other than
    UID 1000 must set `NEUROCORE_UID`; DNS is still resolved again at connect time, so pinning the
    validated address remains open, as do cycle rejection by default (F1) and a container
    boundary for untrusted tools (S6).

### ADR-036: Brain is opt-in, cycles are bounded loops, and one data directory
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Follow-ups from the ADR-035 review. The experimental brain shipped enabled. The
  default flow's Telegram nodes named a `telegram` module that does not exist, so that branch was
  a silent pass-through. The audit (F1) asked for cycles to be rejected by default, but the
  default flow's tool loop (LLM → router → tool → LLM) is itself a cycle, and the runner re-queues
  nodes whose inputs change, bounded by `max_node_loops`: cycles are the loop idiom. What was
  wrong is narrower: cycle breaking took the alphabetically first node, so a source-less run of
  the default flow ran the router, tool and chat output before the LLM. Each remote brain token
  check ran PBKDF2 against every actor and rewrote the ledger. `testclient`/`testserver` were
  trusted as loopback in production. Data files used three conventions (cwd-relative `data/`,
  `project_path("data")`, `state_path("data")`), so tests rewrote the checkout's real databases
  and the brain ledgers nested under `/app/data/data` in Docker.
- **Decision**:
  1. `modules/brain/module.json` ships `enabled: false`. `meta` stays enabled: saved meta-flows
     use its subflow node and the runner rejects nodes from disabled modules. Brain tests load
     the module through an autouse fixture that patches discovery, never the manifest.
  2. The default flow uses `messaging_bridge` `messaging_input` and `messaging_output`
     (`platform: auto`). Existing stored flows are not rewritten.
  3. Cycles stay allowed. Cycle breaking picks a remaining node fed by an already ordered node
     (the cycle entry), falling back to node ID order; `strict_cycle_mode` remains the opt-in
     rejection. The audit's default-rejection recommendation is declined.
  4. Generated brain tokens are `<key_id>.<secret>` with a random public key id; authentication
     verifies one record. Legacy and caller-chosen tokens (no key id) still use the scan, limited
     to records without a key id. `last_used` is rewritten at most once a minute. A hash of the
     token was rejected as the index: it would let a ledger reader pre-filter guesses of a weak
     caller-chosen token without PBKDF2.
  5. The test-client host names are trusted only when `NEUROCORE_TEST_MODE` is set (conftest).
  6. `core.paths.data_path()` is the data directory: `NEUROCORE_STATE_DIR` itself when set, else
     `<project>/data`. Every data file uses it, including the decoder key. `main.py` has pinned
     the working directory to the project since e13d2f0 and Docker sets the state dir to
     `/app/data`, so no existing file moves, except brain ledgers and `hardware_id.txt`, which
     move from `/app/data/data/` back to `/app/data/` in Docker (they were nested there only
     since 060870d). The dashboard now counts chat sessions in the state directory.
  7. Protected test files are snapshotted and restored by absolute path, so a test that changes
     directory cannot redirect a restore.
- **Consequences**:
  - *Positive*: Experimental autonomy is off until chosen; messaging replies work in the default
    flow; source-less cyclic runs start at the cycle entry; brain auth costs one hash; no host name
    string is trusted outside tests; two concurrent full test runs leave every manifest, state file
    and data file byte-identical.
  - *Trade-offs*: Installs that used the brain must enable it; a Docker install that ran 060870d
    keeps any brain ledgers it wrote under `./data/data/` until moved by hand; existing actor
    tokens keep the scan path until rotated.

### ADR-037: Imports and legacy state follow the validated store and canonical data directory
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Follow-up to ADR-036's "one data directory". Four paths still bypassed it: the
  dashboard's recent sessions read a cwd-relative `chat_sessions.json`; marketplace flow import
  read and replaced a cwd-relative `ai_flows.json` directly, so an imported flow stayed invisible
  until restart and skipped validation; the autonomous agent derived its flow store from the
  source tree; and builds between 060870d and this change nested `hardware_id.txt` under
  `<state dir>/data`, so adopting `data_path()` would silently mint a new hardware id and orphan
  marketplace ownership claims.
- **Decision**:
  1. `FlowManager.add_flow()` and `FlowManager.validate_flow()` are the import entry points. A
     package is validated in full before any flow is added, unknown imported fields are kept, and
     the in-memory store as well as the file are updated under the manager lock.
  2. Marketplace flow import and the marketplace Library tab use that store; the dashboard reads
     sessions through `_dashboard_paths()`.
  3. `get_hardware_id()` copies an existing legacy nested id (at least 16 characters) to
     `data_path("hardware_id.txt")` before generating a new one.
  4. The autonomous agent's `flows_store_path` follows `state_path("ai_flows.json")`.
- **Consequences**:
  - *Positive*: An imported flow is usable immediately, malformed packages change nothing, and a
    deployment that briefly nested the hardware id keeps its ownership claims.
  - *Trade-offs*: Marketplace flow packages that fail validation now return 400 instead of being
    written unvalidated; the legacy hardware id file is copied, not moved, so both files remain
    until the operator removes the nested one.

### ADR-038: Node triggers run through the real runner; approvals are identified by the actor
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Review of the pushed runtime changes found that the `POST /ai-flow/{id}/run-node/{node}`
  endpoint referenced `FlowRunner` without importing it, so its background task raised `NameError`
  while the response reported "Node triggered". `FlowEvolution.mutate` and the structured-output
  retry loop also carried unreachable duplicate statements left by an earlier edit. The README
  still told operators to send `approved_by` in request bodies after ADR-031/actor auth moved
  approval identity to the authenticated request actor. The dev dependency pin excluded pytest 9,
  although the project is verified on pytest 9.1.1.
- **Decision**:
  1. The run-node background task imports `FlowRunner` at call time and a regression test executes
     the captured task against a fake runner.
  2. Unreachable duplicate code is removed from `core/flow_evolution.py` and
     `core/structured_output.py`.
  3. README approval examples name the required role; the approver recorded in ledgers is the
     authenticated actor, never a body field.
  4. `pytest>=8.0.0,<10` in the dev extra and `requirements-dev.txt`, so the declared range covers
     the version the suite is verified against.
- **Consequences**:
  - *Positive*: Manual node triggers actually execute; the documented approval flow matches the
    routes; declarations and the verified environment agree.
  - *Trade-offs*: None for runtime behaviour; CI still installs the newest 9.x from the range.

### ADR-039: Kernel error budget, recovery and dwell
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The kernel raised risk from `blocked_actions`, a lifetime counter that only a
  manual reset cleared, and had no per-subsystem error signal. A transient provider or flow
  outage could therefore not accumulate pressure, while a single recovered sample could return
  the brain to NORMAL even though the cause was still present.
- **Decision**: Keep two bounded windows: blocked actions and per-component errors, both pruned
  after 3,600 s. A failed flow or evaluation records one component error; a later success cancels
  one entry. Risk adds at most 20 points from each window. Leaving NORMAL starts a 60-second
  dwell that only a kill switch or an authenticated reset bypasses. Lifetime counters remain for
  audit but never drive risk.
- **Consequences**: A recovered subsystem stops holding the brain down without a manual reset;
  a single clean snapshot cannot flap the mode back. The kernel accepts an injectable clock so the
  behaviour is deterministic in tests.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (evidence and
  call-site review), PRINCIPLE-007 (bounded, no stub) and PRINCIPLE-014 (failing-first tests).

### ADR-040: Deterministic guardrails for untrusted text
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The tool sandbox protects code execution, but user text, tool output and model
  output had no conversation-level screening: a context-switch injection or a leaked system
  prompt or credential could enter a session, a document or memory unchecked.
- **Decision**: Add `modules/brain/guardrails.py`, a local, deterministic scanner (regex and
  string checks only; no network, no LLM, no embeddings). It flags context separators and
  instruction overrides on input, credential-shaped strings on every surface, and verbatim
  long system-prompt echoes on output; it normalises zero-width characters and caps scans at
  20,000 characters. `brain_guardrail_mode` selects `observe` (default; count and report) or
  `enforce` (block injected chat input, replace a leaked response); secrets are redacted in both
  modes. Chat input/output use it, and `GET /brain/guardrails/status` plus
  `POST /brain/guardrails/scan` expose it without side effects.
- **Consequences**: A deployment upgrades safely in observe mode and can tighten to enforce
  without a code change. Heuristic rules can produce false positives, which stay visible in the
  counters and can be allowlisted per rule.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (no invented
  dependencies), PRINCIPLE-007 (bounded scope) and PRINCIPLE-014 (failing-first tests).

### ADR-041: Goal-governed self-modification
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Improvement candidates and configuration changes could be promoted with no link
  to a user goal; quotas, a perfect baseline and an approver did not make a change purposeful.
- **Decision**: `modules/brain/goal_link.py` resolves a link before any ledger entry: an active
  `pending`/`in_progress` goal from the shared memory store, or `goal_kind="self_repair"` with a
  non-empty reason. Improvement candidates store `goal_id`, `goal_kind` and a bounded title, are
  revalidated at promotion, and mark the goal in progress once promoted. Configuration changes
  use the same resolver and record it in the change ledger. `require_goal_link` defaults to true
  in `modules/brain/module.json`; direct engine construction keeps it false for embedding and
  tests, while `BrainService` enforces it.
- **Consequences**: Every operator-facing change names why it exists, and a goal completed while
  a candidate waits blocks the promotion. The API gains `goal_id`/`goal_kind`/`description`
  fields; missing links return 400 before anything is written.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-000 (purposeful change),
  PRINCIPLE-007 (plan and impact) and PRINCIPLE-014 (failing-first tests).
