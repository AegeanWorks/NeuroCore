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

### ADR-042: Per-run execution budget
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: A structurally valid flow could still spend without bound: node re-execution was
  capped but LLM calls, tokens and tool calls were only limited per call, and nested flows had
  no shared allowance. A retry loop could hammer a provider inside one run.
- **Decision**: `core/execution_budget.py` creates one `ExecutionBudget` per top-level run with
  limits for nodes, LLM calls, tokens, tool calls and wall time, published through a context
  variable so nested and bridged runs share it. Flow metadata may lower, never raise, a limit.
  `FlowRunner` spends one node per executor, the LLM node reserves a call before the request and
  charges reported `total_tokens`, and the tool dispatcher reserves one call per execution.
  Exhaustion raises `BudgetExceeded` with `Why:`/`Fix:` and is reported as a normal failure.
- **Consequences**: A malicious or accidental flow stops at a predictable point with a precise
  message. The limits are deployment settings (`budget_max_*`); a flow that legitimately needs
  more can raise the deployment cap or split itself.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-007 (bounded change),
  PRINCIPLE-010 (resource optimisation) and PRINCIPLE-014 (failing-first tests).

### ADR-043: Deterministic pre-flight flow simulation
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Flow validation checked structure only; nothing reported external effects, cycle
  envelopes or whether one pass could fit the new execution budget. Improvement candidates were
  evaluated without a dry structural analysis.
- **Decision**: `core/flow_simulation.py` analyses a flow offline: dangling connections, unknown
  node types when a catalogue is supplied, isolated nodes, strongly connected components,
  external-effect classification (LLM, write, subflow) and a cost envelope against the run
  budget. One pass that cannot fit is blocking; a cycle that may repeat into a cap is a warning,
  because the runtime budget stops it. `GET /ai-flow/{id}/simulate` exposes the report, and the
  improvement engine stores it on each candidate and rejects a blocking one before the ledger
  grows.
- **Consequences**: Editors and operators see the same deterministic report as the improvement
  gate. The simulation never executes node code and cannot replace evaluation: it is a structural
  pre-flight, not a behavioural proof.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (read before write),
  PRINCIPLE-007 (no stub) and PRINCIPLE-014 (failing-first tests).

### ADR-044: Versioned skill documents with auditable rollback
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Skills are prompt material edited in place through the HTTP API and import path.
  Overwriting the only copy removed the evidence of what the model was told before, and there was
  no way to diff, restore or audit a change.
- **Decision**: `modules/skills/versions.py` stores each version's exact content under its
  SHA-256 in a per-skill directory, with an index holding version number, digest, parent, author,
  note and active pointer. History is bounded at 50 versions. Create, import and a
  material content change record a version; identical content does not. `SkillBackend.rollback`
  reads an old version and writes it through `update_skill`, so restoring is recorded as a new
  version. Routes expose list, read and rollback.
- **Consequences**: Every prompt edit is reversible and attributable, and storage stays bounded.
  The content file remains the runtime source of truth, so nothing else in the app needs to
  change; a corrupt index degrades to empty history and is rebuilt on the next write.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (atomic generated
  files), PRINCIPLE-016 (knowledge integrity) and PRINCIPLE-014 (failing-first tests).

### ADR-045: Agent-loop repetition guard
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: `max_iterations` and `max_node_loops` bound a run but cannot see that the model is
  repeating the same tool call with new identifiers or alternating between two actions. Such a
  loop burns tokens without producing progress and was only caught by the iteration cap.
- **Decision**: `modules/agent_loop/loop_guard.py` normalizes tool arguments (drops volatile ID
  keys, masks long hex, sorts, caps at 500 chars) and watches a bounded history. Three identical
  signatures, an A-B-A-B pattern, or the same tool four times with different arguments produce a
  decision: the first is a warning injected as a system message, the second aborts the loop. The
  standard agent loop returns `loop_error`, surfaced as `agent_loop_error` and `loop_detected`.
  The loop also spends from the execution budget for each LLM and tool call.
- **Consequences**: Repetition fails fast with a reason in the trace rather than at the iteration
  cap. Legitimate retries with different arguments are unaffected; a genuine retry after an error
  is allowed unless it repeats identically three times.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (resource use) and
  PRINCIPLE-014 (failing-first tests).

### ADR-046: Unified self-modification budget
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Improvement and configuration each kept a daily quota, but learning writes were
  unbounded and no single view answered how much the system changed itself today.
- **Decision**: `modules/brain/self_budget.py` keeps one persisted ledger of daily counts for
  candidate, evaluation, promotion, configuration change, prompt activation and learning write,
  reset at UTC midnight. Reservations happen before work and are released when the work definitely
  failed; exhaustion raises a 429 with `Why:`/`Fix:`. The brain service reserves for improvement,
  evaluation/promotion, configuration and both explicit and automatic learning writes;
  `GET /brain/budget/status` reports counts, caps and remaining units. Caps default to 50/50/10/
  20/20/500 and can be set in `self_budget_caps`.
- **Consequences**: One operator-visible ceiling spans every self-modification path, and an
  automatic outcome flood can no longer crowd out deliberate work. The per-engine quotas remain as
  the finer control; the unified ledger is the hard outer bound.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-006 (state discipline),
  PRINCIPLE-010 (resource use) and PRINCIPLE-014 (failing-first tests).

### ADR-047: Failure consolidation into avoidance knowledge
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Consolidation promoted only repeated successes. Failures stayed as individual
  experiences, so recall could recommend the same failing approach and nothing recorded "do not
  retry this blindly".
- **Decision**: `LearningLoop.consolidate` also groups training failures by action and normalized
  signature. At `min_failures` (default 2) it writes one bounded `[avoid]` entry to memory with
  confidence 0.3 and the error class, capped at `max_avoidances` (200) with orphan rows pruned.
  Recurrences of the same error class and failure count are a no-op, so consolidation is
  idempotent; a later success does not erase the warning. `recall()` and
  `POST /brain/learn/suggest` return skills and avoidances together.
- **Consequences**: Recurring failures become visible, reviewable knowledge instead of noise, and
  the memory store stays bounded. Avoidances advise verification; they never block a tool call.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded state) and
  PRINCIPLE-014 (failing-first tests).

### ADR-048: Memory evidence links and review-gated contradiction proposals
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Memories had confidence values but no relationships, so the system could not answer
  what supported or contradicted a belief. `find_conflicts` used one blocking LLM call per memory
  pair with no cap, no persistence and no review step; its side effects were invisible to the UI.
- **Decision**: Add a `memory_links` table (source, target, relation, evidence, status, creator,
  timestamp; unique per triple) with `supports`, `contradicts`, `derived_from` and `supersedes`.
  Confidence is evidence-based: `(supports + 1) / (supports + contradictions + 2)` over accepted
  links targeting a memory; accepting a proposal recomputes it in the same transaction. The
  contradiction scan is deterministic: same subject, opposite negation polarity, and at least
  0.8 Jaccard overlap after removing negation words; it creates `proposed` links and never edits
  confidence. The old LLM scanner is removed; `/memory/conflicts` now returns candidates and
  `/memory/contradictions/scan` persists proposals.
- **Consequences**: Contradiction review is bounded, offline, reproducible and attributable.
  Recall quality is lower than an LLM judge's; a future optional LLM pass may rank proposals but
  must not replace the deterministic gate or apply changes automatically.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (no invented
  dependencies), PRINCIPLE-013 (transactional state) and PRINCIPLE-014 (failing-first tests).

### ADR-049: Allowlisted research gateway with provenance
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Research data could be fetched through a generic URL tool with no provenance
  record, and the reference implementation for this idea fell back to scraping search engines
  with spoofed user agents. Results that reach memory or a knowledge base must be citable and
  the fetch surface must be bounded.
- **Decision**: `core/research_gateway.py` exposes three fixed adapters (Wikipedia, arXiv,
  PubMed). Every start URL and redirect hop must use HTTPS, an allowlisted host and a public
  resolved address; responses are limited to 2 MB and 10 seconds, cached for 24 hours, and
  returned with source, retrieval time, licence, source URL and content hash. `persist` stores
  items as research articles with provenance in notes. The route maps policy violations to 400
  and upstream failures to 502. There is no generic web source and no user-agent spoofing.
- **Consequences**: Research ingestion is offline-testable, attributable and cannot be pointed at
  an internal service. DNS can still change between validation and connection (a rebinding
  window), which is documented rather than hidden; a future hardened build could pin the
  resolved address. Source outages serve a stale cached result with an explicit warning.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (no invented
  dependencies), PRINCIPLE-010 (bounded resources) and PRINCIPLE-019 (no credential-bearing
  network surface).

### ADR-050: Review corrections to the loop guard, guardrails and kernel pressure
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: A post-push review found the new controls misbehaving in production-shaped cases:
  the loop guard aborted multi-record work (four calls to one tool with different arguments), it
  stripped domain `id` fields, guardrails turned an ordinary model-field declaration into
  `[REDACTED]` while missing ordinary key/value secrets, one failing subsystem could never reach
  the kernel degrade threshold, and `FlowRunner.run(timeout=0)` silently gained a 600-second
  deadline.
- **Decision**:
  1. The loop guard detects only identical signatures and A-B-A-B oscillation; it removes
     transport identifiers (`request_id`, `trace_id`, `session_id`, ...), never a domain `id`.
  2. Credential-shaped text is flagged on input and never rewritten; model output is redacted in
     both modes. The generic assignment rule requires a 12-character value and ignores common code
     prefixes.
  3. Kernel component pressure is the worst single component's share of the error window and can
     contribute up to 40 risk, so ten errors in one subsystem degrade the brain on their own.
  4. `timeout=0` keeps its documented meaning (no asyncio deadline); the execution budget still
     enforces `max_seconds`. Budget charges for LLM and tool calls move into the shared helpers, so
     every agent executor is covered.
- **Consequences**: Normal multi-record work and code discussions are no longer blocked or
  corrupted, a single failing subsystem can change the mode, and timeout semantics are stable.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (read before write),
  PRINCIPLE-010 (resources) and PRINCIPLE-014 (failing-first tests).

### ADR-051: Evidence integrity and bounded research persistence
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Accepted links inserted directly left `memories.confidence` stale, deleting a link
  or an evidence memory left the old value, and soft-deleted evidence still counted. Research
  `persist=True` was ignored on a cache hit, duplicated articles on repeat fetches, and the cache
  grew without a bound while DNS resolution blocked the event loop.
- **Decision**:
  1. One `_recompute_confidence` helper persists `(s+1)/(s+c+2)` on link insert (accepted),
     acceptance, link deletion and hard deletion of an evidence memory. Counts only include links
     between active memories; deleting a memory removes its links and recomputes affected targets.
  2. `persist=True` is honoured on fresh cache hits, articles are deduplicated by URL, and the cache
     is capped at 500 entries (oldest evicted). DNS validation runs in a worker thread.
- **Consequences**: Stored confidence matches the evidence after every mutation and never counts a
  deleted source. Repeated research persistence is idempotent, the cache is bounded, and the event
  loop does not stall on DNS.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (transactional state)
  and PRINCIPLE-014 (failing-first tests).

### ADR-052: Manifest catalogue and configuration validation
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The simulation route read `node_types`, but manifests publish `provides_nodes`, so
  the unknown-node check never ran. Dangling bridges were ignored. Skill version ids could escape
  their root through `..`, and a malformed `self_budget_caps` value crashed service construction.
- **Decision**:
  1. `node_type_catalogue()` reads `provides_nodes` (with `node_types` as a fallback); the route
     uses it, and a missing bridge endpoint is a blocking `dangling_bridge` finding.
  2. Skill ids must match `[A-Za-z0-9_-]{1,128}` in both the version store and the content path
     helper; anything else is rejected before a path is built.
  3. `self_budget_caps` must be an object; non-integer or non-positive caps fall back to the
     documented defaults with a warning.
- **Consequences**: Simulation detects an uninstalled node type and a broken bridge, path traversal
  is rejected at the boundary, and a bad configuration value degrades instead of blocking startup.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (no invented
  dependencies), PRINCIPLE-011 (safe defaults) and PRINCIPLE-014 (failing-first tests).

### ADR-053: Single-node catalogue, one tool charge and cache hardening
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The second review of the review-fix commit found three regressions. The new
  catalogue read only `provides_nodes`, but `llm_module` and `system_prompt` declare
  `is_flow_node: true` without that list, so the default flow was reported as blocking with two
  unknown nodes. `GoalPursuitExecutor` charged a tool call and then delegated to
  `HybridAgentExecutor`, which charged again. Research cache eviction assumed every stored value
  was an object, so one hand-edited entry could crash a write.
- **Decision**:
  1. `node_type_catalogue` adds `(module_id, module_id)` for every `is_flow_node` manifest and
     keeps reading `provides_nodes`/`node_types`. A regression test covers a single-node module.
  2. The goal-pursuit executor charges only its locally handled tools (`AskUser`,
     `DecomposeGoal`); normal tools are charged once by the parent hybrid executor.
  3. Cache eviction removes non-object entries before sorting, and the cache remains capped.
  4. The shared LLM usage helper sits below the imports, and the RLM tool executor keeps its
     docstring before its budget charge.
- **Consequences**: The default flow simulates as runnable, a goal-pursuit tool call costs one
  unit, and a corrupt cache file degrades instead of breaking a search.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (verify before a
  change), PRINCIPLE-010 (resource accounting) and PRINCIPLE-014 (failing-first tests).

### ADR-054: Closing the remaining review gaps
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The second review left six hardening gaps: the repetition guard ran only in the
  standard agent loop, guardrails covered only chat, the research cache used a process-local lock,
  an invalid legacy skill id could break listing, disabled modules still counted as installed in
  simulation, and merged (archived) memories still counted as evidence.
- **Decision**:
  1. The repetition guard now runs in the RLM and hybrid loops too (goal pursuit inherits the
     hybrid loop); an abort records `loop_error` in `repl_state`/`final_state` and surfaces as
     `agent_loop_error` and `loop_detected`.
  2. Knowledge-base chunks are scanned before indexing (enforce blocks the document, observe logs
     and continues); tool results are scanned for credential shapes and redacted before entering a
     conversation or flow output.
  3. The research cache takes an OS file lock in addition to its thread lock, so workers sharing a
     state directory cannot lose entries.
  4. Invalid legacy skill ids are skipped by listing and treated as missing by lookups; reports,
     updates and deletes return None/False instead of raising.
  5. Simulation only treats enabled manifests as installed.
  6. Confidence counts only links between active, unarchived memories; merging recomputes the
     targets of archived sources, and the merge log write happens after releasing the
     non-reentrant lock (a pre-existing deadlock).
- **Consequences**: All agent modes share loop protection, untrusted documents and tool output are
  screened, multi-worker caches stay consistent, legacy bad data no longer breaks the skills list,
  and merged evidence cannot silently inflate confidence.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (resource accounting),
  PRINCIPLE-013 (state integrity) and PRINCIPLE-014 (failing-first tests).

### ADR-055: Link-aware recall promotes supporting evidence
- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The evidence graph was write-only from the recall path: `search` and
  `keyword_search` ranked by vector/text score and ignored accepted `supports`/`derived_from`
  links, so a fact could be retrieved without the corroborating memory next to it. Tif's
  spreading-activation idea pointed at the same gap, but its implementation is coupled to its
  store and graph.
- **Decision**: Add `link_boost` (default `0`; the memory node uses `recall_link_boost`, default
  `0.5`) to both recall paths. `_expand_with_links` takes the top five seed results, follows one
  hop of accepted `supports` or `derived_from` links, and scores a neighbour
  `seed_score * link_confidence * link_boost`. Only active, unarchived, unexpired neighbours
  count; a neighbour already present keeps the higher score, records a `via_links` provenance
  entry, and never duplicates. Results are capped at the requested limit; `0` is a pure no-op.
- **Consequences**: Corroborating memories surface with the hit that justifies them, with an
  auditable link in each result. The boost is bounded and local, cannot cross a contradiction, and
  costs one indexed query per recall. It changes ranking only, never stored confidence.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded work) and
  PRINCIPLE-014 (failing-first tests).

### ADR-056: Asymmetric kernel risk release
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: The kernel used the raw risk score for both escalation and release. Because the
  one-hour windows decay slowly already, a single sample could also drop the mode as soon as the
  raw score crossed a threshold, and the reported score could oscillate with one bad provider.
- **Decision**: Keep raw risk for escalation, but gate de-escalation with a persisted
  `smoothed_risk`: `smoothed = 0.1 * raw + 0.9 * smoothed` when risk falls, while a rise applies
  immediately. `smoothed_at` is persisted; a gap longer than `ERROR_WINDOW_SECONDS` discards the
  stale release value. Kernel reset clears both values. State is written after snapshot,
  evaluation and drift observations so the release survives a restart.
- **Consequences**: SAFE is never delayed by smoothing, while recovery needs several healthy
  snapshots plus the existing dwell. The reported `risk_score` stays raw and the released value is
  visible as `smoothed_risk` for operators.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (state integrity) and
  PRINCIPLE-014 (failing-first tests).

### ADR-057: Read-only goal review
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: Goals carry description, priority, context, deadline and timestamps, but the brain
  offered no way to find overdue, stale, vague or duplicated objectives before linking a change to
  them. Tif's review idea was useful, but automatic pruning would remove operator intent.
- **Decision**: `POST /brain/goals/review` reads up to 500 goals from the shared memory store and
  returns a deterministic review of `pending`/`in_progress` goals: overdue by deadline, stale by
  `updated_at` (default 14 days), low clarity, near-duplicate descriptions (token Jaccard, default
  0.75), and a clarity score built from description length, context, deadline and priority. The
  route is operator-only and never mutates a goal; thresholds are request parameters, and malformed
  values return 400.
- **Consequences**: Operators can fix goal hygiene before using a goal to justify a change, with no
  hidden deletion or re-ranking. The score is intentionally simple and explainable, not an LLM
  judgement.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded work) and
  PRINCIPLE-014 (failing-first tests).

### ADR-058: Bounded metric-trend repair proposals
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: The brain records pass rate and provider errors but never notices a sustained
  regression across cycles. The archived Tif report proposed a trend detector that emits a
  `self_repair` improvement candidate.
- **Decision**: Add `modules/brain/trends.py`, a locked bounded ledger of the last 100 samples. It
  compares the latest 20-sample window against the previous one and reports a fall of at least 0.10
  in pass rate or a rise of at least 0.10 in provider-error rate. At most one finding is emitted per
  day (six-hour per-metric cooldown), persisted for dedupe. In `suggest` mode the cycle converts a
  finding into a `repair_metric` proposal carrying `goal_kind: "self_repair"`, the reason and the
  evidence; it deliberately does not create a flow candidate, because a metric regression has no
  flow operation list and must not bypass evaluation. `GET /brain/trends` exposes the window.
- **Consequences**: Sustained regressions become reviewable without autonomous edits or LLM calls.
  The detector is cheap, bounded and reconstructible from the persisted samples. An operator still
  decides whether to open a self-repair candidate.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded work),
  PRINCIPLE-013 (atomic state) and PRINCIPLE-014 (failing-first tests).

### ADR-059: Server-side confirmation for irreversible routes
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: Settings reset, knowledge-base delete-all, memory wipe and chat-session delete were
  guarded only by a browser `confirm()` dialog or an HTMX `hx-confirm`. Any HTTP client could call
  them directly, so the UI guard was not an authorization check.
- **Decision**: Add `core/confirmations.py::require_confirmation`, which compares a normalized
  phrase and raises HTTP 400 with `Why:`/`Fix:` guidance. Each destructive route requires the exact
  phrase in a `confirm` query parameter: `reset all settings`, `delete all documents`,
  `wipe all memories`, `delete this session`, `wipe the reasoning book`. The existing dialogs stay
  as UX, and every in-app caller now sends the phrase.
- **Consequences**: A script or a stray request can no longer wipe state by accident or bypass the
  dialog. The phrase is not a secret and does not replace authentication; it makes intent explicit
  and forces callers to name the operation. No route accepts the phrase from the body, so a JSON
  caller must send the query parameter deliberately.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-011 (clear remedies) and
  PRINCIPLE-014 (failing-first tests).

### ADR-060: Opt-in per-node retry policy and node timeout
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: A transient provider timeout or connection drop failed the whole node, and often
  the run, with no recovery path; node configuration had no time ceiling either. Retrying blindly
  is unsafe because tool and messaging nodes can have external effects.
- **Decision**: Add `core/node_retry.py` with a bounded `RetryPolicy` read from node config
  (`retry_count` 0-4, `retry_delay` base 0.5s, `retry_max_delay` 10s, `retry_backoff` 2.0) and
  `timeout_seconds` (0 disables). Retries are off by default. Only transient failures are
  retried: `TimeoutError`/`asyncio.TimeoutError`, `ConnectionError`, `LLMTimeoutError`,
  `ToolTimeoutError`, and `LLMHTTPError` with 408, 425, 429 or 5xx. `ValueError`, `TypeError`,
  `KeyError` and cancellation are never retried. Backoff is `base * factor^(n-1)` capped at
  `retry_max_delay`. `FlowRunner._run_node` is the single execution path for regular and bridge
  nodes, so both share the policy; each attempt spends one node from the execution budget and the
  optional per-node timeout wraps the whole attempt, making a timeout retryable. Exhausted retries
  and timeouts surface as the usual structured node error.
- **Consequences**: Operators can recover from transient faults per node without changing global
  behaviour, while a misconfigured node cannot spin: at most 5 attempts, bounded delay, and the
  run budget still applies. Backoff is clamped to the remaining run wall time, and a typed
  provider/tool failure that runs out of attempts becomes the usual structured node error.
  Default behaviour is unchanged for every existing flow.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded work and
  timeouts) and PRINCIPLE-014 (failing-first tests).

### ADR-061: Node latency stall detection
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: The flow engine records per-run duration and a generic `node.latency` metric when a
  tracing decorator is used, but there was no per-node-type history, no p99, and no way to notice a
  single stalled attempt while a run was still hung.
- **Decision**: Add `core/node_stall.py`, a thread-safe monitor with a bounded history (200 samples,
  at most 512 module/type keys). After at least 20 prior samples, an attempt is a stall when its
  duration exceeds both a 2,000 ms floor and three times the prior p99. Each attempt records
  `node.latency` and `node.last_duration_ms`; a stall also increments `node.stall`, emits a
  structured warning and is kept in a bounded 50-entry findings list. `FlowRunner._run_node` times
  every attempt, including a timed-out or failed one, in a `finally` block. `GET /debug/node-latency`
  (debug mode) returns thresholds, per-node percentiles and recent stalls.
- **Consequences**: Operators can see whether a node type is degrading and which flow/node stalled,
  with thresholds that adapt to that node's own baseline instead of a global constant. Detection is
  observational: it never pauses or aborts a run, so a slow-but-useful node still completes. The
  monitor is process-local and bounded, and a metrics failure cannot break execution.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded work) and
  PRINCIPLE-014 (failing-first tests).

### ADR-062: Post-delivery hardening of the operator controls
- **Date**: 2026-10-26
- **Status**: Accepted
- **Context**: A review of the three preceding deliveries found four gaps. Typed
  `LLMTimeoutError`/`ToolTimeoutError` failures escaped `FlowRunner` as unhandled exceptions after
  retries were exhausted because they are not `RuntimeError`/`OSError` subclasses. Retry backoff
  ignored the run's remaining wall time. `stale_days=-1` silently became `0`, and text sorting
  misordered epoch deadlines. Malformed persisted kernel fields and malformed snapshot inputs could
  raise inside `BrainKernel.observe`, while `TrendMonitor.record` could divide by zero or record a
  bogus sample when provider fields were malformed.
- **Decision**: Catch `NeuroCoreError` in the runner's structured node-error path and keep log
  messages bounded. Clamp each retry delay to `ExecutionBudget.remaining("seconds")`. Validate
  goal-review numbers (`stale_days >= 0`, finite thresholds) and return 400 with `Why:`/`Fix:`;
  sort review results by parsed epoch. Parse kernel restore and snapshot inputs defensively, keeping
  the previous value for malformed rate inputs. Guard trend snapshot shapes and clock conversion.
- **Consequences**: Exhausted retries always produce the normal node error dict instead of a 500,
  backoff can never sleep past the run deadline, and operator reviews fail loudly on bad input
  rather than silently reinterpreting it. Malformed state now degrades to a safe default instead of
  breaking the brain loop.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (empirical hardening),
  PRINCIPLE-011 (clear errors) and PRINCIPLE-014 (failing-first tests).

## Licensing migration (v0.1.0-agpl)

- New versions use the Aegean License 1.1 (AL-1.1); versions up to and including `v0.1.0-agpl` remain under GNU AGPL-3.0.
- Rights holder: AegeanWorks; commercial use under ACL-1.0.
- Counsel review of the 1.1 texts remains pending.

## License notice cleanup (2026-10-26)

- The license-history notes were removed from README.md and COMMERCIAL.md: the project is offered only under the Aegean License 1.1 (AL-1.1), with commercial use under ACL-1.0.

### ADR-063: SSRF-checked fetches connect to the vetted address
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: `core/research_gateway.py` and the skill URL importer resolved a hostname, rejected
  any private, loopback, link-local or reserved answer, and then handed the hostname to httpx,
  which resolved it again on connect. A DNS answer that changes between the two lookups (DNS
  rebinding) therefore reached an address the check had rejected (IDEA-063). Commit e0cf494 made
  the importer check every resolved address but kept the second lookup.
- **Decision**: `core/pinned_http.py` builds a pinned request: the URL on the vetted IP (IPv6
  bracketed, zone dropped, port kept), a `Host` header with the hostname, and the httpcore
  `sni_hostname` extension for HTTPS so TLS verifies the certificate against the name.
  `_vetted_address` in the gateway returns the first public answer and `_safe_get` pins every
  redirect hop to its own vetted address; `_validate_url` keeps its contract. The importer pins to
  the first address it vetted, through a short-lived `httpx.Client`.
- **Consequences**: The connection goes to exactly the address that passed the check. A live fetch
  from Wikipedia succeeded through the pinned path, and the same IP with the name `example.com`
  failed certificate verification, so TLS checking is intact. Only the first vetted address is
  tried; a host whose first answer is down is not retried on the next one.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (the request goes where
  the check looked) and PRINCIPLE-019 (no internal service is reachable through a fetch).

### ADR-064: Outbound tool calls are drafts until the user approves them
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: `SendEmail` was enabled by default and mailed any address the model named; the only
  safeguard was its tool description. Incoming email (email bridge), `FetchURL` and knowledge-base
  documents reach the same agent, so an injected instruction could send any content anywhere
  (IDEA-032). Library tools run from four dispatchers: the tool dispatcher node and three agent
  loop executors.
- **Decision**: `modules/tools/outbound.py` names the outbound tools (`SendEmail`). Every dispatcher
  calls `stage_if_outbound` before running a library tool; an outbound call is saved to
  `data/outbound_drafts.json` (ledger lock, atomic write, at most 200 pending) and the model is
  told it was NOT sent. `GET /tools/drafts` lists drafts; `POST /tools/drafts/{id}/send` requires
  `confirm=send this draft` (the ADR-059 helper), claims the draft (`pending` to `sending`) under the
  lock before running the tool code, and records `sent` or `failed`; `POST .../discard` drops one.
  An unreadable draft store fails closed: nothing is drafted or sent. A test counts sandbox
  executions against stage calls in both dispatcher files, so a new dispatcher cannot skip the gate.
- **Consequences**: No email leaves without one explicit user action, and a draft is sent at most
  once even when approved twice. Agent-driven email now needs that action. Replies through the
  flow's `email_output` node are unchanged; a new outbound tool must be added to `OUTBOUND_TOOLS`.
  There is no UI panel yet; the routes are JSON.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-019 (no exfiltration path
  from untrusted input), PRINCIPLE-013 (claimed under a lock, written atomically) and PRINCIPLE-011
  R6 (every refusal carries Why/Fix).

### ADR-065: Memory and goal writes follow the guardrail policy
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: Guardrails scanned chat input, knowledge-base chunks and tool output, but nothing
  screened text bound for long-term memory or goals. A memory is recalled in every later session
  and the active goal is re-injected as a system message on each turn, so injected text saved once
  persisted indefinitely (IDEA-334).
- **Decision**: Guardrails gain a `memory` surface; `BLOCKING_SURFACES` (`input`, `memory`) are the
  surfaces where enforce mode refuses an injection pattern. `screen_memory_text` in
  `modules/memory/backend.py` scans `add_entry`, `update_entry`, `create_goal` and `update_goal`
  text before the write lock. In enforce mode `add_entry` returns `-1`, its existing "rejected"
  answer that the arbiter and learning loop already handle; the others raise the new
  `MemoryContentBlocked`, which the memory routes return as 400 with Why/Fix. Observe mode stores
  the text, logs the rules matched and counts them under `memory:` in the guardrail status.
- **Consequences**: The persistence path for prompt injection follows the same policy as every
  other surface. With the default `observe` mode nothing is refused, only counted; an operator who
  wants refusals sets `brain_guardrail_mode` to `enforce`. Text already in memory is not rescanned.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-019 (untrusted text does
  not become trusted state) and PRINCIPLE-011 R6 (a refusal carries Why/Fix).

### ADR-066: Untrusted text reaches the model inside a random-id fence
- **Date**: 2026-10-29
- **Status**: Accepted
- **Context**: Tool output (a fetched page, an incoming email, a search result) and retrieved
  knowledge-base chunks entered prompts as plain text, so an instruction inside them read exactly
  like the operator's, and nothing marked where the untrusted text began or ended (IDEA-030).
- **Decision**: `core/untrusted.py` provides `wrap_untrusted`: a one-line note that the block is
  data from a named source, then `<untrusted_content_<16 hex>>` with a fresh `secrets` id per block;
  tag-shaped text in the payload is neutralized and the source label is restricted to safe
  characters. `LLMBridge.chat_completion` and `chat_completion_stream` send a copy of the messages
  with every `role: tool` message fenced (`fence_tool_messages`, never twice), so all four tool
  dispatchers are covered at the one exit. The knowledge-base node fences each chunk when it builds
  `knowledge_context`. A first attempt fenced tool results where they are produced and broke stuck
  detection, which reads the raw text; fencing on the way out keeps the agent loop's own logic
  (stuck detection, truncation, traces) on the raw content.
- **Consequences**: Every untrusted block is visibly data to the model and cannot be closed by its
  own content. Prompts grow by about 120 characters per tool result or chunk. Memory recall and
  the system prompt are not fenced: memory writes are screened instead (ADR-065). Fencing is a
  signal to the model, not a guarantee; the guardrail scans remain the enforcement.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-019 (untrusted input stays
  distinguishable) and PRINCIPLE-003 (the change sits at the single exit every model call uses).

### ADR-067: The brain can act autonomously and be talked to from its page
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: The operator asked for a Brain section to start the brain and interact with it, with
  chat plus structured control, and for the brain to act autonomously. The `/brain/ui` dashboard
  (ADR-032) could start, stop and inspect the brain but offered no conversation, the navbar hid it
  while the opt-in module (ADR-036) was off, and `set_mode` refused anything beyond
  observe/suggest. ADR-028 had already built the kernel, ADR-041 the goal link and ADR-046 the
  daily self-budget as the rails autonomy would need.
- **Decision**:
  1. A third mode, `autonomous`. Its cycles generate proposals as `suggest` does and then, outside
     the state lock, act on pending proposals that have an entry in `AUTONOMOUS_ACTIONS`
     (`modules/brain/autonomy.py`): record a baseline (only through `promote_baseline`, which
     refuses anything below a perfect run), review a failing evaluation, consolidate learning,
     check recall. Each action needs `kernel.can_run_autonomy()` and one unit of its self-budget
     kind; at most `max_autonomous_actions_per_cycle` (default 2) run per cycle; every attempt is
     journaled in `state.actions` (newest first, 50 kept) with actor, status and result. SAFE
     kernel still forces `observe`. No tool, flow or outbound action is reachable; outbound tools
     stay drafts (ADR-064).
  2. `modules/brain/conversation.py`: `POST /brain/chat` sends the message with a bounded view of
     the live state and asks for JSON `{reply, commands}`; at most three commands, only from
     `COMMANDS` (the autonomous actions plus `run_cycle`, `run_evaluation`, `set_goal`). In
     autonomous mode they run as `brain:chat` and are journaled; otherwise they return as
     suggestions that `POST /brain/commands` runs for the requesting actor. History is kept in
     `data/brain_chat.json` (100 messages, ledger lock, atomic write). Goal text passes the memory
     guardrail (ADR-065).
  3. `/brain/ui` gains a mode switch, a chat panel with Run buttons for suggestions and a journal;
     the navbar always shows Brain, and while the module is off the link enables it and opens the
     page, so opting in stays one deliberate click.
- **Consequences**: The brain can now maintain its own evaluation baseline and learning without a
  person, bounded per cycle and per day; everything it does is visible in its journal. A model that
  answers with anything but the allow-listed commands can only talk. Proposals that need a person
  (provider repair, flow activation, memory seeding) still wait. The chat needs the configured LLM;
  without one it returns 502 with Why/Fix.
  `tests/test_module_defaults.py::test_disabled_brain_is_neither_routed_nor_linked` changed at
  the operator's request: a disabled brain is still not routed, but the navbar now shows it
  through a link that enables the module instead of hiding it (ADR-036's "not linked" part).
  Starting the brain on a real install exposed an older bug: before the first evaluation the
  self-model reports `last_pass_rate: None`, and `DriftMonitor._metrics` called `float(None)`,
  so every cycle and `/brain/state` failed with 500. It now reads a missing pass rate as 1.0.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (journal and history
  are written atomically under a lock), PRINCIPLE-019 (no path from chat to tools or outbound
  actions) and PRINCIPLE-011 R6 (every refusal carries Why/Fix).

### ADR-068: The brain resumes the operator's mode and running choice after a restart
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: `brain_state.json` persisted the mode and a running flag, but `BrainService.__init__`
  overwrote the mode with the module config and forced `running` to False, so every server restart
  quietly dropped an autonomous brain back to observe and left it stopped. Separately, the Brain
  page listed proposals with no way to act on one: the allow-listed kinds had an action the API
  could run, and the rest needed a person somewhere else.
- **Decision**:
  1. `BrainState.operator_mode` records the mode a person last chose; `set_mode` writes it. On
     load, the operator mode outranks the config default. A SAFE kernel still forces `observe` and
     records that as the effective choice, so clearing the kill switch does not silently re-arm
     autonomy; the operator chooses again.
  2. The persisted `running` flag is the resume request. `BrainService` exposes it as
     `resume_requested`; the router startup starts the brain when it is set or when `auto_start`
     is configured. `stop()` clears it; the new `shutdown_keep_resume()` preserves whatever the
     current choice is, and the router uses it on shutdown, so an explicit Stop is not undone by
     closing the server.
  3. Every proposal carries `action` (the allow-listed command to run, else null) and `link` (the
     page where a person can act: Settings for provider repair, AI Flow for flow and pass-rate
     work, Memory for seeding). The Brain page renders a Run button for the former and an Open
     link for the latter.
- **Consequences**: A brain that was running at shutdown (or crashed while running) starts again
  at the next server start without a click; one the operator stopped stays stopped. Mode changes
  now survive restarts even while the brain is idle. A state file written before this ADR has no
  `operator_mode`, so it keeps the config mode once and records the operator's next choice.
  `tests/test_brain_autonomy.py` covers the restart round-trip, an explicit stop, and SAFE forcing
  observe; `tests/test_brain_ui.py` covers the Run/Open controls.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (state is still
  written atomically under the ledger lock) and PRINCIPLE-010 (startup resumes only the operator's
  own choice, once).

### ADR-069: Tool calls are classified by tier and unknown tools fail closed
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-060 (IDEAS.md, known bug): the agent loop read `risk` and `reversible` from the
  model's own step JSON, defaulted a missing value to low/True, and only added a prompt warning.
  An unlisted tool, or a model that omitted the fields, therefore ran unguarded; argument text
  could not be trusted to describe the real effect either.
- **Decision**:
  1. `modules/tools/tiers.py` classifies a *structured* call from its tool name and arguments.
     `tools.json` now gives every shipped tool a `tier` and a `tier_reason`. A missing entry, an
     unknown tier, or a tier without a reason is HIGH. Deterministic argument rules may raise a
     tier but never lower it; `FetchURL` is raised for non-http(s) and private-address URLs.
  2. `gate_tool_call` runs before every dispatcher: the tools node plus all three agent-loop
     executors. LOW passes, MEDIUM passes with a log line that names the tool and reason but never
     an argument value, and HIGH is staged in `data/tool_approvals.json` instead of executing.
     The model receives the approval id and is told nothing ran; it cannot supply the approval.
  3. `GET /tools/approvals` lists held calls. `POST /tools/approvals/{id}/run?confirm=run this tool`
     claims and runs one, and `/reject` discards it. The claim happens under the ledger lock, so an
     approval runs at most once; a crash during a run leaves it `running`, never retried blindly.
     The approval ledger is bounded at 200 pending, written atomically, and refuses a new call when
     full.
- **Consequences**: Unregistered library tools (for example the `calculator.py` sandbox fixture)
  are held rather than executed. Tests that used invented tool names to exercise execution errors
  now use a registered MEDIUM tool (`Weather`) with the same fake code, because the invented name
  is supposed to fail closed. `SendEmail` stays LOW on purpose: it is already a draft until the
  user sends it (ADR-064), so it must not collect a second approval. A held call is not a failure
  in the trace; the request was accepted, and the journal says so.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-019 (untrusted input cannot
  choose its own risk), PRINCIPLE-013 (bounded atomic approval state) and PRINCIPLE-011 R6 (the
  refusal carries Why/Fix and a next step).

### ADR-070: Every agent loop verifies the pinned system prompt each iteration
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-631 (approved backlog): compaction and truncation rewrite the model-facing
  message list, and IDEA-370 showed a real case where truncation dropped the persona system
  message while keeping the goal. Nothing checked that the governing system messages survived a
  rewrite, and the three executors (standard, RLM, hybrid) could drift apart.
- **Decision**:
  1. `modules/agent_loop/prompt_digest.py` snapshots the system messages present when a loop
     starts, hashes their contents (JSON-encoded, SHA-256), and re-verifies that they are still the
     leading system messages before each model call.
  2. A mismatch restores the pinned prefix at the front, keeps the conversation messages, and sets
     `integrity_violation` on that iteration's trace entry. It never raises, so a suspect message
     list cannot abort an otherwise valid episode. A compaction summary added *after* the pinned
     prefix is legitimate and does not trip the check; a summary inserted before it does.
  3. The standard, RLM and hybrid loops all use the one helper; goal pursuit inherits hybrid. In
     the RLM loop the ephemeral `REPL state:` system message is appended per call and is not part
     of the pinned prefix.
- **Consequences**: A dropped or rewritten system message is visible in `agent_loop_trace` and
  repaired before the next model call. On a real mismatch the helper drops non-pinned system
  messages, including a summary, because it cannot tell a tampered summary from a legitimate one;
  the pinned prefix and the conversation are kept. The check is O(number of system messages) and
  hashes only their content.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-014 (failing-first tests
  for the mutation, the honest compaction and each loop) and PRINCIPLE-003 (one shared helper
  instead of three copies).

### ADR-071: The chat shell hosts the brain rail; the flow stays the conversation engine
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: The chat module owns the richer UI (sessions, SSE streaming, markdown, thinking
  traces) while the brain owns the control plane (kernel, health, cycles, proposals) in a separate
  page with a second, flowless conversation. Making the brain usable beside the conversation
  required a first step that did not fork the UI or replace the executor.
- **Decision**:
  1. Chat remains the single conversation surface, and `FlowRunner` remains its executor.
  2. `chat_gui.html` renders `brain_rail.html` whenever the brain manifest exists. With the
     module enabled the rail shows mode, kernel, health, cycles and proposals, and calls
     `/brain/start|stop|cycle|mode`; with it disabled the rail offers **Enable brain** through
     `POST /modules/brain/enable`.
  3. The rail only observes and controls the brain. It never blocks, rewrites or reroutes a chat
     message; a brain failure cannot take the chat down.
  4. The brain stays opt-in (`"enabled": false`), and chat works unchanged while it is off.
  5. A later slice unifies the session, not the engine: the flow stays the executor, and the
     brain adds per-turn context, outcome capture and explicit command routing.
- **Consequences**: One screen carries both the conversation and the brain state; no route or
  module moved, so saved flows and `/brain/ui` keep working. Remote browsers still cannot navigate
  `/brain/*` without an actor token, so the rail reports the refusal instead of exposing controls.
  The chat module owns the include, which couples its template to the brain template by name but
  not to the brain Python package.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-009 (a component at a
  boundary, not a merge of packages) and PRINCIPLE-014 (render tests for both rail states).

### ADR-072: A /brain chat turn shares the chat session and stream, not the flow engine
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: The brain page had a second conversation (`brain_chat.json`) with its own history,
  while chat sessions and streaming lived in the chat module. Keeping those apart meant the same
  user had two transcripts and the Brain UI could not use the chat shell. Replacing `FlowRunner`
  with the brain conversation for ordinary messages would lose tools, memory and agent loops.
- **Decision**:
  1. A message whose text starts with `/brain` is answered by `conversation.answer()` against the
     brain's live state; it does not require an active flow. The prefix is stripped before the model
     sees the question.
  2. The turn is persisted in the ordinary chat session (`source="brain"`, `commands=[...]`) and
     streamed through the existing `/chat/stream/{session_id}` SSE queue. Ordinary chat turns keep
     using `FlowRunner`.
  3. `conversation.answer()` is pure: it builds the state context, calls the model, executes
     allow-listed commands in autonomous mode, and returns `reply`, `commands`, `mode` and `usage`.
     `converse()` remains the API wrapper that appends to `brain_chat.json`.
  4. Suggested commands render as buttons in static history and in the streamed reply; both call
     the operator-gated `POST /chat/sessions/{id}/brain/commands`, which executes through the same
     allow-list, marks the stored suggestion `done`/`failed`, and refuses a command already run at
     that message index. Failures are persisted as an assistant error and do not break the stream.
  5. The brain remains opt-in. With it off, `/brain` returns a short enable hint, makes no model
     call, and ordinary chat is untouched.
- **Consequences**: One transcript and one stream for both kinds of turn; the brain sees prior chat
  turns, and the chat sees brain replies. The flow outcome listener still captures ordinary flow
  turns for learning while the brain is running. `brain_chat.json` remains only for API clients
  that call `/brain/chat` directly, so the two histories do not merge automatically.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-009 (a pure `answer()` at the
  brain boundary), PRINCIPLE-012 (bounded turns and context) and PRINCIPLE-014 (tests for success,
  refusal, empty prefix and model failure).

### ADR-073: The brain cockpit points to chat instead of hosting a second conversation
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: ADR-072 moved `/brain` turns into the chat session, but `/brain/ui` still had its own
  chat form and transcript backed by `brain_chat.json`. Two visible conversations could disagree,
  the cockpit's transcript went stale, and a user had to know which page to type into.
- **Decision**:
  1. `brain.html` no longer renders the chat panel, form or its client functions. The cockpit keeps
     kernel/mode controls, the action journal, proposals, reactions and the evaluation scorecard.
  2. A Conversation card links to `/chat` and `/ai-flow`, explains the `/brain` prefix and notes
     that `GET/POST /brain/chat` remains an API for clients that manage their own transcript.
  3. The cockpit's proposal buttons still call `/brain/commands`; chat suggestions call the
     session-aware `/chat/sessions/{id}/brain/commands`.
  4. No route is removed. `/brain/chat`, `conversation.converse()` and `brain_chat.json` stay for
     API compatibility and the existing API tests.
- **Consequences**: One visible conversation and one place to start it. The cockpit is a control
  surface, not a second chat, so its page no longer needs the chat history or its render code.
  API clients can still use the standalone brain transcript, but it does not merge with chat
  sessions automatically.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-009 (the cockpit and the
  conversation are separate components with one boundary) and PRINCIPLE-014 (the UI test now
  asserts the chat panel is gone and the chat link is present).

### ADR-074: Ordinary flow turns can carry a bounded brain-state block
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: With the brain enabled, ordinary chat turns still ran the active flow without any
  awareness of the brain's mode, kernel, health or pending proposals. The brain saw the flow only
  after it finished, through the outcome listener, so a running flow could not adapt to the state
  the rail showed.
- **Decision**:
  1. When the brain module is enabled and `inject_flow_context` is true (default in
     `modules/brain/module.json`), chat attaches `_brain_context` to the flow's initial data.
  2. The value is `conversation.state_context(await service.snapshot())`, already bounded to
     `CONTEXT_CHARS`; a snapshot failure is logged and the flow runs without the block.
  3. `SystemPromptExecutor` appends it at medium priority with an explicit
     "internal state, not user input" label. A flow that does not use the System Prompt node
     ignores the field.
  4. The block is a snapshot of the current turn, not a live feed. Brain-off chats are unchanged,
     and setting `inject_flow_context` to false restores the previous prompt exactly.
- **Consequences**: Flow prompts grow by at most the bounded state block and the System Prompt
  node's token budget can drop it like any medium-priority context. The block is data, not an
  instruction, and never includes chat history. Brain state is read on the request path of the
  background flow task, so a slow snapshot delays the flow start, not the HTTP response.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-002 (the state is attached
  at the lifecycle point where the flow is composed), PRINCIPLE-012 (bounded payload, static label
  first) and PRINCIPLE-014 (tests cover the node context and the chat hand-off).

### ADR-075: Model limits are declared data and every clamp is reported
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-588: a flow author could send `max_tokens` beyond a model's ceiling or assemble
  a prompt larger than its context window; the provider then failed or silently truncated. The
  ceiling lived in commit messages and caller assumptions, not in one place.
- **Decision**:
  1. `core/model_registry.py` owns `ModelLimits`, `ClampField`, `ClampResult` and `ModelRegistry`.
     Resolution is exact id, then a declared alias, then refusal only when `strict_model_limits`
     is true.
  2. `settings.json` supplies `model_limits` as data and `strict_model_limits` as a boolean. An
     empty or absent `model_limits` disables clamping, so existing deployments are unchanged.
  3. `LLMBridge` clamps output tokens before the chat and streaming payloads, refuses an
     over-limit context rather than truncating it, and attaches `_clamp` to a clamped chat
     response. The streaming path logs the clamp because chunks have no response envelope.
  4. Zero or negative ceilings and duplicate/shadowed aliases are refused at load. The context
     estimate is `json.dumps(messages, tools) / 4`, a guard rather than a billing count.
- **Consequences**: The default `local-model` entry allows 4096 output and 32768 context tokens;
  the default 2048-token request is unchanged. A caller that needs the exact requested length can
  use the registry's `require_exact=True` and receive `ModelLimitExceeded` instead of a silent
  clamp. Unknown models pass through unless strict mode is enabled.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one source of truth
  for a model ceiling), PRINCIPLE-011 (Why/Fix refusals) and PRINCIPLE-014 (failing-first tests for
  clamp, alias, refusal, strict mode and the LLM hand-off).

### ADR-076: Provider failures are typed and Retry-After is preserved
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-891: `LLMBridge` raised a generic `LLMHTTPError` for every provider failure and
  `node_retry` applied blind exponential backoff. A 429's `Retry-After` hint, an auth failure and a
  context-window overflow all looked alike, so the retry layer could wait too little, too long, or
  retry a request that could never succeed.
- **Decision**:
  1. `core/llm_errors.py` owns the mapping: 401/403 → `LLMAuthError`, 402 → `LLMBudgetError`,
     429 → `LLMRateLimitError` (with parsed seconds or HTTP-date `Retry-After`), a context marker
     on 400/413/422 → `LLMContextWindowError`, and everything else → `LLMHTTPError`.
  2. `is_retryable()` returns false for auth/context/budget, true for timeouts, connection errors
     and 408/425/429/500/502/503/504.
  3. `LLMBridge` raises the typed error when `raise_errors=True`; otherwise its error dict keeps
     the old `error: http_error` shape and adds `category` plus `retry_after` when present.
  4. `node_retry` uses `is_retryable()` and raises the backoff to `max(backoff, min(max_delay,
     retry_after))`, so a server hint is honoured but the run cannot be slept past its cap.
- **Consequences**: Auth, context and budget failures now fail fast with a type the UI/router can
  distinguish; a 429 waits for the provider's hint within the node's `retry_max_delay`. The legacy
  error-dict shape and the existing status-based retry set are preserved. Unknown provider errors
  remain retryable once under the existing bounded policy.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one mapping instead of
  per-call status checks), PRINCIPLE-010 (the retry wait is bounded) and PRINCIPLE-014
  (failing-first tests for each status class and the delay override).

### ADR-077: Guardrail stability is tested with generated evasion variants
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-625: guardrail tests used hand-authored payloads, so a rule could pass while
  being defeated by case folding, zero-width characters, percent-encoding or HTML entities. The
  real guardrails already normalize NFKC, case and invisible controls, but nothing asserted that
  behaviour mechanically.
- **Decision**:
  1. `tests/support/evasion.py` declares six deterministic generators (`case`, `whitespace`,
     `zero_width`, `interleave`, `percent`, `html_entity`) and `assert_guard_stable()`.
  2. The matrix reports an absolute miss on the canonical payload separately from a variant miss,
     and records an explicitly skipped variant instead of silently excluding it.
  3. The real guardrail is asserted stable for the variants it claims: `case` and `zero_width`.
     The other transformations are opted out because the guard does not claim to decode them.
  4. `tests/fixtures/payloads/canonical.txt` keeps the payload set reviewable; the generators are
     pure functions, so coverage grows without adding hand-written cases.
- **Consequences**: This is test-only; runtime guardrails are unchanged. A naive case-sensitive
  guard now fails with the exact variant named, and an exemption is visible in the matrix rather
  than absent from the suite.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-014 (failing-first
  coverage of an invariance) and PRINCIPLE-003 (one declared variant set instead of ad-hoc tests).

### ADR-078: A knowledge-base collection stamps its embedding space
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-591: a knowledge base can be re-indexed with a different embedding model. Without
  a space label, an old index silently returned meaningless neighbours after the switch and the
  retrieval looked "bad" rather than "incompatible". Dimension changes were caught by FAISS; a model
  change at the same dimension was not.
- **Decision**:
  1. `modules/knowledge_base/embedding_space.py` owns `EmbeddingSpace`
     `(model_id, revision, dimension, normalization)`, `EmbeddingSpaceMismatch`, `space_of()` and
     `require_compatible()`.
  2. One `embedding_space` row is the collection header; every chunk stores its own label. The first
     ingest stamps both; a later ingest, re-embed or query compares the current
     `embedding_model`/`embedding_revision` space against the header and refuses on the first
     differing field.
  3. A rebuild checks each labelled record; a labelled record from another space is refused by chunk
     id. Legacy rows without a label are backfilled from the header rather than losing an otherwise
     valid collection.
  4. `KnowledgeQueryExecutor` turns a mismatch into a bounded `knowledge_context` refusal instead of
     crashing the flow. `clear()` removes the header so a new collection can choose a new space.
- **Consequences**: A model swap now produces an explicit re-index instruction. Existing collections
  are migrated on the next rebuild by stamping their missing labels with the inferred header. An
  empty FAISS index created with the 768 fallback no longer drops the first real batch; it resizes
  to the actual vector length.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one identity instead of
  per-call dimension checks), PRINCIPLE-011 (the refusal names Why/Fix) and PRINCIPLE-014 (tests for
  matching, model, dimension, mixed-record and clear cases).

### ADR-079: Provider exchanges are recorded once and replayed with a named drift check
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-619: flow tests either hand-script a reply per model call or need a live
  endpoint. Hand-scripted replies drift from the prompts they claim to cover, and a live endpoint
  makes the suite non-deterministic. Nothing turned a changed prompt into a test failure.
- **Decision**:
  1. `core/cassette.py` owns `Exchange`, `Cassette`, `CassetteMiss`, `CassetteSchemaError` and
     `CassetteTemplateMismatch`. An exchange stores the request's SHA-256, a structural shape
     (endpoint, model, message roles, tool-schema digest, stream flag), the response, the record
     time and an optional template digest. Prompt text is never written.
  2. Replay consumes exchanges in recorded order. `strict` compares the whole body, `structural`
     compares the shape and `loose` compares only the endpoint. A miss raises `CassetteMiss` with
     the exchange position and the differing keys, never a generic error.
  3. `LLMBridge` gains trailing optional `cassette_mode` (`live`/`record`/`replay`),
     `cassette_path`, `cassette_match` and `template_digest`. `live` is the default, so every
     existing caller is unchanged. Record writes after each successful chat or completed stream;
     replay never opens an HTTP connection.
  4. Responses pass the shipped guardrail credential scrubber before storage, and `save()` writes
     through a same-directory temporary file, fsync and `os.replace`. `Cassette.load` refuses an
     unknown schema or mode and names both digests when the template changed.
- **Consequences**: A recorded flow replays without a model and without provider cost. Strict mode
  turns a prompt edit into a failure naming the changed body; structural mode survives a prompt
  edit and still catches a changed role sequence, model, tool schema or endpoint. Cassettes are
  reviewable fixtures and must be re-recorded deliberately after an accepted prompt change; a
  credential in a prompt is absent because the prompt is only digested, and a credential in a
  response is replaced by `[REDACTED]`. Recording and replaying a stream transpose the emitted
  chunk list, not the raw SSE bytes.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one recorded contract
  instead of ad-hoc fakes), PRINCIPLE-012 (replay costs no model tokens) and PRINCIPLE-014
  (failing-first tests for replay order, both drift modes, template mismatch, schema refusal and
  credential redaction).

### ADR-080: Tool authorization is an ordered, default-deny policy ahead of the tier gate
- **Date**: 2026-10-30
- **Status**: Accepted
- **Context**: IDEA-058: tool gating was a per-node binary `allowed_tools` list plus the tier and
  approval checks. There was no ordered, reviewable policy over tool names and arguments, so a
  denial could not name a rule and a path or host matcher had no hardened implementation.
- **Decision**:
  1. `modules/security/policy.py` owns `Rule`, `Decision`, `PolicyEngine` and `PolicyError`. A
     policy declares `schema: 1`, a name, a positive version and a non-empty ordered rule list.
     Unknown top-level keys, rule keys, matchers or decisions are refused at load rather than
     ignored, and the policy digest covers the validated document.
  2. Evaluation is first-match; no match returns `deny` with rule `default.no_match` and risk 100.
     Matchers are `tool_in` (exact), `path_prefix` (resolved containment), `host_in` (equal or
     dot-bounded suffix) and `identity_in` (exact). A malformed argument or matcher error is a
     non-match, never an exception.
  3. `policy_gate` reads `tool_policy_file` and `tool_policy_mode` (`off`/`observe`/`enforce`).
     No file, or `off`, preserves the previous behaviour; a configured policy starts in `observe`
     and logs the decision. `enforce` returns a named denial, stages a `require_approval` call in
     the existing approval ledger with tier `policy`, or lets an `allow` continue.
  4. `gate_tool_call` evaluates the policy before the tier gate. A policy can only restrict:
     `allow` means the policy has no objection, so a HIGH-tier tool is still staged. The default
     policy is therefore not a second authorization layer that could loosen the first.
- **Consequences**: An operator can review one JSON document for tool authorization, and a denial
  names the rule, policy version, risk and digest. The first rollout is observe-only, so a policy mistake
  is visible before it blocks work. `require_approval` reuses the at-most-once approval ledger
  instead of a parallel queue. The policy is read when a call is gated; a missing or malformed
  file in `enforce` mode fails closed, while `observe` logs and continues.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one decision contract
  instead of scattered conditionals), PRINCIPLE-014 (failing-first tests for every bypass and the
  tier precedence) and PRINCIPLE-019 (no argument values are logged in a denial).

### ADR-081: Reflection adds two opt-in epistemic lenses without changing its contract
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-629: the reflection node returned one generic satisfaction object and never
  read `knowledge_context`, so a sycophantic answer and an unsupported claim looked the same as a
  sound one. Flow routers consume `satisfied`, `reason` and `needs_improvement`, so replacing the
  contract would break the retry loop.
- **Decision**:
  1. `modules/reflection/lenses.py` owns `LensFinding`, `EpistemicCritique`, `LENS_RUBRICS`,
     `context_text`, `lens_enabled` and `build_lens_prompt`. The lenses are `sycophancy` and
     `ungrounded_extrapolation`; the second is offered only when a knowledge context exists.
  2. Both lenses are skipped when the run budget is at 0.8 pressure or above. The module config
     exposes `cognitive_lenses: []`, so a deployment that does nothing keeps the old path.
  3. When lenses are enabled, the node makes one `structured_completion(EpistemicCritique)` call
     with its existing bridge. The reflection object keeps the legacy keys and adds a bounded
     `findings` list (`lens_id`, `triggered`, `critique`), filtered to enabled lens ids.
  4. A structured failure logs and falls back to the legacy chat call. The knowledge context is
     labelled as the only citable source and capped at 4,000 characters; the prompt asks the model
     to set `satisfied=false` when a lens triggers.
- **Consequences**: A configured deployment gets a second, named critique for one extra LLM call;
  an unconfigured one is unchanged. Grounding is auditable because the context is now part of the
  reflection prompt. The self-judging weakness remains: a model prone to sycophancy may also fail
  to flag it, so `findings` are advisory and the prompt, not a second checker, drives the routing
  flag. `tests/fixtures/reflection_lenses.json` is the fixed sycophantic/ungrounded/sound set; the
  automated tests drive it through a deterministic stub because no provider is configured, and an
  operator can replay the same cases against a real model without changing the suite.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one schema for the
  legacy verdict and the finding list), PRINCIPLE-010 (the extra call is skipped under pressure)
  and PRINCIPLE-014 (failing-first tests for legacy keys, both lenses, context gating, the budget
  guard and the fallback).

### ADR-082: Skill text is admitted through the guardrails before it is stored
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-849 (narrowed): a skill is not executed, but its text is injected into the
  system prompt, and `create_skill`, `update_skill`, `import_from_file` and `import_from_url`
  stored content without a scan. An injected instruction or a hardcoded credential therefore
  reached every turn that enabled the skill.
- **Decision**:
  1. `skills` joins the guardrail blocking surfaces. In `enforce` mode an injection finding is
     blocking for skill text, exactly as it already is for input and memory.
  2. `modules/skills/admission.py` owns `SkillScan`, `SkillBlocked`, `content_digest`,
     `scan_skill` and `require_admissible`. The scan records the verdict (`safe`/`flagged`/
     `blocked`), the active mode, the content SHA-256, the findings and the scan time.
  3. The service scans `create_skill`, `update_skill` and `import_from_file` before any write;
     `import_from_url` is covered through `import_from_file`. Enforce raises a `ValueError`
     (HTTP 400) naming the rule ids with a Why/Fix; observe stores the skill with the flagged
     `scan` object in its metadata.
  4. An update re-scans only when the content digest changes; a metadata-only update and an
     identical-content update keep the original scan. Secret-shaped values are flagged but do not
     block, matching the existing guardrail contract.
- **Consequences**: An enforce-mode refusal happens before the file, metadata row or version is
  written, so a refused import leaves no partial skill. Observe mode makes the decision visible in
  the API and UI without breaking an existing import. The blueprint's full package scanner
  (per-file discovery, symlink containment, YAML rule policy, quarantine store, override
  endpoints) is deliberately not built by this slice; the approved decision was to run the
  guardrails on skill content at import. A regex rule remains evadable, so this is one admission
  layer, not a sandbox or a quality judgement.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one scan record
  instead of per-call checks), PRINCIPLE-011 (the refusal names Why and Fix) and PRINCIPLE-014
  (failing-first tests for create/update/file/URL, no partial write, digest no-op and secret
  flagging).

### ADR-083: Every messaging bridge denies unknown senders before any work
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-839 (narrowed): Telegram, Signal and WhatsApp ran every sender's message
  through the active flow, and Discord checked only an optional channel id. Anyone who found a
  bot could spend the owner's tools, memory and LLM quota. The approved decision is a per-bridge
  sender allowlist with default deny, not the full pairing/context-visibility blueprint.
- **Decision**:
  1. `modules/messaging_bridge/access.py` owns `AccessPolicy` and `AccessDecision`. The policy
     reads `<platform>_allowed_senders` and `<platform>_allowed_chats`; no sender list means every
     sender is denied. Both lists accept a JSON list or a comma-separated string.
  2. Each handler evaluates the policy before a command, session creation, media download or
     flow run. Telegram uses the new stable `from_id` field and falls back to `chat_id`; Discord
     uses the author id and channel; Signal and WhatsApp use the `from` id.
  3. Telegram seeds the sender list from `telegram_chat_id` and Discord seeds the chat list from
     `discord_channel_id`, so an existing single-user setup keeps working. Signal and WhatsApp
     have no fallback.
  4. A denial logs the platform and sender id (never the message text) and sends no reply, so a
     stranger cannot use the bot as a reply amplifier.
- **Consequences**: Upgrading is intentionally breaking for Discord members, Signal and WhatsApp
  until the operator lists their ids; Telegram's configured owner keeps working. The full
  blueprint's pairing codes, approval store, wildcard open mode, group mention policy,
  context-visibility filtering and WhatsApp signature verification are not built. A static
  allowlist is one trust boundary per gateway: it is not a tenant boundary, and an allowed sender
  can still be a compromised account.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one policy object
  instead of per-platform conditionals), PRINCIPLE-011 (the denied id is logged so the Fix is
  actionable) and PRINCIPLE-019 (message text is never logged in a denial).

### ADR-084: Memory rows carry a session scope and recall filters on it in SQL
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1109 was approved: recall filtered only source and deletion, and a memory
  written on one user's behalf could be injected into another user's prompt once the messaging
  bridge or more than one agent used the same store. The blueprint's session-scoped recall is
  adopted, not the full per-entity collection split.
- **Decision**:
  1. `modules/memory/scope.py` owns the scope contract: `user_id`, `agent_id`, `run_id`; ids are
     trimmed, bounded to 200 characters and empty ids are absent. `strict` matches only equal ids;
     `shared` additionally admits rows whose `user_id` is NULL for legacy data.
  2. `memories` gains nullable `user_id`, `agent_id` and `run_id` columns with an index on each.
     The migration only adds columns: existing rows stay NULL and are invisible to a scoped
     recall until an operator runs the explicit, idempotent `migrate_scope()` backfill to `local`.
  3. `add_entry`, `search`, `keyword_search`, `find_similar`, `browse`, `update_entry` and
     `delete_entry` accept an optional scope. Every present key becomes a parameterised equality
     predicate in the same statement, and the link expansion is scoped on the joined memory too,
     so a link cannot promote another scope's row.
  4. Recall and save read the scope from `input_data`: explicit ids first, then `sender`/
     `sender_id` for `user_id` and `session_id` for `run_id`. `memory_scope_required` (default
     false) refuses an unscoped recall or save with a `MemoryScopeRequired` Why/Fix message.
- **Consequences**: A scoped recall cannot return another user's row, and an exact duplicate is
  per scope rather than global. Existing flows keep working because an absent scope means no
  predicate, but a flow that supplies a scope only sees rows written with that scope (or NULL
  rows under `shared`). The memory browser and its update/delete routes remain a local admin
  surface and pass no scope; they must not be exposed to untrusted callers. The blueprint's
  `memory_scope_policy` is implemented as `strict|shared`, but no per-entity provider collections
  or household policy are built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one scope helper
  instead of per-call conditionals), PRINCIPLE-011 (the refusal gives Why and Fix) and
  PRINCIPLE-014 (failing-first isolation, refusal, migration and wrong-scope delete tests).

### ADR-085: Resuming an episode requires a matching input digest
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1110 was approved: an unfinished episode was adopted by any later run that
  omitted `episode_id`, even when the request, model or tool set had changed, and its checkpoints
  carried only a timestamp. The blueprint's thread-scoped checkpoint identity is adopted, not a
  new checkpoint store.
- **Decision**:
  1. `core/episode_identity.py` owns the projection and digest: volatile keys (ids, timestamps,
     nonce) are removed, then the normalised request, model and sorted tool names are hashed with
     SHA-256. The episode stores the digest and the projection so a mismatch can name what changed.
  2. `EpisodeState` gains `input_sha256`, `input_projection`, and identified checkpoints:
     `checkpoint_id` (time-ordered), `parent_checkpoint_id`, `source` (`start|loop|replan|complete`),
     `step` and the digest. `latest_checkpoint()` exposes the newest one.
  3. `resume_decision` is `resume`, `new` or `ask`: a match resumes, a mismatch starts a new
     episode, and a legacy episode with no digest asks. `resume_policy` is `strict` (default),
     `ask` or `any`, and `resume_any` overrides one call. A refused candidate is left `paused`.
  4. All four agent variants call `prepare_resume` before adopting an episode and pass the digest
     and projection to `create_episode`, so the same request resumes and a changed one does not.
- **Consequences**: A new task cannot silently adopt an unrelated unfinished episode, and a
  changed model or tool set requires an explicit `resume_any`. Volatile ids do not invalidate a
  digest, but any change to the request or plan material included in the projection does. Existing
  `current.json` files without the new fields load as legacy and ask instead of resuming. The
  blueprint's separate per-checkpoint write store and explicit fork API are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one digest helper
  across four loops), PRINCIPLE-011 (a mismatch logs the decision) and PRINCIPLE-014 (failing-first
  resume, legacy, volatile-key, checkpoint-order and pause tests).

### ADR-086: Tool calls are journaled in the episode for at-most-once execution
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1111 was approved: the episode checkpoint is written after a whole loop
  finishes, and nothing durable records that one side-effecting tool call already ran. A crash
  between the call and the checkpoint lets a resume send a message or write a file twice.
- **Decision**:
  1. `modules/agent_loop/tool_journal.py` writes `{call_id, name, args_sha256, state, attempts,
     result_sha256, result_preview, success, at}` to `EpisodeState.tool_journal`. A `pending` entry
     is persisted before dispatch and rewritten to `done` with a result digest and a 2,000-char
     preview afterwards, through the episode's atomic save.
  2. A `done` entry is replayed from its preview without invoking the tool. A `pending` entry is
     marked `orphaned` on the first resume; `tool_journal_policy` is `ask` (default: report and do
     not run), `skip` (report as skipped) or `rerun`, and `rerun` runs only when the manifest
     declares the tool `idempotent`.
  3. `tools.json` gains `idempotent: true` for the pure compute/read tools only; the tier registry
     exposes `is_tool_idempotent`. The journal is capped at 200 entries, dropping the oldest `done`
     entries first.
  4. All three tool dispatch sites in the agent loops go through `run_journaled_tool`, and every
     receive resets the active episode, manager and policy on the executor.
- **Consequences**: A crash cannot silently repeat a side-effecting call, and a pure call can be
  re-run under `rerun`. Every dispatcher can be resumed from its result preview, which is not
  byte-exact, and each call costs two extra episode writes while episode persistence is enabled.
  The journal is not a general transaction log: a tool that is neither declared pure nor
  journaled by its own driver still needs idempotency at its own boundary.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (the manifest is the
  single source of the idempotency fact), PRINCIPLE-011 (ask/skip name the Fix) and PRINCIPLE-014
  (failing-first replay, pending, rerun-refusal, pre-dispatch persistence, compaction and orphan
  tests).

### ADR-087: A flow run journals committed node outputs and can attach duplicates
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1152 was approved: `FlowRunner._run` kept `node_outputs` in memory, dropped
  every completed node when one failed, and replayed the whole DAG on restart, repeating LLM and
  tool calls. The approved scope is the run/node journal and the duplicate-attach path, behind a
  flag, not a new execution engine.
- **Decision**:
  1. `core/step_journal.py` owns `step_journal(run_id, node_id, input_digest, status,
     output_json, output_digest, effectful, started_at, completed_at)` and
     `run_registry(run_id, idempotency_key, status, result_json)`. WAL and
     `synchronous=FULL`; one transaction per node commit.
  2. `run_id` is `sha256("run", flow_id, idempotency_key)` when the caller supplies
     `_idempotency_key`, else `sha256("run", flow_id, canonical input digest)`. The canonical
     projection removes the same volatile keys as the episode guard (ADR-085).
  3. Before a node runs the runner looks up `(run_id, node_id, input_digest)`; a matching
     `committed` row supplies the output and the executor is not called. Otherwise the row is
     written as `started` before dispatch and `committed` after. A changed upstream output changes
     the downstream digest and forces re-execution; an output over 64 KB is `skipped`, so that
     node re-runs rather than replaying a partial value.
  4. An interrupted `started` row on a node whose manifest entry declares `effectful` (or whose
     flow node sets `effectful`) raises `IncompleteStep`; the caller opts in with
     `_resolve_incomplete`. Pure nodes restart without a flag because their output is a function
     of the recorded input.
  5. A second submission with the same idempotency key attaches: a completed run returns its
     stored result, and a running run returns a `duplicate` result instead of executing again.
  6. The whole path is off unless `step_journal_enabled` is true, so the existing runner is the
     rollback.
- **Consequences**: With the flag on, a retry or restart executes only the uncommitted suffix and
  a duplicate client key cannot fork a second run. The journal adds two small writes per node and
  a 64 KB ceiling per output; a very large node output is recomputed on resume. Deterministic
  ids mean two legitimate identical requests without an idempotency key share a run, which is the
  intended attach semantics but must be documented to callers. The blueprint's compressed spill
  files and per-node batch fsync tuning are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one digest contract
  shared with ADR-085), PRINCIPLE-011 (IncompleteStep names Why and Fix) and PRINCIPLE-014
  (failing-first resume, attach, refusal, invalidation and oversized-output tests).

### ADR-088: Completed episodes expire on two clocks and are quarantined, not deleted
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1153 was approved: `EpisodeState` had no completion or expiry stamp,
  `list_episodes` read every file without an age filter, and only a manual delete removed one, so
  completed episodes and any journal rows accumulated forever.
- **Decision**:
  1. `core/retention.py` owns `RetentionPolicy(completion_days=30, journal_days=7, max_episodes,
     stale_after_days)` and `compute_retention(completed_at, policy, has_journal)`, which returns
     both stamps and caps the journal expiry at the completion expiry.
  2. `EpisodeState` gains `completed_at`, `expires_at`, `journal_expires_at` and `generation`.
     `stamp_retention()` records them once, only for a `completed`/`failed` episode, so a live
     episode has no retention clock.
  3. `prune_episodes(now, policy, dry_run, page_size)` scans completed episodes in bounded pages.
     It skips anything not terminal, quarantines corrupt or future-dated files as
     `skipped_corrupt`, purges the journal when its shorter clock passes, and moves an expired
     episode to `episode_dir/quarantine/<id>-<ts>/` after writing a `tombstones.json` entry with
     the policy version. It never raises on one bad file.
  4. `SessionManager.prune_episodes` defaults to `dry_run=True` and calls the scanner under the
     sync lock; `purge_episode_journal` deletes the run's step-journal rows. A `create_episode`
     that reuses a tombstoned id starts at `generation + 1`.
- **Consequences**: Disk use is bounded without deleting a result a UI still needs, and a mistaken
  policy change is recoverable from quarantine. A paused episode is never swept, so a long human
  approval wait is safe. Retention is operator-triggered, not a background timer; the blueprint's
  heartbeat rows, `max_bytes` budget and automatic scheduling are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (quarantine and
  tombstone instead of unlink) and PRINCIPLE-014 (failing-first expiry, live, corrupt, dry-run,
  page-bound and generation tests).

### ADR-089: A selective memory filter scores SQL candidates instead of ranking the whole index
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1154 was approved: `MemoryStore.search` always called
  `faiss_index.search(..., ntotal)` and filtered afterwards, so a five-row page behind a selective
  source or scope predicate paid a full vector scan and a full Python sort. `type` and
  `(source, type)` were also unindexed.
- **Decision**:
  1. `modules/memory/filter_plan.py` owns `eligible_predicates`, a bounded `estimate_matches`
     (`LIMIT budget_rows + 1`), `choose_plan` and `plan_for_query`. The eligible set always
     includes `deleted = 0`, `parent_id IS NULL`, an embedding, the expiry window, the scope
     predicates and the source filter, in that parameter order.
  2. The planner prefilters only when the count is exact, within `budget_rows` (5,000) and
     `matches * dimension * 4` fits both `memory_filter_scan_budget_kb` (4,096) and
     `memory_prefilter_max_mb` (64). The prefilter scores the retrieved embeddings exactly in
     NumPy and the existing batched fetch still applies the same predicates.
  3. `memory_filter_plan_enabled` (default true) is the rollback to the full-index ranking.
     `idx_memories_source_type` and `idx_memories_type` are added for the new predicates.
  4. The planner's decision is logged at debug level with its match count and byte budget, so a
     latency change can be explained.
- **Consequences**: A selective recall no longer scans every vector and returns the same ids and
  scores within 1e-6 as the index path (covered by a parity test). A broad filter still ranks the
  index. Loading candidate embeddings is bounded by the row and byte budgets, so the prefilter
  cannot allocate unbounded memory. No new index format, collection or relevance model is added;
  the blueprint's `find_similar` over-fetch rework and per-query filter arguments are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (a bounded probe and
  scan budget) and PRINCIPLE-014 (failing-first planner, bounded count, parity and scope tests).

### ADR-090: A knowledge-base recall budget over-fetches and rescores exactly
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1155 was approved: `FaissDocumentStore.search` called
  `faiss_index.search(query, limit)` directly, never set `nprobe` despite the setting, and mapped
  the approximate scores straight into results, so a true neighbour the IVF probe missed could not
  be recovered by any later reranker.
- **Decision**:
  1. `modules/knowledge_base/recall.py` owns `RecallBudget(oversample, nprobe_fraction,
     max_candidates, rescore)` with validation, `candidate_k(limit, ntotal)` and `nprobe(nlist)`.
     `oversample` is capped at 16 so a bad value cannot degenerate into a full scan.
  2. For an IVF index the store sets `nprobe` from the list count, asks for
     `min(limit * oversample, max_candidates, ntotal)` candidates, then rescores those candidates
     against the stored full-precision vectors with the same cosine metric and truncates to
     `limit`. A flat index is exhaustive and keeps the plain `limit` query with no rescore.
  3. The embedding-space compatibility check still runs before the budget path, so rescoring can
     never compare vectors from different models.
  4. `kb_recall_budget` and `kb_recall_budget_enabled` are settings; a caller may also pass a
     `recall_budget` explicitly. The plain `limit` call remains the rollback.
- **Consequences**: A clustered IVF corpus no longer silently loses recall at the top-k, and the
  cost/recall trade-off is explicit and logged. Each budgeted query costs one extra SQL fetch of
  the candidate embeddings, bounded by `max_candidates`; the audit job and `exact=True` escape
  hatch from the blueprint are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded candidate
  fetch) and PRINCIPLE-014 (failing-first budget validation, IVF/ID-map detection, rescore
  promotion and flat-path tests).

### ADR-091: A tool manifest declares its primitives and is refused before dispatch
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1172 was approved: the dispatcher read only `enabled`, the tier gate and the
  outbound staging, then loaded `library/<name>.py` and ran an empty string when the file was
  missing. Several manifest entries had a name mismatch or lived in `rlm_library/`, so they
  returned success-shaped empty output instead of failing.
- **Decision**:
  1. `modules/tools/capabilities.py` defines the closed primitive set (`sandbox`,
     `library_source`, `rlm_library_source`, `outbound_staging`, `human_approval`, `network`,
     `inline_source`, `progress`, `cancel`), `ToolRequirement`, `CapabilityRefusal`,
     `parse_requirements` and `supported_primitives`.
  2. The supported set is computed from the live objects: the sandbox, the two library
     directories, outbound staging, the approval ledger and network policy. `inline_source`,
     `progress` and `cancel` are not advertised because no live path exists for them yet.
  3. `preflight(tool, entry, supported, dirs)` runs in the dispatcher before the tier gate and
     staging. An unknown requirement token, a declared-but-unsupported primitive, or an
     unresolvable source file is a refusal whose text names the tool, the missing primitive and a
     `Fix:`. An entry with no `requires` keeps the legacy behaviour but must still resolve a file.
  4. `resolve_source` prefers an explicit `source` block and otherwise looks in both library
     directories with a case-insensitive fallback, so `Calculator` finds `calculator.py` on a
     case-sensitive filesystem. The dispatcher executes the resolved path instead of a
     re-constructed name.
  5. `tools.json` entries carry `manifest_version`, `requires` and `source`; `GET
     /tools/capabilities` reports the supported set and any declared drift, and the save path
     writes the v2 fields.
- **Consequences**: A manifest written for a newer runtime cannot silently degrade, and a missing
  file is an error instead of a fake success. Capability is orthogonal to risk: a supported call
  can still be HIGH and held. Over-declaration can refuse a working tool, so `requires` is
  documented as a claim about the code, not about the environment. The blueprint's runtime-drift
  startup assertion and inline-source execution are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-011 (refusal names Why and
  Fix) and PRINCIPLE-014 (failing-first refusal, resolution and live-manifest tests).

### ADR-092: A tool call has a cancel signal, a deadline and a status receipt
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1173 was approved: a tool call had only the 30-second sandbox timeout, no
  call id, no cancel path and no progress. A timeout returned an error string and discarded any
  work the child had produced, so the model could not tell a partial run from a finished one.
- **Decision**:
  1. `modules/tools/calls.py` owns `ToolCallContext` (call id, cancel event, deadline, coalesced
     progress capped at 10/s and 20 items, settle status) and the bounded `CallRegistry`, which
     drops the oldest settled entries first and never evicts a live call.
  2. `ToolSandbox.execute` accepts an optional `cancel_event` and `deadline` and polls the child
     every 100 ms. A cancel or an expired deadline terminates the child, waits at most five
     seconds, and raises `ToolCallCancelled` or the existing timeout error.
  3. The dispatcher registers one context per call, passes it to the sandbox, settles it, and on
     cancel, timeout or error returns a receipt whose `status` is `cancelled`, `timeout` or
     `error`. `result` is set only for `ok`; partial output lives under its own key, and the
     rendered text begins with `CANCELLED`/`TIMED OUT`/`FAILED`.
  4. `GET /tools/calls`, `GET /tools/calls/{id}` and `POST /tools/calls/{id}/cancel` expose the
     registry; a settled id answers 409 with a Why/Fix message instead of pretending to cancel.
- **Consequences**: A wedged call can be stopped without waiting for the global timeout, and a
  partial run is visibly partial. A thread that cannot be killed and a child that ignores
  termination can still outlive the call; the receipt then reports the status it observed, not a
  guarantee that no side effect happened. Progress is currently only recorded when a caller
  pushes it, so most tools report an empty list; the blueprint's `partial_output` streaming from
  the child and the approval-runner context are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-010 (bounded polling and
  a capped registry) and PRINCIPLE-014 (failing-first registry, receipt, termination and
  dispatcher tests).

### ADR-093: The recalled memory block is budgeted, evicted whole and reported
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1174 was approved: recall clamped only the number of results and joined every
  text unbounded. The shared prompt trims by priority, so an oversized priority-0 memory block was
  dropped whole, while one that just fit could crowd out knowledge and reasoning.
- **Decision**:
  1. `modules/memory/budget.py` owns `MemoryRecallBudget(max_total_chars=4000,
     max_item_chars=800, min_items=1, dedupe=True)`, `BudgetReceipt(shown, omitted, chars,
     truncated, evicted_long)`, `fit` and `render`. Exhausted values are refused with Why/Fix.
  2. Results stay in rank order. Identical normalised text collapses only within the same
     `(user_id, agent_id, run_id)` scope, so two people's identical sentence stays two memories.
     A candidate over `max_item_chars` is evicted whole, except the top item which is truncated at
     a sentence boundary and marked `truncated`. A candidate that would exceed the total is
     evicted whole.
  3. `MemoryRecallExecutor` reads `recall_max_total_chars`/`recall_max_item_chars` from its node
     config (module defaults) and returns `_memory_budget` alongside `_memory_context`. The footer
     `[memory: N of M shown; K omitted for budget]` appears only when something was omitted, and
     an empty selection returns an empty context rather than a misleading block.
- **Consequences**: The block can no longer starve other prompt sections, and truncation is
  visible to the operator and the trace. Characters remain a proxy for tokens, so a CJK-heavy
  memory can still exceed a token budget; rank-based eviction can drop a long important memory in
  favour of short ones; and a very small budget makes recall look empty. The blueprint's
  token-based estimate and per-provider caps are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-012 (a bounded prompt
  payload) and PRINCIPLE-014 (failing-first budget, eviction, dedupe, empty and node tests).

### ADR-094: An edit records a receipt and undo is conditional on the head and hash
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1175 was approved: `update_entry` archived the previous row and logged an
  `edit` meta action, but the structured payload held only `new_id`, the old text survived only
  inside an English description, and no endpoint exposed history or an inverse.
- **Decision**:
  1. `modules/memory/history.py` owns `MemoryEditReceipt(edit_id, old_id, new_id, old_sha256,
     new_sha256, changed_fields, actor, created_at)`, `build_receipt`, `parse_receipt`,
     `read_edit_rows`, `history_entry` and `undo_problem`. The receipt is stored in the existing
     `meta_memories.new_value` of the existing `edit` action, so no schema change is needed.
  2. `GET /memory/{id}/history` returns at most 20 entries newest-first, each with
     `previous_text`, `new_text`, `changed_fields` and `truncated`. Each side is capped at 4,000
     characters; a missing archived row returns `previous_text: null` with
     `previous_reason: "archived row deleted"` rather than a guess.
  3. `POST /memory/edits/{edit_id}/undo?confirm=undo this edit` is allowed only while the edited
     row is still the current head (`deleted = 0 AND parent_id IS NULL`) and its text hash still
     matches `new_sha256`. It archives the head, inserts a new row from the previous values with
     the same scope, and logs an `undo` meta entry naming `edit_id`, `old_id`, `restored_id` and
     `supersedes`.
  4. A refusal is a 409 with a Why/Fix message: a later edit or delete, a tampered head, a legacy
     payload without `old_id`, or a deleted archived row all refuse instead of clobbering.
- **Consequences**: The previous value is queryable without parsing prose, and undo cannot
  silently overwrite a newer change. Undo creates a new id, so external references to the edited
  id become stale and callers must follow `restored_id`. The archived row remains a second copy
  of sensitive text, so history is as sensitive as memory itself; screen and redact it at the
  boundary. The blueprint's `edit_seq` column, redaction pass and multi-step undo are not built.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (conditional,
  non-destructive recovery) and PRINCIPLE-014 (failing-first receipt, history, undo, conflict and
  tamper tests).

### ADR-095: Evaluation checks are typed and weighted, with required checks authoritative
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1206 was approved: a check was a loose dict and `_evaluate_check` returned
  only `(passed, detail)`. A task passed only when every check passed, so a latency probe carried
  the same weight as a correctness assertion and dashboards had to match English detail text.
- **Decision**:
  1. `modules/brain/assertions.py` owns `CheckSpec(id, type, field, value, weight, metric,
     required, threshold, allow_threshold_override)`, `CheckVerdict(code, score, passed, reason,
     observed)`, `CheckCode`, `evaluate_check`, `aggregate` and `coerce_specs`.
  2. `CheckCode` is closed: `pass`, `value_mismatch`, `field_missing`, `unknown_type`,
     `timeout`, `exec_error`, `invalid_spec`. An unknown type scores 0 and never raises; a bad
     regex is `invalid_spec`.
  3. A `not-` prefix reuses the base handler and flips a decidable result; an unknown type can
     never be flipped into a pass.
  4. Aggregation sums `score * weight` over positive weights and stores each named metric
     separately. A zero-weight check is metric-only and never changes pass/fail. A required
     failure fails the task even under a low threshold, unless that check sets both `threshold`
     and `allow_threshold_override`.
  5. Specs are validated at construction: non-finite or negative weights, a threshold outside
     0..1, more than 100 checks, or all weights zero are refused with Why/Fix. Existing loose
     dicts adapt through `from_legacy`, and `EvalTask` gains an optional `threshold`.
- **Consequences**: A task can report partial quality while a critical check stays binding, and
  regression buckets are stable because they key on codes. Weights are a tuning knob, not a
  policy: a determined operator can still zero out a check, which is now visible in the spec.
  Old run records have no `score`, so score-based trend comparisons start from this version;
  `pass_rate` and `failed_tasks` are unchanged for existing callers.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-011 (validation messages
  carry Why and Fix) and PRINCIPLE-014 (failing-first weighting, override, typed-code and
  validation tests).

### ADR-096: An evaluation run is content-addressed and a changed suite is not a regression
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1207 was approved: a run stored a fixed suite name, `compare_results` checked
  only `flow_id`, and `promote_baseline` stored no task identity. Editing a check and re-running
  could therefore look like a clean improvement against a baseline that never ran those tasks.
- **Decision**:
  1. `modules/brain/suite_manifest.py` canonicalises each task over `id`, `input`, `checks`,
     `threshold` and `timeout_seconds` only, hashes it with SHA-256, and rolls the sorted
     `(id, digest)` pairs plus `MANIFEST_SCHEMA_VERSION` into a `suite_digest`. Descriptions and
     tags are excluded, and canonical JSON uses sorted keys with `allow_nan=False`.
  2. A run record carries `schema_version`, `suite_digest`, `task_digests` and a mean `score`.
  3. `compare_results` decides comparability before the pass-rate gate. Two equal digests are
     comparable; two different digests return `comparable: false`, an `incomparable_reason` and
     the `changed_tasks`, and are reported as neither a regression nor a pass.
  4. `promote_baseline` refuses to replace a baseline whose digest differs unless
     `accept_suite_change=True` **and** a non-empty `reason`; it records `supersedes_digest` and
     `accepted_reason`. `improvement` treats `comparable: false` as non-promotable and names why.
  5. Compatibility: a pair where **neither** side has a digest is compared as before, flagged
     `"legacy record without a suite digest"`. Only a mismatch or a half-migrated pair is
     incomparable. This keeps existing deployments working instead of blocking every promotion
     until re-promoted (the failure mode the source warned about).
- **Consequences**: An edited assertion can no longer masquerade as an improvement, and a
  legitimate suite update needs one explicit, reasoned acceptance. Because both-legacy pairs stay
  comparable, an old baseline and a new *legacy-shaped* run are still trusted; the protection
  starts with the first run that records a digest. Reordering tasks does not churn the digest, but
  any change to an expected value does.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (an explicit,
  auditable replacement instead of a silent overwrite) and PRINCIPLE-014 (failing-first ordering,
  canonicalisation, mismatch, legacy and acceptance tests).

### ADR-097: Browser actions address snapshot ids and verify an observable change
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1208 was approved: `click(selector)` returned `{"success": True}` whenever the
  click dispatched, and `type_text` never read the field back, so the agent could build a false
  success chain. Nothing bound an action to the page it was planned against.
- **Decision**:
  1. `modules/browser_automation/indexed.py` owns `ElementRef`, `ActionExpectation`,
     `ActionResult`, `build_snapshot`, `element_id_for`, the closed result-code set and
     `wait_for_expectation`. Element ids are a hash of generation, role, name and selector, so the
     same node keeps its id within a generation and never reuses one across generations.
  2. `BrowserService.snapshot(scope)` collects interactive elements, bounded to 200 elements and
     40,000 characters, and refuses with `element_budget_exceeded` instead of truncating mid-list.
     Navigation advances `_generation` and clears the id map.
  3. `click_element` and `type_element` resolve the id against the current generation; a
     resolution failure is `stale_element` and the page is not touched. `type_element` defaults
     to a `value_equals` expectation so a fill is only `done` when the field reads back.
  4. The post-condition poll is bounded to 2 s at 100 ms intervals. Only an observable change
     (URL, DOM version, value, checked, focus) returns `done`; otherwise the result is
     `unverified`. A file input or native select is `policy_refused` and is never retried.
  5. The router accepts `element_id`, `expect_kind`, `expect_value`, `timeout_ms` and a
     `snapshot` action; the legacy selector `click` and `type_text` remain for existing callers.
- **Consequences**: A no-op click is now visible, and a re-render is a retryable `stale_element`
  rather than a silent misclick. `unverified` is a warning, not a failure, because a download, a
  focus move or a no-op toggle legitimately changes nothing observable; such flows must pass an
  explicit expectation. The DOM version is a node count, so a change that keeps the count can
  still read as unchanged. Element `name` and `value` are untrusted page text and must be fenced
  by `core.untrusted` before entering a prompt.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (one typed result
  contract across actions) and PRINCIPLE-014 (failing-first done, unverified, stale, refused and
  postcondition tests).

### ADR-098: A page snapshot is measured against a budget and refused, not silently cut
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1209 was approved: `get_html` cut at 50,000 characters and `extract_text` at
  20,000 with a `"... [truncated]"` marker, and `screenshot` had no byte ceiling at all. A caller
  could not tell whether an element was absent or merely cut.
- **Decision**:
  1. `modules/browser_automation/snapshot.py` owns `SnapshotBudget(max_html_chars=50000,
     max_text_chars=20000, max_screenshot_bytes=1_500_000)`, `measure_html`, `measure_text`,
     `measure_screenshot` and `SnapshotRefusal(code, measured, budget, next)`.
  2. The default is a typed refusal: `ok: false` with `code` `html_too_large`,
     `text_too_large` or `screenshot_too_large`, the measured size, the budget and a `next` hint
     naming a narrower scope. Nothing is silently cut.
  3. `allow_truncate=True` opts into a cut that is reported: `truncated: true`,
     `omitted_chars`/`omitted_bytes` and a `resume` hint with the offset. Truncation is never the
     implicit behaviour.
  4. The browser router forwards `allow_truncate`; the legacy `... [truncated]` marker is gone,
     so a caller that ignored it now sees an explicit receipt.
- **Consequences**: The agent can ask for a narrower scope instead of guessing, and a missing
  element is unambiguous. A caller relying on the old always-succeeding `get_html` now receives
  `success: false` on a large page and must opt in or narrow; that is the intended behaviour
  change. The screenshot path still fetches the full image before measuring, so the byte ceiling
  bounds what is returned, not what is captured.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-012 (a bounded payload)
  and PRINCIPLE-014 (failing-first refusal, opt-in truncation and budget validation tests).

### ADR-099: Outcomes carry trace and span identity, sampled once at the root
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1237 was approved. `core/observability.py` already had a `TraceContext` with
  `trace_id`, spans and `parent_id`, but `core/outcomes.py` emitted a bare dict, so a flow or tool
  outcome could not be tied to the step that produced it, and there was no sampling.
- **Decision**:
  1. The existing `TraceContext` gains `sampling`/`sampled`, `sampling_decision(rate)` and
     `current_ids()`. `start_root_trace(name, sampling, trace_id)` records one decision at the
     root; children inherit it through the shared contextvar.
  2. `current_span_ids()` exposes `trace_id`/`span_id`/`parent_span_id` for stamping, returning
     empty values when nothing is traced.
  3. `core/outcomes.emit_outcome` copies the payload and fills any missing id from the active
     trace. It never mutates the caller's dict, never overrides an explicit value, and logs at
     debug level if tracing raises, so an outcome is never dropped by observability.
  4. This ADR does **not** add a second trace module: an earlier draft in `core/trace_context.py`
     duplicated `observability.TraceContext` and was removed before the gate.
- **Consequences**: Outcomes, and any caller of `current_span_ids`, can be joined to a trace and
  its parent chain, and sampling is decided once per trace. Untraced code sees byte-identical
  behaviour. Sampling is recorded but consumers (export/logging) must still honour `sampled`.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-009 (extend the existing
  trace role instead of duplicating it) and PRINCIPLE-014 (failing-first root/child, sampling,
  stamping and passthrough tests).

### ADR-100: Prompts are immutable versions addressed by digest and rollout label
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1238 was approved: prompt text lives inside node code and module config with
  no version, digest or label, so a rollout was an in-place edit and a rollback meant finding the
  old prose by hand.
- **Decision**:
  1. `modules/brain/prompt_registry.py` owns `PromptVersion(name, version, prompt, digest,
     labels, tags, config, commit_message, created_at)` and `PromptRegistry`.
  2. Publishing identical content (same text **and** config) is idempotent and returns the
     existing version; a change gets the next monotonic `version` and a new SHA-256 digest over
     canonical JSON.
  3. A rollout is `move_label(name, version, labels)`, recorded in a label history with the
     previous target and actor. `latest` is reserved and always resolves to the newest version.
  4. `rollback(name, label)` moves a label back to the version it pointed at before its most
     recent move, so a rollback is one auditable operation rather than an edit.
  5. `dump_state`/`load_state` round-trip the registry; `compare` reports versions and digests,
     never prose. Publishing an empty prompt or moving an unknown version/label is refused with
     Why/Fix.
- **Consequences**: Two runs are comparable by `(name, version)` or digest, and a rollout cannot
  silently change content. The registry is in-memory with explicit state export; persisting it to
  a ledger and wiring node prompts to `resolve()` is follow-up work, so existing prompts keep
  their current source until a module opts in. A digest covers config, so a temperature change is
  a new version, which is intentional.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-016 (an auditable,
  versioned source of truth) and PRINCIPLE-014 (failing-first idempotence, versioning,
  label-move, rollback, comparison and round-trip tests).

### ADR-101: A delegated task declares its capabilities, expected output and depth
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1242 was approved: a delegated sub-task was an implicit call with no bound on
  the delegatee's tools, no expected output shape, no declared context and no recursion limit.
- **Decision**:
  1. `core/delegation.py` owns `DelegationContract(task_id, delegatee_role, capability_set,
     expected_output_schema, context_task_ids, timeout, retry, depth)`.
  2. `require_tool` raises `CapabilityRefused` with the allowed set and a Why/Fix message; the
     module never retries a refusal.
  3. `check_output` returns missing or wrongly typed fields; an empty list means accepted. A
     contract with no schema accepts any output, preserving old callers.
  4. `child(...)` creates a nested contract one level deeper and `MAX_DELEGATION_DEPTH = 4` caps
     recursion with `DelegationDepthExceeded`.
  5. Validation refuses an empty task id or role, a non-positive timeout, a negative retry and a
     schema whose values are not types.
- **Consequences**: A parent can state and enforce what a delegatee may do, and a delegatee can
  refuse out of contract before acting. The contract is data: the calling runtime must pass it to
  the delegatee and call `require_tool`/`check_output`; nothing here enforces tool use globally.
  Depth is tracked on the contract, so it only bounds delegation that uses `child()`.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-011 (refusals name Why
  and Fix) and PRINCIPLE-014 (failing-first capability, schema, depth and validation tests).

### ADR-102: A handoff is delivered, accepted and re-queued as explicit states
- **Date**: 2026-10-31
- **Status**: Accepted
- **Context**: IDEA-1243 was approved: context passed between steps as a plain dict with no
  delivery record, no acceptance check and no retry, so a rejected handoff vanished silently.
- **Decision**:
  1. `core/handoff.py` owns `HandoffPacket(packet_id, from_step, to_step, context_summary,
     artifacts, context_items, delivered_at, accepted_at, rejected_at, rejection_reason,
     retry_count, retry_limit, status, history)` with the closed states `pending`, `delivered`,
     `accepted`, `rejected` (transient) and `exhausted`.
  2. `deliver` records delivery only; `accept` runs an optional check and marks acceptance.
     A check may return a bool, a list of problems, or raise; any problem rejects.
  3. `reject` re-queues while `retry_count < retry_limit`, clearing `delivered_at` and recording
     the reason and attempt; otherwise the packet becomes `exhausted` so the failure is visible.
  4. Context is bounded at construction: summary to 4,000 characters, at most 50 artifacts and 20
     context items, with a Why/Fix error past the caps.
  5. `replay_queue` and `summarise` support a crash-recovery sweep and a status count.
- **Consequences**: A handoff can no longer be treated as complete just because it was sent, and
  a rejected packet is either retried or visibly exhausted. Packets are in-memory data; the
  calling flow must persist and re-drive the queue. Calling `accept` with no check is an explicit
  opt-out of verification, not an implicit success.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-013 (an explicit
  retry/exhaustion state instead of a silent drop) and PRINCIPLE-014 (failing-first delivery,
  acceptance, retry, exhaustion, bounds and replay tests).

### ADR-103: Recoverable tool planning is measured, not rebuilt
- **Date**: 2026-11-02
- **Status**: Accepted
- **Context**: Replan flags, `max_replan_depth`, the repetition guard, the execution
  budget, model-limit clamps and tool cancellation already existed, but nothing measured
  plan-decompose-then-execute-then-recover-then-stay-under-budget as one joint
  capability with success, recovery and cost numbers.
- **Decision**:
  1. Add `tests/test_planning_recovery_rung.py` (`planning-recovery-rung-v1`): 6
     deterministic headless tasks (3 clean multi-step, 3 with one injected tool
     failure: transient error, unknown tool, timeout class) driven through the existing
     `PlannerExecutor` plan shape and `AgentLoopExecutor.receive` with fake LLM and
     fake sandbox sequences.
  2. Anti-gaming is structural: each task asserts exact tool-derived tokens in the
     final content plus a minimum succeeded-tool count from the trace, with held-out
     ids, so a correct text with no tool work fails. The suite carries
     `RUNG_SUITE_DIGEST`; a task change must update it.
  3. Cost envelope per task is `<=8` LLM calls, `<=12` tool calls, `<=5 s`, inside the
     existing budget defaults (50/50/600 s). Targets: success `>=5/6`, recovery
     `3/3`, runaway identical-repeat aborts via `LoopGuard`, budget exhaustion
     raises `BudgetExceeded`. No new module, node type, or memory primitive; no
     prod-code change — the rung reuses the existing rails.
- **Consequences**: The first AGI-ladder rung ships as numbers (6/6 success, 3/3
  recovery on this hardware) rather than prose. A future prod change that breaks
  recovery fails this file before any new capability is claimed. Runaway and
  ceiling behavior stay covered by the existing guard and budget, now pinned by a
  joint eval instead of isolated unit tests.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (reuse
  before new code), PRINCIPLE-010 (bounded cost), PRINCIPLE-014 (repeatable eval)
  and PRINCIPLE-019 (fake tools only; sandbox, tiers, and cancel unchanged).

### ADR-104: Durable scoped memory is measured as a joint rung, not rebuilt
- **Date**: 2026-11-02
- **Status**: Accepted
- **Context**: Rung 1 measured plan-then-execute-then-recover. Memory pieces
  (scope predicates, budget receipts, link boosts, train-only consolidation,
  guardrail memory surface, edit receipts with conditional undo) each passed in
  isolation, but nothing proved write-then-reload-then-recall-then-consolidate
  as one chain with scope isolation and cost bounds.
- **Decision**:
  1. Add `tests/test_memory_durability_rung.py`
     (`memory-durability-rung-v1`): 8 deterministic headless tasks
     (persist/reload, scope isolation, budget receipt, link promotion,
     skill consolidation, avoidance+holdout, guardrail surface, edit/undo)
     on temp SQLite with FAISS disabled and explicit small vectors, through
     the public `MemoryStore`, `MemoryRecallBudget.fit`, and `LearningLoop`
     (injected `MemoryBridge` + temp `LearningStore`) APIs only.
  2. Anti-gaming is structural: each task asserts exact stored tokens
     reloaded from a fresh connection/instance plus row/receipt counts and
     JSON payload fields (skill/avoidance action, signature, occurrences,
     error class); holdout outcomes never consolidate; second consolidation
     is idempotent. The suite carries `RUNG_SUITE_DIGEST`.
  3. Cost envelope per task is `<=5 s` wall, `0` LLM/network/FAISS calls, file
     informational `<=60 s`. Targets: `8/8` success, `100%` isolation,
     `100%` train-only precision. No new module, node type, or memory
     primitive; no prod-code change — the rung reuses the existing rails.
- **Consequences**: The second AGI-ladder rung ships as numbers (8/8 on this
  hardware) rather than prose. A future prod change that breaks persistence,
  scoping, budgeting, linking, consolidation, guarding, or undo fails this
  file before any new capability is claimed. Runaway and ceiling behavior stay
  covered by the existing budget, caps, and confirm phrases, now pinned by a
  joint eval instead of isolated unit tests.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003 (reuse
  before new code), PRINCIPLE-010 (bounded cost), PRINCIPLE-014 (repeatable eval)
  and PRINCIPLE-019 (temp DB only; sandbox, tiers, and cancel unchanged).

### ADR-105: Home dashboard keeper; data views wait for LLM and saved data
- **Date**: 2026-11-03
- **Status**: Accepted
- **Context**: No registered screenshots existed; four legacy captures sit in
  root `screenshots/` with no commits. Chat and flow views need a live LLM and
  saved flows; the flow canvas loads empty without them.
- **Decision**: Register `2026-11-03-home.png` (`python main.py` dashboard via
  Playwright, commit `10ebfa5`, viewport 1440x900) in `.aikit/media.json` and
  the README `aegean:screenshots` block. Only counts are shown, no message
  content. Legacy root captures stay untouched as history. Chat, flow, memory
  and tools views wait for an LLM-backed session.
- **Consequences**: README proves the real dashboard. Reproduce with: `python
  main.py`, open `http://localhost:8000`, screenshot. Browsing left no repo
  residue (state lives in ignored `data/`).

### ADR-106: Audit wave 10 (commits 1-10) — foundation verified clean
- **Date**: 2026-11-03
- **Status**: Accepted
- **Context**: Oldest 10 commits (`6be535e` Initial commit through `2d8000b`
  flow node configuration) lay the foundation: testing suite + core LLM bridge
  (`51901c7`), modular architecture with DI (`af712f2`), HTMX UI (`ce2aded`),
  AI Flow builder (`853f38d`), README (`7f82279`), flow/node configuration
  (`2d8000b`). Net wave diff: 46 files, +3542/-0.
- **Decision**: Verify, don't rewrite. Evidence quoted from the wave-10
  boundary tree (`2d8000b`): `tests/run_tests.py` ("Starting NeuroCore Test
  Runner...", root-on-`sys.path` bootstrap) plus 11 test modules
  (`test_chat_module`, `test_flow_manager`, `test_core_llm`, ...); no
  `exec(`/`eval(`/`shell=True` and no hardcoded secrets in the wave scope
  (`git grep` over the boundary tree returns only settings-plumbed
  `llm_api_key` with an empty default). No prod-code change — history is
  superseded by 480 later commits and the findings below.
- **Consequences**: Wave 10 closes with zero carry-forward defects; no
  failing-first test is owed because there is nothing left to fix. Future
  audits cite this ADR instead of re-surveying commits 1-10.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003
  (evidence before synthesis) and PRINCIPLE-014 (quote, don't paraphrase).

### ADR-107: Audit wave 20 (commits 11-20) — memory system verified clean
- **Date**: 2026-11-03
- **Status**: Accepted
- **Context**: Commits 11-20 (`3e77d5d` toast/system-prompt through `825de4e`
  multimodal chat + memory extraction) build long-term memory: memory system
  (`633d168`), consolidation with `parent_id` chaining + `MemoryConsolidator`
  (`4a3ad10`), memory browser, system-prompt module. Net wave diff: 43 files,
  +1975/-93.
- **Decision**: Verify, don't rewrite. Evidence quoted from the wave-20
  boundary tree (`825de4e`): `modules/memory/`
  (`arbiter`, `backend`, `consolidation`, `node`, `router`) plus
  `modules/system_prompt/` all present; `4a3ad10` stat (15 files, +436/-103)
  matches its message claims (`parent_id` column, `set_parent` archive,
  `recall_min_score` 0.0->0.8, `save_confidence_threshold` 0.75->0.85). No
  hardcoded secrets at the boundary (key flows via settings with empty
  default). No prod-code change — superseded by later hardening.
- **Consequences**: Wave 20 closes with zero carry-forward defects; no
  failing-first test is owed. Future audits cite this ADR instead of
  re-surveying commits 11-20.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003
  (evidence before synthesis) and PRINCIPLE-014 (quote, don't paraphrase).

### ADR-108: Audit wave 30 (commits 21-30) — two findings, both remediated
- **Date**: 2026-11-03
- **Status**: Accepted
- **Context**: Commits 21-30 (`0b020d9` telegram through `d5e9948` node
  selection/I-O revert) add telegram, conditional/script logic nodes, tool
  library, plus two surgical UI fixes (`94d0b5f` ID-based node removal,
  `4f176b0` server-reload reorder guard). Net wave diff: 77 files,
  +4164/-1296.
- **Decision**: Record two TRUE historical findings, both already remediated
  upstream, so no prod-code change here:
  1. Bare `exec(code, {}, local_scope)` in `modules/logic/node.py:43`
     (`ScriptExecutor`) and `modules/tools/node.py:46` at `d5e9948` —
     server-side execution of flow-authored code with no sandboxing.
     Remediation (verified this audit): current tree has NO bare `exec(`
     in `modules/logic/` or `modules/tools/node.py`; only
     `modules/tools/sandbox.py:886` (`exec(code, exec_globals)`) and a test
     remain, i.e. execution moved behind the sandbox delivered by later
     runtime-hardening commits.
  2. `llm_api_key` reflected into `web/templates/settings.html`
     (`value="{{ settings.llm_api_key }}"`) at `d5e9948` — secret rendered
     into served HTML. Remediation (verified this audit): current
     `web/templates/settings.html` contains no `llm_api_key` reference;
     the settings surface was reworked by later security hardening.
  Corroborating clean evidence: `94d0b5f` quoted verbatim (one-line selector
  `.absolute.bg-slate-800` -> `div[id^="node-"]`); no hardcoded secrets at
  `d5e9948` (key defaults empty, password-type input). Unverifiable items
  were dropped per the audit contract — no claim above rests on paraphrase.
- **Consequences**: Wave 30 closes with nothing left to fix; a failing-first
  test is not owed because both findings are already dead code paths in the
  current tree. If script-node sandboxing ever regresses, this ADR names the
  exact re-check (`git grep -n "exec(" -- modules/logic/ modules/tools/`).
  Future audits cite this ADR instead of re-surveying commits 21-30.
- Cross-principle check: no principle section changed. Serves PRINCIPLE-003
  (verify-then-drop), PRINCIPLE-014 (failing-first only for live defects)
  and PRINCIPLE-019 (secret handling traced to remediation).
