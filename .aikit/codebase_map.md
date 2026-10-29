# Codebase Map & Symbol Catalog 🗺️

> **AUTO-GENERATED & VERIFIED BY `scripts/update_knowledge_base.py`**
> This document provides an automated, up-to-date registry of all modules, classes, and exported functions across this repository.

---

## 📊 Codebase Metrics
- **Total Python Modules**: `286`
- **Total Classes**: `403`
- **Total Top-Level Functions**: `1878`
- **Total Class Methods**: `2168`
- **Total Source Lines**: `79643`

---

## 📦 Modules & Exported Symbols

### [`core/archive.py`](core/archive.py)
> *Safe ZIP extraction for attacker-controlled marketplace packages.*

**Size**: `118` lines (`5251` bytes)

**Classes:**
- `class ArchiveSecurityError` - *Raised when a ZIP member would escape the destination directory.*

**Functions:**
- `def _is_symlink(info)` - *Whether the member records a symbolic link in its Unix mode bits.*
- `def _safe_member_path(dest_dir, member)` - *The absolute write path for `member`, or raise when it escapes `dest_dir`.*
- `def _common_root(infos)` - *The single shared top-level directory of the archive as ``dir/``, or ``''``.*
- `def extract_zip_safely(archive_path, dest_dir, strip_root, max_member_bytes, max_total_bytes)` - *Extract `archive_path` into `dest_dir`, refusing escaping or symlink members.*

---

### [`core/confirmations.py`](core/confirmations.py)
> *Server-side confirmation for irreversible routes (ADR-059).*

**Size**: `25` lines (`971` bytes)

**Functions:**
- `def require_confirmation(provided, phrase, action)` - *Allow the operation only when ``provided`` matches ``phrase`` after normalization.*

---

### [`core/debug.py`](core/debug.py)
**Size**: `94` lines (`3179` bytes)

**Classes:**
- `class DebugLogger` - *Debug logger for flow execution.*
  - Method `.__init__(max_logs)`
  - Method `.log(flow_id, node_id, node_name, event_type, details)`
  - Method `.get_logs(reverse)` - *Get all logs.*
  - Method `.get_recent_logs(since_timestamp, reverse)` - *Get logs since a given timestamp.*
  - Method `.clear()`

**Functions:**
- `def get_max_logs()` - *Get max_logs setting from settings or use default.*

---

### [`core/dependencies.py`](core/dependencies.py)
**Size**: `90` lines (`3727` bytes)

**Functions:**
- `def get_settings_manager()` - *Dependency to get the global settings manager instance.*
- `def require_debug_mode(settings)` - *Dependency that requires debug mode to be enabled.*
- `def get_llm_bridge(settings)` - *Dependency to get a cached LLMBridge instance.*
- `def get_module_manager(request)` - *Dependency to get the module manager instance from the app state.*
- `def get_research_manager()` - *Dependency to get the global ResearchManager singleton.*

---

### [`core/errors.py`](core/errors.py)
> *Error Hierarchy for NeuroCore*

**Size**: `157` lines (`4321` bytes)

**Classes:**
- `class NeuroCoreError` - *Base exception for all NeuroCore errors.*
  - Method `.__init__(message, details)`
  - Method `.to_dict()` - *Convert exception to dict for backward compatibility.*
- `class LLMError` - *Base class for LLM-related errors.*
  - Method `.__init__(message, model, details)`
- `class LLMTimeoutError` - *Raised when LLM request times out.*
- `class LLMHTTPError` - *Raised when LLM returns an HTTP error.*
  - Method `.__init__(message, status_code, model, details)`
- `class LLMResponseError` - *Raised when LLM returns an invalid or unexpected response.*
- `class ToolError` - *Base class for tool-related errors.*
  - Method `.__init__(message, tool_name, details)`
- `class ToolExecutionError` - *Raised when tool execution fails.*
- `class ToolTimeoutError` - *Raised when tool execution times out.*
- `class SandboxSecurityError` - *Raised when sandbox security policy is violated.*
- `class FlowError` - *Base class for flow execution errors.*
  - Method `.__init__(message, flow_id, node_id, details)`
- `class FlowNotFoundError` - *Raised when a flow is not found.*
- `class FlowValidationError` - *Raised when flow validation fails.*
- `class NodeExecutionError` - *Raised when a node fails during execution.*
- `class ModuleError` - *Base class for module-related errors.*
  - Method `.__init__(message, module_id, details)`
- `class ModuleNotFoundError` - *Raised when a module is not found.*
- `class ModuleLoadError` - *Raised when a module fails to load.*
- `class MemoryError` - *Base class for memory-related errors.*
- `class MemoryConsolidationError` - *Raised when memory consolidation fails.*

**Functions:**
- `def exception_to_dict(exc)` - *Convert any exception to a dict representation.*

---

### [`core/execution_budget.py`](core/execution_budget.py)
> *Per-run execution budget: bound nodes, LLM calls, tokens, tool calls and wall time.*

**Size**: `161` lines (`5928` bytes)

**Classes:**
- `class BudgetExceeded` - *Raised when one dimension of the run budget is exhausted.*
  - Method `.__init__(kind, limit, used)`
- `class ExecutionBudget` - *Limits and usage for one top-level flow run.*
  - Method `.remaining(kind)`
  - Method `.elapsed()`
  - Method `.spend(kind, units)` - *Consume units, or raise :class:`BudgetExceeded` naming the dimension.*
  - Method `.check_time()`
  - Property `.pressure` - *The fullest non-time dimension as a 0..1 fraction (for a 'slow down' signal).*
  - Method `.snapshot()`

**Functions:**
- `def current_budget()`
- `def use_budget(budget)` - *Install a budget for the current task; nested runs inherit it automatically.*
- `def spend(kind, units)` - *Spend from the run budget when one is active; a no-op outside a budgeted run.*
- `def push_budget(budget)` - *Install a budget and return the context token; pair with :func:`pop_budget`.*
- `def pop_budget(token)`
- `def _settings_int(key, default)`
- `def budget_from_flow(flow)` - *Build the budget from settings, then let flow metadata lower (never raise) a limit.*

---

### [`core/flow_context.py`](core/flow_context.py)
> *FlowContext Pydantic Model - Type-safe flow payload between nodes.*

**Size**: `423` lines (`16318` bytes)

**Classes:**
- `class FlowContext` - *Pydantic model representing the canonical flow payload passed between nodes.*
  - Method `.to_dict()` - *Convert to dictionary, excluding None values and extra.*
  - Method `.to_dict_including_none()` - *Convert to dictionary including None values.*
  - Method `.from_dict(data)` - *Create FlowContext from dictionary.*
  - Method `.copy()` - *Create a copy of this FlowContext.*
  - Method `.add_message(role, content)` - *Add a message to the conversation.*
  - Method `.add_plan_step(step)` - *Add a step to the plan.*
  - Method `.advance_step()` - *Advance to the next step in the plan.*
  - Method `.is_plan_complete()` - *Check if plan execution is complete.*
  - Method `.get_current_plan_step()` - *Get the current plan step.*
  - Method `.add_trace_entry(entry)` - *Add an entry to the agent loop trace.*
  - Method `.increment_iterations()` - *Increment the iteration counter.*
  - Method `.increment_replan_count()` - *Increment the replan counter.*
  - Method `.validate_messages(v)` - *Validate messages is a list.*
  - Method `.validate_plan(v)` - *Validate plan is a list.*
  - Method `.validate_completed_steps(v)` - *Validate completed_steps is a list of ints.*

**Functions:**
- `def to_flow_context(data)` - *Convert a plain dict to FlowContext.*
- `def from_flow_context(ctx)` - *Convert FlowContext to plain dict.*
- `def flow_context_to_node_input(ctx)` - *Convert FlowContext to format expected by node receive() methods.*
- `def node_output_to_flow_context(output)` - *Convert node output to FlowContext.*

---

### [`core/flow_data.py`](core/flow_data.py)
> *FlowData TypedDict - Type-safe flow payload between nodes.*

**Size**: `1213` lines (`40035` bytes)

**Classes:**
- `class FlowData` - *TypedDict representing the canonical flow payload passed between nodes.*

**Functions:**
- `def _deep_copy_maybe(value)` - *Create a deep copy of a value if it's mutable (list or dict).*
- `def get_messages(data, default)` - *Get messages from flow data with type safety.*
- `def get_content(data, default)` - *Get content from flow data.*
- `def get_plan(data, default)` - *Get plan from flow data.*
- `def get_current_step(data, default)` - *Get current_step from flow data.*
- `def get_original_request(data, default)` - *Get original_request from flow data.*
- `def get_plan_needed(data, default)` - *Get plan_needed from flow data.*
- `def get_plan_context(data, default)` - *Get plan_context from flow data.*
- `def get_plan_complete(data, default)` - *Get plan_complete from flow data.*
- `def get_next_step(data)` - *Get next_step from flow data.*
- `def get_step_completed(data)` - *Get step_completed from flow data.*
- `def get_completed_steps(data, default)` - *Get completed_steps from flow data.*
- `def get_dependency_error(data)` - *Get dependency_error from flow data.*
- `def get_reflection(data)` - *Get reflection from flow data.*
- `def get_satisfied(data, default)` - *Get satisfied from flow data.*
- `def get_reflection_retry_count(data, default)` - *Get reflection_retry_count from flow data.*
- `def get_iterations(data, default)` - *Get iterations from flow data.*
- `def get_agent_loop_trace(data, default)` - *Get agent_loop_trace from flow data.*
- `def get_agent_loop_error(data)` - *Get agent_loop_error from flow data.*
- `def get_replan_needed(data, default)` - *Get replan_needed from flow data.*
- `def get_replan_count(data, default)` - *Get replan_count from flow data.*
- `def get_replan_reason(data)` - *Get replan_reason from flow data.*
- `def get_suggested_approach(data)` - *Get suggested_approach from flow data.*
- `def get_replan_depth_exceeded(data, default)` - *Get replan_depth_exceeded from flow data.*
- `def get_response(data)` - *Get response (raw LLM response) from flow data.*
- `def get_memory_context(data)` - *Get memory_context from flow data.*
- `def set_memory_context(data, context)` - *Set memory_context in flow data.*
- `def get_knowledge_context(data)` - *Get knowledge_context from flow data.*
- `def get_reasoning_context(data)` - *Get reasoning_context from flow data.*
- `def get_reasoning_history(data, default)` - *Get reasoning_history from flow data.*
- `def get_reasoning_structured(data, default)` - *Get reasoning_structured from flow data.*
- `def get_tool_count(data, default)` - *Get tool_count from flow data.*
- `def set_tool_count(data, count)` - *Set tool_count in flow data.*
- `def get_remaining_tool_calls(data, default)` - *Get remaining_tool_calls from flow data.*
- `def set_remaining_tool_calls(data, calls)` - *Set remaining_tool_calls in flow data.*
- `def get_requires_continuation(data, default)` - *Get requires_continuation from flow data.*
- `def get_choices(data, default)` - *Get choices from flow data.*
- `def get_tools(data, default)` - *Get tools from flow data.*
- `def get_available_tools(data, default)` - *Get available_tools from flow data.*
- `def get_route_targets(data, default)` - *Get _route_targets from flow data.*
- `def get_repeat_count(data, default)` - *Get _repeat_count from flow data.*
- `def get_input_source(data)` - *Get _input_source from flow data.*
- `def get_current_goal(data)` - *Get current_goal from flow data.*
- `def get_error(data)` - *Get error from flow data.*
- `def get_planning_error(data)` - *Get planning_error from flow data.*
- `def set_messages(data, messages)` - *Set messages in flow data (returns new dict with deep copy of list).*
- `def set_content(data, content)` - *Set content in flow data.*
- `def set_plan(data, plan)` - *Set plan in flow data (returns new dict with deep copy of list).*
- `def set_current_step(data, current_step)` - *Set current_step in flow data.*
- `def set_original_request(data, original_request)` - *Set original_request in flow data.*
- `def set_plan_needed(data, plan_needed)` - *Set plan_needed in flow data.*
- `def set_plan_context(data, plan_context)` - *Set plan_context in flow data.*
- `def set_plan_complete(data, plan_complete)` - *Set plan_complete in flow data.*
- `def set_next_step(data, next_step)` - *Set next_step in flow data (returns new dict with deep copy of dict).*
- `def set_step_completed(data, step_completed)` - *Set step_completed in flow data (returns new dict with deep copy of dict).*
- `def set_completed_steps(data, completed_steps)` - *Set completed_steps in flow data (returns new dict with deep copy of list).*
- `def set_dependency_error(data, dependency_error)` - *Set dependency_error in flow data.*
- `def set_reflection(data, reflection)` - *Set reflection in flow data (returns new dict with deep copy of dict).*
- `def set_satisfied(data, satisfied)` - *Set satisfied in flow data.*
- `def set_reflection_retry_count(data, count)` - *Set reflection_retry_count in flow data.*
- `def set_iterations(data, iterations)` - *Set iterations in flow data.*
- `def set_agent_loop_trace(data, trace)` - *Set agent_loop_trace in flow data (returns new dict with deep copy of list).*
- `def set_agent_loop_error(data, error)` - *Set agent_loop_error in flow data.*
- `def set_replan_needed(data, replan_needed)` - *Set replan_needed in flow data.*
- `def set_replan_count(data, count)` - *Set replan_count in flow data.*
- `def set_replan_reason(data, reason)` - *Set replan_reason in flow data.*
- `def set_suggested_approach(data, approach)` - *Set suggested_approach in flow data.*
- `def set_replan_depth_exceeded(data, exceeded)` - *Set replan_depth_exceeded in flow data.*
- `def set_response(data, response)` - *Set response in flow data (returns new dict with deep copy of dict).*
- `def set_knowledge_context(data, context)` - *Set knowledge_context in flow data.*
- `def set_reasoning_context(data, context)` - *Set reasoning_context in flow data.*
- `def set_reasoning_history(data, history)` - *Set reasoning_history in flow data (returns new dict with deep copy of list).*
- `def set_reasoning_structured(data, structured)` - *Set reasoning_structured in flow data (returns new dict with deep copy of list).*
- `def set_tool_count(data, count)` - *Set tool_count in flow data.*
- `def set_remaining_tool_calls(data, calls)` - *Set remaining_tool_calls in flow data.*
- `def set_requires_continuation(data, requires)` - *Set requires_continuation in flow data.*
- `def set_choices(data, choices)` - *Set choices in flow data (returns new dict with deep copy of list).*
- `def set_tools(data, tools)` - *Set tools in flow data (returns new dict with deep copy of list).*
- `def set_available_tools(data, tools)` - *Set available_tools in flow data (returns new dict with deep copy of list).*
- `def set_route_targets(data, targets)` - *Set _route_targets in flow data (returns new dict with deep copy of list).*
- `def set_repeat_count(data, count)` - *Set _repeat_count in flow data.*
- `def set_input_source(data, source)` - *Set _input_source in flow data.*
- `def set_current_goal(data, goal)` - *Set current_goal in flow data (returns new dict with deep copy of dict).*
- `def set_error(data, error)` - *Set error in flow data.*
- `def set_planning_error(data, error)` - *Set planning_error in flow data.*
- `def validate_flow_data(data)` - *Validate flow data and return list of issues.*
- `def is_valid_flow_data(data)` - *Check if data is valid flow data.*
- `def to_flow_data(data)` - *Convert a plain dict to FlowData.*
- `def ensure_flow_data(data)` - *Ensure data is a FlowData dict.*
- `def merge_flow_data(*dicts)` - *Merge multiple FlowData dicts.*
- `def get_with_typo_check(data, key, default)` - *Get value from dict with automatic typo correction.*

---

### [`core/flow_evolution.py`](core/flow_evolution.py)
> *Validated, versioned self-editing for NeuroCore flows.*

**Size**: `358` lines (`16841` bytes)

**Classes:**
- `class FlowEvolutionError` - *Raised when an evolution proposal would produce an invalid flow.*
  - Method `.__init__(message, errors)`
- `class FlowEvolution` - *Apply validated, versioned mutations to NeuroCore flows.*
  - Method `.__init__(flow_manager, max_nodes, max_connections, allowed_node_types, allowed_tools)`
  - Method `.validate(name, nodes, connections, bridges, allowed_node_types, allow_cycles)` - *Return every reason the proposed flow must not be saved.*
  - Method `._validate_edges(label, edges, node_ids)`
  - Method `._find_cycle(node_ids, edges)`
  - Method `.create(name, nodes, connections, bridges, metadata, flow_id, allow_cycles)` - *Validate and persist a new flow.*
  - Method `.preview(flow_id, operations)` - *Apply operations to a copy and validate without touching the saved flow.*
  - Method `._prepare_mutation(flow, operations)`
  - Method `.mutate(flow_id, operations)` - *Apply operations atomically; the previous state becomes a version snapshot.*
  - Method `._apply_operation(name, nodes, connections, bridges, operation)`
  - Method `.history(flow_id)` - *Version snapshots for a flow, newest first.*
  - Method `.rollback(flow_id, version)` - *Restore a snapshot; raises when the flow or version is missing.*

---

### [`core/flow_manager.py`](core/flow_manager.py)
**Size**: `637` lines (`31020` bytes)

**Classes:**
- `class FlowManager`
  - Method `.__init__(storage_file, versions_file)`
  - Method `._fsync_directory(directory)` - *Best-effort directory fsync so a rename survives a power loss.*
  - Method `._write_json_atomic(path, data, indent)` - *Write JSON through a fsynced temp file, keeping the previous file as .bak.*
  - Method `._quarantine_file(path)` - *Move a corrupt state file aside instead of overwriting it.*
  - Method `._read_flow_file(path)`
  - Method `._load_backup_flows()` - *Return validated flows from the .bak file, or None when unusable.*
  - Method `._restore_default_flows()` - *Create and persist the default flow set after an unrecoverable store.*
  - Method `._load_flows()`
  - Method `._validate_flows(flows)` - *Validate flows structure and integrity. Returns {valid: bool, errors: list}.*
  - Method `._validate_flow_structure(flow_id, flow)` - *Structural checks shared by load and opt-in validated saves.*
  - Method `._create_default_flows()`
  - Method `._ensure_default_active(flows)` - *Ensure a default flow is active if one exists.*
  - Method `._save_flows_to_disk_no_lock_required(flows)` - *Internal method to save flows to disk WITHOUT acquiring lock.*
  - Method `._save_flows_to_disk(flows)` - *Save flows to disk. Acquires lock before writing.*
  - Method `._save_flows()`
  - Method `._load_versions()` - *Load the per-flow version store from disk. Returns {} on missing/corrupt file.*
  - Method `._save_versions_to_disk()` - *Persist the versions dict to disk (must be called while holding self.lock).*
  - Method `._save_version(flow_id, persist)` - *Snapshot the current state of *flow_id*.*
  - Method `.get_versions(flow_id)` - *Return lightweight version metadata for *flow_id*, newest first.*
  - Method `.rollback_version(flow_id, version_num)` - *Restore *flow_id* to the snapshot identified by *version_num*.*
  - Method `.save_flow(name, nodes, connections, bridges, flow_id, metadata, reverted_nodes, validate)`
  - Method `.validate_flow(flow_id, flow)` - *Validation errors for one flow, without saving it (for all-or-nothing imports).*
  - Method `.add_flow(flow_id, flow, *, validate)` - *Add one flow without replacing the store, keeping imported fields intact.*
  - Method `.import_flows(flows_data)` - *Replaces all flows with the provided dictionary after validation.*
  - Method `.rename_flow(flow_id, new_name)`
  - Method `.get_flow(flow_id)`
  - Method `.list_flows()`
  - Method `.get_all_flows_dict()` - *Get a copy of all flows as a dictionary (thread-safe).*
  - Method `.delete_flow(flow_id)`
  - Method `.make_active_flow_default()` - *Overwrites the default flow with the first active flow after backing up current default.*

---

### [`core/flow_runner.py`](core/flow_runner.py)
**Size**: `1155` lines (`55625` bytes)

**Classes:**
- `class FlowRunner`
  - Method `.clear_cache()`
  - Method `._get_cache_lock()` - *Return an asyncio.Lock scoped to the currently running event loop.*
  - Method `async def ._get_executor_class(module_id, node_type_id)` - *Thread-safe method to get or create an executor class from the cache.*
  - Method `._manage_cache_size()` - *Ensure cache doesn't grow indefinitely by removing oldest entries when limit reached.*
  - Method `.__init__(flow_id, flow_override, flow_manager_override, capture_outcome)`
  - Method `._build_bridge_groups()` - *Identifies groups of bridged nodes using BFS to find connected components.*
  - Method `._get_bridge_order(node_id)` - *Get bridge chain nodes in execution order (upstream to downstream).*
  - Method `._get_bridge_levels(node_id)` - *Group bridge-group nodes into parallel execution levels using Kahn's algorithm.*
  - Method `async def ._run_node(node_id, node_input, stream_queue)` - *Execute one node under its configured timeout and retry policy (ADR-060).*
  - Method `async def ._run_bridge_node(bridge_node_id, bridge_input, stream_queue)` - *Execute a single bridge node and return its output dict, or None to stop the chain.*
  - Method `._compute_execution_order()` - *Performs a topological sort (Kahn's algorithm) to find the execution order.*
  - Method `._identify_cycle_nodes(adj, sorted_order, remaining_nodes)` - *Identify the nodes that form the cycle in the flow graph.*
  - Method `.validate(module_manager)` - *Validates the flow for potential issues before execution.*
  - Method `async def .run(initial_input, start_node_id, timeout, raise_errors, episode_id, stream_queue)` - *Run a flow and emit one outcome record (unless capture is disabled).*
  - Method `async def ._run(initial_input, start_node_id, timeout, raise_errors, episode_id, stream_queue)` - *Executes the flow in order, passing data between nodes.*

---

### [`core/flow_simulation.py`](core/flow_simulation.py)
> *Deterministic pre-flight flow analysis: structure, effects and a cost envelope.*

**Size**: `321` lines (`12824` bytes)

**Classes:**
- `class Finding`
  - Method `.to_dict()`
- `class SimulationReport`
  - Property `.blocking`
  - Method `.to_dict()`

**Functions:**
- `def _node_effects(node)`
- `def _strongly_connected(nodes, adjacency)` - *Tarjan's SCCs with at least two nodes, or one node pointing at itself.*
- `def _settings_int(key, default)`
- `def node_type_catalogue(modules)` - *Installed (module_id, node_type_id) pairs from module manifests.*
- `def simulate_flow(flow, known_node_types)` - *Analyse a flow without running it and return a JSON-safe report.*

---

### [`core/llm.py`](core/llm.py)
**Size**: `272` lines (`11708` bytes)

**Classes:**
- `class LLMBridge`
  - Method `.__init__(base_url, api_key, embedding_base_url, embedding_model, client, timeout)`
  - Method `._get_url(path, use_embedding_url)` - *Constructs a full URL for the given path.*
  - Method `async def .chat_completion(messages, model, temperature, max_tokens, tools, tool_choice, response_format, raise_errors)` - *Sends a chat completion request to the LLM API.*
  - Method `async def .chat_completion_stream(messages, model, temperature, max_tokens, tools, tool_choice, response_format)` - *Sends a streaming chat completion request to the LLM API.*
  - Method `async def .get_models()` - *Fetches available models from the LLM API.*
  - Method `async def .get_embedding(text, model)` - *Generates an embedding vector for the given text.*

**Functions:**
- `def get_client_lock()` - *Get or create the client lock lazily to avoid loop-affinity issues.*
- `async def get_shared_client(timeout)` - *Get or create a shared AsyncClient for connection pooling.*
- `async def close_shared_client()` - *Close the shared client. Call on application shutdown.*

---

### [`core/meta_flow.py`](core/meta_flow.py)
> *Compose small flows into meta-flows.*

**Size**: `292` lines (`12328` bytes)

**Classes:**
- `class MetaFlowError` - *Raised when member flows or pathways cannot form a valid meta-flow.*
- `class MetaFlowComposer` - *Merge member flows (ganglia) into one validated flow with pathways.*
  - Method `.__init__(flow_manager, evolution)`
  - Method `.compose(name, member_flow_ids, pathways, metadata, flow_id)` - *Compose members into a meta-flow; the result is a normal saved flow.*
  - Method `.compose_live(name, member_flow_ids, pathways, metadata, flow_id)` - *Compose live references: one meta:subflow node per ganglion.*
  - Method `._assert_acyclic_pathways(pathways, member_count)` - *Members may loop internally, but pathways between ganglia must stay acyclic.*
  - Method `._rewrite_config(value, member_index, member_map)` - *Namespace node ids referenced from node configuration.*
  - Method `._rewrite_reference(value, member_map)`
  - Method `._rewire(member_index, edge, node_map, label)`
  - Method `._auto_pathways(members)`
  - Method `._boundaries(flow)`

---

### [`core/module_manager.py`](core/module_manager.py)
**Size**: `302` lines (`14254` bytes)

**Classes:**
- `class ModuleManager`
  - Method `.__init__(app, modules_dir)`
  - Method `._discover_modules()` - *Finds all potential modules in the modules directory.*
  - Method `.load_enabled_modules()` - *Loads routers for all modules marked as enabled.*
  - Method `._load_module_router(module_id)` - *Imports a module and adds its router to the app.*
  - Method `._unload_module_router(module_id)` - *Finds and removes a module's router from the app.*
  - Method `.get_all_modules()` - *Returns a list of all discovered modules and their metadata, sorted by order key.*
  - Method `.get_module(module_id)` - *Returns module metadata with proper locking to avoid race conditions.*
  - Method `._toggle_module(module_id, enabled)` - *Enables or disables a module, updating the running app and the metadata file.*
  - Method `.reorder_modules(module_ids)` - *Updates the 'order' of only modules whose order actually changed.*
  - Method `.enable_module(module_id)`
  - Method `.disable_module(module_id)`
  - Method `.update_module_config(module_id, new_config)` - *Updates the configuration for a specific module.*

**Functions:**
- `def _write_manifest(meta_path, meta)` - *Replace a module.json atomically so readers never see a truncated manifest.*

---

### [`core/node_retry.py`](core/node_retry.py)
> *Opt-in per-node retry policy with bounded exponential backoff (ADR-060).*

**Size**: `140` lines (`5586` bytes)

**Classes:**
- `class RetryPolicy` - *How many times a node may be retried and how long to wait between attempts.*
  - Property `.retries`
  - Method `.from_config(config)` - *Read ``retry_count``/``retry_delay`` from node config; malformed values fall back.*
  - Method `.delay_for(retry)` - *Delay before retry number ``retry`` (1-based), capped at ``max_delay``.*

**Functions:**
- `def _bounded_int(value, default, *, low, high)`
- `def _bounded_float(value, default, *, low, high)`
- `def node_timeout(config)` - *Per-node wall-clock ceiling in seconds; ``0`` means no ceiling.*
- `def is_transient(exc)` - *Whether retrying the same call could plausibly succeed.*
- `async def run_with_retry(operation, policy, *, retryable, sleep, on_retry, delay_cap)` - *Run ``operation`` under ``policy``; re-raise the last error when retries are spent.*

---

### [`core/node_stall.py`](core/node_stall.py)
> *Node latency outlier detection for stalled executions (ADR-061).*

**Size**: `205` lines (`7987` bytes)

**Classes:**
- `class NodeLatencyMonitor` - *Thread-safe bounded latency history per node type with p99 outlier detection.*
  - Method `.__init__(*, min_samples, factor, floor_ms, history_size, clock)`
  - Method `.key(module_id, node_type_id)`
  - Method `.record(module_id, node_type_id, duration_ms, *, flow_id, node_id)` - *Record one attempt and return a stall finding when it is an outlier.*
  - Method `.stats()`
  - Method `.findings()`
  - Method `.report()`
  - Method `.reset()`

**Functions:**
- `def configure_node_stall(*, timing, gauge, counter, warn)` - *Inject the observability sinks; node_stall never imports them (no import cycle).*
- `def _percentile(sorted_values, fraction)`
- `def record_node_latency(module_id, node_type_id, duration_ms, *, flow_id, node_id)` - *Record an attempt on the shared monitor; never raises into the runner.*
- `def node_latency_report()`
- `def reset_node_latency()`

---

### [`core/observability.py`](core/observability.py)
> *Observability Infrastructure for NeuroCore*

**Size**: `1000` lines (`36529` bytes)

**Classes:**
- `class SpanKind` - *Types of spans in distributed tracing.*
- `class Span` - *Represents a single operation in a trace.*
  - Method `.finish(status, error)` - *Mark span as finished.*
  - Property `.duration_ms` - *Get duration in milliseconds.*
  - Method `.to_dict()` - *Convert to dictionary for serialization.*
  - Method `.__enter__()`
  - Method `.__exit__(exc_type, exc_val, exc_tb)`
- `class TraceContext` - *Context for distributed tracing.*
  - Method `.__post_init__()`
  - Method `.add_span(name, kind, attributes, parent_id)` - *Create and start a new span.*
  - Method `.end_span(status, error)` - *End the current span.*
  - Method `.get_trace()` - *Get complete trace as list of span dicts.*
  - Method `.__enter__()`
  - Method `.__exit__(exc_type, exc_val, exc_tb)`
- `class Metrics` - *Simple metrics collector with counters, timers, and gauges.*
  - Method `.__init__()`
  - Method `._load_counters()` - *Load persisted counters from disk on startup.*
  - Method `._save_counters()` - *Save counters to disk on updates.*
  - Method `.increment(metric, value, tags)` - *Increment a counter metric.*
  - Method `.timing(metric, duration_ms, tags)` - *Record a timing metric in milliseconds.*
  - Method `.gauge(metric, value, tags)` - *Set a gauge value.*
  - Method `._make_key(metric, tags)` - *Create metric key with tags.*
  - Method `.get_counter(metric, tags)` - *Get counter value.*
  - Method `.get_timing_stats(metric, tags)` - *Get timing statistics (min, max, avg, p50, p95, p99).*
  - Method `.get_gauge(metric, tags)` - *Get gauge value.*
  - Method `.get_all_metrics()` - *Get all metrics.*
  - Method `.reset()` - *Reset all metrics.*
- `class StructuredLogger` - *Structured JSON logger for NeuroCore.*
  - Method `.__init__(name)`
  - Method `._ensure_handler()` - *Ensure logger has a handler configured.*
  - Method `._log(level, event, message, trace_id, **kwargs)` - *Internal log method.*
  - Method `.debug(event, message, **kwargs)` - *Log debug level.*
  - Method `.info(event, message, **kwargs)` - *Log info level.*
  - Method `.warning(event, message, **kwargs)` - *Log warning level.*
  - Method `.error(event, message, **kwargs)` - *Log error level.*
  - Method `.critical(event, message, **kwargs)` - *Log critical level.*
  - Method `.log_flow(flow_id, node_id, event, **kwargs)` - *Log flow-related event with trace context.*
  - Method `.log_node(flow_id, node_id, node_name, event, **kwargs)` - *Log node-related event.*
  - Method `.log_llm(flow_id, event, **kwargs)` - *Log LLM-related event.*
  - Method `.log_tool(flow_id, tool_name, event, **kwargs)` - *Log tool-related event.*
  - Method `.log_memory(flow_id, operation, **kwargs)` - *Log memory operation.*
- `class JsonFormatter` - *JSON formatter for structured logging.*
  - Method `.format(record)`

**Functions:**
- `def get_trace_context()` - *Get current trace context.*
- `def get_trace_id()` - *Get current trace ID.*
- `def get_or_create_trace_context()` - *Get current trace context or create a new one.*
- `def create_trace_context(trace_id)` - *Create a new trace context with optional trace ID.*
- `def trace(span_name, kind, attributes)` - *Context manager for creating spans.*
- `def trace_async(span_name, kind, attributes)` - *Context manager for creating spans in async context.*
- `def traced(span_name, kind)` - *Decorator for automatic tracing of functions.*
- `def timed(metric, tags)` - *Context manager for timing operations.*
- `def timed_decorator(metric, tags)` - *Decorator for timing functions.*
- `def instrument_flow_runner()` - *Instrument the flow runner for automatic tracing.*
- `def instrument_llm_calls()` - *Instrument LLM calls for automatic tracing and metrics.*
- `def instrument_tools()` - *Instrument tool execution for automatic tracing and metrics.*
- `def get_dashboard_data()` - *Get data for observability dashboard.*
- `def get_token_stats()` - *Get LLM token usage stats, broken down by model.*
- `def get_trace_file_path()` - *Get the path to the execution trace file.*
- `def export_trace_to_file(trace_data, file_path)` - *Export trace data to JSON Lines file.*
- `def get_session_trace_summary()` - *Get a summary of the current session's trace data.*
- `def _maybe_export_span(span_data)` - *Optionally export span data to trace file.*
- `def enable_trace_file_export(enabled)` - *Enable or disable trace file export.*
- `def is_trace_file_export_enabled()` - *Check if trace file export is enabled.*

---

### [`core/outcomes.py`](core/outcomes.py)
> *Small, dependency-free outcome hook used by FlowRunner and the brain.*

**Size**: `30` lines (`818` bytes)

**Functions:**
- `def set_outcome_listener(listener)`
- `def clear_outcome_listener(listener)`
- `def emit_outcome(outcome)`

---

### [`core/paths.py`](core/paths.py)
> *Absolute locations for NeuroCore runtime files.*

**Size**: `37` lines (`1425` bytes)

**Functions:**
- `def project_path(*parts)` - *Return an absolute path inside the NeuroCore project root.*
- `def state_path(*parts)` - *Return a path for mutable state, honouring NEUROCORE_STATE_DIR.*
- `def data_path(*parts)` - *Return a path in the data directory: databases, keys, traces, uploads and ledgers.*

---

### [`core/planner_helpers.py`](core/planner_helpers.py)
> *Shared utility class for plan dependency management.*

**Size**: `236` lines (`8485` bytes)

**Classes:**
- `class PlanHelper` - *Shared utility class for plan dependency management.*
  - Method `.build_dependency_graph(plan)` - *Build adjacency list and in-degree count for topological sort.*
  - Method `.detect_circular_dependencies(plan)` - *Detect circular dependencies using Kahn's algorithm.*
  - Method `.get_executable_steps(plan, completed_steps)` - *Get list of step indices that can be executed (all dependencies satisfied).*
  - Method `.generate_plan_context(plan, current_step, completed_steps)` - *Generate plan_context string showing progress.*
  - Method `.validate_dependencies(plan)` - *Validate that all dependencies in the plan are valid.*

---

### [`core/research_gateway.py`](core/research_gateway.py)
> *Allowlisted research sources with SSRF protection, caching and provenance.*

**Size**: `403` lines (`15632` bytes)

**Classes:**
- `class SourcePolicyError` - *A request violates the source allowlist or URL safety policy.*
- `class SourceUnavailableError` - *The upstream source failed and no cached result can be served.*
- `class _Cache`
  - Method `.__init__(path)`
  - Method `._load()`
  - Method `.get(key)`
  - Method `.put(key, payload)`

**Functions:**
- `def sources()`
- `def _now()`
- `def _strip_markup(text)`
- `def _validate_url(url, allowed_hosts, resolver)` - *Require HTTPS, an allowlisted host and a public address for every hop.*
- `async def _safe_get(url, allowed_hosts)` - *Fetch one page, validating the start URL and every redirect hop.*
- `def build_request(source, query, limit)`
- `def parse_wikipedia(payload, limit)`
- `def parse_arxiv(payload, limit)`
- `def parse_pubmed(payload, limit)`
- `async def _fetch(source, query, limit)`
- `def _cache_key(source, query, limit)`
- `def _persist_articles(source, query, items, retrieved_at, license_text)` - *Store fetched items as research articles, carrying provenance in notes.*
- `async def search(source, query, limit, *, persist)` - *Search one allowlisted source and return items with provenance.*

---

### [`core/research_manager.py`](core/research_manager.py)
> *Research Manager*

**Size**: `915` lines (`38709` bytes)

**Classes:**
- `class ResearchManager` - *Manages persistent storage of research entities using SQLite.*
  - Method `.__init__(db_path)`
  - Method `._connect()`
  - Method `._init_db()`
  - Method `._row_to_dict(row)`
  - Method `.create_hypothesis(data)`
  - Method `.get_hypotheses(status, domain)`
  - Method `.get_hypothesis(hypothesis_id)`
  - Method `.update_hypothesis(hypothesis_id, data)`
  - Method `.delete_hypothesis(hypothesis_id)`
  - Method `.create_article(data)`
  - Method `.get_articles(domain, article_type)`
  - Method `.get_article(article_id)`
  - Method `.delete_article(article_id)`
  - Method `.create_study_design(data)`
  - Method `.get_study_designs(hypothesis_id, status)`
  - Method `._study_dict(row)`
  - Method `.get_study_design(study_id)`
  - Method `.update_study_design(study_id, data)`
  - Method `.delete_study_design(study_id)`
  - Method `.create_finding(data)`
  - Method `.get_findings(hypothesis_id, study_id)`
  - Method `.get_finding(finding_id)`
  - Method `.update_finding(finding_id, data)`
  - Method `.delete_finding(finding_id)`
  - Method `.create_intervention(data)`
  - Method `.get_interventions()`
  - Method `.get_intervention(intervention_id)`
  - Method `.update_intervention(intervention_id, data)`
  - Method `.delete_intervention(intervention_id)`
  - Method `.create_metric(data)`
  - Method `.get_metrics()`
  - Method `.delete_metric(metric_id)`
  - Method `.log_metric(data)`
  - Method `.get_metric_logs(intervention_id, metric_name)`
  - Method `.delete_metric_log(log_id)`
  - Method `.get_metric_names_for_intervention(intervention_id)`
  - Method `.log_event(data)`
  - Method `.get_events(intervention_id)`
  - Method `.delete_event(event_id)`
  - Method `.export_csv(intervention_id)` - *Return a CSV string of all metric logs for an intervention.*
  - Method `.get_stats()`

**Functions:**
- `def _now()`
- `def get_research_manager_instance()`

---

### [`core/routers.py`](core/routers.py)
**Size**: `3368` lines (`147403` bytes)

**Functions:**
- `def format_reasoning_content(content)` - *Extracts the actual content from a raw LLM response dictionary string.*
- `def _legacy_hardware_id_path()` - *The nested path some builds used (``<state dir>/data/hardware_id.txt``).*
- `def _adopt_legacy_hardware_id(id_file, legacy_file)`
- `def get_hardware_id()`
- `def _decoder_key_path()` - *Where the decoder key lives; every reader and writer must use this one location.*
- `def _dashboard_paths()` - *The state files the dashboard counts, at the locations the app actually writes them.*
- `def get_decoder()` - *Load or generate the local decoder key (32 random bytes).*
- `def make_claim(item_id)` - *Compute a cryptographic ownership claim for a marketplace item.*
- `def get_public_handle()` - *A stable, opaque public identifier for this user, shown in the marketplace as the uploader.*
- `def make_vote_id(item_id)` - *Derive a stable anonymous vote identity for an item.*
- `def make_report_id()` - *Stable anonymous reporter identity — same machine always produces the same ID.*
- `def _load_blocklist()`
- `def _save_blocklist(handles)`
- `def _load_profile()` - *Return local marketplace profile dict (username, description).*
- `def _save_profile_field(key, value)` - *Atomically update a single field in the local marketplace profile.*
- `def _get_marketplace_username()`
- `def _get_marketplace_description()`
- `def _load_notifs()`
- `def _save_notifs(notifs)`
- `def _add_notif(notif_type, item_id, item_name, from_handle, from_username, text)` - *Append a notification to the local notifications list (capped at 200).*
- `def _is_legacy_owner(item)` - *Backward-compat check for items uploaded before the decoder system.*
- `def is_item_owner(item)` - *Full ownership check: claim-based (new) with legacy fallback (old).*
- `async def read_root(request, module_manager, settings_man)`
- `async def get_dashboard(request)`
- `async def get_dashboard_stats(request)`
- `async def get_recent_sessions(request)`
- `async def get_navbar(request, module_manager, settings_man)`
- `async def get_llm_status(request, llm)`
- `async def list_modules(request, module_manager)`
- `async def get_module_details(request, module_id, module_manager, settings_man)`
- `async def get_default_prompt(module_id, module_manager)` - *Returns the default prompt for reflection or planner modules as plain text.*
- `async def save_module_config(request, module_id, module_manager)`
- `async def reorder_modules(request, order, module_manager)`
- `async def toggle_module(request, module_id, action, module_manager)`
- `async def ai_flow_page(request, module_manager, settings_man)`
- `async def get_flow_data(flow_id)`
- `async def validate_flow(flow_id, request)` - *Validates a flow for potential issues before execution.*
- `async def simulate_flow_route(flow_id, request)` - *Deterministic pre-flight analysis: structure, external effects and a cost envelope.*
- `async def save_ai_flow(request, name, nodes, connections, bridges, flow_id)`
- `async def rename_flow(request, flow_id, name, settings_man)`
- `async def set_active_flow(request, flow_id, action, settings_man)`
- `async def stop_active_flow(request, settings_man)` - *Stops all active flows by clearing the active flows list and canceling tasks.*
- `async def delete_flow(request, flow_id, settings_man)`
- `async def get_flow_versions(flow_id)` - *Returns the version history for a flow (metadata only, newest first).*
- `async def get_flow_versions_partial(request, flow_id)` - *Returns the version history panel HTML for HTMX swap.*
- `async def rollback_flow_version(request, flow_id, version, settings_man)` - *Restores a flow to the specified version snapshot.*
- `async def make_active_flow_default(request)` - *Overwrites the default flow with the currently active flow.*
- `async def run_flow_node(flow_id, node_id, request, background_tasks)` - *Manually triggers a specific node in a flow.*
- `async def get_marketplace(request, settings_man, module_manager)`
- `async def upload_marketplace_item(name, description, type, file, image)`
- `async def get_marketplace_image(item_id)`
- `async def download_marketplace_item(item_id)`
- `async def marketplace_item_detail(request, item_id, module_manager, settings_man)`
- `async def marketplace_item_file(item_id, path)`
- `async def preview_marketplace_item(item_id)`
- `async def update_marketplace_item(item_id, description, update_notes, file, image)` - *Owner publishes a new version. Bumps version counter, optionally replaces file/image/description.*
- `async def delete_marketplace_item(item_id)`
- `async def report_marketplace_item(item_id, reason)` - *Add a user report with description to a marketplace item.*
- `async def block_uploader(handle)`
- `async def unblock_uploader(handle)`
- `async def marketplace_uploader_profile(request, handle, preview, module_manager, settings_man)` - *Public uploader profile: all their items, stats, and reports.*
- `async def vote_marketplace_item(item_id, direction)`
- `async def add_marketplace_comment(item_id, text)` - *Append a comment to a marketplace item. Comments persist through owner updates.*
- `async def delete_marketplace_comment(item_id, comment_id)` - *Delete a comment if it belongs to the current user.*
- `async def import_marketplace_item_to_neurocore(item_id, module_manager, settings_man)` - *Import a marketplace item directly into NeuroCore (no file saved to user's disk).*
- `async def set_marketplace_username(username)` - *Set the user's display name shown alongside their handle.*
- `async def set_marketplace_description(description)` - *Set the user's short bio shown on their uploader profile.*
- `async def get_marketplace_handles()` - *Return all distinct uploader handles + display names from the catalog.*
- `async def get_marketplace_notifications()` - *Return all local marketplace notifications, newest first.*
- `async def mark_all_notifications_read()` - *Mark all notifications as read.*
- `async def mark_notification_read(notif_id)` - *Mark a single notification as read.*
- `async def clear_all_notifications()` - *Delete all notifications.*
- `async def import_decoder_key(key)` - *Import a decoder key from another machine.*
- `async def delete_skill(skill_id)`
- `async def save_skill(skill_id, instructions)`
- `async def create_skill(name, description, category, instructions)`
- `async def upload_skill_to_marketplace(skill_id)`
- `async def get_settings(request, settings_man, module_manager)`
- `async def get_system_time(request)`
- `async def get_settings_modules_nav(request, module_manager)`
- `async def get_footer(request, settings_man)`
- `async def export_config(settings_man)` - *Downloads the current settings.json file, without plaintext secrets by default.*
- `async def import_config(request, file, settings_man)` - *Imports settings from a JSON file.*
- `async def export_flows()` - *Downloads the current ai_flows.json file.*
- `async def import_flows(request, file)` - *Imports flows from a JSON file.*
- `async def reset_settings(request, confirm, settings_man)` - *Resets settings to defaults.*
- `async def save_settings_route(request, settings_man)`
- `async def debug_page(request, settings_man, module_manager)`
- `async def get_debug_logs(request, _)`
- `async def get_debug_events(request, since, _)`
- `async def get_agent_summary(request, since, limit, _)` - *Get a summary of the last agent session trace events.*
- `async def get_debug_summary(request, since, limit, _)` - *Get the agent summary panel for the debug page.*
- `async def get_token_stats_json(request, _)` - *Return LLM token usage counters (total + per-model) as JSON.*
- `async def get_node_latency_json(_)` - *Return node latency percentiles and recent p99 stall findings as JSON.*
- `async def get_token_stats_partial(request, _)` - *Return the token stats panel as an HTML partial for HTMX polling.*
- `async def clear_debug_logs(request, _)`
- `async def goals_page(request, module_manager, settings_man)`
- `async def get_default_config(module_id, module_manager)` - *Returns the default configuration for modules that support loading defaults (e.g., agent_loop).*
- `async def research_source_list()` - *Allowlisted literature sources and their licence terms.*
- `async def research_source_fetch(request)` - *Search one allowlisted source; provenance is returned with every item.*
- `async def get_research(request, settings_man, module_manager)`
- `async def get_research_stats(request, rm)`
- `async def list_hypotheses(request, status, domain, rm)`
- `async def edit_hypothesis_form(request, hypothesis_id, rm)`
- `async def create_hypothesis(request, title, statement, hypothesis_type, domain, independent_variable, dependent_variable, notes, rm)`
- `async def update_hypothesis(request, hypothesis_id, title, statement, hypothesis_type, status, domain, independent_variable, dependent_variable, notes, rm)`
- `async def delete_hypothesis(hypothesis_id, rm)`
- `async def list_articles(request, domain, article_type, q, rm)`
- `async def create_article(request, title, authors, year, journal, doi, url, abstract, article_type, domain, notes, rm)`
- `async def delete_article(article_id, rm)`
- `async def list_study_designs(request, hypothesis_id, status, rm)`
- `async def create_study_design(request, title, study_type, population, sample_size, sampling_method, design_type, control_group, randomization, blinding, notes, rm)`
- `async def update_study_design(study_id, status, notes, rm)`
- `async def delete_study_design(study_id, rm)`
- `async def list_findings(request, hypothesis_id, study_id, significance, rm)`
- `async def create_finding(request, title, summary, conclusion, statistical_test, p_value, effect_size, significance, status, notes, rm)`
- `async def delete_finding(finding_id, rm)`
- `async def draft_paper(title, domain, section, context, style, llm, settings_man)`
- `async def get_experiments(request, rm)`
- `async def create_intervention(request, name, description, start_date, end_date_projected, end_date_actual, notes, rm)`
- `async def update_intervention(intervention_id, end_date_actual, name, description, start_date, end_date_projected, notes, rm)`
- `async def delete_intervention(intervention_id, rm)`
- `async def export_intervention_csv(intervention_id, rm)`
- `async def list_metrics_html(request, rm)`
- `async def list_metrics_json(rm)`
- `async def create_metric(name, description, unit, rm)`
- `async def delete_metric(metric_id, rm)`
- `async def log_metric(intervention_id, metric_name, log_date, value, notes, rm)`
- `async def get_metric_logs(request, intervention_id, metric, rm)`
- `async def delete_metric_log(log_id, rm)`
- `async def log_event(intervention_id, name, event_datetime, severity, notes, rm)`
- `async def get_events(request, intervention_id, rm)`
- `async def delete_event(event_id, rm)`
- `async def analyze_experiment(intervention_id, metric_name, start_date, baseline_days, intervention_days, alpha, rm)`
- `async def analyze_statistics(type, data_a, data_b, mu, alpha, confidence)`

---

### [`core/schemas/__init__.py`](core/schemas/__init__.py)
> *Scientific Schemas for NeuroCore*

**Size**: `22` lines (`535` bytes)

---

### [`core/schemas/article.py`](core/schemas/article.py)
> *Article Schema*

**Size**: `199` lines (`4830` bytes)

**Classes:**
- `class Article` - *Represents an academic article.*

---

### [`core/schemas/finding.py`](core/schemas/finding.py)
> *Finding Schema*

**Size**: `179` lines (`4646` bytes)

**Classes:**
- `class Finding` - *Represents a research finding.*

---

### [`core/schemas/hypothesis.py`](core/schemas/hypothesis.py)
> *Hypothesis Schema*

**Size**: `114` lines (`3131` bytes)

**Classes:**
- `class Hypothesis` - *Represents a scientific hypothesis.*

---

### [`core/schemas/study_design.py`](core/schemas/study_design.py)
> *Study Design Schema*

**Size**: `169` lines (`4592` bytes)

**Classes:**
- `class StudyDesign` - *Represents a research study design.*

---

### [`core/security.py`](core/security.py)
> *Control-plane security policy for NeuroCore.*

**Size**: `312` lines (`12967` bytes)

**Classes:**
- `class ControlPlaneGuard` - *CSRF origin check plus an opt-in API token for non-loopback clients.*
  - Method `async def .dispatch(request, call_next)`

**Functions:**
- `def _env_flag(name)` - *True when the named environment variable holds an affirmative value.*
- `def _env_list(name)` - *The comma-separated, non-empty, lower-cased entries of an environment variable.*
- `def test_mode()` - *Whether the test suite is running (``NEUROCORE_TEST_MODE``, set by tests/conftest.py).*
- `def bind_host()` - *The interface the server binds to; loopback unless NEUROCORE_HOST opts in.*
- `def bind_port()` - *The TCP port the server binds to; 8000 when NEUROCORE_PORT is missing or invalid.*
- `def reload_enabled()` - *Auto-reload is off unless NEUROCORE_RELOAD explicitly turns it on.*
- `def allowed_hosts()` - *Loopback hosts (plus the test host in test mode) and any NEUROCORE_ALLOWED_HOSTS opt-in.*
- `def export_includes_secrets()` - *Secrets stay out of the config export unless NEUROCORE_EXPORT_SECRETS opts in.*
- `def _is_sensitive_key(key)` - *Whether a mapping key names a secret (its value must be masked or dropped).*
- `def redact_secrets(values)` - *Return `values` with every sensitive entry replaced by REDACTED, recursively.*
- `def strip_secrets(values)` - *Return `values` with every sensitive key removed, recursively.*
- `def api_token()` - *The control-plane API token; empty means non-loopback clients are rejected.*
- `def unauthenticated_remote_allowed()` - *Whether the operator explicitly accepted a non-loopback bind without a token.*
- `def deployment_is_remote()` - *Whether the configured bind interface is reachable from another machine.*
- `def validate_deployment_security()` - *Refuse a non-loopback bind that has no credential and no explicit opt-out.*
- `def trusted_origins()` - *Extra origins accepted for state-changing requests (NEUROCORE_TRUSTED_ORIGINS).*
- `def _is_loopback_host(host)` - *Whether `host` names this machine (loopback or the test client).*
- `def _origin_tuple(scheme, hostname, port)` - *A normalized (scheme, host, effective-port) origin triple.*
- `def _origin_allowed(origin, request)` - *Whether a browser `Origin` header may make a state-changing request to `request`.*
- `def _client_is_loopback(request)` - *Whether the connecting peer is this machine (so the API token is not required).*
- `def _token_matches(request, expected)` - *Constant-time check of the X-API-Token header or an Authorization: Bearer token.*
- `def install_security_middleware(app)` - *Attach the trusted-host guard, the CSRF check and the optional API token guard.*

---

### [`core/session_manager.py`](core/session_manager.py)
> *Session Manager for NeuroCore*

**Size**: `1037` lines (`36537` bytes)

**Classes:**
- `class TraceWriter` - *Append-only JSON Lines trace writer.*
  - Method `.__init__(trace_file)`
  - Method `._ensure_directory()` - *Ensure trace file directory exists.*
  - Method `.append(event)` - *Append a JSON event to the trace file.*
  - Method `.read_all()` - *Read all trace events from file.*
  - Method `.read_since(timestamp)` - *Read trace events since given timestamp.*
  - Method `._parse_timestamp(ts)` - *Parse ISO timestamp to unix timestamp.*
  - Method `.clear()` - *Clear trace file (for testing).*
- `class EpisodeState` - *Represents the persistent state of an episode (long-running autonomous task).*
  - Method `.__init__(episode_id, session_id, phase, replan_count, completed_steps, budgets, current_step, plan, messages, input_data, checkpoints, metadata)`
  - Method `.to_dict()` - *Convert to dictionary for serialization.*
  - Method `.from_dict(data)` - *Create EpisodeState from dictionary.*
  - Method `.add_checkpoint()` - *Add a checkpoint with current timestamp.*
  - Method `.update_phase(new_phase)` - *Update the current phase.*
- `class SessionManager` - *Manages agent session persistence and tracing.*
  - Method `.__init__(session_file, trace_file, episode_dir)`
  - Method `._ensure_directory()` - *Ensure session file directory exists.*
  - Method `._load_session()` - *Load existing session or create new one.*
  - Method `._create_new_session()` - *Create a new session.*
  - Method `._save_session_unlocked()` - *Save session (must be called with lock held).*
  - Method `.load_or_create_session()` - *Get current session_id, creating one if needed.*
  - Method `.get_session_id()` - *Get current session_id.*
  - Method `.get_state()` - *Get current session state.*
  - Method `.update_state(updates)` - *Update session state.*
  - Method `.save_state()` - *Persist session state to disk.*
  - Method `.increment_tick()` - *Increment and return current tick.*
  - Method `.get_tick()` - *Get current tick.*
  - Method `.reset_session()` - *Create a new session (for testing or new conversation).*
  - Method `._make_timestamp()` - *Generate ISO timestamp.*
  - Method `.log_tool_call(tool, input_data)` - *Log a tool call event.*
  - Method `.log_tool_result(tool, output, success, duration_ms, error)` - *Log a tool result event.*
  - Method `.log_llm_call(model, tokens, latency_ms)` - *Log an LLM call event.*
  - Method `.log_agent_event(event_type, data)` - *Log a general agent event.*
  - Method `.log_rlm_event(event_type, data)` - *Log an RLM-specific event.*
  - Method `.get_trace()` - *Get all trace events.*
  - Method `.get_trace_since(timestamp)` - *Get trace events since given timestamp.*
  - Method `.get_trace_summary(limit, since)` - *Get a summary of trace events.*
  - Method `.trace_context(operation)` - *Context manager for tracing operations with timing.*
  - Method `._ensure_episode_directory()` - *Ensure episode file directory exists.*
  - Method `._load_episode_from_file()` - *Load episode state from file.*
  - Method `._save_episode_to_file(episode_data)` - *Save episode state to file (atomic write).*
  - Method `.create_episode(input_data, budgets, metadata)` - *Create a new episode for tracking a long-running autonomous task.*
  - Method `._save_episode_by_id_to_file(episode_id, episode_data)` - *Save episode state to file by episode ID (atomic write).*
  - Method `.save_episode_state(phase, replan_count, completed_steps, budgets, current_step, plan, messages, input_data, add_checkpoint, metadata)` - *Save/update the current episode state.*
  - Method `.load_episode_state()` - *Load the current episode state from disk.*
  - Method `.get_episode_state()` - *Get the current episode state (from memory cache).*
  - Method `.get_or_create_episode(input_data, budgets)` - *Get existing episode or create new one.*
  - Method `.clear_episode()` - *Clear the current episode state (for starting fresh).*
  - Method `.is_episode_active()` - *Check if there's an active episode that can be resumed.*
  - Method `.get_episode_summary()` - *Get a summary of the current episode.*
  - Method `._get_episode_path(episode_id)` - *Get the file path for a specific episode.*
  - Method `.list_episodes()` - *List all available episodes.*
  - Method `.load_episode_by_id(episode_id)` - *Load a specific episode by ID.*
  - Method `.save_episode_by_id(episode_id, phase, **kwargs)` - *Save a specific episode by ID (creates if doesn't exist).*
  - Method `.delete_episode(episode_id)` - *Delete a specific episode.*

**Functions:**
- `def get_session_manager()` - *Get or create the global SessionManager instance.*

---

### [`core/settings.py`](core/settings.py)
**Size**: `213` lines (`9285` bytes)

**Classes:**
- `class SettingsManager`
  - Method `.__init__(file_path)`
  - Method `.load_settings()`
  - Method `.save_settings(new_settings)`
  - Method `._validate_settings(new_settings)` - *Validate settings before saving. Returns only the validated new settings.*
  - Method `._parse_bool(value, field_name)` - *Parse a value to boolean with strict handling.*
  - Method `.get(key, default)`

---

### [`core/statistics_engine.py`](core/statistics_engine.py)
> *Statistics Engine*

**Size**: `771` lines (`28591` bytes)

**Functions:**
- `def parse_data(raw)` - *Parse a user-supplied string of numbers into a float list.*
- `def _fmt(v, decimals)` - *Format a float to fixed decimals, returning None if v is None.*
- `def _sig_stars(p)`
- `def _interpret_effect_d(d)`
- `def _interpret_r(r)`
- `def descriptive_stats(data)` - *Compute comprehensive descriptive statistics for one dataset.*
- `def confidence_interval(data, confidence)` - *Compute the confidence interval for the mean.*
- `def one_sample_ttest(data, mu, alpha)` - *Test whether the sample mean differs from a hypothesized value mu.*
- `def two_sample_ttest(data1, data2, equal_var, alpha)` - *Welch's (or Student's) t-test for two independent samples.*
- `def paired_ttest(data1, data2, alpha)` - *Paired-samples t-test (before/after or matched pairs).*
- `def pearson_correlation(data1, data2, alpha)` - *Pearson product-moment correlation.*
- `def spearman_correlation(data1, data2, alpha)` - *Spearman rank-order correlation (non-parametric).*
- `def chi_square_goodness(observed, expected, alpha)` - *Chi-square goodness-of-fit test. If expected is None, assumes uniform distribution.*
- `def one_way_anova(*groups, alpha)` - *One-way ANOVA across multiple groups.*
- `def mann_whitney(data1, data2, alpha)` - *Mann-Whitney U test (non-parametric, does not assume normality).*
- `def power_analysis(effect_size, n, alpha, test_type)` - *Post-hoc power analysis for t-tests.*
- `def bootstrap_mean_diff_ci(data1, data2, n_boot, confidence, seed)` - *Bootstrap 95% CI for the difference in means (data1 - data2).*
- `def linear_trend(values, alpha)` - *Fit a linear trend to a sequence of values.*
- `def intervention_analysis(baseline, intervention, alpha, n_boot)` - *Full comparison of baseline vs intervention period.*

---

### [`core/structured_output.py`](core/structured_output.py)
> *Structured Output Enforcement Module*

**Size**: `342` lines (`13463` bytes)

**Classes:**
- `class StructuredOutputError` - *Exception raised when structured output cannot be obtained*
  - Method `.__init__(message, schema, attempts, last_error)`
  - Method `.__str__()`

**Functions:**
- `async def structured_completion(messages, schema, max_retries, temperature, max_tokens, model, llm_bridge, timeout)` - *LLM call that guarantees schema-valid output.*
- `async def structured_completion_with_fallback(messages, schema, max_retries, fallback_value, **kwargs)` - *Structured completion that returns fallback value on failure instead of raising.*
- `def create_json_schema(schema)` - *Create a JSON schema dictionary from a Pydantic model.*
- `async def structured_chat(messages, response_schema, system_prompt, **kwargs)` - *Simplified interface for structured chat completions.*

---

### [`main.py`](main.py)
**Size**: `193` lines (`8029` bytes)

**Functions:**
- `async def verify_debug_key(x_debug_key)` - *Verify debug API key for protected debug endpoints.*
- `def _fire_repeater_nodes(app, flow_id, flow, is_auto_start)` - *Fire all repeater nodes in a flow.*
- `def background_task_callback(task, flow_id, node_id, app)`
- `async def lifespan(app)` - *Handles startup and shutdown events for the application.*
- `async def debug_fire_flow()`

---

### [`modules/agent_loop/loop_guard.py`](modules/agent_loop/loop_guard.py)
> *Deterministic repetition and deadlock detection for an agent loop.*

**Size**: `97` lines (`3861` bytes)

**Classes:**
- `class LoopGuard` - *Watch a bounded call history and return a warning or an abort decision.*
  - Method `.__init__(history, repeat_limit)`
  - Method `.observe(tool_name, arguments)` - *Record one call; return ``None`` or a decision with ``action`` warn/abort.*
  - Method `._reason(signature)`

**Functions:**
- `def normalize_arguments(arguments)` - *Stable representation of tool arguments with volatile identifiers removed.*
- `def call_signature(tool_name, arguments)`

---

### [`modules/agent_loop/node.py`](modules/agent_loop/node.py)
**Size**: `5776` lines (`266519` bytes)

**Classes:**
- `class AgentBaseExecutor` - *Shared infrastructure for all agent loop variants.*
  - Method `.__init__()`
  - Method `._estimate_tokens(text)` - *Rough token estimate: ~4 characters per token.*
  - Method `._estimate_messages_tokens(messages)` - *Estimate total token count across all messages.*
  - Method `._truncate_messages(messages, max_context_tokens, preserve_system)` - *Truncate messages to fit within token budget.*
  - Method `async def ._compact_messages(messages, keep_last)` - *LLM-summarize old agent messages to free up context window.*
  - Method `._load_tools()` - *Load tool definitions from tools.json with mtime-based caching.*
  - Method `._build_system_prompt(input_data, config)` - *Build system prompt from upstream context fields.*
  - Method `async def ._llm_with_retry(messages, model, temperature, max_tokens, tools, max_retries, retry_delay)` - *Call LLM with exponential backoff retry on failure.*
  - Method `._log_tool_call(func_name, args)` - *Log a tool call to the session trace and return start_time.*
  - Method `._log_tool_result(func_name, output, success, start_time, error)` - *Log a tool result to the session trace.*
  - Method `._log_llm_call(model)` - *Log an LLM call to the session trace and return start_time.*
  - Method `._log_llm_result(model, start_time, response)` - *Log LLM latency + token usage to the session trace.*
  - Method `._log_agent_event(event_type, data)` - *Log a general agent lifecycle event to the session trace.*
  - Method `._log_rlm_event(event_type, data)` - *Log an RLM-specific event to the session trace.*
  - Method `._log_replan_event(result)` - *Log a replan event when replan_needed is True, or agent_end on success.*
  - Method `._extract_user_content(messages)` - *Extract first non-empty user message content from a message list.*
  - Method `._classify_content(content)` - *Classify content type for REPL environment metadata.*
  - Method `async def ._execute_sub_call(args, repl_state)` - *Execute SubCall as an async built-in (not via sandbox exec).*
- `class AgentLoopExecutor`
  - Method `._load_tool_library()` - *Load tool implementations from library directory with file-level mtime caching.*
  - Method `async def ._execute_tool(tool_call, tool_library)` - *Execute a single tool call in the sandbox and return a tool result message.*
  - Method `async def ._run_agent_loop(llm_messages, tools_list, tool_library, model, temperature, max_tokens, max_iterations, max_llm_retries, retry_delay, tool_error_strategy, trace, stream_queue, compact_threshold, compact_keep_last, max_tool_output_chars, thinking_steps, loop_guard)` - *Core agent loop: LLM â†” Tool execution until no more tool calls or max_iterations.*
  - Method `async def .receive(input_data, config)` - *Agent loop: repeatedly calls LLM with tools until no more tool calls.*
  - Method `async def .send(processed_data)`
- `class REPLEnvironmentExecutor` - *REPL Environment Node — initializes a persistent REPL state.*
  - Method `async def .receive(input_data, config)`
  - Method `._build_repl_system_prompt(metadata)`
  - Method `async def .send(processed_data)`
- `class RLMAgentLoopExecutor` - *RLM (Recursive Language Model) variant of the agent loop.*
  - Method `._load_tool_library()` - *Load RLM tool implementations from both LIBRARY_DIR and RLM_LIBRARY_DIR.*
  - Method `._resolve_tool_name(func_name, tool_library)` - *Resolve tool name with case-insensitive and camelCase↔snake_case matching.*
  - Method `._build_repl_system_prompt(metadata)`
  - Method `._init_repl_state(messages)` - *Initialize REPL state from messages.*
  - Method `async def ._execute_tool(tool_call, tool_library, repl_state)` - *Execute a tool call — SubCall is intercepted as an async built-in.*
  - Method `async def ._run_rlm_loop(initial_messages, repl_state, tools_list, tool_library, model, config, trace, stream_queue, thinking_steps)` - *Core RLM loop: LLM generates code → executes tools → only metadata goes back.*
  - Method `async def .receive(input_data, config)` - *RLM Agent Loop: processes arbitrarily long inputs via REPL environment.*
  - Method `async def .send(processed_data)`
- `class HybridAgentExecutor` - *Hybrid Agent Loop — standard tool calling with RLM-style adaptive output routing.*
  - Method `._build_variable_system_note(threshold)` - *Return a brief system-level instruction explaining the variable storage system.*
  - Method `._load_tool_library()` - *Load implementations from both LIBRARY_DIR and RLM_LIBRARY_DIR.*
  - Method `._variable_inventory_msg(repl_state)` - *Return an ephemeral system message listing currently stored variables.*
  - Method `async def ._execute_tool(tool_call, tool_library, repl_state, large_output_threshold)` - *Execute one tool call with adaptive output routing.*
  - Method `._is_unhelpful_result(content)` - *Return True when a tool result is too sparse to be useful.*
  - Method `async def ._run_hybrid_loop(llm_messages, tools_list, tool_library, repl_state, model, temperature, max_tokens, max_iterations, max_llm_retries, retry_delay, tool_error_strategy, large_output_threshold, trace, stream_queue, thinking_steps, max_consecutive_unhelpful)` - *Core hybrid loop.  Returns (final_response, iterations, had_tool_error).*
  - Method `async def .receive(input_data, config)` - *Hybrid Agent Loop node executor.*
  - Method `async def .send(processed_data)`
- `class GoalPursuitExecutor` - *Goal Pursuit Agent — hybrid agent loop with integrated planning, per-step*
  - Method `async def ._execute_tool(tool_call, tool_library, repl_state, large_output_threshold)` - *Intercept AskUser and DecomposeGoal before delegating to parent.*
  - Method `._topo_sort_steps(steps)` - *Kahn's topological sort on a step list.*
  - Method `async def ._parse_plan_response(content, max_steps, step_offset)` - *Parse LLM JSON plan response into a list of step dicts.*
  - Method `async def ._create_plan(user_request, config, lessons, tools_summary, pattern_insights, planning_assumptions)` - *Call LLM to create an initial step-by-step plan.*
  - Method `async def ._generate_ask_user_assumption(question, repl_state, config)` - *Generate a reasonable assumed answer when the user is unavailable.*
  - Method `async def ._evaluate_step(step, execution_result, config, tool_history, user_goal)` - *LLM judge: did the step's result actually satisfy its goal?*
  - Method `async def ._replan(original_request, completed_steps, step_results, failed_step, failure_reason, config, tool_history, repl_state)` - *Generate a revised plan for remaining work after a step failure.*
  - Method `._build_plan_context(completed_steps, remaining_steps, current_idx, step_results)` - *Format a progress-aware plan context string.*
  - Method `._build_step_system_prompt(step, plan_context, input_data, config, large_output_threshold, repl_state)` - *Assemble the system prompt for a single step execution.*
  - Method `async def ._save_run_reflection(goal, completed_steps, step_results, replan_count, goal_pursuit_trace, succeeded)` - *Save a structured post-run lesson to long-term memory so future*
  - Method `async def ._fetch_goal_lessons(config)` - *Fetch goal_reflection memories from the store for planning context.*
  - Method `async def ._compress_lessons(lessons, config)` - *Chunk and summarize past goal_reflection lessons so they fit in the planning prompt.*
  - Method `async def ._compress_step_results(repl_state, config)` - *Pre-compress large step_N_result variables before synthesis.*
  - Method `async def ._assess_task_clarity(user_goal, config)` - *Lightweight pre-plan assessment of task specificity.*
  - Method `async def ._mid_run_assessment(completed_steps, remaining_steps, repl_state, config, user_goal)` - *Self-assessment run once after the first wave completes.*
  - Method `async def ._extract_failure_patterns(lessons_list, config)` - *Infer recurring patterns from past goal-execution lessons.*
  - Method `async def ._synthesize_results(completed, results, repl_state, config, user_goal, tools_list, tool_library, model, max_tokens, tool_error_strategy, large_output_threshold, goal_pursuit_trace, stream_queue, thinking_steps)` - *Synthesize step results into a final coherent answer.*
  - Method `async def ._reason_about_step(step, repl_state, config, user_goal, tools_list, reasoning_history)` - *Brief chain-of-thought LLM call before a step executes.*
  - Method `._context_pressure(repl_state, max_context_tokens)` - *Estimate context pressure as a fraction in [0.0, 1.0].*
  - Method `async def .receive(input_data, config)` - *Goal pursuit loop.*
  - Method `async def .send(processed_data)`

**Functions:**
- `def _charge_llm_usage(response)` - *Charge the run budget with the tokens the provider reported.*
- `def _redact_tool_output(tool_result)` - *Redact credential-shaped text before a tool result enters the context.*
- `async def get_executor_class(node_type_id)`

---

### [`modules/annotations/__init__.py`](modules/annotations/__init__.py)
**Size**: `1` lines (`20` bytes)

---

### [`modules/annotations/node.py`](modules/annotations/node.py)
**Size**: `20` lines (`849` bytes)

**Classes:**
- `class CommentExecutor` - *Executor for the Comment Node.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/autonomous_agent/__init__.py`](modules/autonomous_agent/__init__.py)
> *Autonomous Agent Module - Main entry point.*

**Size**: `42` lines (`1263` bytes)

**Functions:**
- `def __getattr__(name)`

---

### [`modules/autonomous_agent/core/communicator.py`](modules/autonomous_agent/core/communicator.py)
> *Communication interface for the Autonomous Agent.*

**Size**: `442` lines (`16140` bytes)

**Classes:**
- `class MessagePriority`
- `class MessageQueue` - *Priority queue for outgoing messages.*
  - Method `.add(message)` - *Add message maintaining priority order.*
  - Method `.pop()` - *Get highest priority message.*
  - Method `.peek()` - *View highest priority message without removing.*
  - Method `.__len__()`
- `class BaseChannel` - *Base class for communication channels.*
  - Method `.__init__(config)`
  - Method `async def .send(message)` - *Send message through this channel.*
  - Method `._check_rate_limit()` - *Check if rate limit allows sending.*
  - Method `._format_message(message)` - *Format message for channel (override in subclass).*
  - Method `async def ._send_impl(formatted)` - *Actual sending implementation (override in subclass).*
- `class ChatChannel` - *Web chat channel (NeuroCore chat UI).*
  - Method `.__init__(config, message_callback)`
  - Method `async def ._send_impl(formatted)` - *Send to chat UI via callback or queue.*
  - Method `.get_pending()` - *Get pending messages for polling.*
- `class TelegramChannel` - *Telegram messaging channel.*
  - Method `.__init__(config)`
  - Method `._format_message(message)` - *Format for Telegram API.*
  - Method `async def ._send_impl(formatted)` - *Send via Telegram Bot API.*
- `class WebhookChannel` - *Generic webhook channel.*
  - Method `.__init__(config)`
  - Method `._format_message(message)`
  - Method `async def ._send_impl(formatted)`
- `class EmailChannel` - *Email channel (SMTP).*
  - Method `.__init__(config)`
  - Method `._format_message(message)`
  - Method `async def ._send_impl(formatted)`
- `class AgentCommunicator` - *Handles all user-facing communication for the agent.*
  - Method `.__init__(agent_id, config)`
  - Method `._init_channels()` - *Initialize communication channels from config.*
  - Method `async def .send_message(message)` - *Send message through appropriate channel.*
  - Method `async def ._process_queue()` - *Process outgoing message queue.*
  - Method `async def .handle_incoming(incoming)` - *Process incoming user message.*
  - Method `async def .notify_user(content, channel, priority, metadata)` - *Send proactive notification to user.*
  - Method `async def .share_reflection(reflection_summary, channel)` - *Share self-reflection with user.*
  - Method `async def ._on_chat_message(formatted)` - *Buffer a chat message for the NeuroCore UI to poll.*
  - Method `._log_communication(message, result)` - *Log communication for audit trail.*
  - Method `.get_communication_log(limit)` - *Get recent communication log.*
  - Method `.get_pending_outgoing()` - *Get count of pending outgoing messages.*
  - Method `.get_pending_chat_messages()` - *Drain and return buffered chat messages.*
  - Method `async def .flush_queue()` - *Force send all queued messages.*
- `class RateLimitExceeded` - *Raised when rate limit is exceeded.*

---

### [`modules/autonomous_agent/core/flow_creator.py`](modules/autonomous_agent/core/flow_creator.py)
> *Flow Creator - Agent creates and manages its own flows dynamically.*

**Size**: `417` lines (`15671` bytes)

**Classes:**
- `class FlowCreator` - *Agent creates and manages its own flows dynamically.*
  - Method `.__init__(agent_id, sandbox, memory, config, flow_manager)`
  - Method `async def .create_flow(specification)` - *Create a new flow from specification.*
  - Method `async def ._generate_flow(spec)` - *Generate flow JSON from specification.*
  - Method `._match_pattern(spec)` - *Match specification to a known flow pattern.*
  - Method `async def ._build_from_pattern(pattern_name, spec)` - *Build flow from predefined pattern.*
  - Method `async def ._build_custom_flow(spec)` - *Build custom flow using LLM (delegated to sandbox).*
  - Method `async def .update_flow(flow_id, updates)` - *Update an existing flow.*
  - Method `async def .delete_flow(flow_id)` - *Delete a flow.*
  - Method `.get_owned_flows()` - *Get list of flows owned by this agent.*
  - Method `.get_flow(flow_id)` - *Get a specific flow.*

**Functions:**
- `def node_template_types()` - *The (moduleId, nodeTypeId) pairs the agent's flow builder may use.*

---

### [`modules/autonomous_agent/core/flow_proposer.py`](modules/autonomous_agent/core/flow_proposer.py)
> *Structured self-evolution proposals generated by an LLM.*

**Size**: `161` lines (`7411` bytes)

**Classes:**
- `class FlowProposerError` - *Raised when the model does not return a usable operation list.*
- `class FlowProposer` - *Turn a goal into validated-shape flow operations without applying them.*
  - Method `.__init__(llm, allowed_node_types, allowed_tools, max_operations)`
  - Method `async def .propose(flow, goal)` - *Ask the model for operations; return them for FlowEvolution to validate.*
  - Method `._build_prompt(flow, goal)`
  - Method `._parse_response(response)`
  - Method `._validate_operation(index, operation)`
  - Method `._check_node(index, node)`

---

### [`modules/autonomous_agent/core/memory.py`](modules/autonomous_agent/core/memory.py)
> *Memory system for the Autonomous Agent - Three-tier memory architecture.*

**Size**: `552` lines (`21560` bytes)

**Classes:**
- `class MemoryEntry` - *A single memory entry.*
- `class BaseMemory` - *Base class for memory systems.*
  - Method `.__init__(agent_id, memory_type)`
  - Method `async def .store(entry)` - *Store a memory entry.*
  - Method `async def .retrieve(memory_id)` - *Retrieve a specific memory.*
  - Method `async def .recall_by_tags(tags, limit)` - *Recall memories by tags.*
  - Method `async def .search(query, limit)` - *Simple text search (in production, use vector search).*
  - Method `async def .cleanup_expired()` - *Remove expired memories.*
  - Method `.get_stats()` - *Get memory statistics.*
  - Method `.to_dict()` - *Serialize this memory tier to JSON-compatible data.*
  - Method `.load_dict(data)` - *Replace this tier with entries produced by :meth:`to_dict`.*
- `class EpisodicMemory` - *Episodic memory - specific experiences and events.*
  - Method `.__init__(agent_id, retention_days)`
  - Method `async def .store_experience(experience)` - *Store a full experience as episodic memory.*
  - Method `async def .get_recent(days)` - *Get experiences from recent days.*
- `class SemanticMemory` - *Semantic memory - facts, knowledge, concepts.*
  - Method `.__init__(agent_id)`
  - Method `async def .store_fact(fact, category, confidence, source_experience_id)` - *Store a fact as semantic memory.*
  - Method `async def .get_facts_by_category(category)` - *Get all facts in a category.*
  - Method `async def .check_conflicts(new_fact, category)` - *Check for conflicting facts.*
  - Method `._might_conflict(fact1, fact2)` - *Simple heuristic for conflict detection.*
  - Method `.load_dict(data)` - *Restore facts and rebuild the fact index.*
- `class ProceduralMemory` - *Procedural memory - skills, patterns, procedures.*
  - Method `.__init__(agent_id, min_occurrences)`
  - Method `async def .store_pattern(pattern_name, pattern_data, occurrences, success_rate)` - *Store a learned pattern/procedure.*
  - Method `async def .get_solidified_patterns()` - *Get all solidified patterns.*
  - Method `async def .get_pattern_for_task(task_description)` - *Find relevant pattern for a task.*
  - Method `.load_dict(data)` - *Restore patterns and rebuild occurrence counts.*
- `class AgentMemory` - *Three-tier memory system for the autonomous agent.*
  - Method `.__init__(agent_id, config)`
  - Method `async def .store_experience(experience)` - *Store experience across all relevant memory systems.*
  - Method `async def .recall_relevant(context, limit)` - *Retrieve relevant memories from all systems.*
  - Method `async def ._extract_facts(experience)` - *Extract facts from experience (simplified).*
  - Method `async def ._extract_flow_pattern(experience)` - *Extract a reusable flow pattern from execution.*
  - Method `async def .consolidate()` - *Run memory consolidation (cleanup, promotion).*
  - Method `.get_all_stats()` - *Get statistics for all memory systems.*
  - Method `.to_dict()` - *Serialize all memory tiers for persistence.*
  - Method `.load_dict(data)` - *Restore all memory tiers from :meth:`to_dict` output.*

---

### [`modules/autonomous_agent/core/sandbox.py`](modules/autonomous_agent/core/sandbox.py)
> *Sandbox for the Autonomous Agent - Restricted execution environment.*

**Size**: `396` lines (`17454` bytes)

**Classes:**
- `class SandboxViolation` - *Raised when agent attempts unauthorized action.*
- `class RateLimitExceeded` - *Raised when rate limit is exceeded.*
- `class FlowValidationError` - *Raised when flow specification is invalid.*
- `class ActionPolicy` - *Policy for a specific action.*
- `class AgentSandbox` - *Sandboxed execution environment for the autonomous agent.*
  - Method `.__init__(config)`
  - Method `._apply_config_limits(sandbox_config)` - *Apply the configured limits to the default policies.*
  - Method `.validate_action(action_type)` - *Check if action is allowed and return its policy.*
  - Method `.check_rate_limit(action_key, limit, window_seconds)` - *Check if rate limit allows this action.*
  - Method `.validate_flow_spec(spec)` - *Validate flow specification against sandbox limits.*
  - Method `.validate_tool(tool_name)` - *Check if tool is allowed.*
  - Method `.validate_config_key(key)` - *Check if config key can be modified.*
  - Method `.validate_inspect_field(field)` - *Check if self-inspection field is allowed.*
  - Method `.register_flow_ownership(flow_id)` - *Register that agent owns this flow.*
  - Method `.unregister_flow_ownership(flow_id)` - *Forget ownership after the agent deletes a flow.*
  - Method `.check_flow_ownership(flow_id)` - *Check if agent owns this flow.*
  - Method `.check_resource_limits(action_type, **kwargs)` - *Check if action would exceed resource limits.*
  - Method `async def .execute_action(action)` - *Execute an action with full sandbox protection.*
  - Method `async def ._dispatch_action(action, policy)` - *Dispatch action to appropriate handler.*
  - Method `async def ._handle_create_flow(action, policy)` - *Handle flow creation.*
  - Method `async def ._handle_update_flow(action, policy)`
  - Method `async def ._handle_compose_flow(action, policy)`
  - Method `async def ._handle_delete_flow(action, policy)`
  - Method `async def ._handle_execute_flow(action, policy)`
  - Method `async def ._handle_remember(action, policy)`
  - Method `async def ._handle_recall(action, policy)`
  - Method `async def ._handle_search_knowledge(action, policy)`
  - Method `async def ._handle_send_message(action, policy)`
  - Method `async def ._handle_notify_user(action, policy)`
  - Method `async def ._handle_inspect_self(action, policy)`
  - Method `async def ._handle_modify_config(action, policy)`
  - Method `async def ._handle_use_tool(action, policy)`
  - Method `async def ._handle_reflect(action, policy)`
  - Method `._log_action(action)` - *Log action for audit trail.*
  - Method `._update_resource_usage(action_type, result)` - *Update resource usage tracking.*
  - Method `.get_audit_log(limit)` - *Get recent audit log entries.*
  - Method `.get_resource_usage()` - *Get current resource usage.*
  - Method `.get_owned_flows()` - *Get list of flow IDs owned by agent.*

---

### [`modules/autonomous_agent/core/self_model.py`](modules/autonomous_agent/core/self_model.py)
> *Self-Model and Reflection system for the Autonomous Agent.*

**Size**: `399` lines (`14878` bytes)

**Classes:**
- `class NeuroCoreStatus` - *Current status of NeuroCore as perceived by agent.*
- `class GoalProgress` - *Progress on a specific goal.*
- `class NeuroCoreStateTracker` - *Tracks NeuroCore system state from agent's perspective.*
  - Method `.__init__()`
  - Method `async def .capture()` - *Capture current NeuroCore state.*
  - Method `.get_cached()` - *Get last captured state if still fresh.*
- `class BeliefSystem` - *Agent's belief system - what it believes about the world.*
  - Method `.__init__()`
  - Method `.add_belief(key, content, confidence, source)` - *Add or update a belief.*
  - Method `.get_belief(key)` - *Get a belief.*
  - Method `.update_confidence(key, new_confidence)` - *Update belief confidence.*
  - Method `.get_high_confidence_beliefs(threshold)` - *Get beliefs above confidence threshold.*
- `class SelfModel` - *Agent's internal model of itself and its environment.*
  - Method `.__init__(agent_id, memory, sandbox, config)`
  - Method `._initialize_core_beliefs()` - *Initialize fundamental beliefs.*
  - Method `async def .reflect(perception)` - *Generate comprehensive reflection on current state.*
  - Method `async def ._assess_self()` - *Comprehensive self-assessment.*
  - Method `._compute_performance()` - *Compute performance metrics from history.*
  - Method `._identify_limitations()` - *Identify known limitations.*
  - Method `._compute_confidence()` - *Compute overall confidence level.*
  - Method `._evaluate_goals()` - *Evaluate progress on goals.*
  - Method `async def ._generate_insights(perception, neurocore_status, self_assessment)` - *Generate insights from reflection.*
  - Method `._recommend_actions(insights, goal_progress)` - *Recommend actions based on insights.*
  - Method `.record_execution(execution_record)` - *Record execution for performance tracking.*
  - Method `.get_self_summary()` - *Get summary of self-model for reporting.*

---

### [`modules/autonomous_agent/core/service.py`](modules/autonomous_agent/core/service.py)
> *Main Agent Service - Ties all components together and runs the cognitive loop.*

**Size**: `1044` lines (`44623` bytes)

**Classes:**
- `class AgentState` - *Current state of the agent.*
- `class AgentService` - *Main service running the autonomous agent.*
  - Method `.__init__(config, flow_manager, llm)`
  - Method `async def .initialize()` - *Initialize the agent and all components.*
  - Method `async def .start()` - *Start the agent's cognitive loop.*
  - Method `async def .stop()` - *Stop the agent gracefully.*
  - Method `async def ._cognitive_loop()` - *Main cognitive loop - runs continuously.*
  - Method `async def ._gather_perceptions()` - *Gather all perceptions for this cycle.*
  - Method `async def ._plan_actions(reflection, perceptions)` - *Plan actions based on reflection and perceptions.*
  - Method `async def ._plan_for_user_message(perception)` - *Plan actions for a user message.*
  - Method `async def ._extract_flow_spec(content)` - *Extract flow specification from user message.*
  - Method `._extract_flow_id(content)` - *Extract flow ID from user message.*
  - Method `._extract_flow_ids(content)` - *Extract unique flow IDs in appearance order.*
  - Method `async def ._execute_action(action)` - *Execute an action through the sandbox.*
  - Method `async def ._execute_create_flow(action, sandbox_result)` - *Execute flow creation.*
  - Method `async def ._execute_compose_flow(action, sandbox_result)` - *Compose owned member flows into a meta-flow.*
  - Method `async def ._execute_update_flow(action, sandbox_result)` - *Apply a self-evolution proposal to a flow the agent owns.*
  - Method `async def .evolve_flow(flow_id, operations)` - *Validated, versioned self-edit of an owned flow.*
  - Method `._evaluation_succeeded(result)`
  - Method `async def .evolve_and_evaluate(flow_id, operations, evaluation_input, auto_revert, evaluator)` - *Apply an edit, run it, and revert when the evaluation fails.*
  - Method `._get_proposer()`
  - Method `async def .propose_evolution(flow_id, goal)` - *Ask the model for a validated-shape edit proposal (no write).*
  - Method `async def .propose_and_evolve(flow_id, instruction, evaluation_input, auto_revert)` - *Generate operations, then apply them (with evaluation when provided).*
  - Method `async def .compose_meta_flow(name, member_flow_ids, pathways, metadata, live)` - *Combine owned flows (ganglia) into one saved meta-flow.*
  - Method `async def .rollback_flow(flow_id, version)` - *Restore an owned flow to a version and refresh the agent cache.*
  - Method `._resolve_active_flow()` - *First active flow that still exists, else the default chat flow.*
  - Method `async def ._execute_flow(action, sandbox_result)` - *Execute the requested flow, falling back to the active chat flow.*
  - Method `async def ._execute_remember(action, sandbox_result)` - *Execute memory storage.*
  - Method `async def ._execute_recall(action, sandbox_result)` - *Execute memory recall.*
  - Method `async def ._execute_inspect_self(action, sandbox_result)` - *Execute self-inspection.*
  - Method `async def ._execute_send_message(action, sandbox_result)` - *Execute sending a message.*
  - Method `async def ._learn_from_action(action, result)` - *Learn from action execution.*
  - Method `async def ._handle_action_error(action, error)` - *Handle action execution error.*
  - Method `async def ._queue_processor()` - *Background task to process message queues.*
  - Method `async def ._periodic_consolidation()` - *Periodic memory consolidation.*
  - Method `async def ._on_perception(perception)` - *Record a perception so the next cycle can inspect it.*
  - Method `._state_path()` - *Resolve the configured persistence path, if persistence is enabled.*
  - Method `async def ._load_existing_flows()` - *Adopt flows this agent created from the shared flow store.*
  - Method `async def ._load_memory()` - *Restore memory and aggregate counters from the persistence file.*
  - Method `async def ._save_state()` - *Persist memory and cycle statistics atomically.*
  - Method `async def .create_flow(spec)` - *Public API to create a flow.*
  - Method `async def .execute_flow(flow_id, input_data, timeout)` - *Execute an owned flow through the real FlowRunner.*
  - Method `async def ._run_flow(flow_id, input_data, timeout)` - *Run any stored flow; ownership is enforced by the public/caller path.*
  - Method `.activate_flow(flow_id)` - *Make an owned flow the first active flow for the app chat gateway.*
  - Method `async def .send_message(message)` - *Public API to send a message.*
  - Method `async def .notify_user(content, channel, priority)` - *Public API to notify user.*
  - Method `async def .get_self_status()` - *Get comprehensive agent status.*
  - Method `async def .handle_user_message(channel, content, user_id, metadata)` - *Handle incoming user message.*

**Functions:**
- `def create_agent_service(config_dict)` - *Factory function to create agent service from config dict.*

---

### [`modules/autonomous_agent/core/types.py`](modules/autonomous_agent/core/types.py)
> *Core types and data structures for the Autonomous Agent.*

**Size**: `192` lines (`6364` bytes)

**Classes:**
- `class ActionType` - *Types of actions the agent can take.*
- `class ChannelType` - *Communication channels.*
- `class AgentConfig` - *Configuration for the autonomous agent.*
- `class Perception` - *What the agent perceives in one cognitive cycle.*
- `class AgentMessage` - *Outgoing message from agent to user.*
- `class IncomingMessage` - *Incoming message from user.*
- `class FlowSpec` - *Specification for creating a flow.*
  - Method `.to_dict()`
- `class Reflection` - *Result of agent's self-reflection.*
- `class SelfAssessment` - *Agent's assessment of itself.*
- `class ExecutionRecord` - *Record of a flow execution for learning.*
- `class Experience` - *A complete experience for memory storage.*
- `class AgentAction` - *An action the agent decides to take.*

---

### [`modules/autonomous_agent/examples/basic_usage.py`](modules/autonomous_agent/examples/basic_usage.py)
> *Example: How to use the Autonomous Agent in NeuroCore.*

**Size**: `139` lines (`4271` bytes)

**Functions:**
- `async def main()`
- `async def simulate_user_interaction(agent)` - *Simulate user sending messages to the agent.*
- `async def programmatic_example()` - *Using agent directly without chat.*

---

### [`modules/autonomous_agent/nodes/__init__.py`](modules/autonomous_agent/nodes/__init__.py)
> *Nodes for the Autonomous Agent module.*

**Size**: `11` lines (`192` bytes)

---

### [`modules/autonomous_agent/nodes/agent_node.py`](modules/autonomous_agent/nodes/agent_node.py)
> *Autonomous Agent Node - Flow node that executes the agent's cognitive loop.*

**Size**: `188` lines (`6956` bytes)

**Classes:**
- `class AgentNodeConfig` - *Configuration for the autonomous agent node.*
- `class AutonomousAgentNode` - *Flow node that runs the autonomous agent.*
  - Method `.__init__()`
  - Method `async def .receive(input_data, config)` - *Process input through the autonomous agent.*
  - Method `async def ._initialize(config)` - *Initialize the agent service.*
  - Method `async def ._trigger_cycle(input_data)` - *Trigger a single cognitive cycle.*
  - Method `async def ._process_input(input_data)` - *Process a single input through the agent.*
  - Method `async def .send(processed_data)` - *Finalize output.*
  - Method `async def .shutdown()` - *Shutdown the agent service.*

---

### [`modules/autonomous_agent/service.py`](modules/autonomous_agent/service.py)
> *Autonomous Agent Module - Service entry point.*

**Size**: `34` lines (`909` bytes)

**Functions:**
- `async def create_service(config)` - *Create and initialize the agent service.*

---

### [`modules/brain/__init__.py`](modules/brain/__init__.py)
> *AI-Brain module: self-model, event bus, cognitive cycles and evaluations.*

**Size**: `5` lines (`130` bytes)

---

### [`modules/brain/auth.py`](modules/brain/auth.py)
> *Brain actor identity, token registry and role gates.*

**Size**: `279` lines (`10120` bytes)

**Classes:**
- `class BrainActor`
- `class BrainActorRegistry` - *Persistent actor credentials; only salted hashes are stored.*
  - Method `.__init__(path, iterations)`
  - Method `.load()`
  - Method `.save(data)`
  - Method `._new_token(token)` - *(key_id, token) for a new credential; a caller-chosen token has no key id.*
  - Method `._verify(record, token)`
  - Method `._hash(token, salt)`
  - Method `.create(name, role, token)`
  - Method `.authenticate(token)`
  - Method `._last_used_is_stale(last_used, now)` - *Rewrite last_used at most once a minute, not on every authenticated request.*
  - Method `.list()`
  - Method `.rotate(name)`
  - Method `.disable(name)`
  - Method `.delete(name)`
  - Method `._public(record)`

**Functions:**
- `def get_actor_registry()`
- `def reset_actor_registry()`
- `def _is_local(host)`
- `def _set_request_actor(request, actor)`
- `def get_brain_actor(request)`
- `def require_role(minimum)`

---

### [`modules/brain/configuration.py`](modules/brain/configuration.py)
> *Policy-gated self-configuration for settings, modules and brain options.*

**Size**: `428` lines (`18533` bytes)

**Classes:**
- `class ConfigurationPolicyError` - *Raised when a requested change violates the configuration policy.*
- `class ConfigurationQuotaError` - *Raised when the daily configuration-change quota is exhausted (HTTP 429).*
- `class ConfigurationStore` - *Atomic ledger for applied changes and their rollback snapshots.*
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `._quarantine()`
  - Method `.save(data)`
- `class ConfigurationEngine` - *Preview, apply and roll back bounded configuration changes.*
  - Method `.__init__(settings_manager, module_manager, store, runtime_apply, config_path, allowed_settings, configurable_modules, max_changes_per_day, max_changes, require_goal_link, goal_lookup)`
  - Method `._reload_from_store()`
  - Method `.attach_module_manager(module_manager)`
  - Method `.preview(operation, module_manager)`
  - Method `.apply(operation, approved_by, module_manager)`
  - Method `.rollback(change_id, approved_by, module_manager)`
  - Method `._target(op)`
  - Method `._apply_value(op, value, manager)`
  - Method `.status()`
  - Method `.summary()` - *Compact status for brain snapshots (never embeds the change list).*
  - Method `._normalize(operation)`
  - Method `._require_manager(manager)`
  - Method `._validate_setting(key, value)`
  - Method `._validate_brain_option(key, value)`
  - Method `._validate_allowed_tools(value, config)`
  - Method `._read_brain_config()`
  - Method `._write_brain_config(config)`
  - Method `._prune_quotas()`
  - Method `._prune_changes()`
  - Method `._save()`

---

### [`modules/brain/drift.py`](modules/brain/drift.py)
> *Drift detection against a measured healthy baseline.*

**Size**: `136` lines (`4921` bytes)

**Classes:**
- `class DriftMonitor` - *Compare live self-model metrics with a baseline and score divergence.*
  - Method `.__init__(path)`
  - Method `._load()`
  - Method `._save()`
  - Method `._metrics(snapshot)`
  - Method `.set_baseline(snapshot, recorded_by)`
  - Method `.observe(snapshot)`
  - Method `.status()`

**Functions:**
- `def _now()`

---

### [`modules/brain/evaluation.py`](modules/brain/evaluation.py)
> *Deterministic evaluation harness used as the brain's scorecard.*

**Size**: `362` lines (`13698` bytes)

**Classes:**
- `class UnsafeEvaluationError` - *Raised when a flow contains nodes that must not run during evaluation.*
- `class EvalTask`
- `class FlowEvalExecutor` - *Run an inline or stored flow for one evaluation task.*
  - Method `.__init__(flow_id, flow_override, timeout, manager, allow_unsafe)`
  - Method `async def .__call__(input_data)`
- `class EvaluationRunner` - *Execute a deterministic suite and produce a comparable result document.*
  - Method `.__init__(manager, allow_unsafe)`
  - Method `async def .run(tasks, executor, flow_id, flow_override, default_timeout)`
- `class EvalStore` - *Persist last and baseline results under the configured state directory.*
  - Method `.__init__(last_path, baseline_path)`
  - Method `._lock_for(path)`
  - Method `._write(path, data)`
  - Method `._read(path)`
  - Method `.save_last(result)`
  - Method `.load_last()`
  - Method `.save_baseline(result)`
  - Method `.load_baseline()`

**Functions:**
- `def validate_evaluation_flow(flow, allow_unsafe)` - *Reject flows with external-effect or state-mutating nodes.*
- `def _field_value(output, field)`
- `def _evaluate_check(check, output, latency_ms)`
- `def compare_results(current, baseline, max_pass_drop, max_latency_increase_pct, min_latency_increase_ms)` - *Compare a current run to a baseline; promotion requires no regression.*

---

### [`modules/brain/events.py`](modules/brain/events.py)
> *Bounded, async event bus for brain perceptions.*

**Size**: `88` lines (`3238` bytes)

**Classes:**
- `class BrainEvent`
  - Method `.to_dict()`
- `class EventBus` - *In-process pub/sub with a bounded history and optional deduplication.*
  - Method `.__init__(max_history, handler_timeout)`
  - Method `.subscribe(event_type, handler)`
  - Method `.unsubscribe(event_type, handler)`
  - Method `async def .publish(event_type, source, payload, salience, dedupe_key)`
  - Method `.recent(limit)`
  - Method `.clear()`

---

### [`modules/brain/goal_link.py`](modules/brain/goal_link.py)
> *Goal linkage shared by the brain's self-modification engines.*

**Size**: `86` lines (`3600` bytes)

**Functions:**
- `def default_goal_lookup(goal_id)` - *Read one goal from the shared memory store; a missing table is not an error.*
- `def default_goal_marker(goal_id)` - *Mark an accepted goal as in progress when work under it starts.*
- `def resolve_goal_link(goal_id, goal_kind, reason, *, require, goal_lookup)` - *Return the goal fields for a ledger entry, or raise ``ValueError`` with the remedy.*
- `def revalidate_goal_link(entry, *, require, goal_lookup)` - *A promotion or apply must still be justified; the goal may have completed.*

---

### [`modules/brain/goal_review.py`](modules/brain/goal_review.py)
> *Deterministic, read-only review of the shared goal ledger (ADR-057).*

**Size**: `174` lines (`5994` bytes)

**Functions:**
- `def _tokens(text)`
- `def _jaccard(left, right)`
- `def _brief(goal, *, clarity)`
- `def clarity(goal)` - *Score how actionable a goal description is, from 0 to 1.*
- `def _timestamp(value)`
- `def _finite(value, name, *, minimum)`
- `def _sort_epoch(value)`
- `def review_goals(goals, *, now, stale_days, clarity_threshold, duplicate_threshold)` - *Return a bounded review of active goals; never raises for malformed rows.*

---

### [`modules/brain/guardrails.py`](modules/brain/guardrails.py)
> *Deterministic guardrails for untrusted text entering or leaving the platform.*

**Size**: `258` lines (`9043` bytes)

**Classes:**
- `class Finding` - *One matched rule, with the surface and the action the mode applied.*
  - Method `.to_dict()`
- `class GuardVerdict` - *The decision for one piece of text; ``text`` is the content to use downstream.*
  - Property `.blocked`
  - Property `.safe`
  - Method `.to_dict()`
- `class GuardrailStats` - *Process-local counters for operators; the ledger stays out of the hot path.*
  - Method `.__init__()`
  - Method `.record(verdict)`
  - Method `.snapshot()`
  - Method `.reset()`

**Functions:**
- `def guardrail_mode()` - *The configured policy; an unknown value falls back to ``observe`` and logs once.*
- `def _normalize(text)`
- `def _scan_rules(text)`
- `def redact_secrets(text)` - *Replace credential-shaped runs; returns the text and how many replacements were made.*
- `def _system_prompt_leaks(text, system_prompt)` - *Verbatim long sentences of the system prompt found in model output.*
- `def scan_text(text, surface, mode, system_prompt)` - *Scan one piece of text for a surface; never raises on odd input.*
- `def guardrail_stats()`
- `def scan_input(text, **kwargs)`
- `def scan_output(text, **kwargs)`

---

### [`modules/brain/improvement.py`](modules/brain/improvement.py)
> *Evaluation-gated flow improvement with quotas, promotion and rollback.*

**Size**: `476` lines (`20231` bytes)

**Classes:**
- `class QuotaExceededError` - *Raised when a daily improvement quota is exhausted (HTTP 429).*
- `class Candidate`
  - Method `.to_dict()`
- `class ImprovementStore` - *Atomic candidate ledger under the configured state directory.*
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `._quarantine()`
  - Method `.save(data)`
- `class ImprovementEngine` - *Propose, evaluate and promote flow edits without leaving failed edits behind.*
  - Method `.__init__(flow_manager, evolution, evaluation_runner, eval_store, store, max_candidates_per_day, max_evaluations_per_day, max_promotions_per_day, max_candidates, generator, require_goal_link, goal_lookup, goal_marker)`
  - Method `._reload_from_store()`
  - Method `._resolve_goal(goal_id, goal_kind, reason)` - *Bind a change to an active goal, or to an explicit self-repair reason (ADR-041).*
  - Method `._revalidate_goal(candidate)` - *A promotion must still be justified: the goal may have completed since proposal.*
  - Method `async def .propose(flow_id, operations, description, source, *, goal_id, goal_kind)`
  - Method `async def .generate(flow_id, instruction, *, goal_id, goal_kind)`
  - Method `async def .evaluate(candidate_id, approve, approved_by)`
  - Method `async def .rollback(candidate_id)`
  - Method `._baseline_quality_reasons(baseline, flow_id)`
  - Method `._prune_candidates()`
  - Method `.summary()` - *Compact status for self-model snapshots (never embeds every candidate).*
  - Method `.status()`
  - Method `._quota()`
  - Method `._prune_quotas()`
  - Method `._save()`

---

### [`modules/brain/kernel.py`](modules/brain/kernel.py)
> *Brain governance kernel: modes, risk score, gates, kill switch and persistence.*

**Size**: `482` lines (`20128` bytes)

**Classes:**
- `class KernelMode`
- `class KernelBlockedError` - *Raised when the kernel forbids an action in the current mode.*
- `class KernelState`
  - Method `.to_dict()`
- `class KernelStore`
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `.save(state)`
- `class BrainKernel` - *Risk-driven mode gate. It never executes actions; it only allows or blocks them.*
  - Method `.__init__(degrade_threshold, safe_threshold, safe_exit_threshold, store, clock, dwell_seconds)`
  - Method `._state_from(restored)`
  - Method `._reload_from_store()`
  - Method `._prune(now)` - *Drop window entries that aged out; callers hold the ledger lock.*
  - Property `.mode`
  - Property `.is_safe`
  - Method `._error_pressure()` - *The worst single component's share of the error window, 0..1.*
  - Property `.risk_score`
  - Method `.observe(snapshot)` - *Update risk inputs from one self-model snapshot and reassess the mode.*
  - Method `.record_evaluation(result)`
  - Method `.record_drift(score)`
  - Method `.record_blocked_action(reason)`
  - Method `.record_component_error(component, reason)` - *Count one failure of a named subsystem inside the rolling error window.*
  - Method `.record_component_recovery(component)` - *Cancel one fresh error from a subsystem that succeeded again.*
  - Method `.assess(reason, persist)`
  - Method `._in_dwell(now)`
  - Method `.kill_switch(reason, approved_by)`
  - Method `.reset(approved_by)`
  - Method `.can_run_autonomy()`
  - Method `.can_write_memory()`
  - Method `.can_promote()`
  - Method `.can_configure()`
  - Method `.require_autonomy(action)`
  - Method `.require_memory_write(action)`
  - Method `.require_promotion(action)`
  - Method `.require_configuration(action)`
  - Method `.to_dict()`
  - Method `._save()`

**Functions:**
- `def _clamp_unit(value, default)` - *Clamp a 0..1 risk input; a malformed value keeps the previous input.*

---

### [`modules/brain/learning.py`](modules/brain/learning.py)
> *Outcome-driven learning: experiences, consolidation and held-out recall.*

**Size**: `676` lines (`26627` bytes)

**Classes:**
- `class Outcome`
  - Method `.signature()`
  - Method `.to_dict()`
- `class Skill`
  - Method `.to_dict()`
- `class MemoryBridge`
  - Method `.add_entry(text, **kwargs)`
  - Method `.update_entry(memory_id, text, **kwargs)`
  - Method `.delete_entry(memory_id, reason)`
  - Method `.browse(limit, search_text, mem_type, source, **kwargs)`
- `class GlobalMemoryBridge` - *Adapter over the shared MemoryStore; imported lazily to avoid startup cost.*
  - Method `.add_entry(text, **kwargs)`
  - Method `.update_entry(memory_id, text, **kwargs)`
  - Method `.delete_entry(memory_id, reason)`
  - Method `.browse(limit, search_text, mem_type, source, **kwargs)`
- `class LearningStore` - *Bounded local learning ledger with atomic persistence.*
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `._quarantine()`
  - Method `.save(data)`
- `class LearningLoop` - *Record outcomes, consolidate repeated successes into rules, measure recall.*
  - Method `.__init__(memory, store, max_experiences, min_occurrences, holdout_ratio, max_skills, min_failures, max_avoidances, mirror_experiences)`
  - Method `._reload_from_store()`
  - Method `._split_for(outcome)`
  - Method `.record_outcome(action, input_data, output, success, error, latency_ms, tags, trace_id)`
  - Method `._delete_mirrored_experience(record)`
  - Method `._mirror_experience(record)`
  - Method `.consolidate()`
  - Method `._consolidate_avoidances(groups)` - *Turn repeated failures into bounded 'do not repeat this' knowledge.*
  - Method `._error_class(error)`
  - Method `._best_approach(experiences)`
  - Method `._add_memory(text)`
  - Method `._update_memory(memory_id, text)`
  - Method `._add_avoidance_memory(text)`
  - Method `._update_avoidance_memory(memory_id, text)`
  - Method `.retrieve_skills(query, limit)` - *Retrieve candidate skills from global memory, then rank lexically.*
  - Method `.retrieve_avoidances(query, limit)` - *Rank bounded failure knowledge for a query; empty when nothing close is known.*
  - Method `.recall(query, top_k)` - *Skills to reuse and failures to avoid for one query, from one ledger.*
  - Method `.evaluate_recall(top_k)`
  - Method `.status()`
  - Method `.experiences_page(limit)`
  - Method `._save()`

**Functions:**
- `def _now()`
- `def _signature(input_data)`
- `def _tokens(text)`
- `def _token_overlap(query, text)`

---

### [`modules/brain/persistence.py`](modules/brain/persistence.py)
> *Cross-process locking and atomic JSON persistence shared by brain ledgers.*

**Size**: `123` lines (`4009` bytes)

**Classes:**
- `class LedgerLock` - *Serialize access to one ledger across threads and processes.*
  - Method `.__init__(path, timeout)`
  - Property `.reentrant` - *Whether this thread already holds the lock (a nested engine call).*
  - Method `.held()`

**Functions:**
- `def atomic_write_json(path, data, *, default)` - *Write JSON through a fsynced temp file and atomic replace; the caller holds the lock.*
- `def ledger_transaction(method)` - *Run a store method under its own ledger lock.*
- `def engine_transaction(method)` - *Run a synchronous engine mutation under its store lock, refreshing state once.*
- `def engine_transaction_async(method)` - *Async variant of :func:`engine_transaction`.*
- `def _refresh(instance)`

---

### [`modules/brain/reactivity.py`](modules/brain/reactivity.py)
> *Event-driven reactivity: sources, dedupe/mute and proposal-only reactions.*

**Size**: `392` lines (`15482` bytes)

**Classes:**
- `class ReactionStore`
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `.save(data)`
- `class ReactivityEngine` - *Consume events into deduplicated, mute-able proposals. Never executes actions.*
  - Method `.__init__(bus, store, config, calendar_provider, metrics_provider, can_react)`
  - Method `._reload_from_store()`
  - Method `async def .start()`
  - Method `async def .stop()`
  - Method `async def ._on_event(event)`
  - Method `async def .process_event(event)`
  - Method `.dismiss(reaction_id)`
  - Method `.mute(key, minutes)`
  - Method `.unmute(key)`
  - Method `async def .poll_once()`
  - Method `async def ._emit(event)`
  - Method `._calendar_events()`
  - Method `._metric_events()`
  - Method `._file_events()`
  - Method `async def ._poll_loop()`
  - Method `._dedupe_key(event)`
  - Method `._prune(now)`
  - Method `.summary()`
  - Method `.status()`
  - Method `._save()`
  - Method `._default_calendar()`
  - Method `._default_metrics()`

---

### [`modules/brain/router.py`](modules/brain/router.py)
> *HTTP surface for the AI-Brain substrate and evaluation harness.*

**Size**: `552` lines (`20088` bytes)

**Functions:**
- `async def brain_startup()`
- `async def brain_shutdown()`
- `async def brain_ui(request)`
- `async def brain_actor(actor)`
- `async def brain_actors(_actor)`
- `async def brain_actor_create(body, _actor)`
- `async def brain_actor_rotate(name, _actor)`
- `async def brain_actor_disable(name, _actor)`
- `async def brain_actor_delete(name, _actor)`
- `async def brain_state()`
- `async def brain_events(limit)`
- `async def brain_publish_event(body, actor)`
- `async def brain_start(_actor)`
- `async def brain_stop(_actor)`
- `async def brain_cycle(_actor)`
- `async def brain_mode(body, _actor)`
- `async def brain_proposals()`
- `async def brain_eval_suite()`
- `async def brain_eval_run(body, _actor)`
- `async def brain_eval_baseline(actor)`
- `async def brain_eval_status()`
- `async def brain_budget_status()`
- `async def brain_learn_status()`
- `async def brain_learn_experiences(limit)`
- `async def brain_learn(body, actor)`
- `async def brain_learn_consolidate(_actor)`
- `async def brain_learn_recall(body)`
- `async def brain_learn_suggest(body)`
- `async def brain_goals_review(body, _actor)`
- `async def brain_improve_status()`
- `async def brain_improve_candidate(body, actor)`
- `async def brain_improve_generate(body, _actor)`
- `async def brain_improve_evaluate(body, actor)`
- `async def brain_improve_rollback(body, _actor)`
- `async def brain_config_status()`
- `async def brain_kernel_status()`
- `async def brain_kernel_kill(body, actor)`
- `async def brain_kernel_reset(actor)`
- `async def brain_trends_status()`
- `async def brain_guardrails_status()`
- `async def brain_guardrails_scan(body, _actor)`
- `async def brain_drift_baseline(actor)`
- `async def brain_reactions()`
- `async def brain_reaction_dismiss(body, _actor)`
- `async def brain_reaction_mute(body, _actor)`
- `async def brain_reaction_unmute(body, _actor)`
- `async def brain_react_start(_actor)`
- `async def brain_react_stop(_actor)`
- `async def brain_react_poll(_actor)`
- `async def brain_config_preview(body, module_manager)`
- `async def brain_config_apply(body, module_manager, actor)`
- `async def brain_config_rollback(body, module_manager, actor)`

---

### [`modules/brain/self_budget.py`](modules/brain/self_budget.py)
> *One daily ceiling for everything the brain changes about itself.*

**Size**: `149` lines (`5303` bytes)

**Classes:**
- `class SelfBudgetExceeded` - *Raised when the daily ceiling for one kind of self-modification is reached.*
  - Method `.__init__(kind, cap, used)`
- `class SelfBudget`
  - Method `.__post_init__()`
  - Method `._load()`
  - Method `._save()`
  - Method `._roll()`
  - Method `.reserve(kind, units)`
  - Method `.release(kind, units)` - *Give back a reservation whose work definitely did not happen.*
  - Method `.snapshot()`

**Functions:**
- `def _today()`
- `def _validated_cap(raw, kind)`

---

### [`modules/brain/self_model.py`](modules/brain/self_model.py)
> *Real self-model for the brain.*

**Size**: `204` lines (`7443` bytes)

**Classes:**
- `class BrainSelfModel` - *Compose live subsystem snapshots into the brain's model of itself.*
  - Method `.__init__(providers)`
  - Method `async def .snapshot(mode)`
  - Method `._flows()`
  - Method `._modules()`
  - Method `._memory()`
  - Method `._knowledge()`
  - Method `._metrics()`
  - Method `._tools()`
  - Method `._learning()`
  - Method `._evaluation()`

---

### [`modules/brain/service.py`](modules/brain/service.py)
> *AI-Brain control service: observe/suggest cycles, state and evaluation.*

**Size**: `793` lines (`34496` bytes)

**Classes:**
- `class BrainService` - *Single brain control plane. No actuator can be called in observe/suggest mode.*
  - Method `.__init__(config, self_model, event_bus, state_store, evaluation_runner, eval_store, learning, improvement, configuration, kernel, drift, trends, reactivity)`
  - Method `async def .start()`
  - Method `async def .stop()`
  - Method `async def .set_mode(mode)`
  - Method `async def .run_cycle(trigger)`
  - Method `._on_flow_outcome(outcome)` - *Capture ordinary flow/chat outcomes for learning, unless SAFE or disabled.*
  - Method `._observe_self(snapshot)`
  - Method `async def .snapshot()`
  - Method `async def .record_outcome(action, input_data, output, success, error, latency_ms, tags, trace_id)`
  - Method `async def .consolidate_learning()`
  - Method `async def .evaluate_learning_recall(top_k)`
  - Method `.suggest_learning(query, top_k)` - *Skills worth reusing and past failures worth avoiding for one query.*
  - Method `.learning_status()`
  - Method `.review_goals(*, stale_days, clarity_threshold, duplicate_threshold)` - *Read the shared goal ledger and return a deterministic, read-only review.*
  - Method `async def .propose_improvement(flow_id, operations, description, source, goal_id, goal_kind)`
  - Method `async def .evaluate_improvement(candidate_id, approve, approved_by)`
  - Method `async def .rollback_improvement(candidate_id)`
  - Method `async def .generate_improvement(flow_id, instruction, goal_id, goal_kind)`
  - Method `.improvement_status()`
  - Method `.preview_configuration(operation, module_manager)`
  - Method `.apply_configuration(operation, approved_by, module_manager)`
  - Method `.self_budget_status()`
  - Method `.rollback_configuration(change_id, approved_by, module_manager)`
  - Method `.configuration_status()`
  - Method `.kernel_status()`
  - Method `.trends_status()`
  - Method `.reactivity_status()`
  - Method `async def .start_reactivity()`
  - Method `async def .stop_reactivity()`
  - Method `async def .poll_reactivity()`
  - Method `.dismiss_reaction(reaction_id)`
  - Method `.mute_reaction(key, minutes)`
  - Method `.unmute_reaction(key)`
  - Method `.engage_kill_switch(reason, approved_by)`
  - Method `.reset_kernel(approved_by)`
  - Method `async def .set_drift_baseline(approved_by)`
  - Method `._apply_brain_option(key, value)`
  - Method `async def ._generate_flow_candidate(flow_id, instruction)` - *Use the configured LLM to draft a bounded flow operation list.*
  - Method `.events(limit)`
  - Method `async def .publish_event(event_type, source, payload, salience, dedupe_key)`
  - Method `async def .run_evaluation(flow_id)`
  - Method `async def .promote_baseline(approved_by)`
  - Method `.evaluation_status()`
  - Method `async def ._loop()`
  - Method `._propose(snapshot)`
  - Method `._proposal(kind, title, rationale, confidence)`
  - Method `._save()`

**Functions:**
- `def _load_module_config()`
- `def get_brain_service()`
- `def get_existing_brain_service()` - *Return the service only if something already created it (e.g. /brain/start).*
- `def brain_auto_start_enabled()`
- `def reset_brain_service()`

---

### [`modules/brain/state.py`](modules/brain/state.py)
> *Persistent brain state and mode definitions.*

**Size**: `81` lines (`2546` bytes)

**Classes:**
- `class BrainState` - *Observable state of the brain control plane.*
  - Method `.to_dict()`
  - Method `.from_dict(data)`
- `class BrainStateStore` - *Atomic JSON persistence for brain state under the configured state dir.*
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `.save(state)`

**Functions:**
- `def utc_now()`

---

### [`modules/brain/trends.py`](modules/brain/trends.py)
> *Bounded metric-trend detection for reviewable self-repair proposals (ADR-058).*

**Size**: `229` lines (`9097` bytes)

**Classes:**
- `class TrendStore` - *Atomic JSON ledger for samples and emitted findings.*
  - Method `.__init__(path)`
  - Method `.load()`
  - Method `.save(data)`
- `class TrendMonitor` - *Record bounded samples and emit at most one deterministic repair finding per day.*
  - Method `.__init__(store, clock, window, history, min_delta, cooldown_seconds, max_per_day)`
  - Method `._value(sample, metric)`
  - Method `._mean(values)`
  - Method `.record(snapshot)` - *Append one sample derived from a self-model snapshot, bounded by history.*
  - Method `.detect()` - *Return zero or one finding; persisted dedupe prevents proposal spam.*
  - Method `._prune_proposals(raw)`
  - Method `.review()` - *Read-only view of the sample window, means and emitted findings.*

---

### [`modules/browser_automation/__init__.py`](modules/browser_automation/__init__.py)
**Size**: `3` lines (`49` bytes)

---

### [`modules/browser_automation/router.py`](modules/browser_automation/router.py)
**Size**: `69` lines (`2639` bytes)

**Classes:**
- `class BrowserActionPayload`

**Functions:**
- `async def startup_event()`
- `async def shutdown_event()`
- `async def perform_browser_action(payload)` - *Internal API endpoint for the sandboxed tool to perform browser actions.*

---

### [`modules/browser_automation/service.py`](modules/browser_automation/service.py)
**Size**: `139` lines (`5569` bytes)

**Classes:**
- `class BrowserService`
  - Method `.__new__()`
  - Property `.lock` - *Lazily create the asyncio.Lock on first access inside the running event loop.*
  - Method `async def .start(config)` - *Initializes the playwright browser if not already running.*
  - Method `async def .stop()` - *Stops the headless browser.*
  - Method `async def .ensure_running()`
  - Method `async def .goto(url)`
  - Method `async def .click(selector)`
  - Method `async def .type_text(selector, text)`
  - Method `async def .screenshot(full_page)`
  - Method `async def .get_html(selector)`
  - Method `async def .extract_text(selector)` - *Extracts visible text from the page or a specific selector.*

---

### [`modules/calendar/__init__.py`](modules/calendar/__init__.py)
**Size**: `1` lines (`26` bytes)

---

### [`modules/calendar/events.py`](modules/calendar/events.py)
**Size**: `163` lines (`6195` bytes)

**Classes:**
- `class EventManager`
  - Method `.__init__(storage_file)`
  - Method `._ensure_file()` - *Ensure the storage file exists. Check and create inside lock to avoid race condition.*
  - Method `._with_lock(fn)` - *Execute a function holding the lock for the entire read-modify-write cycle.*
  - Method `._load_events_unsafe()` - *Load events without acquiring lock (caller must hold lock).*
  - Method `._save_events_unsafe(events)` - *Save events without acquiring lock (caller must hold lock).*
  - Method `._load_events()`
  - Method `._save_events(events)` - *Save events using atomic temp-file-then-rename pattern.*
  - Method `.add_event(title, start_time)`
  - Method `.get_events_by_date(date_str)`
  - Method `.get_upcoming(limit)`
  - Method `.get_event_by_id(event_id)`
  - Method `.update_event(event_id, title, start_time)` - *Update an existing event's title and/or start_time. Returns the updated event or None.*
  - Method `.mark_notified(event_id)` - *Mark an event as notified.*
  - Method `.delete_event(event_id)`

---

### [`modules/calendar/node.py`](modules/calendar/node.py)
**Size**: `75` lines (`2906` bytes)

**Classes:**
- `class CalendarWatcherExecutor`
  - Method `async def .receive(input_data, config)` - *Checks for calendar events scheduled for the current time.*
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/calendar/router.py`](modules/calendar/router.py)
**Size**: `154` lines (`5805` bytes)

**Functions:**
- `def get_enriched_upcoming_events(event_manager)` - *Enriches events with nav_year and nav_month for UI navigation.*
- `async def calendar_page(request, module_manager)`
- `async def get_calendar_sidebar(request)`
- `async def calendar_gui(request, year, month)`
- `async def save_event(request, title, date, time)`
- `async def delete_event_route(request, event_id)`

---

### [`modules/chat/__init__.py`](modules/chat/__init__.py)
**Size**: `1` lines (`27` bytes)

---

### [`modules/chat/node.py`](modules/chat/node.py)
**Size**: `76` lines (`3232` bytes)

**Classes:**
- `class ChatInputExecutor` - *Node to start a flow with chat data.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class ChatOutputExecutor` - *Node to format the final AI response for the chat UI.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)` - *Acts as a dispatcher to get the correct node executor class.*

---

### [`modules/chat/router.py`](modules/chat/router.py)
**Size**: `574` lines (`26079` bytes)

**Functions:**
- `def _flow_system_prompt(flow)` - *Concatenated system prompts configured in a flow, bounded for leak detection.*
- `def _extract_thinking_steps(flow_result)` - *Extract intermediate agent thinking steps from a flow result.*
- `async def chat_page(request)`
- `async def chat_gui(request, session_id)`
- `async def get_chat_sessions(request)`
- `async def create_new_session()`
- `async def compact_session_route(request, session_id, llm)` - *Manual compact: summarize old messages, keep last N verbatim.*
- `async def delete_session(request, session_id, confirm)`
- `async def rename_session(session_id, name)`
- `async def save_chat_settings(request, auto_rename_turns, auto_compact_tokens, compact_keep_last)`
- `async def send_message(request, message, session_id, image, goal_id, llm)`
- `async def stream_chat(session_id)`

---

### [`modules/chat/sessions.py`](modules/chat/sessions.py)
**Size**: `345` lines (`13418` bytes)

**Classes:**
- `class SessionManager`
  - Method `.__init__(storage_file, max_sessions, max_messages, lock_timeout)`
  - Method `._file_mtime()` - *A change fingerprint of the store: mtime alone misses writes within a coarse clock tick.*
  - Method `._quarantine_unlocked()`
  - Method `._load_sessions_unlocked()`
  - Method `._load_sessions()`
  - Method `._refresh_sessions_locked()` - *Reload when another process changed the file since our last read/write.*
  - Method `._save_sessions_locked()` - *Write sessions through a fsynced unique temp file and atomic replace.*
  - Method `._save_sessions()` - *Persist the current sessions. Callers must hold the in-process and file locks.*
  - Method `._mutate(callback)` - *Serialise a read-modify-write across threads and processes.*
  - Method `._prune_sessions_locked()`
  - Method `._trim_history_locked(session)`
  - Method `.create_session(name)`
  - Method `.add_tokens(session_id, usage)` - *Accumulate token usage for a session from an LLM usage dict.*
  - Method `.get_session(session_id)`
  - Method `.delete_session(session_id)`
  - Method `.rename_session(session_id, new_name)`
  - Method `.list_sessions()`
  - Method `.add_message(session_id, role, content, **kwargs)`
  - Method `._get_session_snapshot(session_id)` - *Get a snapshot of session data under lock. Called from async context via to_thread.*
  - Method `._update_compacted_session(session_id, snapshot_updated_at, new_history)` - *Update session with compacted history under lock. Called from async context via to_thread.*
  - Method `async def .compact_session(session_id, llm_bridge, keep_last)` - *Summarize old messages and replace them with a compact summary system message.*

**Functions:**
- `def _now_iso()`
- `def _estimate_tokens(messages)` - *Rough token estimate using the ~4 chars/token approximation.*

---

### [`modules/email_bridge/__init__.py`](modules/email_bridge/__init__.py)
**Size**: `1` lines (`27` bytes)

---

### [`modules/email_bridge/imap_bridge.py`](modules/email_bridge/imap_bridge.py)
> *IMAPBridge — blocking IMAP polling helper.*

**Size**: `180` lines (`6293` bytes)

**Classes:**
- `class IMAPBridge` - *Connects to an IMAP server and polls for UNSEEN messages.*
  - Method `.__init__(host, port, username, password, folder, use_ssl, mark_as_read, log_fn)`
  - Method `._connect()`
  - Method `._decode_header(value)`
  - Method `._get_text_body(msg)` - *Extract the first text/plain part from an email message.*
  - Method `.fetch_unseen(filter_sender)` - *Connect, fetch UNSEEN messages, and return them as dicts.*
  - Method `.poll_loop(callback, running_fn, interval)` - *Blocking poll loop. Calls callback(msg_dict) for each new email.*

---

### [`modules/email_bridge/node.py`](modules/email_bridge/node.py)
> *Email Bridge node executors.*

**Size**: `117` lines (`3949` bytes)

**Classes:**
- `class _ConfigLoader`
  - Method `.get_config()`
- `class EmailInputExecutor` - *Gate-keeps Repeater triggers; optionally filters by sender.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class EmailOutputExecutor` - *Sends flow 'content' as a reply email to the originating sender.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/email_bridge/router.py`](modules/email_bridge/router.py)
> *Email Bridge router — settings management endpoints.*

**Size**: `88` lines (`2634` bytes)

**Functions:**
- `def _success(message)`
- `def _error(message)`
- `async def save_settings(request, imap_host, imap_port, imap_use_ssl, imap_username, imap_password, imap_folder, imap_filter_sender, poll_interval, mark_as_read, smtp_host, smtp_port, smtp_use_tls, smtp_username, smtp_password, smtp_from_address, reply_to_address)`

---

### [`modules/email_bridge/service.py`](modules/email_bridge/service.py)
> *EmailService — singleton that manages IMAP polling and SMTP replies.*

**Size**: `329` lines (`11673` bytes)

**Classes:**
- `class _SessionStore` - *Thread-safe sender→session_id map persisted to sessions.json.*
  - Method `.__init__()`
  - Method `._load()`
  - Method `._save()`
  - Method `.get(key)`
  - Method `.set(key, value)`
  - Method `.delete(key)`
- `class _EmailHandler` - *Processes one incoming email: runs the flow, sends the reply.*
  - Method `.__init__()`
  - Method `._get_or_create_loop()`
  - Method `._session_key(sender)`
  - Method `._get_or_create_session(sender)`
  - Method `.handle_message(msg)` - *Entry point called from the IMAP poll thread.*
  - Method `async def .process_message(msg)`
- `class EmailService`
  - Method `.__new__()`
  - Method `.start()` - *Start the IMAP polling thread if credentials are configured.*
  - Method `.restart()` - *Stop and re-start the polling thread (called after config save).*
  - Method `.get_smtp()` - *Return the configured SMTP bridge (used by EmailOutputExecutor).*
  - Method `._start_poller(config)`
  - Method `._stop()`
  - Method `._poll_thread(interval, filter_sender)` - *Daemon thread: polls IMAP and dispatches messages.*

**Functions:**
- `def _load_config()`
- `def _is_enabled()`
- `async def _run_flow(initial_data)`

---

### [`modules/email_bridge/smtp_bridge.py`](modules/email_bridge/smtp_bridge.py)
> *SMTPBridge — simple SMTP send helper.*

**Size**: `78` lines (`2530` bytes)

**Classes:**
- `class SMTPBridge` - *Sends plain-text emails via SMTP with optional In-Reply-To threading.*
  - Method `.__init__(host, port, username, password, from_address, use_tls, reply_to_address, log_fn)`
  - Method `.send(to, subject, body, in_reply_to)` - *Send a plain-text email. Returns True on success.*

---

### [`modules/knowledge_base/__init__.py`](modules/knowledge_base/__init__.py)
**Size**: `1` lines (`26` bytes)

---

### [`modules/knowledge_base/backend.py`](modules/knowledge_base/backend.py)
**Size**: `1263` lines (`50551` bytes)

**Classes:**
- `class QueryExpansionMixin` - *Mixin that adds query expansion and re-ranking capabilities to FaissDocumentStore.*
  - Method `.__init__(*args, **kwargs)`
  - Method `._get_cross_encoder()` - *Get or initialize cross-encoder model for re-ranking.*
  - Method `async def .expand_query_async(query, llm_bridge)` - *Generate alternative phrasings of the query asynchronously.*
  - Method `.expand_query_sync(query)` - *Synchronous version of query expansion (generates simple variations).*
  - Method `.rerank_candidates(query, candidates, use_cross_encoder)` - *Re-rank candidates by relevance to the query.*
  - Method `async def .search_enhanced(query_text, query_embedding, limit, expand_queries, rerank, llm_bridge)` - *Enhanced search with query expansion and re-ranking.*
- `class FaissDocumentStore` - *FAISS-enhanced document store with fast vector search.*
  - Method `.__init__(db_path, embed_fn)`
  - Method `._connect()`
  - Method `._init_db()`
  - Method `._load_faiss_index()` - *Load or create FAISS index*
  - Method `._create_empty_index()` - *Helper to create a new empty index*
  - Method `._detect_embedding_dimension()` - *Detect the embedding dimension from the model*
  - Method `._save_faiss_index()` - *Save FAISS index to disk*
  - Method `._add_embeddings_to_faiss(embeddings, chunk_ids, save_index)` - *Add embeddings to FAISS index.*
  - Method `._sync_faiss_index()` - *Ensure FAISS index is in sync with SQLite chunks.*
  - Method `._sync_fts_index()` - *Ensure FTS index is populated.*
  - Method `._rebuild_index()` - *Rebuild FAISS index from SQLite chunks.*
  - Method `.compute_file_hash(file_path)` - *Compute SHA256 hash of file for deduplication.*
  - Method `.document_exists(file_hash)` - *Check if document already exists in database.*
  - Method `.get_document_by_filename(filename)` - *Return document metadata for the first document with the given filename, or None.*
  - Method `.reindex_document(document_id, new_file_hash, new_file_size, new_page_count, new_chunks, stored_filename)` - *Delta re-index a document: preserve unchanged chunks, remove stale ones, add new ones.*
  - Method `.add_document(file_hash, filename, file_type, file_size, page_count, chunks, upload_source, stored_filename)` - *Add document and its chunks to database with FAISS indexing.*
  - Method `.list_documents(limit)` - *List all documents.*
  - Method `.get_document(document_id)` - *Retrieve a single document's metadata by ID.*
  - Method `.get_document_chunks(document_id)` - *Return all chunks for a document as {id, chunk_index, text} dicts.*
  - Method `.reembed_document(document_id, chunk_embeddings)` - *Replace embeddings for all chunks of a document in SQLite and FAISS.*
  - Method `.delete_document(document_id)` - *Delete document and all its chunks (and remove from FAISS).*
  - Method `.find_broken_documents()` - *Find documents with integrity issues (0 chunks, count mismatch, missing embeddings).*
  - Method `.get_orphaned_chunk_count()` - *Count chunks that have no parent document.*
  - Method `.delete_orphaned_chunks()` - *Delete chunks that have no parent document.*
  - Method `.optimize()` - *Rebuild FAISS index to remove ghost vectors (deleted documents).*
  - Method `.vacuum()` - *Reclaim unused disk space in SQLite.*
  - Method `.clear()` - *DANGEROUS: Clear all documents and chunks.*
  - Method `.get_total_documents()`
  - Method `.get_total_chunks()`
  - Method `.search(query_embedding, limit)` - *Search for similar chunks using the query embedding.*
  - Method `.search_keyword(query, limit)` - *Search for chunks using SQLite FTS5 (Keyword Search).*
  - Method `.search_hybrid(query_text, query_embedding, limit, rrf_k)` - *Combine Vector Search and Keyword Search using Reciprocal Rank Fusion (RRF).*

---

### [`modules/knowledge_base/node.py`](modules/knowledge_base/node.py)
**Size**: `86` lines (`3252` bytes)

**Classes:**
- `class KnowledgeQueryExecutor`
  - Method `.__init__()`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `def get_knowledge_llm_bridge()` - *Get or create a cached LLMBridge instance for knowledge base queries.*
- `async def get_executor_class(node_type_id)`

---

### [`modules/knowledge_base/processor.py`](modules/knowledge_base/processor.py)
**Size**: `253` lines (`10159` bytes)

**Classes:**
- `class DocumentProcessor` - *Document processor for PDF, DOCX, and TXT files.*
  - Method `.__init__(llm_bridge, chunk_size, chunk_overlap)`
  - Method `async def .process_document(file_path, progress_callback)` - *Auto-detect file type and process.*
  - Method `.get_file_type(filename)`
  - Method `async def .process_pdf(file_path, progress_callback)`
  - Method `async def .process_docx(file_path, progress_callback)`
  - Method `async def .process_txt(file_path, progress_callback)`
  - Method `async def ._generate_embeddings(chunks, progress_callback)` - *Generate embeddings using the LLMBridge.*
  - Method `._chunk_pages(pages)`
  - Method `._chunk_text(text)`
  - Method `._break_at_sentence(text)`
  - Method `.clean_text(text)`

---

### [`modules/knowledge_base/router.py`](modules/knowledge_base/router.py)
**Size**: `570` lines (`23299` bytes)

**Functions:**
- `def _format_size(size_bytes)` - *Return a human-readable file size string.*
- `def _resolve_stored_path(document, upload_dir)` - *Return the on-disk path for a document, including legacy UUID-prefixed uploads.*
- `def _document_storage_names(documents, disk_files)` - *Map document rows to the filenames that should exist in the upload directory.*
- `def validate_upload(file)` - *Validate file upload for security.*
- `def _cleanup_stale_uploads()` - *Remove stale entries from upload_progress that exceed TTL.*
- `async def process_and_save_document(tracking_id, file_path, original_filename, stored_filename, llm)` - *Background task to process document and update progress.*
- `async def knowledge_base_page(request)`
- `async def get_gui(request)`
- `async def list_docs(request, q)`
- `async def upload_doc(request, background_tasks, files, llm)`
- `async def get_upload_progress(request, tracking_id)`
- `async def abort_upload(request, tracking_id)`
- `async def delete_doc(request, doc_id)`
- `async def delete_all_docs(request, confirm)`
- `async def reindex_doc(doc_id, llm)` - *Re-embed all chunks for a document using the current embedding model.*
- `async def check_integrity(request)`

---

### [`modules/llm_module/__init__.py`](modules/llm_module/__init__.py)
**Size**: `1` lines (`26` bytes)

---

### [`modules/llm_module/node.py`](modules/llm_module/node.py)
**Size**: `265` lines (`12136` bytes)

**Classes:**
- `class LLMExecutor`
  - Method `.__init__()`
  - Method `async def .receive(input_data, config)` - *Executes LLM completion.*
  - Method `async def ._execute_with_retry_and_timeout(messages, timeout, max_retries, retry_delay, **kwargs)` - *Execute LLM call with optional timeout and retry logic.*
  - Method `async def ._receive_with_stream(messages, params, queue)` - *Execute LLM call using streaming endpoint and pushing tokens to the queue.*
  - Method `async def .send(processed_data)`

**Functions:**
- `def _spend_usage(response)` - *Charge the run budget with the tokens the provider reported, when available.*
- `async def get_executor_class(node_type_id)`

---

### [`modules/llm_module/router.py`](modules/llm_module/router.py)
**Size**: `21` lines (`890` bytes)

**Functions:**
- `async def save_generation_settings(request, temperature, max_tokens)`

---

### [`modules/logic/__init__.py`](modules/logic/__init__.py)
**Size**: `1` lines (`14` bytes)

---

### [`modules/logic/node.py`](modules/logic/node.py)
**Size**: `397` lines (`17475` bytes)

**Classes:**
- `class ContextLengthRouterExecutor` - *Routes to RLM or standard LLM based on estimated token count.*
  - Method `async def .receive(input_data, config)`
  - Method `._estimate_tokens(messages)` - *Estimate token count. Rough estimate: 4 chars per token.*
  - Method `async def .send(processed_data)`
- `class DelayExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class ScriptExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class RepeaterExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class ConditionalRouterExecutor`
  - Method `async def .receive(input_data, config)` - *Routes data flow based on tool existence or other conditions.*
  - Method `async def .send(processed_data)`
- `class TriggerExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class ScheduleStartExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/memory/__init__.py`](modules/memory/__init__.py)
**Size**: `36` lines (`1355` bytes)

**Functions:**
- `async def shutdown()` - *Await any in-flight background memory save tasks before the server exits.*

---

### [`modules/memory/arbiter.py`](modules/memory/arbiter.py)
**Size**: `132` lines (`5169` bytes)

**Classes:**
- `class MemoryArbiter` - *Autonomous bridge between Reasoning and MemoryStore.*
  - Method `.__init__(memory_store, embed_fn, config)`
  - Method `async def .consider(text, confidence, subject, source, embedding, mem_type, verified, expires_at)` - *Decide whether to promote reasoning into memory.*
  - Method `async def .consider_batch(candidates)` - *Process a batch of memory candidates in parallel.*

---

### [`modules/memory/backend.py`](modules/memory/backend.py)
**Size**: `1414` lines (`63535` bytes)

**Classes:**
- `class MemoryStore` - *Simplified MemoryStore for NeuroCore.*
  - Method `.__init__(db_path)`
  - Method `._connect()`
  - Method `._init_db()`
  - Method `._execute_init(con)`
  - Method `._parse_embedding(blob)`
  - Method `._save_faiss_index(force)`
  - Method `._load_faiss_index()`
  - Method `._sync_faiss_index()` - *Ensures FAISS index count matches DB count on startup.*
  - Method `._build_faiss_index()`
  - Method `.compute_identity(text)`
  - Method `.has_memories()`
  - Method `.add_entry(text, embedding, confidence, subject, created_at, mem_type, source, verified, expires_at)`
  - Method `.set_parent(child_id, parent_id)` - *Links a memory to a parent, effectively archiving the child as an older version.*
  - Method `.search(query_embedding, limit, source_filter, access_weight, link_boost)`
  - Method `.keyword_search(query, limit, link_boost)` - *Text-based fallback search used when no embedding model is configured.*
  - Method `._expand_with_links(results, limit, link_boost)` - *Promote one hop of accepted supporting evidence; zero disables the feature.*
  - Method `.get_recent(limit)`
  - Method `.browse(limit, offset, search_text, filter_date, mem_type, source, sort_field, sort_dir)` - *Retrieves memories with optional filtering for the Memory Browser UI.*
  - Method `.find_similar(embedding, threshold, limit, exclude_source)` - *Find memories with cosine similarity above threshold.*
  - Method `.boost_importance(memory_id, boost)`
  - Method `.wipe_all()`
  - Method `.record_recall(memory_id, session_id, score)` - *Record that a memory was surfaced during recall in a session.*
  - Method `.get_memory_sessions(memory_id)` - *Return all sessions that recalled a specific memory, grouped by session.*
  - Method `.get_session_memories(session_id)` - *Return all memories that were recalled in a specific session.*
  - Method `.get_memory_stats()` - *Returns statistics about the stored memories.*
  - Method `.log_meta_memory(action, target_ids, new_value, description)` - *Logs an action to the meta_memories table.*
  - Method `.update_entry(memory_id, text, mem_type, verified)` - *Updates a memory entry. Archives the old version via parent_id.*
  - Method `.delete_entry(memory_id, reason)` - *Hard deletes a memory entry. Logs to meta_memories.*
  - Method `.merge_memories(memory_ids, new_text, new_type, new_verified)` - *Merges multiple memories into one. Archives originals via parent_id.*
  - Method `._content_tokens(text)`
  - Method `._negated(text)`
  - Method `._link_row(row)`
  - Method `.add_link(source_id, target_id, relation, evidence, status, created_by)` - *Record evidence between two memories; duplicates return the existing link.*
  - Method `.get_link(link_id)`
  - Method `.get_links(memory_id, relation, status)` - *Links touching one memory, with the neighbour's text for display.*
  - Method `.delete_link(link_id)`
  - Method `._confidence_stats(memory_id, con)` - *Accepted supports/contradictions from active memories about one memory.*
  - Method `._recompute_confidence(memory_id, con)` - *Persist the evidence-based confidence; callers already hold the write lock.*
  - Method `.bayesian_confidence(memory_id)` - *Laplace-smoothed belief from accepted evidence: (s+1) / (s+c+2).*
  - Method `.accept_link(link_id)` - *Accept a proposal, then recompute the target's confidence in one transaction.*
  - Method `.find_conflicts(memory_ids, limit)` - *Deterministic contradiction candidates: shared terms plus opposite polarity.*
  - Method `.suggest_contradictions(limit)` - *Persist contradiction candidates as reviewable proposals; never edits confidence.*
  - Method `.get_meta_memories(limit, offset)` - *Retrieves meta-memory audit logs.*
  - Method `.create_goal(description, priority, deadline, context)` - *Create a new goal. Returns goal ID.*
  - Method `.get_goals(status, limit, offset)` - *Get goals, optionally filtered by status.*
  - Method `.get_goal(goal_id)` - *Get a single goal by ID.*
  - Method `.update_goal(goal_id, description, priority, status, context, deadline)` - *Update a goal. Returns True if successful.*
  - Method `.delete_goal(goal_id)` - *Delete a goal. Returns True if successful.*
  - Method `.complete_goal(goal_id)` - *Mark a goal as completed. Returns True if successful.*
  - Method `.get_next_goal()` - *Get the highest priority pending/in_progress goal. In_progress goals take priority regardless of lower priority.*
  - Method `.shutdown()` - *Shutdown the ThreadPoolExecutor to prevent resource leak.*

---

### [`modules/memory/consolidation.py`](modules/memory/consolidation.py)
**Size**: `305` lines (`11409` bytes)

**Classes:**
- `class ConsolidationState` - *Tracks the state of the memory consolidation background task.*
  - Method `.last_run_iso()`
  - Method `.to_dict()`
- `class MemoryConsolidator` - *Identifies semantic duplicates and consolidates them using parent_id chaining.*
  - Method `.__init__(config)`
  - Method `._fetch_candidates_with_timestamps()` - *Fetch candidate memories with timestamps for consolidation.*
  - Method `._find_similar_pairs_faiss(threshold)` - *Use FAISS to find similar memory pairs above threshold.*
  - Method `._find_similar_pairs_linear(matrix, ids, threshold)` - *Cosine-similarity fallback used when the FAISS index is unavailable.*
  - Method `._get_processed_pairs()` - *Get already-processed consolidation pairs from meta_memories for idempotency.*
  - Method `._log_consolidation(child_id, parent_id, text_a, text_b)` - *Log consolidation to meta_memories for idempotency tracking.*
  - Method `async def .run()` - *Run consolidation process. Returns number of memories consolidated.*
  - Method `async def ._run_inner()` - *Internal consolidation logic. Returns number of memories consolidated.*
  - Method `async def ._check_entailment_with_timeout(text_a, text_b, timeout)` - *LLM entailment check with timeout protection.*
  - Method `async def ._check_entailment(text_a, text_b)` - *Uses LLM to verify if two texts are semantically equivalent.*

---

### [`modules/memory/node.py`](modules/memory/node.py)
**Size**: `400` lines (`17465` bytes)

**Classes:**
- `class ConfigLoader`
  - Method `.get_config()`
- `class MemoryRecallExecutor`
  - Method `.__init__()`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class MemorySaveExecutor`
  - Method `.__init__()`
  - Method `async def ._save_background(text, subject, source, confidence, mem_type, verified, expires_at, save_delay)` - *Handles embedding generation and saving in the background.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class CheckGoalExecutor`
  - Method `.__init__()`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `def _consolidation_done(task)`
- `async def get_executor_class(node_type_id)`

---

### [`modules/memory/router.py`](modules/memory/router.py)
**Size**: `364` lines (`17227` bytes)

**Functions:**
- `async def get_stats(request)`
- `async def save_memory_settings(request, recall_limit, recall_min_score, save_confidence_threshold, consolidation_threshold, auto_consolidation_hours, arbiter_model, arbiter_prompt, similarity_threshold, belief_ttl_days, recall_access_weight)`
- `async def consolidation_status(request)`
- `async def consolidate_memories(request)`
- `async def wipe_memories(request, confirm)`
- `async def update_memory(request, memory_id, text, mem_type, verified)`
- `async def delete_memory(request, memory_id, reason)`
- `async def merge_memories(request, memory_ids, new_text, new_type, new_verified)`
- `async def get_conflicts(request, memory_ids)`
- `async def scan_conflicts(request, memory_ids)`
- `async def get_meta_memories(request, limit, offset)`
- `async def get_goals(request, status, limit, offset)`
- `async def get_next_goal(request)`
- `async def get_goal(request, goal_id)`
- `async def create_goal(request, description, priority, deadline)`
- `async def update_goal(request, goal_id, description, priority, status, context, deadline)`
- `async def delete_goal(request, goal_id)`
- `async def complete_goal(request, goal_id)`
- `async def list_memory_links(memory_id, relation, status)`
- `async def add_memory_link(request)`
- `async def accept_memory_link(link_id)`
- `async def delete_memory_link(link_id)`
- `async def contradiction_candidates(limit)`
- `async def scan_contradictions(request)`

---

### [`modules/memory_browser/__init__.py`](modules/memory_browser/__init__.py)
**Size**: `1` lines (`26` bytes)

---

### [`modules/memory_browser/router.py`](modules/memory_browser/router.py)
**Size**: `120` lines (`5242` bytes)

**Functions:**
- `def format_timestamp(ts)`
- `async def browser_page(request)`
- `async def browser_gui(request)`
- `async def list_memories(request, q, filter_date, filter_type)`
- `async def delete_memory(request, memory_id)`
- `async def boost_memory(request, memory_id, boost)`
- `async def unboost_memory(request, memory_id)`
- `async def get_memory_provenance(request, memory_id)` - *Return the sessions that have recalled a specific memory entry.*
- `async def get_session_provenance(request, session_id)` - *Return all memories recalled in a specific session.*

---

### [`modules/messaging_bridge/__init__.py`](modules/messaging_bridge/__init__.py)
**Size**: `1` lines (`27` bytes)

---

### [`modules/messaging_bridge/discord_bridge.py`](modules/messaging_bridge/discord_bridge.py)
> *DiscordBridge — thin async wrapper around the Discord REST API and Gateway.*

**Size**: `188` lines (`6998` bytes)

**Classes:**
- `class DiscordBridge` - *Handles communication with the Discord API.*
  - Method `.__init__(bot_token, channel_id, log_fn)`
  - Method `async def ._get_client()`
  - Method `async def .close()`
  - Method `async def .stop()`
  - Method `async def .send_message(content, channel_id)` - *Send a message, splitting into <=1900-char chunks.*
  - Method `async def ._send_heartbeat(ws)`
  - Method `async def ._identify(ws)`
  - Method `async def ._heartbeat_loop(ws)`
  - Method `async def .listen(on_message, running_check)` - *Connect to the Discord Gateway and dispatch MESSAGE_CREATE events.*

---

### [`modules/messaging_bridge/node.py`](modules/messaging_bridge/node.py)
**Size**: `173` lines (`6985` bytes)

**Classes:**
- `class ConfigLoader`
  - Method `.get_config()`
  - Method `.is_enabled()`
- `class MessagingInputExecutor` - *Gate-keeps Repeater triggers and optionally filters by platform.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class MessagingOutputExecutor` - *Routes the flow's ``content`` back to the originating platform.*
  - Method `async def .receive(input_data, config)`
  - Method `async def ._send(platform, content, reply_to)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/messaging_bridge/router.py`](modules/messaging_bridge/router.py)
**Size**: `136` lines (`3982` bytes)

**Functions:**
- `def _success(message)`
- `def _not_found()`
- `async def save_telegram_settings(request, bot_token, chat_id)`
- `async def save_discord_settings(request, bot_token, channel_id)`
- `async def save_signal_settings(request, api_url, phone_number)`
- `async def save_whatsapp_settings(request, api_url, api_key, instance, phone_number)`
- `async def whatsapp_webhook(request)` - *Receives inbound message events pushed by Evolution API.*

---

### [`modules/messaging_bridge/service.py`](modules/messaging_bridge/service.py)
> *MessagingService — unified singleton that manages Telegram, Discord, and Signal bridges.*

**Size**: `651` lines (`25048` bytes)

**Classes:**
- `class _SessionStore` - *Thread-safe session map persisted to sessions.json.*
  - Method `.__init__()`
  - Method `._load()`
  - Method `._save()`
  - Method `.get(key)`
  - Method `.set(key, value)`
  - Method `.delete(key)`
- `class _PlatformBase` - *Common message-processing logic shared by all platform handlers.*
  - Method `._get_or_create_loop()`
  - Method `._session_key(identifier)`
  - Method `._get_or_create_session(identifier, label)`
  - Method `.handle_message(msg)`
  - Method `async def .process_message(msg)`
- `class _TelegramHandler`
  - Method `.__init__()`
  - Method `async def .process_message(msg)`
- `class _DiscordHandler`
  - Method `.__init__()`
  - Method `async def .process_message(msg)`
- `class _SignalHandler`
  - Method `.__init__()`
  - Method `async def .process_message(msg)`
- `class _WhatsAppHandler` - *Processes inbound WhatsApp messages delivered via Evolution API webhooks.*
  - Method `.__init__()`
  - Method `.handle_incoming_webhook(payload)` - *Entry point called by the router for each incoming webhook event.*
  - Method `async def .process_message(msg)`
- `class MessagingService`
  - Method `.__new__()`
  - Method `.start()` - *Start all configured platform listeners.*
  - Method `.restart_platform(platform)` - *Stop and re-start a single platform (called after config save).*
  - Method `.get_handler(platform)` - *Return the handler for a given platform (used by output node and webhook).*
  - Method `._start_telegram(config)`
  - Method `._start_discord(config)`
  - Method `._start_signal(config)`
  - Method `._start_whatsapp(config)`
  - Method `._stop_platform(platform)`
  - Method `._run_listener(platform)`
  - Method `._check_running(platform)`

**Functions:**
- `def _load_config()`
- `def _is_enabled()`
- `async def _run_flow(initial_data)` - *Run the first active flow and return the response text.*

---

### [`modules/messaging_bridge/signal_bridge.py`](modules/messaging_bridge/signal_bridge.py)
> *SignalBridge — async wrapper around the signal-cli REST API.*

**Size**: `145` lines (`5188` bytes)

**Classes:**
- `class SignalBridge` - *Handles communication with signal-cli REST API.*
  - Method `.__init__(api_url, phone_number, log_fn)`
  - Method `async def ._get_client()`
  - Method `async def .stop()`
  - Method `async def .close()`
  - Method `async def .send_message(text, recipient)` - *Send a text message to *recipient* (E.164 phone number, e.g. +1234567890).*
  - Method `async def .receive_messages()` - *Fetch and return queued inbound messages via the REST API.*
  - Method `async def .listen(on_message, running_check)` - *Poll for inbound messages with exponential backoff.*

---

### [`modules/messaging_bridge/telegram_bridge.py`](modules/messaging_bridge/telegram_bridge.py)
**Size**: `184` lines (`7510` bytes)

**Classes:**
- `class TelegramBridge` - *Handles communication with Telegram API using async httpx.*
  - Method `.__init__(bot_token, chat_id, log_fn)`
  - Method `async def ._get_client()`
  - Method `async def .stop()`
  - Method `async def .close()`
  - Method `async def ._send_message_chunk(text, chat_id)`
  - Method `async def .send_message(text, chat_id)` - *Send a message, splitting into <=3072-char chunks.*
  - Method `async def .get_messages()` - *Long-poll for new messages.*
  - Method `async def .get_file_info(file_id)`
  - Method `async def .download_file(file_path, save_path, max_size_mb)`
  - Method `async def .listen(on_message, running_check)` - *Poll for messages with exponential backoff.*

---

### [`modules/messaging_bridge/whatsapp_bridge.py`](modules/messaging_bridge/whatsapp_bridge.py)
> *WhatsAppBridge — async wrapper around the Evolution API for WhatsApp messaging.*

**Size**: `145` lines (`5013` bytes)

**Classes:**
- `class WhatsAppBridge` - *Handles outbound WhatsApp messaging via the Evolution API REST endpoint.*
  - Method `.__init__(api_url, api_key, instance_name, log_fn)`
  - Method `async def ._get_client()`
  - Method `async def .close()`
  - Method `async def .send_message(text, recipient)` - *Send a text message to *recipient* (JID or plain phone number).*
  - Method `.parse_webhook(payload)` - *Extract a normalised message dict from an Evolution API webhook payload.*

**Functions:**
- `def _to_jid(phone)` - *Normalise a phone number or JID to a WhatsApp JID string.*
- `def _from_jid(jid)` - *Extract the plain phone number from a WhatsApp JID.*

---

### [`modules/meta/__init__.py`](modules/meta/__init__.py)
> *Meta-flow module: runs a stored flow as one ganglion node.*

**Size**: `1` lines (`65` bytes)

---

### [`modules/meta/node.py`](modules/meta/node.py)
> *Subflow executor: a ganglion is a reference to another stored flow.*

**Size**: `47` lines (`1677` bytes)

**Classes:**
- `class SubflowExecutor` - *Run the flow named by ``config['flow_id']`` and pass its output through.*
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/planner/node.py`](modules/planner/node.py)
**Size**: `716` lines (`29854` bytes)

**Classes:**
- `class PlannerExecutor`
  - Method `.__init__()`
  - Method `._generate_plan_context(plan, current_step, completed_steps)` - *Generate plan_context string showing progress (used in auto_track mode).*
  - Method `._build_dependency_graph(plan)` - *Build adjacency list and in-degree count for topological sort.*
  - Method `._detect_circular_dependencies(plan)` - *Detect circular dependencies using Kahn's algorithm.*
  - Method `._get_executable_steps(plan, completed_steps)` - *Get list of step indices that can be executed (all dependencies satisfied).*
  - Method `._run_auto_track(data)` - *Run one progression tick (tracker behavior) when auto_track is enabled.*
  - Method `._get_reasoning_service()` - *Lazy load ReasoningBookService to avoid circular imports.*
  - Method `._extract_user_message(input_data)` - *Extract the latest user message from input data.*
  - Method `async def .receive(input_data, config)` - *Creates a step-by-step plan for the user's request.*
  - Method `async def .send(processed_data)`
- `class PlanStepTracker` - *Tracks progress through a plan with dependency-aware ordering.*
  - Method `._generate_plan_context(plan, current_step, completed_steps)` - *Generate plan_context string showing progress.*
  - Method `._build_dependency_graph(plan)` - *Build adjacency list and in-degree count for topological sort.*
  - Method `._detect_circular_dependencies(plan)` - *Detect circular dependencies using Kahn's algorithm.*
  - Method `._get_executable_steps(plan, completed_steps)` - *Get list of step indices that can be executed (all dependencies satisfied).*
  - Method `async def .receive(data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/reasoning_book/__init__.py`](modules/reasoning_book/__init__.py)
**Size**: `1` lines (`26` bytes)

---

### [`modules/reasoning_book/node.py`](modules/reasoning_book/node.py)
**Size**: `90` lines (`3269` bytes)

**Classes:**
- `class ReasoningSaveExecutor`
  - Method `async def .receive(data, config)`
  - Method `async def .send(data)`
- `class ReasoningLoadExecutor`
  - Method `async def .receive(data, config)`
  - Method `async def .send(data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/reasoning_book/router.py`](modules/reasoning_book/router.py)
**Size**: `67` lines (`2818` bytes)

**Functions:**
- `def format_reasoning_content(content)` - *Extracts the actual content from a raw LLM response dictionary string.*
- `async def reasoning_page(request, module_manager, settings_man)` - *Serves the full page shell with the reasoning book active.*
- `async def reasoning_gui(request)` - *Serves the inner content of the reasoning book.*
- `async def get_thoughts(request)` - *Returns just the list of thoughts for polling.*
- `async def clear_thoughts(request, confirm)`

---

### [`modules/reasoning_book/service.py`](modules/reasoning_book/service.py)
**Size**: `120` lines (`4533` bytes)

**Classes:**
- `class ReasoningBookService`
  - Method `.__init__(data_file)`
  - Method `._load()`
  - Method `._save()`
  - Method `async def .log_thought(content, source, step_id, parent_thought_id, tags, session_id)` - *Log a thought with optional structured metadata.*
  - Method `.get_thoughts()` - *Return a copy of thoughts to avoid internal state corruption.*
  - Method `async def .clear()` - *Clear all thoughts. Thread-safe with lock.*
  - Method `async def .reload()` - *Reload thoughts from disk. Thread-safe.*
  - Method `.get_thought_by_id(thought_id)` - *Get a specific thought by its ID.*
  - Method `.get_thoughts_by_step(step_id)` - *Get all thoughts associated with a specific step.*
  - Method `.get_thoughts_by_tag(tag)` - *Get all thoughts with a specific tag.*
  - Method `.get_thoughts_by_session(session_id)` - *Get all thoughts from a specific session.*

---

### [`modules/reflection/node.py`](modules/reflection/node.py)
**Size**: `289` lines (`13083` bytes)

**Classes:**
- `class ReflectionExecutor`
  - Method `.__init__()`
  - Method `._get_reasoning_service()` - *Lazy load ReasoningBookService to avoid circular imports.*
  - Method `._extract_user_message(input_data)` - *Extract the original user request from messages.*
  - Method `._extract_assistant_response(input_data)` - *Extract the latest assistant response.*
  - Method `._inject_improvement_message(input_data, needs_improvement, user_request)` - *Inject an improvement feedback message into the conversation so that*
  - Method `async def .receive(input_data, config)` - *Evaluates whether the agent's response satisfies the user's request.*
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/skills/__init__.py`](modules/skills/__init__.py)
> *Skills module for NeuroCore - manages instruction files (SKILL.md) containing *

**Size**: `10` lines (`292` bytes)

---

### [`modules/skills/backend.py`](modules/skills/backend.py)
> *Backend for Skills module - handles file storage and retrieval of SKILL.md files.*

**Size**: `291` lines (`10687` bytes)

**Classes:**
- `class SkillBackend` - *Handles file-based storage for skills.*
  - Method `.__init__(storage_path)`
  - Method `._ensure_storage_exists()` - *Ensure storage directory and metadata file exist.*
  - Method `._save_metadata(metadata)` - *Save skills metadata to JSON file.*
  - Method `._load_metadata()` - *Load skills metadata from JSON file.*
  - Method `._valid_skill_id(skill_id)`
  - Method `._get_skill_path(skill_id)` - *Get file path for a skill's markdown file; reject anything that can escape it.*
  - Method `._generate_skill_id(name)` - *Generate a URL-friendly skill ID from name.*
  - Method `.list_skills()` - *List all skills with their metadata.*
  - Method `.get_skill(skill_id)` - *Get a skill's metadata and content.*
  - Method `.create_skill(name, description, content, category, tags)` - *Create a new skill.*
  - Method `.update_skill(skill_id, name, description, content, category, tags)` - *Update an existing skill.*
  - Method `.delete_skill(skill_id)` - *Delete a skill.*
  - Method `.import_skill(skill_id, name, description, content, category, tags)` - *Import a skill with a specific ID (used for import/export).*
  - Method `.get_skill_content_only(skill_id)` - *Get only the content of a skill (for system prompt injection).*
  - Method `.skill_exists(skill_id)` - *Check if a skill exists.*
  - Method `.list_versions(skill_id)` - *Version history for one skill, oldest first.*
  - Method `.get_version(skill_id, version)` - *One version, including its content.*
  - Method `.rollback(skill_id, version)` - *Restore a version's content; the restore is itself recorded as a new version.*

---

### [`modules/skills/router.py`](modules/skills/router.py)
> *Router for Skills module - API endpoints for skill management.*

**Size**: `244` lines (`8086` bytes)

**Functions:**
- `async def skills_gui(request)` - *Returns the skills management GUI.*
- `async def list_skills(category, tag)` - *List all skills with optional filtering.*
- `async def get_categories()` - *Get all unique skill categories.*
- `async def get_tags()` - *Get all unique skill tags.*
- `async def get_skill(skill_id)` - *Get a specific skill by ID.*
- `async def create_skill(request)` - *Create a new skill.*
- `async def update_skill(skill_id, request)` - *Update an existing skill.*
- `async def delete_skill(skill_id)` - *Delete a skill.*
- `async def import_from_file(file)` - *Import a skill from an uploaded file.*
- `async def import_from_url(request)` - *Import a skill from a URL.*
- `async def export_skill(skill_id, format)` - *Export a skill in various formats.*
- `async def get_skill_content(skill_id)` - *Get only the content of a skill (for system prompt injection).*
- `async def get_enabled_skills_content(request)` - *Get content for multiple skills (for system prompt injection).*
- `async def list_skill_versions(skill_id)` - *Version history for one skill, newest last, with the active version.*
- `async def get_skill_version(skill_id, version)` - *One skill version, including its exact content.*
- `async def rollback_skill(skill_id, version)` - *Restore a version; the restore is recorded as a new version, never a rewrite.*

---

### [`modules/skills/service.py`](modules/skills/service.py)
> *Service layer for Skills module - business logic for skill management.*

**Size**: `353` lines (`15275` bytes)

**Classes:**
- `class SkillService` - *Business logic for skill management.*
  - Method `.__init__(storage_path)`
  - Method `.list_skills(category, tag)` - *List all skills with optional filtering.*
  - Method `.get_skill(skill_id)` - *Get a skill by ID.*
  - Method `.create_skill(name, description, content, category, tags)` - *Create a new skill.*
  - Method `.update_skill(skill_id, **kwargs)` - *Update an existing skill.*
  - Method `.delete_skill(skill_id)` - *Delete a skill.*
  - Method `.import_from_file(file_content, file_name)` - *Import a skill from file content.*
  - Method `.import_from_url(url)` - *Import a skill from a URL with SSRF protection.*
  - Method `.export_skill(skill_id)` - *Export a skill for download.*
  - Method `.get_categories()` - *Get all unique categories.*
  - Method `.get_all_tags()` - *Get all unique tags across all skills.*
  - Method `.get_enabled_skills_content(skill_ids)` - *Get content for enabled skills (for system prompt injection).*
  - Method `._validate_skill_content(content)` - *Validate that skill content is properly formatted.*
  - Method `._extract_metadata_from_content(content, file_name)` - *Try to extract metadata from skill content, supporting YAML frontmatter.*
  - Method `._extract_title(content)` - *Extract title from markdown content (first H1).*
  - Method `._extract_description(content)` - *Extract description from content (first paragraph after title).*
  - Method `._clean_file_name(file_name)` - *Clean file name to use as skill name.*
  - Method `._generate_import_id(name)` - *Generate a unique ID for imported skill.*
  - Method `._create_export_markdown(skill)` - *Create a formatted markdown export of a skill with YAML frontmatter.*

---

### [`modules/skills/versions.py`](modules/skills/versions.py)
> *Content-addressed versions for skill documents.*

**Size**: `149` lines (`6024` bytes)

**Classes:**
- `class SkillVersionStore` - *Per-skill version history rooted beside the skill storage.*
  - Method `.__init__(root)`
  - Method `._skill_dir(skill_id)`
  - Method `._index_path(skill_id)`
  - Method `._load_index(skill_id)`
  - Method `._save_index(skill_id, index)`
  - Method `.record(skill_id, content, author, note)` - *Store one version of the content and mark it active.*
  - Method `._prune(skill_dir, index)`
  - Method `.versions(skill_id)`
  - Method `.active(skill_id)`
  - Method `.read(skill_id, version)`

**Functions:**
- `def _now()`
- `def _atomic_write(path, content)`

---

### [`modules/system_prompt/__init__.py`](modules/system_prompt/__init__.py)
**Size**: `1` lines (`22` bytes)

---

### [`modules/system_prompt/node.py`](modules/system_prompt/node.py)
**Size**: `302` lines (`14346` bytes)

**Classes:**
- `class SystemPromptExecutor`
  - Method `async def ._load_available_tools()` - *Load available tools from the tools module (cached by mtime with async lock).*
  - Method `async def ._get_tools_in_openai_format(enabled_tool_names)` - *Convert enabled tools to OpenAI tool format.*
  - Method `async def ._load_skills_content(enabled_skills)` - *Load content from enabled skills (cached by mtime). Uses async to avoid blocking.*
  - Method `._load_skills_from_disk(storage_path, metadata_file, enabled_skills)` - *Helper to load skills content from disk.*
  - Method `async def .receive(input_data, config)` - *Receives the current conversation state and prepends a system message with available tools.*
  - Method `._estimate_tokens(text)` - *Token estimation with CJK support and tiktoken fallback.*
  - Method `._apply_token_budget(base_prompt, context_parts, max_tokens)` - *Apply token budget management with priority-based trimming.*
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/system_prompt/router.py`](modules/system_prompt/router.py)
**Size**: `95` lines (`3824` bytes)

**Functions:**
- `async def _load_tools_cached()` - *Load available tools from the tools module with mtime-based caching.*
- `async def get_available_tools(request, module_manager)` - *Get available tools with checkboxes.*

---

### [`modules/tools/__init__.py`](modules/tools/__init__.py)
**Size**: `1` lines (`26` bytes)

---

### [`modules/tools/library/ArXivSearch.py`](modules/tools/library/ArXivSearch.py)
**Size**: `69` lines (`2727` bytes)

---

### [`modules/tools/library/BrowserAction.py`](modules/tools/library/BrowserAction.py)
**Size**: `57` lines (`2286` bytes)

---

### [`modules/tools/library/calculator.py`](modules/tools/library/calculator.py)
**Size**: `132` lines (`4153` bytes)

**Functions:**
- `def evaluate_node(node, variables)` - *Safe AST-based expression evaluator.*

---

### [`modules/tools/library/CheckCalendar.py`](modules/tools/library/CheckCalendar.py)
**Size**: `21` lines (`782` bytes)

---

### [`modules/tools/library/ConversionCalculator.py`](modules/tools/library/ConversionCalculator.py)
**Size**: `129` lines (`2766` bytes)

---

### [`modules/tools/library/CurrencyConverter.py`](modules/tools/library/CurrencyConverter.py)
**Size**: `74` lines (`3781` bytes)

---

### [`modules/tools/library/DeleteGoal.py`](modules/tools/library/DeleteGoal.py)
**Size**: `31` lines (`1101` bytes)

---

### [`modules/tools/library/FetchURL.py`](modules/tools/library/FetchURL.py)
**Size**: `64` lines (`2479` bytes)

---

### [`modules/tools/library/GetGoals.py`](modules/tools/library/GetGoals.py)
**Size**: `45` lines (`1436` bytes)

---

### [`modules/tools/library/GetNextGoal.py`](modules/tools/library/GetNextGoal.py)
**Size**: `34` lines (`1265` bytes)

---

### [`modules/tools/library/MarkGoalComplete.py`](modules/tools/library/MarkGoalComplete.py)
**Size**: `31` lines (`1101` bytes)

---

### [`modules/tools/library/SaveReminder.py`](modules/tools/library/SaveReminder.py)
**Size**: `18` lines (`539` bytes)

---

### [`modules/tools/library/SendEmail.py`](modules/tools/library/SendEmail.py)
**Size**: `42` lines (`1535` bytes)

---

### [`modules/tools/library/SetGoal.py`](modules/tools/library/SetGoal.py)
**Size**: `36` lines (`1165` bytes)

---

### [`modules/tools/library/SystemTime.py`](modules/tools/library/SystemTime.py)
**Size**: `4` lines (`159` bytes)

---

### [`modules/tools/library/TimeZoneConverter.py`](modules/tools/library/TimeZoneConverter.py)
**Size**: `37` lines (`1210` bytes)

---

### [`modules/tools/library/Weather.py`](modules/tools/library/Weather.py)
**Size**: `36` lines (`1558` bytes)

---

### [`modules/tools/library/WikipediaLookup.py`](modules/tools/library/WikipediaLookup.py)
**Size**: `59` lines (`2071` bytes)

---

### [`modules/tools/library/YouTubeTranscript.py`](modules/tools/library/YouTubeTranscript.py)
**Size**: `37` lines (`1362` bytes)

---

### [`modules/tools/node.py`](modules/tools/node.py)
**Size**: `175` lines (`7434` bytes)

**Classes:**
- `class ToolDispatcherExecutor`
  - Method `.__init__()`
  - Method `._permitted_environment()` - *Only environment values the tools manifest explicitly authorizes reach a tool.*
  - Method `._load_tools()`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def get_executor_class(node_type_id)`

---

### [`modules/tools/rlm_library/Chunk.py`](modules/tools/rlm_library/Chunk.py)
**Size**: `44` lines (`1427` bytes)

---

### [`modules/tools/rlm_library/GetVariable.py`](modules/tools/rlm_library/GetVariable.py)
**Size**: `29` lines (`893` bytes)

---

### [`modules/tools/rlm_library/Peek.py`](modules/tools/rlm_library/Peek.py)
**Size**: `27` lines (`893` bytes)

---

### [`modules/tools/rlm_library/Search.py`](modules/tools/rlm_library/Search.py)
**Size**: `47` lines (`1569` bytes)

---

### [`modules/tools/rlm_library/SetFinal.py`](modules/tools/rlm_library/SetFinal.py)
**Size**: `17` lines (`566` bytes)

---

### [`modules/tools/rlm_library/SetVariable.py`](modules/tools/rlm_library/SetVariable.py)
**Size**: `24` lines (`768` bytes)

---

### [`modules/tools/rlm_library/SubCall.py`](modules/tools/rlm_library/SubCall.py)
**Size**: `69` lines (`2242` bytes)

---

### [`modules/tools/router.py`](modules/tools/router.py)
**Size**: `431` lines (`16201` bytes)

**Functions:**
- `def _sanitize_tool_name(name)` - *Sanitize tool name to prevent path traversal attacks.*
- `def load_tools()` - *Load tools from JSON file with file locking for thread safety.*
- `def save_tools(tools)` - *Save tools to JSON file with file locking for thread safety.*
- `async def load_tools_async()` - *Async wrapper for load_tools to avoid blocking event loop.*
- `async def save_tools_async(tools)` - *Async wrapper for save_tools to avoid blocking event loop.*
- `async def tools_page(request)` - *Loads the main dashboard with the Tool Library active.*
- `async def tools_gui(request)` - *Returns the Tool Library GUI fragment.*
- `async def tools_list(request)`
- `async def save_tool(request, name, description, parameters, code, original_name)`
- `async def toggle_tool(request, name)`
- `async def delete_tool(request, name)`
- `async def edit_tool(name)` - *Returns tool metadata and code for editing.*
- `async def get_tool_names()` - *Returns a list of all tool names.*
- `async def get_tools_with_descriptions()` - *Returns all tools with their names and descriptions.*
- `async def get_definitions()` - *Returns the JSON definitions for all tools to be used in LLM config.*
- `async def export_tools(name)`
- `async def get_tool_config(request, name)` - *Returns the configuration form for a tool.*
- `async def save_tool_config(request, name)` - *Saves the configuration for a tool.*
- `async def import_tools(request, file)`

---

### [`modules/tools/sandbox.py`](modules/tools/sandbox.py)
> *Tool Execution Sandbox for NeuroCore*

**Size**: `1072` lines (`46161` bytes)

**Classes:**
- `class SecurityError` - *Raised when sandbox security policy is violated.*
- `class ResourceLimitError` - *Raised when resource limits are exceeded.*
- `class TimeoutError` - *Raised when tool execution exceeds time limit.*
- `class SandboxStartupError` - *Raised when the isolated worker cannot start.*
- `class RestrictedImport` - *Import hook that blocks dangerous modules and only allows safe ones.*
  - Method `.__init__(allowed_modules, mock_modules, passthrough_modules)`
  - Method `._is_passthrough(name)` - *Whether `name` is exactly, or a submodule of, an allowlisted first-party module.*
  - Method `.__call__(name, globals, locals, fromlist, level)`
- `class SafeOpen` - *Restricted file open that only allows access to specific directories.*
  - Method `.__init__(allowed_dirs, read_only)`
  - Method `._is_path_allowed(path)` - *Check if a path is within allowed directories.*
  - Method `.__call__(file, mode, *args, **kwargs)`
- `class SafeHttpxClient` - *Restricted HTTP client that enforces network security policies.*
  - Method `.__init__(allowed_domains, timeout, max_response_size, max_redirects)`
  - Method `._is_domain_allowed(url)` - *Check if a URL's domain is in the allowed list.*
  - Method `._is_ip_blocked(host)` - *Check if a host/IP is in blocked ranges using CIDR notation.*
  - Method `._resolve_and_validate_ip(hostname)` - *Resolve hostname and validate every resulting IP.*
  - Method `._is_loopback(host)`
  - Method `._validate_control_plane_path(parsed)` - *Allow only the tool APIs when the target is NeuroCore's own port on loopback.*
  - Method `._validate_target(url)` - *Validate scheme, allowlist and every DNS answer for one request target.*
  - Method `.request(method, url, **kwargs)` - *Make a restricted HTTP request.*
  - Method `._without_credentials(kwargs)` - *Request options with credentials removed, for a redirect to another origin.*
  - Method `.get(url, **kwargs)`
  - Method `.post(url, **kwargs)`
  - Method `.delete(url, **kwargs)`
  - Method `.put(url, **kwargs)`
  - Method `.patch(url, **kwargs)`
- `class SafeEnv` - *Restricted mock for the ``os`` module.*
  - Method `.__init__(permitted)`
  - Method `.getenv(key, default)`
  - Method `.__getattr__(name)`
- `class ToolSandbox` - *Main sandbox class for executing tool code securely.*
  - Method `.__init__(timeout, max_output_size, allowed_file_dirs, allowed_domains, read_only_files, max_memory_mb, permitted_environment)`
  - Method `._build_restricted_globals()` - *Build a dictionary of restricted globals for sandboxed execution.*
  - Method `._check_code_safety(code)` - *Static analysis to check for dangerous code patterns.*
  - Method `._execute_internal(code, local_vars)` - *Internal execution without multiprocessing - used by child process.*
  - Method `.execute(code, local_vars)` - *Execute code in the sandboxed environment using multiprocessing for true isolation.*

**Functions:**
- `def _execute_in_process(code, local_vars, result_file, allowed_file_dirs, read_only_files, allowed_domains, timeout, max_output_size, max_memory_mb, permitted_environment)` - *Execute code in a separate process with restricted environment.*
- `def execute_sandboxed(code, args, timeout, allowed_domains)` - *Execute tool code in a sandbox with the given arguments.*

---

### [`tests/conftest.py`](tests/conftest.py)
> *Pytest configuration and fixtures for test isolation.*

**Size**: `495` lines (`17692` bytes)

**Classes:**
- `class FileBackupManager` - *Manages backup and restore operations for files during tests.*
  - Method `.__init__()`
  - Method `.backup_file(filepath)` - *Back up a file if it exists.*
  - Method `.restore_file(filepath)` - *Restore a file from backup if one exists.*
  - Method `.cleanup()` - *Clean up any temporary files.*

**Functions:**
- `def pytest_sessionfinish(session, exitstatus)`
- `def reset_shared_async_state()` - *Reset module-level async singletons between tests.*
- `def disable_debug_mode_for_tests()` - *Force debug_mode=False during every test.*
- `def restore_real_faiss_module(request)` - *Restore sys.modules['faiss'] to the real FAISS library for tests that need it.*
- `def backup_manager()` - *Session-scoped fixture to manage file backups across tests.*
- `def clear_flow_outcome_listener()` - *Never let a test brain capture flow outcomes from later tests.*
- `def _snapshot_protected_file(filepath)` - *The file's bytes, refusing to start from a JSON file that is already corrupt.*
- `def _restore_protected_file(filepath, content)` - *Replace the file atomically so no reader ever sees it truncated.*
- `def protect_module_files()` - *Restore module.json files (and legacy root state files) that a test modified.*
- `def brain_module_enabled(request, monkeypatch)` - *Load the opt-in brain module for brain tests without editing its manifest on disk.*
- `def temp_module_dir(tmp_path)` - *Provides a temporary directory for creating test module structures.*
- `def sample_module_json()` - *Provides a sample module.json structure for testing.*
- `def backup_specific_file()` - *Fixture to backup a specific file before a test and restore it after.*
- `def isolated_temp_dir(tmp_path)` - *Provides an isolated temporary directory with proper cleanup.*
- `def mock_settings_file(tmp_path, monkeypatch)` - *Creates a temporary settings file for testing settings functionality.*
- `def module_json_backup()` - *Explicit fixture for backing up and restoring specific module.json files.*

---

### [`tests/run_tests.py`](tests/run_tests.py)
**Size**: `55` lines (`1956` bytes)

**Functions:**
- `def run_tests()`

---

### [`tests/test_agent_loop.py`](tests/test_agent_loop.py)
> *Tests for modules/agent_loop/node.py*

**Size**: `1688` lines (`67920` bytes)

**Classes:**
- `class TestAgentLoopBasic` - *Tests for basic agent loop functionality.*
  - Method `async def .test_none_input_returns_empty_dict()` - *None input should be treated as empty dict and returned unchanged (no messages).*
  - Method `async def .test_empty_messages_returns_input_unchanged()` - *When messages list is empty the input should be returned as-is.*
  - Method `async def .test_basic_no_tool_calls()` - *Single LLM call with no tool calls should complete in 1 iteration.*
  - Method `async def .test_tool_call_executed_and_result_appended()` - *Tool calls should be executed and results appended to messages.*
  - Method `async def .test_max_iterations_respected()` - *Loop must stop at max_iterations even if LLM keeps returning tool calls.*
  - Method `async def .test_send_returns_data_unchanged()` - *send() must be a passthrough.*
  - Method `async def .test_unknown_tool_produces_error_in_trace()` - *Calling a tool not in the library should produce an error in the trace.*
  - Method `async def .test_output_contains_messages_key()` - *Result must always contain a 'messages' key when messages were provided.*
  - Method `async def .test_response_key_present_in_output()` - *Result must contain the raw 'response' dict from the LLM.*
- `class TestLLMRetry` - *Tests for _llm_with_retry() and its integration in receive().*
  - Method `async def .test_retry_on_error_response()` - *LLM should be retried when the response contains an 'error' key.*
  - Method `async def .test_retry_on_none_response()` - *LLM should be retried when the response is None.*
  - Method `async def .test_retry_on_missing_choices()` - *LLM should be retried when the response has no 'choices' key.*
  - Method `async def .test_exponential_backoff_delays()` - *Retry delays must follow the pattern: delay * 2^(attempt-1).*
  - Method `async def .test_all_retries_exhausted_records_error_in_trace()` - *When all retries are exhausted the trace should record the LLM error.*
  - Method `async def .test_llm_exception_triggers_retry()` - *An exception raised by chat_completion should trigger a retry.*
  - Method `async def .test_no_retry_when_max_llm_retries_zero()` - *With max_llm_retries=0 there should be exactly one LLM call and no sleep.*
  - Method `async def .test_successful_first_attempt_no_sleep()` - *When the first LLM call succeeds, no sleep should occur.*
- `class TestToolErrorStrategy` - *Tests for tool_error_strategy: continue vs stop.*
  - Method `async def .test_continue_strategy_keeps_looping_after_tool_error()` - *With strategy='continue', a tool error should not stop the loop.*
  - Method `async def .test_stop_strategy_halts_loop_on_tool_error()` - *With strategy='stop', a tool error should stop the loop immediately.*
  - Method `async def .test_tool_error_recorded_in_trace()` - *Tool errors should be recorded in the iteration trace.*
  - Method `async def .test_successful_tool_no_error_in_trace()` - *Successful tool execution should not produce errors in the trace.*
  - Method `async def .test_multiple_tools_per_turn_all_recorded()` - *Multiple tool calls in one turn should all appear in the trace.*
- `class TestTimeout` - *Tests for timeout support via asyncio.wait_for.*
  - Method `async def .test_timeout_returns_error_result()` - *When the loop times out, result should contain agent_loop_error.*
  - Method `async def .test_timeout_zero_disables_timeout()` - *timeout=0 should disable the timeout entirely.*
  - Method `async def .test_timeout_preserves_input_data_keys()` - *On timeout, original input_data keys should be preserved in result.*
  - Method `async def .test_timeout_error_message_includes_duration()` - *The timeout error message should mention the configured timeout value.*
- `class TestSystemPromptInjection` - *Tests for context-based system prompt injection.*
  - Method `async def .test_plan_context_injected_into_system_prompt()` - *plan_context should be injected as a system message.*
  - Method `async def .test_memory_context_injected()` - *_memory_context should be injected into the system message.*
  - Method `async def .test_existing_system_message_is_extended()` - *If a system message already exists, context should be appended to it.*
  - Method `async def .test_no_context_no_system_message_added()` - *Without any context fields, no system message should be injected.*
  - Method `async def .test_knowledge_context_injected()` - *knowledge_context should be injected into the system message.*
  - Method `async def .test_context_disabled_via_config()` - *Setting include_plan_in_context=False should suppress plan injection.*
- `class TestGetExecutorClass` - *Tests for the get_executor_class module-level dispatcher.*
  - Method `async def .test_returns_agent_loop_executor_for_correct_id()` - *get_executor_class('agent_loop') should return AgentLoopExecutor.*
  - Method `async def .test_returns_none_for_unknown_id()` - *get_executor_class with an unknown id should return None.*
  - Method `async def .test_executor_class_is_instantiable()` - *The returned class should be instantiable without arguments.*
- `class TestLLMWithRetryDirect` - *Direct unit tests for the _llm_with_retry helper method.*
  - Method `async def .test_returns_valid_response_immediately()` - *Should return on the first attempt when response is valid.*
  - Method `async def .test_returns_none_after_all_retries()` - *Should return None when all retries are exhausted.*
  - Method `async def .test_passes_tools_to_llm()` - *Tools list should be forwarded to chat_completion.*
  - Method `async def .test_empty_tools_passes_none()` - *Empty tools list should be passed as None to avoid API errors.*
- `class TestIntegration` - *Integration-style tests combining multiple features.*
  - Method `async def .test_retry_then_tool_call_then_finish()` - *LLM fails once, retries, then makes a tool call, then finishes.*
  - Method `async def .test_full_pipeline_with_context_and_tools()` - *Full pipeline: plan context + tool call + final answer.*
  - Method `async def .test_input_data_preserved_in_output()` - *All original input_data keys should be preserved in the output.*
- `class TestContextCompaction` - *Tests for _compact_messages and the compact_threshold config key.*
  - Method `._make_executor()`
  - Method `._make_messages(n_turns, system)`
  - Method `async def .test_compact_messages_returns_original_when_short()`
  - Method `async def .test_compact_messages_summarizes_old_turns()`
  - Method `async def .test_compact_messages_fails_gracefully_on_llm_error()`
  - Method `async def .test_compact_messages_preserves_no_system()`
  - Method `async def .test_estimate_messages_tokens()`
  - Method `async def .test_compact_threshold_triggers_in_loop()` - *compact_threshold in config causes compaction during _run_agent_loop.*
  - Method `async def .test_estimate_tokens_counts_tool_calls_json()` - *tool_calls JSON on assistant messages must be included in token estimate.*
  - Method `async def .test_estimate_tokens_none_content_does_not_crash()` - *content=None (typical for tool-calling assistant turns) must not raise.*
  - Method `async def .test_summary_budget_scales_with_message_count()` - *More messages to summarize → larger max_tokens budget (up to 800).*
  - Method `async def .test_summary_budget_floors_at_200()` - *Very few messages → budget floors at 200.*
  - Method `async def .test_compaction_cooldown_prevents_immediate_refire()` - *After a compaction, the loop must not compact again until compact_keep_last*
  - Method `async def .test_compact_threshold_zero_disables_compaction()` - *compact_threshold=0 means no compaction ever fires.*
- `class TestHybridAgentExecutor` - *Tests for HybridAgentExecutor — adaptive output routing and combined tool library.*
  - Method `._make_executor()`
  - Method `._repl_state()`
  - Method `.test_excluded_tools_set()` - *Only SetFinal is excluded; Peek/Search/Chunk/SubCall are now hybrid-compatible.*
  - Method `.test_build_variable_system_note_contains_threshold()`
  - Method `async def .test_small_output_stays_inline()`
  - Method `async def .test_large_output_stored_as_variable()`
  - Method `async def .test_variable_counter_increments()`
  - Method `.test_variable_inventory_msg_empty()`
  - Method `.test_variable_inventory_msg_lists_vars()`
  - Method `async def .test_receive_no_messages_returns_error()`
  - Method `async def .test_receive_no_tool_calls_terminates()`
  - Method `async def .test_receive_large_output_routed_to_variable()`
  - Method `async def .test_receive_timeout()`
  - Method `async def .test_receive_injects_system_note()`
  - Method `async def .test_thinking_steps_populated()`
  - Method `async def .test_sub_call_executes_llm()`
  - Method `async def .test_sub_call_respects_max_sub_calls()`
  - Method `async def .test_sub_call_intercepted_in_execute_tool()` - *SubCall tool_call must be handled as built-in, not via sandbox.*
  - Method `async def .test_get_executor_class_hybrid()`
  - Method `async def .test_send_passthrough()`
- `class TestBugFixes` - *Regression tests that cover the specific bugs identified and fixed.*
  - Method `async def .test_duplicate_tool_call_skips_only_duplicate_not_subsequent()` - *After a duplicate, remaining unique tool calls in the same batch must still execute.*
  - Method `async def .test_input_messages_not_mutated_by_receive()` - *Original messages list and its dicts must be unchanged after receive().*
  - Method `async def .test_truncate_messages_counts_tool_calls_tokens()` - *_truncate_messages must account for tool_calls payload in token budget.*
  - Method `async def .test_planning_prompt_has_no_double_braces()` - *_DEFAULT_PLANNING_PROMPT must not contain {{ or }} after .replace() processing.*
  - Method `async def .test_replan_prompt_has_no_double_braces()` - *_DEFAULT_REPLAN_PROMPT must not contain {{ or }} after .replace() processing.*
- `class TestHybridBugFixes` - *Regression tests for HybridAgentExecutor-specific fixes.*
  - Method `._make_executor()`
  - Method `async def .test_input_messages_not_mutated_by_hybrid_receive()` - *Hybrid receive() must not mutate the original system message dict.*
  - Method `async def .test_duplicate_tool_call_skips_only_duplicate_in_hybrid()` - *Hybrid loop must continue past a duplicate to execute remaining unique calls.*
- `class TestRLMBugFixes` - *Regression tests for RLMAgentLoopExecutor-specific fixes.*
  - Method `async def .test_rlm_loop_appends_assistant_message_before_tool_results()` - *_run_rlm_loop must insert an assistant message carrying tool_calls*

**Functions:**
- `def make_llm_response(content, tool_calls)` - *Build a minimal valid LLM response dict.*
- `def make_tool_call(name, args, call_id)` - *Build a minimal tool_call dict as returned by the LLM.*
- `def make_executor(tools, library)` - *Create an AgentLoopExecutor with _load_tools and _load_tool_library patched*

---

### [`tests/test_agent_loop_guard.py`](tests/test_agent_loop_guard.py)
> *Agent-loop repetition guard: normalization, warning, abort and trace reporting.*

**Size**: `109` lines (`4054` bytes)

**Functions:**
- `def test_transport_identifiers_do_not_make_a_call_unique()`
- `def test_domain_identifiers_remain_part_of_the_signature()`
- `def test_hex_domain_identifiers_remain_part_of_the_signature()`
- `def test_three_identical_calls_warn_then_abort()`
- `def test_alternating_calls_are_detected_as_oscillation()`
- `def test_reading_many_records_does_not_trip_the_guard()`
- `def test_distinct_calls_do_not_trip_the_guard()`
- `def test_malformed_arguments_still_produce_a_stable_signature()`
- `async def test_the_agent_loop_aborts_after_a_repeated_call()`
- `def test_tool_output_credentials_are_redacted_before_context()`

---

### [`tests/test_agent_loop_replan.py`](tests/test_agent_loop_replan.py)
> *Tests for Agent Loop re-planning functionality*

**Size**: `417` lines (`14927` bytes)

**Functions:**
- `def make_executor()` - *Create an AgentLoopExecutor with patched LLMBridge.*
- `def make_input(messages, plan, current_step, replan_count)` - *Create input data with optional plan.*
- `def make_tool_call(name, args, call_id)` - *Build a minimal tool_call dict as returned by the LLM.*
- `async def test_replan_needed_on_max_iterations_with_failure()` - *Should set replan_needed=True when max_iterations reached AND there's a failure.*
- `async def test_no_replan_when_max_iterations_reached_successfully()` - *Should NOT set replan_needed when max_iterations reached but agent succeeded.*
- `async def test_replan_needed_on_tool_error()` - *Should set replan_needed=True when tool errors occur.*
- `async def test_no_replan_when_successful()` - *Should set replan_needed=False when execution succeeds.*
- `async def test_replan_suggestion_for_multi_step_plan()` - *Should suggest breaking multi-step plans when re-planning.*
- `async def test_replan_suggestion_for_single_step_plan()` - *Should suggest different approach for single-step plan failure.*
- `async def test_replan_suggestion_when_no_plan()` - *Should suggest creating a plan when no plan exists.*
- `async def test_replan_on_timeout()` - *Should set replan_needed=True on timeout.*
- `async def test_replan_on_exception()` - *Should set replan_needed=True on unexpected exception.*
- `async def test_replan_preserves_input_data()` - *Should preserve all input data fields when adding replan info.*
- `async def test_max_replan_depth_enforced()` - *Should hard-stop when replan_count >= max_replan_depth.*
- `async def test_replan_count_increments_each_failure()` - *Should increment replan_count with each re-planning cycle.*
- `async def test_replan_count_reset_on_success()` - *Should reset replan_count to 0 when execution succeeds.*
- `async def test_custom_max_replan_depth()` - *Should respect custom max_replan_depth configuration.*
- `async def test_replan_depth_zero_disables_check()` - *Should allow unlimited re-planning when max_replan_depth=0.*

---

### [`tests/test_annotations_node.py`](tests/test_annotations_node.py)
**Size**: `23` lines (`683` bytes)

**Functions:**
- `async def test_get_executor_class()`
- `async def test_comment_executor_stops_propagation()`

---

### [`tests/test_archive_safety.py`](tests/test_archive_safety.py)
> *Regression tests for M2: ZIP extraction must never escape its destination (zip-slip).*

**Size**: `106` lines (`4042` bytes)

**Functions:**
- `def _write_zip(path, members)`
- `def _write_symlink_zip(path)`
- `def test_rejects_zip_slip(tmp_path)`
- `def test_rejects_absolute_member(tmp_path)`
- `def test_rejects_symlink_member(tmp_path)`
- `def test_extracts_flat_archive(tmp_path)`
- `def test_strips_single_root_directory(tmp_path)`
- `async def test_import_route_refuses_zip_slip(tmp_path, monkeypatch)`
- `async def test_import_route_extracts_safe_module(tmp_path, monkeypatch)`

---

### [`tests/test_autonomous_agent.py`](tests/test_autonomous_agent.py)
> *Tests for the experimental autonomous agent runtime.*

**Size**: `1175` lines (`38639` bytes)

**Classes:**
- `class FakeProposer`
  - Method `.__init__(operations)`
  - Method `async def .propose(flow, goal)`

**Functions:**
- `def make_agent(tmp_path, **overrides)` - *Build an agent whose runtime files live under ``tmp_path``.*
- `async def test_handle_user_message_records_perception_and_chat_ack(tmp_path)`
- `async def test_memory_persists_across_service_instances(tmp_path)`
- `async def test_corrupt_state_is_quarantined(tmp_path)`
- `async def test_valid_json_state_with_wrong_shape_is_quarantined(tmp_path)`
- `async def test_persistence_can_be_disabled(tmp_path)`
- `async def test_flow_creation_registers_ownership(tmp_path)`
- `async def test_created_flow_persists_and_is_adopted(tmp_path)`
- `async def test_flow_update_creates_version_and_delete_removes(tmp_path)`
- `def test_agent_defaults_to_the_shared_flow_manager()`
- `async def test_agent_rejects_unallowed_node_types(tmp_path)`
- `async def test_agent_rollback_restores_and_syncs_the_cache(tmp_path)`
- `async def test_execute_flow_runs_the_engine(tmp_path, monkeypatch)`
- `async def test_evolution_activation_and_execution_end_to_end(tmp_path, monkeypatch)`
- `def test_autonomous_agent_module_remains_disabled()`
- `async def test_evolve_and_evaluate_keeps_a_successful_edit(tmp_path, monkeypatch)`
- `async def test_evolve_and_evaluate_reverts_a_failed_result(tmp_path, monkeypatch)`
- `async def test_evolve_and_evaluate_reverts_on_an_exception(tmp_path, monkeypatch)`
- `async def test_update_flow_action_forwards_evaluation_input(tmp_path, monkeypatch)`
- `async def test_planner_emits_update_flow_from_metadata(tmp_path)`
- `async def test_agent_composes_owned_flows_into_a_meta_flow(tmp_path)`
- `async def test_agent_cannot_compose_an_unowned_flow(tmp_path)`
- `async def test_planner_emits_compose_flow_from_metadata(tmp_path)`
- `async def test_planner_emits_compose_flow_from_text(tmp_path)`
- `async def test_execute_compose_action_calls_the_composer(tmp_path, monkeypatch)`
- `async def test_agent_composes_a_live_meta_flow(tmp_path)`
- `async def test_agent_can_evolve_a_live_meta_flow(tmp_path)`
- `async def test_sandbox_rejects_compose_with_an_unowned_member(tmp_path)`
- `async def test_sandbox_allows_compose_with_owned_members(tmp_path)`
- `async def test_propose_evolution_rejects_a_flow_not_owned(tmp_path)`
- `async def test_propose_and_evolve_applies_llm_operations(tmp_path, monkeypatch)`
- `async def test_update_flow_action_with_instruction_uses_proposer(tmp_path, monkeypatch)`
- `async def test_planner_emits_update_flow_for_an_evolve_instruction(tmp_path)`
- `async def test_execute_flow_integrates_with_the_real_flow_runner(tmp_path)`
- `async def test_execute_action_without_flow_id_uses_the_active_flow(tmp_path, monkeypatch)`
- `async def test_execute_action_rejects_an_explicit_unowned_flow(tmp_path)`
- `async def test_execute_action_skips_a_stale_active_flow(tmp_path, monkeypatch)`
- `def test_activate_flow_rejects_an_unowned_flow(tmp_path)`
- `def test_activate_flow_rejects_a_missing_owned_flow(tmp_path)`
- `def test_activate_flow_updates_settings(tmp_path, monkeypatch)`
- `async def test_agent_handles_a_null_sandbox_config(tmp_path)`
- `async def test_update_flow_rejects_non_dict_metadata(tmp_path)`
- `async def test_execute_flow_rejects_an_unowned_flow(tmp_path)`
- `async def test_blank_description_uses_a_fallback_name(tmp_path)`
- `async def test_agent_evolves_an_owned_flow(tmp_path)`
- `async def test_agent_cannot_evolve_an_unowned_flow(tmp_path)`
- `async def test_flow_update_rejects_a_cycle(tmp_path)`
- `async def test_sandbox_rejects_flow_over_configured_limit(tmp_path)`
- `async def test_load_existing_flows_adopts_only_owned(tmp_path)`
- `async def test_load_existing_flows_skips_malformed_metadata(tmp_path)`
- `async def test_communicator_buffers_and_drains_chat_messages()`
- `async def test_agent_node_processes_single_message(tmp_path)`
- `async def test_every_node_template_dispatches_to_a_real_executor(template)`
- `async def test_every_flow_pattern_builds_a_valid_flow(tmp_path, pattern)`
- `async def test_scheduled_request_without_notification_builds_a_flow(tmp_path)`
- `async def test_start_refuses_when_initialization_fails(tmp_path, monkeypatch)`
- `async def test_unimplemented_actions_are_rejected_not_reported_done(tmp_path, action_type)`

---

### [`tests/test_brain_auth.py`](tests/test_brain_auth.py)
> *Brain actor authentication and remote access gate.*

**Size**: `241` lines (`7674` bytes)

**Functions:**
- `def make_request(host, headers)`
- `def test_local_actor_is_trusted(monkeypatch)`
- `def test_local_actor_stays_trusted_when_a_remote_token_is_configured(monkeypatch)`
- `def test_remote_actor_requires_configured_token(monkeypatch)`
- `def test_remote_actor_with_token(monkeypatch)`
- `def test_actor_registry_hashes_rotates_and_disables(tmp_path)`
- `def test_role_hierarchy()`
- `def test_registry_token_authenticates_remote_actor(tmp_path, monkeypatch)`
- `def test_viewer_cannot_use_admin_route()`
- `def test_actor_admin_routes(tmp_path, monkeypatch)`
- `def test_actor_route_reports_local_identity()`
- `def _counting_registry(tmp_path, monkeypatch)`
- `def test_authentication_hashes_once_however_many_actors_exist(tmp_path, monkeypatch)`
- `def test_token_key_id_reveals_nothing_about_the_secret(tmp_path)`
- `def test_legacy_and_caller_supplied_tokens_still_authenticate(tmp_path)`
- `def test_last_used_is_written_at_most_once_a_minute(tmp_path, monkeypatch)`

---

### [`tests/test_brain_configuration.py`](tests/test_brain_configuration.py)
> *Policy-gated self-configuration: preview, apply, rollback and quotas.*

**Size**: `252` lines (`8517` bytes)

**Classes:**
- `class FakeSettings`
  - Method `.__init__()`
  - Method `.get(key, default)`
  - Method `.save_settings(update)`
- `class FakeModuleManager`
  - Method `.__init__()`
  - Method `.get_module(module_id)`
  - Method `.enable_module(module_id)`
  - Method `.disable_module(module_id)`

**Functions:**
- `def make_engine(tmp_path, **kwargs)`
- `def test_setting_preview_apply_and_rollback(tmp_path)`
- `def test_setting_policy_and_bounds(tmp_path)`
- `def test_module_allowlist_protection_and_rollback(tmp_path)`
- `def test_brain_option_bounds_runtime_and_rollback(tmp_path)`
- `def test_allowed_tools_can_only_be_narrowed(tmp_path)`
- `def test_rollback_rejects_an_older_change_for_the_same_target(tmp_path)`
- `def test_configuration_quota_and_status(tmp_path)`
- `def test_configuration_routes(tmp_path, monkeypatch)`

---

### [`tests/test_brain_evaluation.py`](tests/test_brain_evaluation.py)
> *Deterministic evaluation harness and baseline regression gate.*

**Size**: `157` lines (`4967` bytes)

**Functions:**
- `async def test_runner_passes_echo_tasks_through_a_fake_executor()`
- `async def test_runner_marks_failing_checks()`
- `async def test_echo_flow_runs_through_the_real_flow_runner()`
- `def test_baseline_store_and_regression_comparison(tmp_path)`
- `def test_compare_without_baseline_is_not_a_regression()`
- `async def test_evaluation_rejects_state_mutating_nodes()`
- `def test_tiny_latency_difference_does_not_block_promotion()`
- `async def test_service_evaluation_promotes_and_compares(tmp_path)`

---

### [`tests/test_brain_goal_governance.py`](tests/test_brain_goal_governance.py)
> *Goal-governed improvement: a promotion names an active goal or a self-repair reason.*

**Size**: `178` lines (`6576` bytes)

**Functions:**
- `def linked_engine(tmp_path, goal_map, marked)`
- `async def test_a_candidate_without_a_goal_or_repair_reason_is_rejected(tmp_path)`
- `async def test_an_unknown_goal_is_rejected(tmp_path)`
- `async def test_a_completed_goal_cannot_justify_a_change(tmp_path)`
- `async def test_an_active_goal_is_recorded_on_the_candidate(tmp_path)`
- `async def test_a_self_repair_candidate_needs_a_reason(tmp_path)`
- `async def test_an_unknown_goal_kind_is_rejected(tmp_path)`
- `async def test_an_unknown_active_goal_is_rejected(tmp_path)`
- `async def test_promotion_revalidates_the_goal_and_marks_it_in_progress(tmp_path)`
- `async def test_unlinked_mode_keeps_the_engine_usable_for_direct_embedding(tmp_path)`
- `def configuration_engine(tmp_path, goals)`
- `def test_configuration_apply_requires_a_goal_or_repair_reason(tmp_path)`
- `def test_configuration_apply_records_an_active_goal(tmp_path)`
- `def test_configuration_apply_accepts_an_explicit_self_repair(tmp_path)`
- `def test_configuration_apply_rejects_a_completed_goal(tmp_path)`

---

### [`tests/test_brain_goal_review.py`](tests/test_brain_goal_review.py)
> *Deterministic goal review: overdue, stale, clarity and near-duplicate detection.*

**Size**: `93` lines (`3640` bytes)

**Functions:**
- `def goal(goal_id, description, *, status, priority, context, deadline, updated_at)`
- `def test_review_reports_overdue_stale_low_clarity_and_duplicates_without_mutation()`
- `def test_clarity_is_bounded_and_explains_what_is_missing()`
- `def test_goal_review_route_is_read_only_and_reports_a_review()`
- `def test_review_rejects_negative_stale_days()`
- `def test_review_sorts_overdue_and_stale_by_epoch_not_text()`
- `def test_goal_review_route_rejects_negative_stale_days()`

---

### [`tests/test_brain_guardrails.py`](tests/test_brain_guardrails.py)
> *Deterministic guardrails: injection, secret redaction and prompt-leak detection.*

**Size**: `160` lines (`5772` bytes)

**Functions:**
- `def test_context_switch_and_override_attempts_are_flagged(text)`
- `def test_enforce_blocks_an_injected_input()`
- `def test_benign_discussion_is_not_flagged()`
- `def test_invisible_characters_cannot_split_an_override_phrase()`
- `def test_a_very_long_input_is_scanned_within_a_fixed_cap()`
- `def test_input_credentials_are_flagged_but_never_rewritten()`
- `def test_output_credentials_are_redacted_in_both_modes()`
- `def test_source_code_is_not_mistaken_for_a_credential()`
- `def test_redact_secrets_is_idempotent()`
- `def test_a_leaked_system_prompt_is_flagged_and_blocked_in_enforce()`
- `def test_short_shared_phrases_are_not_treated_as_a_leak()`
- `def test_stats_count_findings_and_blocks()`
- `def test_an_unknown_mode_falls_back_to_observe(monkeypatch)`
- `def test_guardrail_routes_report_and_scan()`

---

### [`tests/test_brain_improvement.py`](tests/test_brain_improvement.py)
> *Eval-gated flow improvement: preview, quotas, promotion and rollback.*

**Size**: `349` lines (`12927` bytes)

**Classes:**
- `class FakeEvalRunner`
  - Method `.__init__(pass_rate, failed_tasks)`
  - Method `async def .run(flow_id, **kwargs)`

**Functions:**
- `def make_flow_manager(tmp_path)`
- `def make_engine(tmp_path, manager, pass_rate, failed_tasks, **kwargs)`
- `def rename_ops(name)`
- `def test_preview_applies_operations_without_saving(tmp_path)`
- `async def test_candidate_is_promoted_only_after_evaluation_and_can_rollback(tmp_path)`
- `async def test_failed_candidate_never_leaves_the_flow_modified(tmp_path)`
- `async def test_candidate_with_a_foreign_baseline_is_not_promotable(tmp_path)`
- `async def test_candidate_without_baseline_is_not_promotable(tmp_path)`
- `async def test_weak_baseline_cannot_authorize_promotion(tmp_path)`
- `async def test_promotion_requires_an_approver_identity(tmp_path)`
- `async def test_rollback_rejects_an_older_promotion(tmp_path)`
- `async def test_candidate_is_evaluated_in_memory_with_the_real_runner(tmp_path)`
- `async def test_propose_rejects_non_object_operations(tmp_path)`
- `async def test_policy_allowlist_rejects_unlisted_node_types(tmp_path)`
- `async def test_summary_does_not_embed_candidate_list(tmp_path)`
- `async def test_candidate_quota_is_enforced(tmp_path)`
- `async def test_promotion_quota_marks_the_candidate_blocked(tmp_path)`
- `async def test_generator_hook_proposes_a_candidate(tmp_path)`
- `def test_improvement_routes(tmp_path, monkeypatch)`

---

### [`tests/test_brain_kernel.py`](tests/test_brain_kernel.py)
> *Governance kernel, drift monitor and SAFE-mode actuator gates.*

**Size**: `360` lines (`13442` bytes)

**Classes:**
- `class DegradedSelfModel`
  - Method `async def .snapshot(mode)`

**Functions:**
- `def snapshot(provider_errors, pass_rate, drift, memory, documents)`
- `def test_kernel_transitions_and_gates(tmp_path)`
- `def test_safe_mode_hysteresis_and_kill_switch(tmp_path)`
- `def test_component_errors_accumulate_and_recovery_lowers_pressure(tmp_path)`
- `def test_one_failing_subsystem_alone_can_degrade(tmp_path)`
- `def test_risk_escalates_immediately_but_releases_slowly(tmp_path)`
- `def test_error_windows_expire_so_a_transient_outage_does_not_stick(tmp_path)`
- `def test_component_errors_require_a_name(tmp_path)`
- `def test_blocked_action_window_is_bounded(tmp_path)`
- `def test_drift_compares_against_baseline(tmp_path)`
- `async def test_safe_mode_blocks_memory_improvement_and_configuration(tmp_path)`
- `def test_kernel_routes(tmp_path, monkeypatch)`
- `def test_kernel_tolerates_malformed_persisted_fields(tmp_path)`
- `def test_observe_tolerates_malformed_risk_inputs(tmp_path)`
- `def test_record_evaluation_and_drift_ignore_malformed_values(tmp_path)`
- `def test_non_finite_kernel_inputs_keep_the_previous_values(tmp_path)`
- `def test_restore_rejects_a_non_finite_risk_score(tmp_path)`

---

### [`tests/test_brain_learning.py`](tests/test_brain_learning.py)
> *Learning loop: outcomes, consolidation into global memory and held-out recall.*

**Size**: `422` lines (`13945` bytes)

**Classes:**
- `class FakeMemoryBridge` - *In-memory stand-in for the global MemoryStore used by the learning loop.*
  - Method `.__init__()`
  - Method `.add_entry(text, mem_type, subject, source, confidence, verified, **kwargs)`
  - Method `.update_entry(memory_id, text, mem_type, verified, **kwargs)`
  - Method `.delete_entry(memory_id, reason)`
  - Method `.browse(limit, search_text, mem_type, source, **kwargs)`

**Functions:**
- `def make_loop(tmp_path, min_occurrences)`
- `def record(loop, action, input_data, output, success, tags)`
- `def test_record_outcome_mirrors_experience_and_persists(tmp_path)`
- `def test_consolidation_creates_one_skill_from_repeated_successes(tmp_path)`
- `def test_consolidation_skips_failures_and_below_threshold(tmp_path)`
- `def test_skill_success_rate_counts_training_failures(tmp_path)`
- `def test_second_consolidation_does_not_duplicate_skill(tmp_path)`
- `def test_held_out_recall_uses_only_training_experiences(tmp_path)`
- `def test_experiences_page_zero_is_empty(tmp_path)`
- `def test_recall_is_zero_without_holdout_examples(tmp_path)`
- `def test_skill_ledger_is_capped_and_prunes_memory(tmp_path)`
- `def test_experience_cap_prunes_mirrored_memory(tmp_path)`
- `def test_learning_ledger_survives_reload_and_quarantines_corrupt_data(tmp_path)`
- `async def test_service_evaluation_records_outcomes(tmp_path)`
- `def test_learning_routes_record_consolidate_and_recall(tmp_path, monkeypatch)`
- `def test_repeated_failures_consolidate_into_one_avoidance(tmp_path)`
- `def test_avoidance_consolidation_is_idempotent(tmp_path)`
- `def test_a_single_failure_does_not_become_an_avoidance(tmp_path)`
- `def test_later_success_does_not_erase_an_avoidance(tmp_path)`
- `def test_recall_returns_skills_and_avoidance_warnings(tmp_path)`
- `def test_avoidance_recall_is_empty_for_an_unknown_query(tmp_path)`
- `def test_avoidance_history_is_bounded_and_memory_is_pruned(tmp_path)`
- `def test_the_suggest_route_requires_a_query()`

---

### [`tests/test_brain_ledger_locking.py`](tests/test_brain_ledger_locking.py)
> *Cross-process safety of the brain ledgers: reload, mutate, persist under one lock.*

**Size**: `152` lines (`5393` bytes)

**Classes:**
- `class FakeSettings`
  - Method `.__init__()`
  - Method `.get(key, default)`
  - Method `.save_settings(values)`

**Functions:**
- `def learning_loop(tmp_path)`
- `def test_learning_ledgers_do_not_clobber_each_other(tmp_path)`
- `def test_consolidation_sees_another_process_experience(tmp_path)`
- `def test_kernel_instances_share_blocked_action_counts(tmp_path)`
- `def test_configuration_engines_keep_both_changes(tmp_path)`
- `def test_actor_registries_keep_both_actors(tmp_path)`
- `def test_reaction_engines_keep_both_mutes(tmp_path)`
- `async def test_improvement_engines_keep_both_candidates(tmp_path)`
- `def test_ledger_lock_excludes_a_second_writer(tmp_path)`

---

### [`tests/test_brain_outcomes.py`](tests/test_brain_outcomes.py)
> *Automatic flow outcome capture into the brain learning loop.*

**Size**: `123` lines (`4032` bytes)

**Classes:**
- `class BoomExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`

**Functions:**
- `async def test_flow_runner_emits_a_success_outcome()`
- `async def test_flow_runner_emits_a_failure_outcome(monkeypatch)`
- `async def test_evaluation_does_not_emit_flow_outcomes()`
- `async def test_brain_service_captures_flow_outcomes_until_safe(tmp_path)`

---

### [`tests/test_brain_reactivity.py`](tests/test_brain_reactivity.py)
> *Reactivity: proposals, dedupe, mute, sources, kernel gating and API.*

**Size**: `165` lines (`5463` bytes)

**Functions:**
- `def make_engine(tmp_path, **config)`
- `async def test_event_becomes_a_deduplicated_pending_reaction(tmp_path)`
- `async def test_mute_and_dismiss(tmp_path)`
- `async def test_calendar_metric_and_file_sources(tmp_path)`
- `async def test_kernel_gate_blocks_normal_reactions(tmp_path)`
- `async def test_reaction_ledger_persists(tmp_path)`
- `async def test_reactivity_routes(tmp_path, monkeypatch)`

---

### [`tests/test_brain_self_budget.py`](tests/test_brain_self_budget.py)
> *Unified self-modification budget: caps, rollover, release and service wiring.*

**Size**: `125` lines (`4796` bytes)

**Functions:**
- `def make_budget(tmp_path, **caps)`
- `def test_a_reservation_consumes_one_unit_and_denies_past_the_cap(tmp_path)`
- `def test_release_gives_back_a_reservation_that_did_not_happen(tmp_path)`
- `def test_release_never_goes_negative(tmp_path)`
- `def test_an_unknown_kind_or_empty_reservation_is_rejected(tmp_path)`
- `def test_counts_persist_and_roll_over_on_a_new_utc_day(tmp_path, monkeypatch)`
- `def test_a_corrupt_ledger_starts_from_zero_and_is_replaced(tmp_path)`
- `def test_the_service_bounds_learning_writes(tmp_path, monkeypatch)`
- `def test_the_budget_route_reports_counts()`
- `def test_budget_exhaustion_returns_429_instead_of_a_name_error(tmp_path, monkeypatch)`
- `def test_malformed_cap_config_uses_defaults_instead_of_crashing(tmp_path)`

---

### [`tests/test_brain_substrate.py`](tests/test_brain_substrate.py)
> *Brain substrate: event bus, real self-model, service modes and API wiring.*

**Size**: `191` lines (`6386` bytes)

**Classes:**
- `class NullMemoryBridge`
  - Method `.add_entry(text, **kwargs)`
  - Method `.update_entry(memory_id, text, **kwargs)`
  - Method `.browse(limit, search_text, mem_type, source, **kwargs)`
- `class FakeSelfModel`
  - Method `async def .snapshot(mode)`

**Functions:**
- `def temp_learning(tmp_path)`
- `async def test_event_bus_publishes_deduplicates_and_records()`
- `async def test_event_bus_times_out_a_slow_handler()`
- `async def test_self_model_uses_live_flows_and_modules_not_mocks()`
- `async def test_self_model_reports_provider_failure()`
- `async def test_service_cycles_in_suggest_mode_and_rejects_auto(tmp_path)`
- `async def test_service_start_stop_and_state_persistence(tmp_path)`
- `def test_brain_routes_are_mounted_and_serve_state(tmp_path, monkeypatch)`

---

### [`tests/test_brain_trends.py`](tests/test_brain_trends.py)
> *Bounded metric-trend detection and its reviewable proposals.*

**Size**: `135` lines (`4086` bytes)

**Functions:**
- `def snapshot(pass_rate, errors)`
- `def monitor(tmp_path, now, **overrides)`
- `def test_a_fall_in_pass_rate_emits_one_finding_and_dedupes_for_the_day(tmp_path)`
- `def test_a_rise_in_provider_errors_emits_a_finding(tmp_path)`
- `def test_history_is_bounded_and_an_empty_ledger_is_quiet(tmp_path)`
- `def test_the_service_turns_a_finding_into_a_reviewable_proposal()`
- `def test_trend_route_reports_the_window()`
- `def test_trend_record_survives_malformed_snapshot_fields(tmp_path)`

---

### [`tests/test_brain_ui.py`](tests/test_brain_ui.py)
> *Brain dashboard route and navigation.*

**Size**: `24` lines (`645` bytes)

**Functions:**
- `def test_brain_dashboard_renders()`
- `def test_brain_dashboard_is_linked_from_navbar()`

---

### [`tests/test_browser_automation.py`](tests/test_browser_automation.py)
**Size**: `69` lines (`3267` bytes)

**Functions:**
- `def mock_browser_service()`
- `async def test_browser_goto_action(mock_browser_service)`
- `async def test_browser_missing_url_for_goto(mock_browser_service)`
- `async def test_browser_click_action(mock_browser_service)`
- `async def test_sandbox_internal_api_whitelist()` - *Verify that localhost connections are permitted by the sandbox.*

---

### [`tests/test_calendar.py`](tests/test_calendar.py)
**Size**: `135` lines (`4161` bytes)

**Functions:**
- `def temp_event_file()` - *Create a temporary event file for testing.*
- `def event_manager(temp_event_file)` - *Create an EventManager with temporary storage.*
- `def test_add_event(event_manager)` - *Test adding an event.*
- `def test_get_events_by_date(event_manager)` - *Test retrieving events by date.*
- `def test_get_events_by_date_no_events(event_manager)` - *Test retrieving events when none exist for that date.*
- `def test_get_upcoming(event_manager)` - *Test retrieving upcoming events.*
- `def test_get_upcoming_with_limit(event_manager)` - *Test retrieving upcoming events with limit.*
- `def test_delete_event(event_manager)` - *Test deleting an event.*
- `def test_delete_nonexistent_event(event_manager)` - *Test deleting an event that doesn't exist.*
- `def test_get_event_by_id(event_manager)` - *Test getting a specific event by ID.*
- `def test_get_event_by_id_not_found(event_manager)` - *Test getting a non-existent event.*
- `def test_event_persistence(event_manager)` - *Test that events persist after creating a new instance.*
- `def test_multiple_events_same_time(event_manager)` - *Test adding multiple events at the same time.*

---

### [`tests/test_calendar_events.py`](tests/test_calendar_events.py)
**Size**: `176` lines (`5697` bytes)

**Functions:**
- `def temp_event_file()`
- `def event_manager(temp_event_file)`
- `def test_add_event(event_manager)`
- `def test_get_events_by_date(event_manager)`
- `def test_get_upcoming(event_manager)`
- `def test_get_event_by_id(event_manager)`
- `def test_get_event_by_id_not_found(event_manager)`
- `def test_delete_event(event_manager)`
- `def test_delete_nonexistent_event(event_manager)`
- `def test_get_upcoming_sorted_by_start_time(event_manager)` - *get_upcoming() should return events sorted by start_time ascending.*
- `def test_get_upcoming_limit_respected(event_manager)` - *get_upcoming() should respect the limit parameter.*
- `def test_get_upcoming_excludes_past_events(event_manager)` - *get_upcoming() should not include events in the past.*
- `def test_update_event_title(event_manager)` - *update_event() should update the title of an existing event.*
- `def test_update_event_start_time(event_manager)` - *update_event() should update the start_time of an existing event.*
- `def test_update_event_both_fields(event_manager)` - *update_event() should update both title and start_time simultaneously.*
- `def test_update_event_nonexistent_returns_none(event_manager)` - *update_event() with a non-existent id should return None.*
- `def test_update_event_persisted(event_manager)` - *update_event() changes should be persisted to storage.*

---

### [`tests/test_calendar_node.py`](tests/test_calendar_node.py)
> *Tests for modules/calendar/node.py — CalendarWatcherExecutor*

**Size**: `160` lines (`5580` bytes)

**Functions:**
- `def now_str()` - *Return current time as 'YYYY-MM-DD HH:MM' string.*
- `def make_event(title, start_time)`
- `async def test_no_events_file_returns_none()` - *When calendar_events.json does not exist, receive() should return None.*
- `async def test_empty_events_list_returns_none()` - *When events file is empty list, receive() should return None.*
- `async def test_event_at_current_minute_triggers()` - *An event whose start_time matches the current minute should trigger.*
- `async def test_event_at_different_minute_returns_none()` - *An event scheduled for a different time should not trigger.*
- `async def test_malformed_event_skipped()` - *Events with unparseable start_time should be skipped without crashing.*
- `async def test_legacy_date_time_format_supported()` - *Events using legacy 'date' + 'time' keys should also be parsed.*
- `async def test_multiple_events_same_minute_all_trigger()` - *Multiple events at the same minute should all be included in the result.*
- `async def test_corrupt_json_returns_none()` - *Corrupt JSON in events file should return None without crashing.*
- `async def test_get_executor_class_dispatcher()` - *get_executor_class('calendar_watcher') should return CalendarWatcherExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*

---

### [`tests/test_chat_features.py`](tests/test_chat_features.py)
**Size**: `120` lines (`5332` bytes)

**Functions:**
- `def client()`
- `def mock_chat_sessions(monkeypatch)`
- `def patch_settings_url(monkeypatch)`
- `async def test_multimodal_image_upload(client, mock_chat_sessions)`
- `async def test_auto_rename_trigger(client, mock_chat_sessions)`

---

### [`tests/test_chat_module.py`](tests/test_chat_module.py)
**Size**: `167` lines (`7055` bytes)

**Functions:**
- `def client()` - *A TestClient that handles startup and shutdown events.*
- `def clean_db()`
- `def mock_chat_sessions(monkeypatch)` - *Mocks the session manager to use an in-memory dict instead of a file.*
- `def mock_memory_store()` - *Mocks the memory store to prevent flow failures during chat tests.*
- `def test_chat_gui_route(client)`
- `def test_get_chat_sessions_route(client, mock_chat_sessions)` - *Tests the endpoint that lists all chat sessions.*
- `async def test_chat_send_route(client, httpx_mock, mock_chat_sessions, monkeypatch)`
- `def test_create_new_session_route(client, mock_chat_sessions)` - *Tests the endpoint for creating a new session.*
- `def test_delete_session_route(client, mock_chat_sessions)` - *Tests the endpoint for deleting a session.*
- `def test_rename_session_route(client, mock_chat_sessions)` - *Tests the endpoint for renaming a session.*
- `def test_chat_page_route(client)` - *Tests if the main chat page loads correctly.*

---

### [`tests/test_chat_nodes.py`](tests/test_chat_nodes.py)
**Size**: `109` lines (`3664` bytes)

**Functions:**
- `async def test_chat_input_send_validation()` - *Test that Chat Input requires 'messages' in the data.*
- `async def test_chat_output_receive_parsing()` - *Test that Chat Output correctly parses OpenAI-style responses.*
- `async def test_chat_output_content_field_passthrough()` - *Chat Output should pass through 'content' field directly if already present.*
- `async def test_chat_output_messages_fallback()` - *Chat Output should extract last assistant message when no choices/content.*
- `async def test_chat_input_repeat_suppression()` - *ChatInputExecutor should return None when _repeat_count > 0.*
- `async def test_chat_input_repeat_count_zero_passes()` - *ChatInputExecutor should pass through when _repeat_count == 0.*
- `async def test_chat_output_none_input()` - *ChatOutputExecutor should handle None input gracefully.*
- `async def test_get_executor_class_chat_input()` - *get_executor_class('chat_input') should return ChatInputExecutor.*
- `async def test_get_executor_class_chat_output()` - *get_executor_class('chat_output') should return ChatOutputExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*

---

### [`tests/test_chat_persistence.py`](tests/test_chat_persistence.py)
> *Chat persistence: stale-read protection, retention and corrupt-store recovery.*

**Size**: `78` lines (`2604` bytes)

**Functions:**
- `def test_second_manager_does_not_overwrite_newer_messages(tmp_path)`
- `def test_message_retention_keeps_the_newest_messages(tmp_path)`
- `def test_session_retention_drops_the_oldest_sessions(tmp_path)`
- `def test_corrupt_session_store_is_quarantined(tmp_path)`
- `def test_concurrent_write_is_seen_even_with_a_coarse_file_clock(tmp_path)`

---

### [`tests/test_chat_router_flow.py`](tests/test_chat_router_flow.py)
**Size**: `172` lines (`7416` bytes)

**Functions:**
- `def client()` - *Create test client with chat module enabled.*
- `def mock_chat_sessions()` - *Mocks the session manager to use an in-memory dict.*
- `def test_send_message_no_active_flow(client, mock_chat_sessions)` - *Test response when no AI flow is active.*
- `def test_send_message_flow_execution_error(client, mock_chat_sessions)` - *Test response when the AI flow returns an error.*
- `def test_send_message_flow_crash(client, mock_chat_sessions)` - *Test response when the FlowRunner raises an exception.*
- `def test_send_message_success(client, mock_chat_sessions)` - *Test successful message flow.*

---

### [`tests/test_chat_sessions.py`](tests/test_chat_sessions.py)
**Size**: `119` lines (`3239` bytes)

**Functions:**
- `def temp_session_file()`
- `def session_manager(temp_session_file)`
- `def test_create_session(session_manager)`
- `def test_create_session_with_name(session_manager)`
- `def test_get_session(session_manager)`
- `def test_get_session_not_found(session_manager)`
- `def test_delete_session(session_manager)`
- `def test_delete_session_not_found(session_manager)`
- `def test_rename_session(session_manager)`
- `def test_rename_session_not_found(session_manager)`
- `def test_list_sessions(session_manager)`
- `def test_add_message(session_manager)`
- `def test_add_message_to_nonexistent_session(session_manager)`

---

### [`tests/test_confirmations.py`](tests/test_confirmations.py)
> *Server-side confirmation gates for irreversible routes (ADR-059).*

**Size**: `51` lines (`1957` bytes)

**Functions:**
- `def test_confirmation_matches_case_and_whitespace()`
- `def test_a_missing_or_wrong_phrase_is_rejected_with_a_remedy(provided)`
- `def test_destructive_routes_require_the_phrase()`
- `def test_memory_wipe_runs_only_after_confirmation(monkeypatch)`

---

### [`tests/test_containment_regressions.py`](tests/test_containment_regressions.py)
> *Archive expansion caps, statistics edge cases and exact index synchronization.*

**Size**: `123` lines (`3621` bytes)

**Functions:**
- `def write_zip(path, members)`
- `def test_extract_rejects_an_oversized_member(tmp_path)`
- `def test_extract_rejects_an_oversized_total(tmp_path)`
- `def test_extract_normal_archive_still_works(tmp_path)`
- `def test_descriptive_stats_single_value_returns_undefined_fields()`
- `def test_descriptive_stats_empty_raises()`
- `def test_confidence_interval_requires_two_values()`
- `def test_one_sample_ttest_constant_data_does_not_crash()`
- `def test_paired_ttest_constant_differences_does_not_crash()`
- `def test_two_sample_ttest_requires_two_values_per_group()`
- `def test_kb_small_faiss_mismatch_is_rebuilt(tmp_path, monkeypatch)`

---

### [`tests/test_core_concurrency.py`](tests/test_core_concurrency.py)
> *Tests for concurrency and deadlock scenarios.*

**Size**: `279` lines (`9685` bytes)

**Classes:**
- `class TestFlowManagerConcurrency` - *Test concurrent access to FlowManager.*
  - Method `.test_concurrent_reads(flow_manager_instance)` - *Test that multiple threads can read flows concurrently.*
  - Method `.test_concurrent_read_write(flow_manager_instance)` - *Test concurrent reads and writes don't deadlock or lose writes.*
- `class TestModuleManagerConcurrency` - *Test concurrent access to ModuleManager.*
  - Method `.test_concurrent_module_access()` - *Test that multiple threads can access module manager.*
- `class TestAsyncConcurrency` - *Test async concurrency primitives.*
  - Method `async def .test_asyncio_lock_concurrent_access()` - *Test asyncio.Lock allows concurrent coroutines.*
  - Method `async def .test_flow_runner_cache_concurrent_access()` - *Test FlowRunner cache is thread-safe.*
- `class TestDeadlockPrevention` - *Test that we don't have classic deadlock patterns.*
  - Method `.test_no_cross_lock_deadlock(flow_manager_instance)` - *Verify we don't have A->B, B->A deadlock pattern.*
  - Method `async def .test_async_timeout_prevents_deadlock()` - *Test that operations can timeout to prevent deadlock.*
- `class TestLockOrdering` - *Test lock ordering is followed.*
  - Method `.test_flow_manager_lock_type(flow_manager_instance)` - *Verify FlowManager uses RLock for reentrancy.*
  - Method `.test_module_manager_lock_type()` - *Verify ModuleManager uses RLock for reentrancy.*
- `class TestStressConcurrency` - *Stress tests to detect concurrency issues.*
  - Method `.test_high_concurrency_flow_manager(flow_manager_instance)` - *Stress test with high concurrent access.*
  - Method `async def .test_high_concurrency_async_operations()` - *Stress test with high async concurrency.*

**Functions:**
- `def flow_manager_instance(tmp_path)` - *Isolated flow store so concurrency tests never touch project state.*

---

### [`tests/test_core_debug.py`](tests/test_core_debug.py)
> *Tests for core/debug.py — DebugLogger*

**Size**: `104` lines (`3429` bytes)

**Classes:**
- `class TestDebugLogger` - *Tests for DebugLogger class.*
  - Method `.test_log_adds_entry()` - *log should add an entry to the logs deque.*
  - Method `.test_log_includes_timestamp()` - *log should include timestamp in entry.*
  - Method `.test_get_logs_returns_reversed_list()` - *get_logs should return logs in reverse order (newest first).*
  - Method `.test_get_recent_logs_filters_by_timestamp()` - *get_recent_logs should only return logs after given timestamp.*
  - Method `.test_get_recent_logs_returns_empty_for_no_matches()` - *get_recent_logs should return empty list if no logs after cutoff.*
  - Method `.test_clear_removes_all_logs()` - *clear should remove all logs.*
  - Method `.test_max_logs_limit_enforced()` - *Logs should be limited to max_logs (default 50).*

---

### [`tests/test_core_flow_manager.py`](tests/test_core_flow_manager.py)
> *Tests for core/flow_manager.py — FlowManager*

**Size**: `205` lines (`7532` bytes)

**Classes:**
- `class TestFlowManager` - *Tests for FlowManager class.*
  - Method `.temp_flows_file()` - *Create a temporary flows file.*
  - Method `.test_init_creates_default_flows()` - *Should create default flows if file doesn't exist.*
  - Method `.test_save_flow_adds_new_flow()` - *save_flow should add a new flow when flow_id is None.*
  - Method `.test_save_flow_updates_existing_flow()` - *save_flow should update existing flow when flow_id provided.*
  - Method `.test_get_flow_returns_flow()` - *get_flow should return the flow with the given ID.*
  - Method `.test_get_flow_returns_none_for_missing()` - *get_flow should return None for non-existent ID.*
  - Method `.test_list_flows_returns_sorted_list()` - *list_flows should return a list sorted by created_at.*
  - Method `.test_delete_flow_removes_flow()` - *delete_flow should remove the flow with given ID.*
  - Method `.test_delete_flow_returns_false_for_missing()` - *delete_flow should return False for non-existent ID.*
  - Method `.test_rename_flow_changes_name()` - *rename_flow should change the flow's name.*
  - Method `.test_rename_flow_returns_false_for_missing()` - *rename_flow should return False for non-existent ID.*
  - Method `.test_import_flows_replaces_all_flows()` - *import_flows should replace all existing flows.*
- `class TestDefaultFlow` - *Tests for default flow creation.*
  - Method `.test_default_flow_has_required_nodes()` - *Default flow should have required nodes.*
  - Method `.test_default_flow_has_connections()` - *Default flow should have connections between nodes.*

---

### [`tests/test_core_improvements.py`](tests/test_core_improvements.py)
**Size**: `99` lines (`3953` bytes)

**Functions:**
- `def client()`
- `def test_hidden_config_preservation(client)` - *Test that hidden config keys are preserved when saving via the generic editor.*
- `async def test_flow_runner_reloads_module()` - *Test that FlowRunner reloads the node module to support hot-swapping.*

---

### [`tests/test_core_llm.py`](tests/test_core_llm.py)
**Size**: `117` lines (`3741` bytes)

**Functions:**
- `async def test_chat_completion_success(httpx_mock)`
- `async def test_chat_completion_error(httpx_mock)`
- `async def test_get_models_success(httpx_mock)`
- `async def test_get_models_error(httpx_mock)`
- `async def test_get_embedding_success(httpx_mock)`
- `async def test_get_embedding_error(httpx_mock)`
- `async def test_llm_bridge_uses_injected_client()` - *Test that LLMBridge uses the provided AsyncClient if available.*

---

### [`tests/test_core_llm_bridge.py`](tests/test_core_llm_bridge.py)
> *Tests for core/llm.py — LLMBridge*

**Size**: `112` lines (`4837` bytes)

**Classes:**
- `class TestLLMBridgeInit` - *Tests for LLMBridge initialization.*
  - Method `.test_base_url_trailing_slash_stripped()` - *base_url should have trailing slashes stripped.*
  - Method `.test_base_url_multiple_slashes_stripped()` - *base_url should strip multiple trailing slashes.*
  - Method `.test_base_url_empty_string_handled()` - *Empty base_url should result in empty string.*
  - Method `.test_embedding_base_url_defaults_to_base_url()` - *embedding_base_url should default to base_url if not provided.*
  - Method `.test_embedding_base_url_used_when_provided()` - *embedding_base_url should be used when explicitly provided.*
  - Method `.test_embedding_base_url_trailing_slash_stripped()` - *embedding_base_url should have trailing slashes stripped.*
  - Method `.test_api_key_stored()` - *API key should be stored.*
  - Method `.test_default_api_key_none()` - *Default API key should be None.*
  - Method `.test_embedding_model_stored()` - *embedding_model should be stored when provided.*
  - Method `.test_embedding_model_defaults_to_none()` - *embedding_model should default to None.*
  - Method `.test_client_initialized_as_none()` - *client should be initialized as None (lazy initialization).*
  - Method `.test_timeout_defaults_to_60()` - *timeout should default to 60 seconds.*
  - Method `.test_custom_timeout()` - *Custom timeout should be stored.*
- `class TestLLMBridgeURLs` - *Tests for URL construction via _get_url.*
  - Method `.test_get_url_chat_completions()` - *_get_url should construct chat/completions URL correctly.*
  - Method `.test_get_url_models()` - *_get_url should construct models URL correctly.*
  - Method `.test_get_url_embeddings_uses_embedding_base_url()` - *_get_url with use_embedding_url=True should use embedding_base_url.*
  - Method `.test_get_url_strips_leading_slash()` - *_get_url should strip leading slashes from path.*
  - Method `.test_get_url_with_embedding_url_default()` - *_get_url without use_embedding_url should use base_url.*

---

### [`tests/test_core_robustness.py`](tests/test_core_robustness.py)
**Size**: `92` lines (`3049` bytes)

**Functions:**
- `def settings_manager()`
- `def test_settings_concurrency(settings_manager)` - *Test that settings can be updated concurrently without corruption.*
- `async def test_flow_runner_input_isolation()` - *Test that FlowRunner copies input for source nodes.*

---

### [`tests/test_core_routers.py`](tests/test_core_routers.py)
**Size**: `38` lines (`1155` bytes)

**Functions:**
- `def test_format_reasoning_content_passthrough()`
- `def test_format_reasoning_content_openai_dict_string()`
- `def test_format_reasoning_content_invalid_dict()`
- `def test_format_reasoning_content_dict_without_choices()`
- `def test_format_reasoning_content_empty_choices()`
- `def test_format_reasoning_content_message_without_content()`

---

### [`tests/test_core_settings.py`](tests/test_core_settings.py)
> *Tests for core/settings.py — SettingsManager*

**Size**: `229` lines (`8602` bytes)

**Classes:**
- `class TestSettingsManager` - *Tests for SettingsManager class.*
  - Method `.test_load_settings_creates_file_if_missing()` - *Should create settings file with defaults if it doesn't exist.*
  - Method `.test_load_settings_merges_with_defaults()` - *Loaded settings should merge with defaults, preserving loaded values.*
  - Method `.test_load_settings_handles_invalid_json()` - *Invalid JSON should fall back to defaults.*
  - Method `.test_save_settings_updates_values()` - *save_settings should update in-memory and file.*
  - Method `.test_save_settings_preserves_existing_keys()` - *save_settings should not remove existing keys not in the update.*
  - Method `.test_get_returns_default_when_key_missing()` - *get should return default value when key not found.*
  - Method `.test_get_returns_none_when_no_default_and_key_missing()` - *get should return None when key not found and no default provided.*
- `class TestDefaultSettings` - *Tests for DEFAULT_SETTINGS.*
  - Method `.test_default_settings_has_required_keys()` - *DEFAULT_SETTINGS should have all required configuration keys.*
  - Method `.test_default_settings_types()` - *DEFAULT_SETTINGS should have correct types for values.*
- `class TestParseBool` - *Tests for SettingsManager._parse_bool.*
  - Method `.setup_method()`
  - Method `.test_true_bool()`
  - Method `.test_false_bool()`
  - Method `.test_truthy_strings(val)`
  - Method `.test_falsy_strings(val)`
  - Method `.test_integer_one()`
  - Method `.test_integer_zero()`
  - Method `.test_integer_two_raises()`
  - Method `.test_integer_negative_raises()`
  - Method `.test_none_raises()`
  - Method `.test_invalid_string_raises()`
- `class TestMaxNodeLoopsValidation` - *Tests for max_node_loops upper-bound enforcement.*
  - Method `.setup_method()`
  - Method `.test_valid_loop_count()`
  - Method `.test_max_boundary_accepted()`
  - Method `.test_above_max_raises()`
  - Method `.test_very_large_value_raises()`
  - Method `.test_zero_raises()`
  - Method `.test_negative_raises()`

---

### [`tests/test_debug.py`](tests/test_debug.py)
**Size**: `76` lines (`2101` bytes)

**Functions:**
- `def debug_logger()`
- `def test_debug_logger_initialization(debug_logger)`
- `def test_log_entry_creation(debug_logger)`
- `def test_get_logs_returns_reversed(debug_logger)`
- `def test_get_recent_logs_by_timestamp(debug_logger)`
- `def test_max_logs_limit(debug_logger)`
- `def test_clear_logs(debug_logger)`
- `def test_global_debug_logger_exists()`

---

### [`tests/test_default_flows.py`](tests/test_default_flows.py)
> *The flows a fresh install starts with must only use node types that really execute.*

**Size**: `34` lines (`1203` bytes)

**Functions:**
- `def default_flows(tmp_path)`
- `async def test_every_default_flow_node_dispatches_to_an_executor(default_flows)`
- `def test_default_flow_replies_on_the_platform_a_message_came_from(default_flows)`

---

### [`tests/test_dependencies.py`](tests/test_dependencies.py)
**Size**: `29` lines (`1002` bytes)

**Functions:**
- `def test_get_llm_bridge_uses_settings()` - *Tests that the get_llm_bridge dependency correctly uses the*

---

### [`tests/test_e2e.py`](tests/test_e2e.py)
> *End-to-end tests for NeuroCore module configuration workflows.*

**Size**: `99` lines (`3544` bytes)

**Functions:**
- `def base_url()` - *Return base URL for E2E server after validating availability.*
- `def test_planner_module_details(base_url)`
- `def test_planner_default_prompt(base_url)`
- `def test_save_custom_prompt(base_url)`
- `def test_verify_custom_prompt_saved(base_url)`
- `def test_get_default_prompt_again(base_url)`
- `def test_load_default_prompt_button(base_url)`
- `def test_reflection_module_workflow(base_url)`

---

### [`tests/test_email_bridge.py`](tests/test_email_bridge.py)
> *Tests for the email_bridge module.*

**Size**: `446` lines (`18177` bytes)

**Classes:**
- `class TestIMAPBridgeHelpers`
  - Method `.setup_method()`
  - Method `.test_decode_header_plain()`
  - Method `.test_decode_header_empty()`
  - Method `.test_decode_header_encoded()`
  - Method `._make_text_msg(body, content_type)`
  - Method `.test_get_text_body_simple()`
  - Method `.test_get_text_body_multipart()`
  - Method `.test_get_text_body_no_text_part()`
- `class TestIMAPBridgeFetchUnseen`
  - Method `._make_raw_email(from_addr, subject, body, msg_id)`
  - Method `.test_fetch_unseen_returns_parsed_messages()`
  - Method `.test_fetch_marks_as_seen_when_configured()`
  - Method `.test_fetch_does_not_mark_seen_when_disabled()`
  - Method `.test_fetch_empty_inbox()`
  - Method `.test_fetch_connection_error_returns_empty()`
  - Method `.test_fetch_skips_messages_without_body()`
- `class TestSMTPBridge`
  - Method `.setup_method()`
  - Method `.test_send_uses_starttls_for_port_587()`
  - Method `.test_send_ssl_for_port_465()`
  - Method `.test_send_sets_in_reply_to_header()`
  - Method `.test_send_returns_false_on_error()`
- `class TestEmailInputExecutor`
  - Method `async def .test_blocks_repeat_triggers()`
  - Method `async def .test_passes_email_input()`
  - Method `async def .test_blocks_non_email_input_source()`
  - Method `async def .test_filter_sender_blocks_other()`
  - Method `async def .test_filter_sender_passes_matching()`
- `class TestEmailOutputExecutor`
  - Method `async def .test_sends_reply_via_smtp()`
  - Method `async def .test_subject_not_doubled_when_already_re()`
  - Method `async def .test_skips_send_when_no_recipient()`
  - Method `async def .test_no_content_passes_through()`
  - Method `async def .test_to_override_respected()`
- `class TestEmailBridgeRouter`
  - Method `.setup_method()`
  - Method `.test_save_settings_module_not_found()`
  - Method `.test_save_settings_success()`

**Functions:**
- `async def test_get_executor_class_email_input()`
- `async def test_get_executor_class_email_output()`
- `async def test_get_executor_class_unknown()`

---

### [`tests/test_episode_persistence.py`](tests/test_episode_persistence.py)
> *Tests for Episode Persistence functionality.*

**Size**: `567` lines (`19681` bytes)

**Classes:**
- `class TestEpisodeState` - *Tests for EpisodeState class.*
  - Method `.test_episode_state_creation()` - *Test creating an EpisodeState.*
  - Method `.test_episode_state_to_dict()` - *Test converting EpisodeState to dictionary.*
  - Method `.test_episode_state_from_dict()` - *Test creating EpisodeState from dictionary.*
  - Method `.test_add_checkpoint()` - *Test adding checkpoints to episode.*
  - Method `.test_update_phase()` - *Test updating episode phase.*
- `class TestSessionManagerEpisodes` - *Tests for SessionManager episode functionality.*
  - Method `.temp_dir()` - *Create a temporary directory for test files.*
  - Method `.session_manager(temp_dir)` - *Create a SessionManager with temporary storage.*
  - Method `.test_create_episode(session_manager)` - *Test creating a new episode.*
  - Method `.test_save_and_load_episode(session_manager)` - *Test saving and loading episode state.*
  - Method `.test_load_episode_by_id(session_manager)` - *Test loading a specific episode by ID.*
  - Method `.test_save_episode_by_id(session_manager)` - *Test saving episode by ID.*
  - Method `.test_list_episodes(session_manager)` - *Test listing all episodes.*
  - Method `.test_delete_episode(session_manager)` - *Test deleting an episode.*
  - Method `.test_clear_episode(session_manager)` - *Test clearing current episode.*
  - Method `.test_is_episode_active(session_manager)` - *Test checking if episode is active.*
  - Method `.test_get_episode_summary(session_manager)` - *Test getting episode summary.*
  - Method `.test_checkpoint_persistence(session_manager)` - *Test checkpoint persistence across saves.*
  - Method `.test_get_or_create_episode(session_manager)` - *Test get_or_create_episode method.*
  - Method `.test_multiple_episode_files(temp_dir)` - *Test managing multiple episode files.*
- `class TestResumeFromCheckpoint` - *Tests for resume from checkpoint functionality.*
  - Method `.temp_dir()` - *Create a temporary directory for test files.*
  - Method `.test_resume_with_plan_state(temp_dir)` - *Test resuming with plan and step state.*
  - Method `.test_resume_with_messages(temp_dir)` - *Test resuming with conversation history.*
  - Method `.test_crash_recovery_scenario(temp_dir)` - *Test crash recovery - loading after unexpected shutdown.*
- `class TestEpisodePhases` - *Tests for episode phase management.*
  - Method `.temp_dir()` - *Create a temporary directory for test files.*
  - Method `.test_phase_transitions(temp_dir)` - *Test episode phase transitions.*
  - Method `.test_failed_phase(temp_dir)` - *Test episode in failed phase.*
  - Method `.test_paused_phase(temp_dir)` - *Test episode in paused phase.*

---

### [`tests/test_execution_budget.py`](tests/test_execution_budget.py)
> *Per-run execution budget: caps, inheritance, flow overrides and executor spending.*

**Size**: `184` lines (`6649` bytes)

**Classes:**
- `class FakeSettings`
  - Method `.__init__(values)`
  - Method `.get(key, default)`

**Functions:**
- `def test_spend_tracks_usage_and_raises_with_the_remedy()`
- `def test_unknown_or_negative_spends_are_rejected()`
- `def test_time_limit_is_checked_before_each_spend()`
- `def test_spend_outside_a_budgeted_run_is_a_noop()`
- `def test_nested_scopes_inherit_one_budget()`
- `def test_flow_metadata_may_lower_but_not_raise_a_limit(monkeypatch)`
- `def test_pressure_reports_the_fullest_dimension()`
- `async def test_a_flow_cannot_run_more_nodes_than_its_budget()`
- `async def test_the_llm_node_spends_a_call_and_the_reported_tokens()`
- `async def test_timeout_zero_still_means_no_asyncio_deadline(monkeypatch)`
- `async def test_the_shared_llm_helper_charges_the_run_budget(monkeypatch)`

---

### [`tests/test_fixes_verification.py`](tests/test_fixes_verification.py)
> *Quick verification tests for the bug fixes*

**Size**: `70` lines (`2430` bytes)

**Functions:**
- `def normalize_month_fixed(year, month)`

---

### [`tests/test_flow_composition.py`](tests/test_flow_composition.py)
> *Composition and agent-pattern regressions for real (possibly cyclic) flows.*

**Size**: `185` lines (`5591` bytes)

**Functions:**
- `def manager(tmp_path)`
- `def composer(manager)`
- `def chat_node(node_id, kind)`
- `def test_compose_accepts_a_cyclic_member(manager, composer)`
- `def test_compose_accepts_the_default_flow(manager, composer)`
- `def test_compose_rewrites_router_branch_configs(manager, composer)`
- `def test_compose_rewrites_repeated_member_references(manager, composer)`
- `def test_mutate_allows_editing_an_existing_cyclic_flow(manager)`
- `def test_mutate_rejects_a_new_cycle_in_an_acyclic_flow(manager)`
- `def test_agent_tool_pattern_is_acyclic_and_routed()`

---

### [`tests/test_flow_context.py`](tests/test_flow_context.py)
> *Tests for core.flow_context - Pydantic-based FlowContext model*

**Size**: `421` lines (`12617` bytes)

**Classes:**
- `class TestFlowContextCreation` - *Test FlowContext creation.*
  - Method `.test_create_empty()` - *Test creating empty FlowContext.*
  - Method `.test_create_with_messages()` - *Test creating with messages.*
  - Method `.test_create_with_plan()` - *Test creating with plan.*
- `class TestFlowContextValidation` - *Test FlowContext field validation.*
  - Method `.test_validate_messages_none()` - *Test validation of None messages.*
  - Method `.test_validate_messages_not_list()` - *Test validation of non-list messages.*
  - Method `.test_validate_plan_none()` - *Test validation of None plan.*
- `class TestFlowContextUtilityMethods` - *Test FlowContext utility methods.*
  - Method `.test_add_message()` - *Test adding a message.*
  - Method `.test_add_plan_step()` - *Test adding a plan step.*
  - Method `.test_advance_step()` - *Test advancing to next step.*
  - Method `.test_is_plan_complete()` - *Test plan completion check.*
  - Method `.test_get_current_plan_step()` - *Test getting current plan step.*
  - Method `.test_get_current_plan_step_out_of_bounds()` - *Test getting step when out of bounds.*
  - Method `.test_add_trace_entry()` - *Test adding trace entry.*
  - Method `.test_increment_iterations()` - *Test incrementing iterations.*
  - Method `.test_increment_replan_count()` - *Test incrementing replan count.*
- `class TestFlowContextConversion` - *Test FlowContext conversion methods.*
  - Method `.test_to_dict()` - *Test converting to dictionary.*
  - Method `.test_to_dict_excludes_none()` - *Test that to_dict excludes None values.*
  - Method `.test_to_dict_including_none()` - *Test to_dict_including_none includes all values.*
  - Method `.test_from_dict()` - *Test creating from dictionary.*
  - Method `.test_from_dict_none()` - *Test creating from None.*
  - Method `.test_from_dict_underscore_fields()` - *Test handling underscore-prefixed fields.*
  - Method `.test_from_dict_extra_fields()` - *Test handling extra fields.*
  - Method `.test_copy()` - *Test copying FlowContext.*
- `class TestBackwardCompatibility` - *Test backward compatibility with existing FlowData.*
  - Method `.test_from_existing_flowdata()` - *Test creating from existing TypedDict FlowData.*
  - Method `.test_to_dict_backward_compatible()` - *Test that to_dict produces backward compatible output.*
- `class TestNodeIntegration` - *Test node integration helpers.*
  - Method `.test_flow_context_to_node_input()` - *Test converting to node input format.*
  - Method `.test_node_output_to_flow_context()` - *Test converting node output to FlowContext.*
- `class TestMigrationUtilities` - *Test migration utilities.*
  - Method `.test_to_flow_context()` - *Test to_flow_context utility.*
  - Method `.test_to_flow_context_none()` - *Test to_flow_context with None.*
  - Method `.test_from_flow_context()` - *Test from_flow_context utility.*
- `class TestIntegration` - *Integration tests for FlowContext.*
  - Method `.test_full_flow_example()` - *Test a complete flow with FlowContext.*
  - Method `.test_agent_loop_flow()` - *Test agent loop with FlowContext.*
  - Method `.test_replan_flow()` - *Test replanning flow.*

---

### [`tests/test_flow_data.py`](tests/test_flow_data.py)
> *Tests for core.flow_data - Type-safe flow data handling.*

**Size**: `575` lines (`18382` bytes)

**Classes:**
- `class TestFlowDataGetters` - *Test type-safe getter functions.*
  - Method `.test_get_messages_valid()`
  - Method `.test_get_messages_empty()`
  - Method `.test_get_messages_none()`
  - Method `.test_get_messages_wrong_type()`
  - Method `.test_get_messages_default()`
  - Method `.test_get_content_valid()`
  - Method `.test_get_content_empty()`
  - Method `.test_get_content_none()`
  - Method `.test_get_plan_valid()`
  - Method `.test_get_plan_empty()`
  - Method `.test_get_current_step_valid()`
  - Method `.test_get_current_step_string()`
  - Method `.test_get_current_step_invalid()`
  - Method `.test_get_current_step_default()`
  - Method `.test_get_original_request_valid()`
  - Method `.test_get_plan_needed_true()`
  - Method `.test_get_plan_needed_false()`
  - Method `.test_get_plan_needed_string()`
  - Method `.test_get_satisfied_valid()`
  - Method `.test_get_satisfied_default()`
  - Method `.test_get_iterations_valid()`
  - Method `.test_get_iterations_default()`
  - Method `.test_get_replan_needed_valid()`
  - Method `.test_get_replan_count_valid()`
  - Method `.test_get_replan_count_default()`
  - Method `.test_get_tool_count_valid()`
  - Method `.test_get_tool_count_default()`
  - Method `.test_get_route_targets_valid()`
  - Method `.test_get_route_targets_default()`
  - Method `.test_get_repeat_count_valid()`
  - Method `.test_get_reflection_valid()`
  - Method `.test_get_reflection_none()`
  - Method `.test_get_response_valid()`
  - Method `.test_get_memory_context_valid()`
  - Method `.test_get_knowledge_context_valid()`
  - Method `.test_get_reasoning_context_valid()`
  - Method `.test_get_error_valid()`
  - Method `.test_get_error_none()`
- `class TestFlowDataSetters` - *Test type-safe setter functions.*
  - Method `.test_set_messages()`
  - Method `.test_set_content()`
  - Method `.test_set_plan()`
  - Method `.test_set_current_step()`
  - Method `.test_set_original_request()`
  - Method `.test_set_plan_needed()`
  - Method `.test_set_plan_context()`
  - Method `.test_set_plan_complete()`
  - Method `.test_set_next_step()`
  - Method `.test_set_reflection()`
  - Method `.test_set_satisfied()`
  - Method `.test_set_iterations()`
  - Method `.test_set_replan_needed()`
  - Method `.test_set_replan_count()`
  - Method `.test_set_error()`
  - Method `.test_set_route_targets()`
  - Method `.test_set_preserves_existing()`
- `class TestFlowDataValidation` - *Test validation functions.*
  - Method `.test_validate_valid_data()`
  - Method `.test_validate_not_dict()`
  - Method `.test_validate_messages_not_list()`
  - Method `.test_validate_content_not_str()`
  - Method `.test_validate_plan_not_list()`
  - Method `.test_validate_current_step_not_int()`
  - Method `.test_validate_plan_needed_not_bool()`
  - Method `.test_validate_satisfied_not_bool()`
  - Method `.test_validate_iterations_not_int()`
- `class TestFlowDataMigration` - *Test migration utilities.*
  - Method `.test_to_flow_data()`
  - Method `.test_ensure_flow_data_valid()`
  - Method `.test_ensure_flow_data_none()`
  - Method `.test_ensure_flow_data_not_dict()`
  - Method `.test_merge_flow_data_empty()`
  - Method `.test_merge_flow_data_single()`
  - Method `.test_merge_flow_data_multiple()`
  - Method `.test_merge_flow_data_later_overrides()`
- `class TestFlowDataTypoCheck` - *Test typo detection and correction.*
  - Method `.test_get_with_typo_check_direct()`
  - Method `.test_get_with_typo_check_missing()`
  - Method `.test_get_with_typo_check_known_typo()`
  - Method `.test_get_with_typo_check_unknown_key()`
  - Method `.test_get_with_typo_check_default()`
- `class TestFlowDataIntegration` - *Integration tests demonstrating typed flow data usage.*
  - Method `.test_full_flow_example()` - *Demonstrates a complete flow with typed accessors.*
  - Method `.test_backward_compatibility()` - *Demonstrates backward compatibility with untyped dicts.*
  - Method `.test_partial_flow_data()` - *Tests that partial flow data works correctly.*
  - Method `.test_plan_step_tracker_flow()` - *Tests plan step tracking with typed accessors.*
  - Method `.test_agent_loop_flow()` - *Tests agent loop state with typed accessors.*

---

### [`tests/test_flow_evolution.py`](tests/test_flow_evolution.py)
> *Tests for validated, versioned flow self-evolution.*

**Size**: `276` lines (`9705` bytes)

**Functions:**
- `def node(node_id, node_type, module_id, **config)`
- `def chat_flow()`
- `def flow_manager(tmp_path)`
- `def evolution(flow_manager)`
- `def test_create_persists_a_valid_flow(evolution, flow_manager)`
- `def test_create_rejects_missing_node_keys(evolution)`
- `def test_create_rejects_duplicate_node_ids(evolution)`
- `def test_create_rejects_unknown_connection_endpoint(evolution)`
- `def test_create_rejects_self_loop_and_cycle(evolution)`
- `def test_create_rejects_too_many_nodes(flow_manager)`
- `def test_create_rejects_empty_flow(evolution)`
- `def test_mutate_adds_node_and_connection_with_a_version(evolution, flow_manager)`
- `def test_mutate_update_remove_and_rename(evolution)`
- `def test_mutate_invalid_operation_leaves_flow_unchanged(evolution, flow_manager)`
- `def test_mutate_rejects_unknown_operation(evolution)`
- `def test_history_and_rollback_restore_metadata(evolution, flow_manager)`
- `def test_mutate_missing_flow_raises(evolution)`
- `def test_validate_accepts_tuple_sequences(evolution)`
- `def test_mutate_allows_existing_types_outside_the_allowlist(flow_manager)`
- `def test_allowlist_rejects_unknown_node_type(flow_manager)`
- `def test_allowlist_rejects_disallowed_tools(flow_manager)`
- `def test_create_rejects_an_existing_flow_id(evolution)`
- `def test_mutate_preserves_reverted_nodes(evolution, flow_manager)`

---

### [`tests/test_flow_integration.py`](tests/test_flow_integration.py)
**Size**: `110` lines (`5374` bytes)

**Functions:**
- `def temp_memory_store()`
- `async def test_memory_flow_integration(temp_memory_store)` - *Integration test for a flow with Memory Recall -> LLM -> Memory Save.*

---

### [`tests/test_flow_manager.py`](tests/test_flow_manager.py)
**Size**: `383` lines (`16099` bytes)

**Classes:**
- `class TestFlowVersioning`
  - Method `._make_flow(fm, name, nodes, connections, flow_id)`
  - Method `.test_new_flow_has_no_versions(flow_manager)` - *First save of a brand-new flow produces no version entry (nothing to snapshot).*
  - Method `.test_second_save_creates_version(flow_manager)` - *Re-saving an existing flow creates exactly one version.*
  - Method `.test_versions_returned_newest_first(flow_manager)` - *get_versions() returns versions with highest version number first.*
  - Method `.test_versions_include_metadata(flow_manager)` - *Version entries include name, saved_at, node_count, connection_count.*
  - Method `.test_max_versions_pruned(flow_manager)` - *Versions exceeding MAX_VERSIONS_PER_FLOW are pruned (oldest removed).*
  - Method `.test_rollback_restores_snapshot(flow_manager)` - *rollback_version() restores the flow to the snapshotted state.*
  - Method `.test_rollback_preserves_created_at(flow_manager)` - *Rollback restores the original creation timestamp.*
  - Method `.test_rollback_restores_metadata(flow_manager)` - *Version snapshots carry metadata so rollback keeps ownership.*
  - Method `.test_rollback_adds_a_redo_version(flow_manager)` - *Rollback records the pre-rollback state so the rollback itself is undoable.*
  - Method `.test_rollback_nonexistent_version_returns_none(flow_manager)` - *rollback_version() with an unknown version number returns None.*
  - Method `.test_rollback_nonexistent_flow_returns_none(flow_manager)` - *rollback_version() with an unknown flow_id returns None.*
  - Method `.test_versions_persisted_to_disk(flow_manager)` - *Version data is written to the versions file on disk.*
  - Method `.test_versions_loaded_on_init(flow_manager)` - *A freshly constructed FlowManager loads existing versions from disk.*
  - Method `.test_get_versions_unknown_flow_returns_empty_list(flow_manager)` - *get_versions() for a flow that never existed returns [].*

**Functions:**
- `def flow_manager()` - *Provides a FlowManager instance using a temporary file, ensuring test isolation.*
- `def test_initialization_creates_file(flow_manager)` - *Tests that a new, empty flow file is created on first run.*
- `def test_save_and_get_flow(flow_manager)` - *Tests saving a flow and retrieving it.*
- `def test_save_flow_preserves_metadata_and_created_at(flow_manager)` - *Metadata is stored and an update keeps the original created_at.*
- `def test_save_flow_preserves_reverted_nodes(flow_manager)` - *An update keeps reverted_nodes that were not passed again.*
- `def test_list_flows_sorted(flow_manager)` - *Tests that flows are listed with the newest first.*
- `def test_delete_flow(flow_manager)` - *Tests deleting an existing and non-existing flow.*
- `def test_updated_flow_sorts_first(flow_manager)` - *Editing a flow moves it to the top of the list without changing created_at.*
- `def test_delete_flow_retains_versions_for_restore(flow_manager)` - *Deleting keeps snapshots so the flow can be restored.*
- `def test_delete_flow_persists_when_settings_pruning_fails(flow_manager, monkeypatch)` - *A settings failure must not desynchronize the flow store.*
- `def test_delete_flow_prunes_active_settings(flow_manager, monkeypatch)` - *Deleting an active flow also removes it from active_ai_flows.*
- `def test_returned_flows_are_copies(flow_manager)` - *Mutating a returned flow must not change the store.*
- `def test_rename_flow(flow_manager)` - *Tests renaming a flow.*
- `def test_add_flow_validates_and_preserves_imported_fields(tmp_path)`
- `def test_add_flow_rejects_a_second_identical_id(tmp_path)`
- `def test_validate_flow_reports_a_dangling_connection_without_saving(tmp_path)`

---

### [`tests/test_flow_proposer.py`](tests/test_flow_proposer.py)
> *Tests for LLM-generated flow edit proposals.*

**Size**: `89` lines (`2941` bytes)

**Classes:**
- `class FakeLLM`
  - Method `.__init__(content)`
  - Method `async def .chat_completion(**kwargs)`

**Functions:**
- `def flow()`
- `async def test_propose_returns_valid_operations()`
- `async def test_propose_strips_json_code_fences()`
- `async def test_propose_rejects_invalid_json()`
- `async def test_propose_rejects_unknown_operation()`
- `async def test_propose_rejects_too_many_operations()`
- `async def test_propose_rejects_disallowed_node_type()`
- `async def test_propose_surfaces_a_missing_choice()`

---

### [`tests/test_flow_runner.py`](tests/test_flow_runner.py)
**Size**: `220` lines (`9120` bytes)

**Functions:**
- `def mock_flow()` - *A fixture for a valid, simple, linear flow.*
- `def mock_flow_with_cycle()` - *A fixture for a flow with a cycle, which should be invalid.*
- `def test_topological_sort_success(mock_flow)` - *Tests that a valid flow produces the correct execution order.*
- `def test_topological_sort_cycle_breaking(mock_flow_with_cycle)` - *Tests that a flow with a cycle is handled by breaking the cycle.*
- `async def test_flow_run_success(mock_flow)` - *Tests the successful execution of a flow from start to finish.*
- `async def test_flow_run_node_execution_error(mock_flow)` - *Tests that an error during a node's execution is caught and reported.*
- `async def test_messages_list_not_shared_between_branches()` - *Regression test for the shared-messages-list bug.*
- `def _node(node_id)`
- `def test_cycle_is_entered_at_the_node_fed_from_outside_it()`
- `def test_default_tool_loop_runs_the_llm_before_the_router(tmp_path)`

---

### [`tests/test_flow_simulation.py`](tests/test_flow_simulation.py)
> *Deterministic flow simulation: structure, effects, cycles and cost envelope.*

**Size**: `170` lines (`6587` bytes)

**Functions:**
- `def node(node_id, module, node_type, **extra)`
- `def flow(nodes, connections, **extra)`
- `def test_empty_flow_is_blocking()`
- `def test_dangling_connection_blocks_before_execution()`
- `def test_isolated_node_is_a_warning_not_a_block()`
- `def test_unknown_node_type_blocks_when_a_catalogue_is_supplied()`
- `def test_effects_are_classified_without_executing_anything()`
- `def test_a_cycle_reports_its_envelope_as_a_warning_not_a_block()`
- `def test_a_flow_that_cannot_fit_one_pass_is_blocking()`
- `def test_simulation_route_returns_404_and_a_report()`
- `def test_a_blocked_candidate_is_rejected_by_improvement(tmp_path, monkeypatch)`
- `def test_the_catalogue_reads_provides_nodes_from_manifests()`
- `def test_a_missing_provides_nodes_entry_blocks_the_flow()`
- `def test_a_dangling_bridge_blocks_before_execution()`
- `def test_the_catalogue_includes_single_node_modules()`

---

### [`tests/test_flow_store_safety.py`](tests/test_flow_store_safety.py)
> *Flow store durability and validation regressions.*

**Size**: `149` lines (`4863` bytes)

**Functions:**
- `def valid_flow(flow_id, name)`
- `def make_manager(tmp_path)`
- `def test_validated_save_rejects_invalid_shape_without_touching_store(tmp_path)`
- `def test_validated_save_rejects_orphaned_connection(tmp_path)`
- `def test_writes_are_atomic_and_keep_a_backup(tmp_path)`
- `def test_corrupt_store_is_quarantined_and_backup_restored(tmp_path)`
- `def test_non_dict_flow_entry_does_not_crash_load(tmp_path)`
- `def test_import_flows_reports_invalid_data_and_keeps_state(tmp_path)`
- `async def test_save_route_requests_validation_and_returns_400(monkeypatch)`
- `async def test_import_route_surfaces_validation_failure(monkeypatch)`

---

### [`tests/test_flow_validation.py`](tests/test_flow_validation.py)
**Size**: `290` lines (`8581` bytes)

**Functions:**
- `def mock_module_manager()` - *Create a mock module manager.*
- `def basic_flow()` - *Create a basic valid flow.*
- `def flow_with_disabled_module()` - *Create a flow with a node referencing disabled module.*
- `def flow_with_orphaned_connection()` - *Create a flow with orphaned connections.*
- `def flow_with_unconnected_node()` - *Create a flow with unconnected (non-trigger) nodes.*
- `def flow_with_bridges()` - *Create a flow with bridged nodes.*
- `def flow_with_annotation()` - *Create a flow with annotation/comment nodes.*
- `def test_validate_valid_flow(basic_flow, mock_module_manager)` - *Test validation of a valid flow.*
- `def test_validate_disabled_module(flow_with_disabled_module, mock_module_manager)` - *Test validation detects nodes referencing disabled modules.*
- `def test_validate_orphaned_connection(flow_with_orphaned_connection, mock_module_manager)` - *Test validation detects orphaned connections.*
- `def test_validate_unconnected_node(flow_with_unconnected_node, mock_module_manager)` - *Test validation warns about unconnected nodes.*
- `def test_validate_bridged_nodes_count_as_connected(flow_with_bridges, mock_module_manager)` - *Test that bridged nodes are considered connected.*
- `def test_validate_annotation_no_warning(flow_with_annotation, mock_module_manager)` - *Test that annotation/comment nodes don't trigger unconnected warnings.*
- `def test_validate_trigger_node_no_warning(mock_module_manager)` - *Test that trigger nodes don't trigger unconnected warnings.*
- `def test_validate_repeater_node_no_warning(mock_module_manager)` - *Test that repeater nodes don't trigger unconnected warnings.*

---

### [`tests/test_flow_versioning.py`](tests/test_flow_versioning.py)
> *Version history for rename, delete, import and reversible rollback.*

**Size**: `80` lines (`2839` bytes)

**Functions:**
- `def make_manager(tmp_path)`
- `def chat_flow_nodes(suffix)`
- `def test_rename_is_versioned_and_rolls_back(tmp_path)`
- `def test_delete_retains_history_and_rollback_restores_the_flow(tmp_path)`
- `def test_rollback_adds_a_redo_snapshot(tmp_path)`
- `def test_import_snapshots_replaced_flows(tmp_path)`

---

### [`tests/test_goal_pursuit.py`](tests/test_goal_pursuit.py)
> *Tests for GoalPursuitExecutor — integrated planning, step evaluation, replanning.*

**Size**: `3811` lines (`151690` bytes)

**Functions:**
- `def make_executor()` - *Create a GoalPursuitExecutor with patched LLMBridge and sandbox.*
- `def llm_response(content)` - *Build a minimal valid LLM response dict.*
- `def plan_json(*actions)` - *Serialize a list of step dicts to JSON.*
- `def eval_json(success, reason, extracted)`
- `def _isolate_goal_lessons(monkeypatch)` - *Keep the shared memory store out of planning.*
- `def _stub_step_reasoning(monkeypatch)` - *Stub out _reason_about_step for all GoalPursuit tests.*
- `def _stub_metacognition_calls(monkeypatch)` - *Stub out the three new metacognition LLM calls for all GoalPursuit tests.*
- `async def test_parse_plan_response_valid()`
- `async def test_parse_plan_response_respects_max_steps()`
- `async def test_parse_plan_response_invalid_json_returns_empty()`
- `async def test_parse_plan_response_step_offset()`
- `async def test_create_plan_returns_structured_steps()`
- `async def test_create_plan_llm_failure_returns_empty()`
- `async def test_create_plan_strips_js_comments()`
- `async def test_evaluate_step_success()`
- `async def test_evaluate_step_failure()`
- `async def test_evaluate_step_llm_failure_returns_failure()`
- `async def test_evaluate_step_fallback_unparseable_returns_failure()` - *When JSON parse fails, returns fail-safe False (no keyword guessing).*
- `async def test_replan_returns_revised_steps()`
- `async def test_replan_llm_failure_returns_none()` - *_replan must return None (not []) on LLM failure so the caller can*
- `def test_build_plan_context_shows_progress()`
- `async def test_receive_no_messages_returns_error()`
- `async def test_receive_no_user_message_returns_error()`
- `async def test_receive_single_step_success()`
- `async def test_receive_multi_step_with_synthesis()`
- `async def test_receive_step_fails_replans_and_succeeds()`
- `async def test_receive_replan_depth_exceeded_stops()`
- `async def test_receive_evaluation_disabled_uses_structural_check()`
- `async def test_receive_empty_plan_falls_back_to_hybrid()`
- `async def test_receive_timeout_returns_error()`
- `async def test_receive_unexpected_exception_returns_error()` - *An exception that escapes the internal loop surfaces as agent_loop_error.*
- `async def test_receive_repl_state_shared_across_steps()` - *Variables stored in step 1 should be visible to step 2.*
- `async def test_receive_output_keys_present()`
- `async def test_receive_preserves_input_fields()`
- `async def test_get_executor_class_goal_pursuit()`
- `async def test_get_executor_class_unknown()`
- `async def test_evaluate_step_strips_markdown_fences()` - *JSON wrapped in ```json ... ``` code fences is correctly parsed.*
- `async def test_receive_step_skipped_after_max_retries()` - *Step that fails max_step_retries consecutive times is skipped, not infinitely replanned.*
- `async def test_receive_step_result_stored_in_repl_state()` - *Successful step stores its full content in repl_state['variables']['step_N_result'].*
- `async def test_receive_step_timeout_treated_as_failure()` - *When step_timeout fires, the step is treated as failed (skipped via max_step_retries=1).*
- `def test_topo_sort_linear_no_deps()` - *Steps with no depends_on come out in original order.*
- `def test_topo_sort_respects_dependencies()` - *Step 3 depends on step 1; step 2 is independent. Valid orders: 1,2,3 or 1,3 after 1, or 2,1,3.*
- `def test_topo_sort_chain()` - *3 → 2 → 1 dependency chain must run as 1, 2, 3.*
- `def test_topo_sort_cycle_returns_original()` - *Cyclic deps are detected and original order is returned unchanged.*
- `def test_topo_sort_ignores_external_deps()` - *Deps pointing to step numbers not in the plan are ignored.*
- `async def test_parse_plan_response_extracts_depends_on()` - *depends_on field is parsed from plan JSON.*
- `async def test_parse_plan_response_default_empty_depends_on()` - *Steps without depends_on get an empty list.*
- `async def test_receive_dependency_respected()` - *Steps with depends_on are deferred until their prerequisites complete.*
- `async def test_receive_repl_variables_restored_from_input()` - *Variables in input_data['_repl_variables'] are loaded into repl_state at startup.*
- `async def test_ask_user_pauses_execution()` - *When the agent calls AskUser during a step, receive() returns immediately with paused=True.*
- `async def test_ask_user_resume_injects_answer()` - *_user_answer injected via _repl_variables (as episode-resume path does) is visible in repl_state.*
- `async def test_execute_tool_intercepts_ask_user()` - *_execute_tool override sets _pending_question and does not call super().*
- `async def test_alternative_approach_succeeds_after_max_retries()` - *When a step hits max_step_retries=1, an alternative attempt is made before skipping.*
- `async def test_alternative_approach_fails_then_step_skipped()` - *When normal attempt and alternative both fail, the step is skipped.*
- `async def test_save_run_reflection_called_on_completion()` - *_save_run_reflection is scheduled as a task after _execute() returns.*
- `async def test_compress_step_results_noop_when_small()` - *No compression when total step results are under the threshold.*
- `async def test_compress_step_results_compresses_large_vars()` - *Large step results are summarized when total exceeds threshold.*
- `async def test_compress_step_results_called_before_synthesis()` - *_compress_step_results is awaited before _synthesize during plan execution.*
- `async def test_fetch_goal_lessons_returns_empty_on_db_error()` - *DB errors in _fetch_goal_lessons are silently swallowed.*
- `async def test_compress_lessons_passthrough_when_small()` - *Short lessons are returned unchanged (no LLM call).*
- `async def test_compress_lessons_calls_llm_when_large()` - *Large lesson list triggers chunked LLM summarization.*
- `async def test_lessons_injected_into_planning_prompt()` - *Past lessons are fetched and injected when _create_plan_with_trace runs.*
- `async def test_decompose_goal_tool_injected_when_enabled()` - *DecomposeGoal appears in tools_list when enable_subgoals=True (default).*
- `async def test_decompose_goal_tool_not_injected_at_max_depth()` - *DecomposeGoal is absent when subgoal_depth >= max_subgoal_depth.*
- `async def test_decompose_goal_tool_not_injected_when_disabled()` - *DecomposeGoal is absent when enable_subgoals=False.*
- `async def test_execute_tool_decompose_goal_invokes_sub_receive()` - *_execute_tool with DecomposeGoal calls receive() recursively and returns result.*
- `async def test_execute_tool_decompose_goal_missing_subgoal_returns_error()` - *DecomposeGoal with no subgoal parameter returns an error result.*
- `async def test_subgoal_depth_incremented_in_recursive_call()` - *When DecomposeGoal spawns a sub-call, _subgoal_depth in the sub-config is depth+1.*
- `async def test_subgoal_result_content_returned_as_tool_result()` - *DecomposeGoal returns the sub-goal's content string as the tool result.*
- `async def test_parse_plan_response_applies_offset_to_depends_on()` - *When step_offset > 0 (as used in _replan), the depends_on values in the*
- `async def test_parse_plan_response_zero_offset_depends_on_unchanged()` - *With step_offset=0 (initial plan) depends_on must be kept as-is.*
- `async def test_rlm_subcall_tool_call_id_override()` - *RLMAgentLoopExecutor._execute_tool must override the tool_call_id returned*
- `def test_ask_user_pause_remaining_steps_matches_plan()` - *On AskUser pause, result["remaining_steps"] must equal result["plan"]*
- `def test_build_step_system_prompt_filters_only_step_result_vars()` - *Variables like step_timeout must not appear in the Available Step Results list.*
- `async def test_decompose_goal_blocked_when_subgoals_disabled()` - *If enable_subgoals=False, a hallucinated DecomposeGoal call must return*
- `async def test_evaluate_step_uses_configurable_truncation()` - *_evaluate_step must truncate execution_result at eval_response_max_chars*
- `async def test_evaluate_step_custom_truncation_respected()` - *eval_response_max_chars config value must be honoured over the default.*
- `async def test_goal_id_non_integer_raises_clear_error(capsys)` - *A UUID-style goal_id must trigger a ValueError with a clear message, not*
- `async def test_rlm_loop_deduplicates_tool_calls()` - *_run_rlm_loop must skip duplicate tool calls within the same response,*
- `async def test_ask_user_auto_proceed_generates_assumption()` - *When ask_user_auto_proceed=True, _execute_tool must NOT pause.*
- `async def test_ask_user_standard_mode_still_pauses()` - *When ask_user_auto_proceed is False (default), AskUser must pause normally.*
- `async def test_generate_ask_user_assumption_returns_llm_content()` - *_generate_ask_user_assumption must use LLM output as the assumption.*
- `async def test_generate_ask_user_assumption_fallback_on_llm_failure()` - *If the LLM call fails, a safe fallback string must be returned.*
- `async def test_ask_user_assumptions_surfaced_in_result()` - *ask_user_assumptions must appear in the final result dict when*
- `async def test_reason_about_step_filters_var_names_correctly(monkeypatch)` - *_reason_about_step must only list step_*_result variables, not any step_**
- `async def test_reason_about_step_includes_tool_names(monkeypatch)` - *When tools_list is passed, _reason_about_step must include tool names in*
- `async def test_reason_about_step_includes_reasoning_history(monkeypatch)` - *When reasoning_history is provided (from ReasoningBook), it must appear*
- `async def test_reason_about_step_no_tools_no_tools_note(monkeypatch)` - *When tools_list is empty, the prompt must not include a tools line.*
- `def test_context_pressure_zero_when_no_stdout()` - *Zero stdout_history → pressure 0.0.*
- `def test_context_pressure_scales_with_stdout()` - *Accumulated stdout chars / 4 should equal the fraction.*
- `def test_context_pressure_capped_at_one()` - *Pressure never exceeds 1.0 regardless of stdout size.*
- `def test_context_pressure_zero_max_context_guard()` - *max_context_tokens=0 must not raise ZeroDivisionError; returns 1.0.*
- `async def test_step_trace_contains_execution_mode_and_pressure(monkeypatch)` - *step_trace emitted for each step must include execution_mode and context_pressure.*
- `async def test_step_execution_mode_rlm_forces_rlm(monkeypatch)` - *step_execution_mode='rlm' must always choose RLM regardless of pressure.*
- `async def test_step_execution_mode_auto_triggers_rlm_above_threshold(monkeypatch)` - *In 'auto' mode, exceed the pressure threshold → RLM loop is used.*
- `async def test_step_execution_mode_auto_uses_hybrid_below_threshold(monkeypatch)` - *In 'auto' mode, below the pressure threshold → hybrid loop is used.*
- `async def test_reasoning_history_accumulates_in_repl_state(monkeypatch)` - *After each step, its reasoning must be stored in repl_state['_step_reasoning_history'].*
- `async def test_reasoning_history_fed_to_next_step(monkeypatch)` - *The second step's _reason_about_step call must receive the first step's reasoning.*
- `async def test_replan_uses_full_step_output_from_repl_state()` - *_replan must prefer repl_state['variables']['step_N_result'] over extracted_result[:150].*
- `async def test_replan_falls_back_to_extracted_result_when_no_variable()` - *When repl_state has no variable for a step, fall back to extracted_result.*
- `async def test_replan_without_repl_state_still_works()` - *_replan must remain backward-compatible when repl_state is not passed.*
- `async def test_evaluate_step_includes_original_goal_in_prompt()` - *_evaluate_step must include user_goal in the prompt sent to the LLM.*
- `async def test_evaluate_step_falls_back_to_step_goal_when_no_user_goal()` - *When user_goal is empty, {original_goal} falls back to step.goal.*
- `async def test_replan_history_included_in_prompt_on_second_attempt()` - *On a second replan, prior attempt details must appear in the replanner prompt.*
- `async def test_replan_history_empty_when_no_prior_replans()` - *When there are no prior replans, the history section must not appear in the prompt.*
- `async def test_replan_history_recorded_before_replan_call()` - *The call site must append to _replan_history before calling _replan.*
- `def test_is_unhelpful_result_empty()`
- `def test_is_unhelpful_result_short()`
- `def test_is_unhelpful_result_error()`
- `def test_is_unhelpful_result_no_data_patterns()`
- `def test_is_unhelpful_result_useful_content()`
- `async def test_stuck_detection_injects_redirect_after_threshold()` - *After max_consecutive_unhelpful iterations of empty results, a redirect is injected.*
- `async def test_stuck_detection_resets_on_useful_result()` - *A useful result resets the consecutive counter — loop continues normally.*
- `async def test_parse_plan_response_extracts_risk_and_reversible()` - *_parse_plan_response must extract risk and reversible from LLM JSON.*
- `async def test_parse_plan_response_defaults_missing_risk()` - *When risk/reversible are absent, defaults (low/True) are used.*
- `async def test_parse_plan_response_rejects_invalid_risk_value()` - *Invalid risk values (not low/medium/high) must default to 'low'.*
- `def test_step_trace_includes_risk_and_reversible()` - *step_trace emitted per step must include risk and reversible from the step dict.*
- `def test_build_step_system_prompt_warns_for_high_risk()` - *High-risk irreversible steps must include a warning in the system prompt.*
- `def test_build_step_system_prompt_no_warning_for_low_risk()` - *Low-risk reversible steps must NOT include a risk warning.*
- `def test_context_pressure_zero_for_hybrid_steps_with_no_history()` - *No hybrid history → hybrid_pressure = 0.*
- `def test_context_pressure_uses_hybrid_output_history()` - *Peak of _hybrid_output_history drives pressure when stdout_history is empty.*
- `def test_context_pressure_takes_max_of_both_signals()` - *Returns max(rlm_pressure, hybrid_pressure).*
- `def test_context_pressure_uses_peak_not_sum_of_hybrid_history()` - *Pressure uses peak of last 3 hybrid steps, not cumulative sum.*
- `async def test_hybrid_step_records_output_in_hybrid_output_history(monkeypatch)` - *After a hybrid step, step_messages size is recorded in _hybrid_output_history.*
- `async def test_pending_question_uses_task_scoped_key()` - *Two parallel steps calling AskUser must not overwrite each other's question.*
- `async def test_estimated_cost_reserved_before_await()` - *Cost is incremented before the LLM await so parallel sub-calls see the updated total.*
- `async def test_estimated_cost_parallel_steps_blocked_together()` - *Second sub-call is blocked when its cost would push the total over max.*
- `async def test_estimated_cost_reverted_on_block()` - *When a sub-call is blocked, the cost reservation must be reverted.*
- `async def test_synthesize_results_all_empty_returns_failure_message()` - *When all steps produced no output, synthesize must return a clear failure.*
- `async def test_synthesize_results_inlines_small_result()` - *Results <=600 chars must appear directly in the system message without GetVariable.*
- `async def test_synthesize_results_uses_get_variable_for_large_result()` - *Results >600 chars must use GetVariable instruction, not inline full text.*
- `async def test_synthesize_results_includes_reasoning_history()` - *_step_reasoning_history must appear in the synthesis system message.*
- `async def test_synthesize_results_no_reasoning_section_when_history_empty()` - *No reasoning section must appear in the prompt when history is empty.*
- `def test_run_insights_appended_after_success()` - *A successful step must append the extracted_result to _run_insights.*
- `def test_run_insights_not_appended_on_empty_extracted_result()` - *Steps with empty extracted_result must not pollute _run_insights.*
- `async def test_build_step_system_prompt_shows_insights()` - *_build_step_system_prompt must include Established Findings when insights exist.*
- `async def test_build_step_system_prompt_no_findings_section_when_empty()` - *No Established Findings section must appear when _run_insights is empty.*
- `async def test_reason_about_step_includes_insights(monkeypatch)` - *_reason_about_step must include _run_insights in the LLM prompt.*
- `async def test_reason_about_step_no_insights_section_when_empty(monkeypatch)` - *No insights section must appear in the reasoning prompt when history is empty.*
- `async def test_evaluate_step_evidence_quality_no_tools()` - *NO_TOOLS_CALLED must be injected when tool_history has no result lines.*
- `async def test_evaluate_step_evidence_quality_all_failed()` - *ALL_FAILED must be injected when every tool result is an error.*
- `async def test_evaluate_step_evidence_quality_all_empty()` - *ALL_EMPTY must be injected when every successful tool returned no-data content.*
- `async def test_evaluate_step_evidence_quality_has_data()` - *HAS_DATA must be injected when at least one tool returned useful content.*
- `async def test_evaluate_step_prompt_contains_anti_filler_rules()` - *The eval prompt must contain explicit anti-filler and fabrication rules.*
- `async def test_assess_task_clarity_returns_structured_result(monkeypatch)` - *_assess_task_clarity must return is_actionable, critical_unknowns, safe_assumptions.*
- `async def test_assess_task_clarity_defaults_on_llm_failure(monkeypatch)` - *On LLM failure, _assess_task_clarity must return safe defaults (is_actionable=True).*
- `async def test_assess_task_clarity_defaults_on_bad_json(monkeypatch)` - *On unparseable LLM response, defaults must be returned.*
- `def test_metacognition_warning_set_on_no_progress_spiral()` - *_metacognition_warning must be set when replans >= half-depth AND insights empty.*
- `def test_metacognition_warning_not_set_when_insights_exist()` - *Warning must NOT be set when the agent has made progress (has insights).*
- `def test_metacognition_warning_not_set_below_threshold()` - *Warning must NOT be set when replan_count is below the spiral threshold.*
- `def test_build_step_system_prompt_shows_metacognition_warning()` - *_build_step_system_prompt must include the warning when it's set.*
- `def test_build_step_system_prompt_shows_mid_run_assessment()` - *_build_step_system_prompt must show Strategic Self-Assessment when set.*
- `async def test_mid_run_assessment_returns_string(monkeypatch)` - *_mid_run_assessment must return a non-empty string from the LLM.*
- `async def test_mid_run_assessment_returns_empty_on_llm_failure(monkeypatch)` - *On LLM failure, _mid_run_assessment must return empty string (not crash).*
- `async def test_mid_run_assessment_prompt_includes_goal_and_insights(monkeypatch)` - *The mid-run assessment prompt must include goal and current findings.*
- `async def test_extract_failure_patterns_returns_empty_for_few_lessons(monkeypatch)` - *Pattern extraction must return empty string when < 3 lessons.*
- `async def test_extract_failure_patterns_calls_llm_with_pattern_query(monkeypatch)` - *_extract_failure_patterns must call LLM asking for patterns, not summaries.*
- `async def test_extract_failure_patterns_returns_empty_on_llm_failure(monkeypatch)` - *On LLM failure, pattern extraction must return empty string.*
- `async def test_create_plan_receives_pattern_insights()` - *_create_plan must inject pattern_insights into the planning prompt.*

---

### [`tests/test_goals.py`](tests/test_goals.py)
**Size**: `179` lines (`5630` bytes)

**Functions:**
- `def temp_db()` - *Create a temporary database for testing.*
- `def memory_store(temp_db)` - *Create a MemoryStore with temporary database.*
- `async def test_create_goal(memory_store)` - *Test creating a new goal.*
- `async def test_create_goal_with_deadline(memory_store)` - *Test creating a goal with deadline.*
- `async def test_get_goals(memory_store)` - *Test retrieving all goals.*
- `async def test_get_goals_by_status(memory_store)` - *Test filtering goals by status.*
- `async def test_update_goal(memory_store)` - *Test updating a goal.*
- `async def test_update_goal_status(memory_store)` - *Test updating goal status.*
- `async def test_complete_goal(memory_store)` - *Test marking a goal as completed.*
- `async def test_delete_goal(memory_store)` - *Test deleting a goal.*
- `async def test_get_next_goal(memory_store)` - *Test getting the highest priority pending/in_progress goal.*
- `async def test_get_next_goal_returns_in_progress_before_pending(memory_store)` - *Test that in_progress goals are returned before pending.*
- `async def test_update_nonexistent_goal(memory_store)` - *Test updating a goal that doesn't exist.*
- `async def test_delete_nonexistent_goal(memory_store)` - *Test deleting a goal that doesn't exist.*
- `async def test_goal_priority_ordering(memory_store)` - *Test that goals are returned in priority order.*

---

### [`tests/test_index_and_correlation.py`](tests/test_index_and_correlation.py)
> *Correlation edge cases and FTS/FAISS self-healing.*

**Size**: `89` lines (`2657` bytes)

**Classes:**
- `class CountingConnection` - *Minimal sqlite connection double that returns chosen FTS/chunk counts.*
  - Method `.__init__(fts_count, chunk_count)`
  - Method `.__enter__()`
  - Method `.__exit__(*args)`
  - Method `.execute(sql, *params)`
  - Method `.fetchone()`
  - Method `.commit()`

**Functions:**
- `def test_pearson_constant_series_is_undefined_not_nan()`
- `def test_spearman_constant_series_is_undefined_not_nan()`
- `def test_correlation_requires_two_values()`
- `def test_intervention_analysis_requires_two_values_per_group()`
- `def test_fts_sync_rebuilds_on_row_count_mismatch(tmp_path, monkeypatch)`
- `def test_fts_sync_leaves_matching_indexes_alone(tmp_path, monkeypatch)`

---

### [`tests/test_kb_stored_filename.py`](tests/test_kb_stored_filename.py)
> *Knowledge-base storage-name regressions.*

**Size**: `110` lines (`3428` bytes)

**Functions:**
- `def test_add_document_persists_stored_filename(tmp_path)`
- `def test_reindex_document_updates_stored_filename(tmp_path)`
- `def test_legacy_documents_table_gets_stored_filename_column(tmp_path)`
- `def test_resolve_stored_path_prefers_recorded_name(tmp_path)`
- `def test_resolve_stored_path_finds_legacy_uuid_prefixed_file(tmp_path)`
- `def test_document_storage_names_maps_legacy_rows_to_disk(tmp_path)`

---

### [`tests/test_knowledge_base.py`](tests/test_knowledge_base.py)
**Size**: `584` lines (`24007` bytes)

**Classes:**
- `class TestFaissDimensionMismatchWarning` - *Regression tests for the FAISS/SQLite dimension-mismatch split-brain bug.*
  - Method `.store(tmp_path)`
  - Method `._make_chunk(dim, value)`
  - Method `.test_skip_count_returned_on_dimension_mismatch(store)` - *_add_embeddings_to_faiss() returns the number of skipped embeddings.*
  - Method `.test_add_document_logs_warning_on_dimension_mismatch(store, caplog)` - *add_document() logs a WARNING when chunks are skipped by FAISS.*
  - Method `.test_no_warning_when_all_chunks_indexed(store, caplog)` - *add_document() must NOT warn when all chunks are accepted by FAISS.*
- `class TestIncrementalReindex` - *Tests for the delta re-indexing feature (idea 3.3).*
  - Method `.store(tmp_path)`
  - Method `._chunk(text, page)`
  - Method `._add_doc(store, filename, chunks, file_hash)`
  - Method `.test_get_document_by_filename_returns_none_for_missing(store)`
  - Method `.test_get_document_by_filename_returns_metadata(store)`
  - Method `.test_add_document_stores_chunk_hash(store)`
  - Method `.test_reindex_preserves_unchanged_chunks(store)`
  - Method `.test_reindex_adds_new_chunks(store)`
  - Method `.test_reindex_removes_stale_chunks(store)`
  - Method `.test_reindex_replaces_changed_chunk(store)`
  - Method `.test_reindex_updates_document_metadata(store)`
  - Method `.test_processor_chunk_text_includes_hash()`
  - Method `.test_processor_chunk_pages_includes_hash()`
- `class TestGetDocumentChunks`
  - Method `._make_store(tmp_path)`
  - Method `.test_returns_chunks_in_order(tmp_path)`
  - Method `.test_unknown_doc_returns_empty(tmp_path)`
- `class TestReembedDocument`
  - Method `._make_store_with_doc(tmp_path)`
  - Method `.test_updates_sqlite_embeddings(tmp_path)`
  - Method `.test_counts_none_as_failed(tmp_path)`
  - Method `.test_empty_input_returns_zeros(tmp_path)`
- `class TestReindexEndpoint`
  - Method `.test_reindex_success()`
  - Method `.test_reindex_not_found()`
  - Method `.test_reindex_partial_failure()`

**Functions:**
- `def kb_store()`
- `def test_kb_backend_add_search(kb_store)` - *Test adding documents and searching them.*
- `async def test_kb_node_execution()` - *Test the KnowledgeQueryExecutor.*
- `def client()`
- `def test_kb_router_list(client)` - *Test listing documents.*
- `def test_kb_router_delete(client)` - *Test deleting a document.*
- `def test_kb_router_upload_flow(client)` - *Test the upload flow (mocking heavy processing).*
- `def test_format_size()`

---

### [`tests/test_knowledge_base_improvements.py`](tests/test_knowledge_base_improvements.py)
**Size**: `245` lines (`8773` bytes)

**Functions:**
- `async def test_processor_parallel_embeddings()` - *Test that DocumentProcessor can generate embeddings.*
- `def test_backend_optimized_search()` - *Test the optimized SQL IN search.*
- `def test_query_expansion_sync()` - *Test synchronous query expansion generates variations.*
- `def test_rerank_candidates()` - *Test re-ranking of candidates.*
- `def test_rerank_limits_results()` - *Test that re-ranking limits results to top k.*
- `async def test_expand_query_async_with_llm()` - *Test async query expansion with LLM.*
- `def test_search_enhanced_fallback()` - *Test enhanced search falls back correctly.*

---

### [`tests/test_knowledge_base_node.py`](tests/test_knowledge_base_node.py)
> *Tests for modules/knowledge_base/node.py — KnowledgeQueryExecutor*

**Size**: `164` lines (`6356` bytes)

**Functions:**
- `def make_executor()` - *Create a KnowledgeQueryExecutor with fresh import to avoid stale class refs.*
- `async def test_none_input_returns_none()` - *None input should return None.*
- `async def test_config_none_guard()` - *config=None should not raise.*
- `async def test_empty_document_store_passthrough()` - *When document store is empty, input should be returned unchanged.*
- `async def test_embedding_search_returns_context()` - *When embedding succeeds, knowledge_context should be set.*
- `async def test_embedding_failure_falls_back_to_keyword_search()` - *When embedding returns None, keyword search should be used as fallback.*
- `async def test_multimodal_query_extraction()` - *Multimodal content (list of parts) should be extracted as text for query.*
- `async def test_content_field_used_as_query()` - *When input has 'content' but no 'messages', it should be used as the query.*
- `async def test_get_executor_class_dispatcher()` - *get_executor_class('query_knowledge') should return KnowledgeQueryExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*

---

### [`tests/test_llm_node.py`](tests/test_llm_node.py)
**Size**: `343` lines (`13461` bytes)

**Functions:**
- `def reset_shared_client()` - *Reset the shared client before each test to avoid event loop issues.*
- `def mock_settings()` - *Mock settings to return predictable values for tests.*
- `async def test_llm_executor_receive()` - *Test that the executor calls the bridge with correct messages.*
- `async def test_llm_executor_explicit_fallbacks(mock_settings)` - *Test that LLMExecutor uses explicit fallbacks when module_defaults is empty.*
- `async def test_llm_executor_timeout(mock_settings)` - *Test that LLMExecutor respects timeout config.*
- `async def test_llm_executor_timeout_disabled(mock_settings)` - *Test that LLMExecutor with timeout=0 doesn't use asyncio.wait_for.*
- `async def test_llm_executor_retry_on_failure(mock_settings)` - *Test that LLMExecutor retries on failure with exponential backoff.*
- `async def test_llm_executor_all_retries_exhausted(mock_settings)` - *Test that LLMExecutor returns error when all retries exhausted.*
- `async def test_llm_executor_default_retry_config(mock_settings)` - *Test that LLMExecutor uses default retry config when not specified.*
- `async def test_llm_executor_config_override(mock_settings)` - *Test that node configuration overrides input data.*
- `async def test_llm_executor_module_defaults(mock_settings)` - *Test that module defaults are used when no other config is present.*
- `async def test_llm_executor_input_override(mock_settings)` - *Test that input data overrides module defaults but not node config.*
- `async def test_llm_executor_receives_tools_from_system_prompt(mock_settings)` - *Test that LLM executor receives and passes tools from system prompt.*
- `async def test_llm_executor_config_override_tools(mock_settings)` - *Test that node config can override tools from system prompt.*
- `async def test_llm_executor_none_input_guard()` - *LLMExecutor should handle None input without crashing.*
- `async def test_llm_executor_empty_messages(mock_settings)` - *LLMExecutor should handle empty messages list without crashing.*
- `async def test_get_executor_class_llm()` - *get_executor_class('llm_module') should return LLMExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*

---

### [`tests/test_load_error_protection.py`](tests/test_load_error_protection.py)
> *Test that load_error is never persisted to module.json*

**Size**: `60` lines (`1856` bytes)

---

### [`tests/test_logic_nodes.py`](tests/test_logic_nodes.py)
**Size**: `490` lines (`18582` bytes)

**Functions:**
- `async def test_delay_executor_valid()` - *Test that DelayExecutor waits for the specified time.*
- `async def test_delay_executor_max_delay_cap()` - *Test that DelayExecutor caps delay to MAX_DELAY (1 hour).*
- `async def test_delay_executor_max_delay_warning()` - *Test that DelayExecutor logs warning when delay exceeds MAX_DELAY.*
- `async def test_delay_executor_invalid_config()` - *Test that DelayExecutor handles invalid config gracefully.*
- `async def test_script_executor_modify_data()` - *Test that Python script can modify data.*
- `async def test_script_executor_error_handling()` - *Test that syntax errors in scripts are caught and returned as errors.*
- `async def test_repeater_executor_trigger()` - *Test that RepeaterExecutor schedules a new flow run.*
- `async def test_conditional_router_tool_exists()` - *Test router detects when tool_calls field exists.*
- `async def test_conditional_router_tool_not_exists()` - *Test router blocks flow when tool_calls doesn't exist.*
- `async def test_conditional_router_default_field()` - *Test router uses default field name when not specified.*
- `async def test_conditional_router_custom_field()` - *Test router checks custom field name.*
- `async def test_conditional_router_none_input()` - *Test router returns None for None input.*
- `async def test_schedule_start_executor_past_date_time()` - *Test ScheduleStartExecutor proceeds immediately for past date/time.*
- `async def test_schedule_start_executor_no_config()` - *Test ScheduleStartExecutor returns input when no time configured.*
- `async def test_schedule_start_executor_none_input()` - *Test ScheduleStartExecutor returns None for None input.*
- `async def test_schedule_start_executor_end_of_month()` - *ScheduleStartExecutor should not crash at end of month (timedelta fix).*
- `async def test_schedule_start_executor_long_sleep_warning()` - *ScheduleStartExecutor should warn when wait time exceeds LONG_SLEEP_WARNING_THRESHOLD.*
- `async def test_conditional_router_invert_flag()` - *invert=True should flip the routing condition.*
- `async def test_conditional_router_satisfied_field_true()` - *check_field='satisfied' with satisfied=True should route to true_branches.*
- `async def test_conditional_router_satisfied_field_false()` - *check_field='satisfied' with satisfied=False should route to false_branches.*
- `async def test_conditional_router_requires_continuation()` - *check_field='requires_continuation' should route based on that flag.*
- `async def test_script_executor_non_dict_input()` - *ScriptExecutor should handle non-dict input without crashing.*
- `async def test_script_executor_none_input_returns_none()` - *ScriptExecutor should return None for None input.*
- `async def test_script_executor_empty_code_passthrough()` - *ScriptExecutor with empty code should return input unchanged.*
- `async def test_get_executor_class_all_types()` - *get_executor_class should return correct class for all known node types.*
- `async def test_script_executor_blocks_os_import()` - *ScriptExecutor exposes only the safe os surface, never process APIs.*
- `async def test_script_executor_blocks_subprocess()` - *ScriptExecutor should block import of dangerous 'subprocess' module.*
- `async def test_script_executor_blocks_sys_import()` - *ScriptExecutor should block import of dangerous 'sys' module.*
- `async def test_script_executor_blocks_dunder_import()` - *ScriptExecutor should block __import__ builtin.*
- `async def test_script_executor_blocks_eval()` - *ScriptExecutor should block eval() builtin.*
- `async def test_script_executor_blocks_exec()` - *ScriptExecutor should block exec() builtin.*
- `async def test_script_executor_allows_safe_code()` - *ScriptExecutor should allow safe code to execute.*
- `async def test_script_executor_allows_json_module()` - *ScriptExecutor should allow json module usage.*
- `async def test_script_executor_allows_re_module()` - *ScriptExecutor should allow re module usage.*

---

### [`tests/test_main.py`](tests/test_main.py)
**Size**: `18` lines (`587` bytes)

**Functions:**
- `def client()` - *A TestClient that handles startup and shutdown events.*
- `def test_read_root(client)` - *Tests if the root dashboard page loads correctly.*

---

### [`tests/test_main_routes.py`](tests/test_main_routes.py)
**Size**: `188` lines (`7097` bytes)

**Functions:**
- `def client()` - *A TestClient that provides mock managers to isolate web routes from the file system.*
- `def test_ai_flow_page(client)` - *Tests if the AI Flow page loads correctly (ai_flow_page).*
- `def test_get_flow_data(client)` - *Tests fetching data for an existing and non-existing flow (get_flow_data).*
- `def test_save_ai_flow(client)` - *Tests saving a new AI flow via the form post (save_ai_flow).*
- `def test_save_ai_flow_invalid_json(client)` - *Tests that saving a flow with invalid JSON returns a 400 error.*
- `def test_set_active_flow(client)` - *Tests setting an AI flow as active (set_active_flow).*
- `def test_delete_flow(client)` - *Tests deleting an AI flow, including the active one (delete_flow).*
- `def test_get_module_details(client)` - *Tests getting details for an existing and non-existent module (get_module_details).*
- `def test_enable_module_route(client)` - *Tests enabling a module via the API endpoint.*
- `def test_disable_module_route(client)` - *Tests disabling a module via the API endpoint.*

---

### [`tests/test_marketplace_flow_import.py`](tests/test_marketplace_flow_import.py)
> *Marketplace flow imports go through the validated flow store, not a direct file write.*

**Size**: `101` lines (`3226` bytes)

**Classes:**
- `class FakeSettings`
  - Method `.__init__()`
  - Method `.get(key, default)`
  - Method `.save_settings(values)`

**Functions:**
- `def marketplace(tmp_path, monkeypatch)`
- `def package(state, item_id, flow, filename)`
- `async def test_imported_flow_is_saved_and_visible_without_restart(marketplace)`
- `async def test_invalid_import_changes_nothing(marketplace)`
- `async def test_a_name_of_only_punctuation_still_imports(marketplace)`

---

### [`tests/test_memory_arbiter.py`](tests/test_memory_arbiter.py)
**Size**: `109` lines (`3689` bytes)

**Functions:**
- `def mock_store()`
- `async def test_arbiter_consider_success(mock_store)` - *Test that valid high-confidence memories are passed to the store.*
- `async def test_arbiter_confidence_gate(mock_store)` - *Test that low-confidence memories are rejected.*
- `async def test_arbiter_unknown_type_defaults_to_belief(mock_store)` - *Test that unknown memory types default to BELIEF.*
- `async def test_arbiter_backend_rejection(mock_store)` - *Test handling of backend rejection (e.g. duplicates).*
- `async def test_arbiter_consider_batch(mock_store)` - *Test batch processing of memories.*
- `async def test_arbiter_passes_embedding(mock_store)` - *Test that embedding is passed to the store.*

---

### [`tests/test_memory_backend.py`](tests/test_memory_backend.py)
> *Tests for modules/memory/backend.py - specifically the FAISS index handling improvements.*

**Size**: `313` lines (`12456` bytes)

**Classes:**
- `class TestFaissIndexValidation` - *Tests for FAISS index loading and validation.*
  - Method `.test_load_faiss_index_validates_dimension()` - *_load_faiss_index should reject indexes with invalid dimension.*
  - Method `.test_load_faiss_index_handles_corrupted_file()` - *_load_faiss_index should handle corrupted files gracefully.*
- `class TestFaissIndexSync` - *Tests for FAISS index synchronization.*
  - Method `.test_sync_rebuilds_when_faiss_has_entries_but_db_empty()` - *_sync_faiss_index should rebuild when FAISS has entries but DB is empty.*
  - Method `.test_sync_rebuilds_when_db_has_entries_but_faiss_empty()` - *_sync_faiss_index should rebuild when DB has entries but FAISS is empty.*
  - Method `.test_sync_handles_exception_gracefully()` - *_sync_faiss_index should handle exceptions and rebuild.*
- `class TestWipeAll` - *Tests for wipe_all method including FAISS file deletion.*
  - Method `.test_wipe_all_deletes_faiss_file(mock_remove, mock_exists)` - *wipe_all should delete the FAISS index file.*
  - Method `.test_wipe_all_handles_missing_faiss_file(mock_remove, mock_exists)` - *wipe_all should handle missing FAISS file gracefully.*
- `class TestBuildFaissIndex` - *Tests for _build_faiss_index method.*
  - Method `.test_build_faiss_index_empty_db()` - *_build_faiss_index should handle empty database gracefully.*
  - Method `.test_build_faiss_index_with_memories()` - *_build_faiss_index should build index from existing memories.*
- `class TestSessionProvenance` - *Tests for cross-session memory linking (record_recall, get_memory_sessions, get_session_memories).*
  - Method `.store(tmp_path)` - *MemoryStore backed by a real temp SQLite file, FAISS disabled.*
  - Method `.test_record_recall_inserts_row(store)`
  - Method `.test_record_recall_accumulates_multiple_sessions(store)`
  - Method `.test_get_memory_sessions_empty(store)`
  - Method `.test_get_session_memories_returns_joined_data(store)`
  - Method `.test_get_session_memories_empty(store)`
  - Method `.test_record_recall_multiple_memories_same_session(store)`
  - Method `.test_browse_includes_session_count(store)`

---

### [`tests/test_memory_browser.py`](tests/test_memory_browser.py)
**Size**: `109` lines (`3448` bytes)

**Functions:**
- `def client()`
- `def mock_store()`
- `def test_browser_gui_route(client, mock_store)`
- `def test_browser_list_route(client, mock_store)` - *Test the memory browser list endpoint.*
- `def test_browser_list_date_filter(client, mock_store)` - *Test that date filter is passed correctly.*
- `def test_delete_memory_route(client, mock_store)`
- `def test_delete_memory_error(client, mock_store)` - *Test error handling during deletion.*

---

### [`tests/test_memory_consolidation.py`](tests/test_memory_consolidation.py)
**Size**: `159` lines (`6445` bytes)

**Functions:**
- `def mock_store()`
- `def mock_llm()`
- `async def test_consolidation_logic(mock_store, mock_llm)` - *Test that high similarity + entailment triggers consolidation.*
- `async def test_consolidation_entailment_check(mock_llm)` - *Test the LLM entailment verification logic.*
- `def test_consolidation_state_defaults()`
- `def test_consolidation_state_to_dict()`
- `async def test_run_updates_state_on_success(mock_store, mock_llm)`
- `async def test_run_updates_state_on_error(mock_store, mock_llm)`
- `async def test_run_accumulates_consolidated_count(mock_store, mock_llm)`

---

### [`tests/test_memory_links.py`](tests/test_memory_links.py)
> *Memory evidence links: CRUD, Bayesian confidence and review-gated contradictions.*

**Size**: `364` lines (`14049` bytes)

**Functions:**
- `def store(tmp_path, monkeypatch)`
- `def add(store, text, subject, mem_type, verified)`
- `def test_a_link_round_trips_and_cannot_target_itself(store)`
- `def test_invalid_relations_statuses_and_missing_memories_are_rejected(store)`
- `def test_a_duplicate_link_returns_the_existing_row(store)`
- `def test_links_report_direction_and_neighbour_text(store)`
- `def test_bayesian_confidence_uses_laplace_smoothing(store)`
- `def test_a_proposal_does_not_change_confidence_until_accepted(store)`
- `def test_accept_and_delete_report_missing_rows(store)`
- `def test_deleting_a_link_removes_it_from_both_sides(store)`
- `def test_deterministic_conflicts_need_shared_terms_and_opposite_polarity(store)`
- `def test_conflict_scan_persists_proposals_and_is_idempotent(store)`
- `def test_the_conflict_scan_never_calls_an_llm(store, monkeypatch)`
- `def test_the_link_routes_round_trip(store, monkeypatch)`
- `def test_the_scan_route_returns_new_proposals(store, monkeypatch)`
- `def stored_confidence(store, memory_id)`
- `def test_an_accepted_link_persists_confidence_immediately(store)`
- `def test_deleting_a_link_restores_the_stored_confidence(store)`
- `def test_deleting_an_evidence_memory_removes_its_links_and_confidence(store)`
- `def test_merging_archives_sources_and_recomputes_confidence(store)`
- `def keyword(store, query, **kwargs)`
- `def test_keyword_recall_boosts_accepted_supporting_evidence(store)`
- `def test_link_boost_zero_keeps_the_original_ranking(store)`
- `def test_only_accepted_supporting_or_derived_links_boost(store)`
- `def test_deleted_archived_and_expired_neighbours_do_not_boost(store)`
- `def test_an_existing_neighbour_is_merged_and_keeps_the_higher_score(store)`
- `def test_link_boost_respects_the_limit(store)`
- `def test_contractions_count_as_negation()`

---

### [`tests/test_memory_nodes.py`](tests/test_memory_nodes.py)
**Size**: `440` lines (`17061` bytes)

**Functions:**
- `def mock_store()`
- `def mock_llm_bridge()`
- `async def test_memory_recall_success(mock_store, mock_llm_bridge)` - *Test that relevant memories are injected into the message history.*
- `async def test_memory_recall_no_results(mock_store, mock_llm_bridge)` - *Test that nothing changes if no memories are found.*
- `async def test_memory_save_assistant_content(mock_store, mock_llm_bridge)` - *Test saving content from Chat Output (Assistant).*
- `async def test_memory_save_openai_response(mock_store, mock_llm_bridge)` - *Test saving content from raw OpenAI-style response (LLM Core).*
- `async def test_memory_save_user_message(mock_store, mock_llm_bridge)` - *Test saving content from Chat Input (User).*
- `async def test_memory_save_short_text_ignored(mock_store)` - *Test that short text is ignored.*
- `async def test_memory_recall_min_score_filtering(mock_store, mock_llm_bridge)` - *Test that results below min_score are filtered out.*
- `async def test_memory_save_config_passing(mock_store)` - *Test that config values (type, confidence) are passed to save.*
- `async def test_save_background_logic(mock_store)` - *Test the _save_background method logic.*
- `async def test_save_background_embedding_failure(mock_store)` - *Test that save is aborted if embedding generation fails.*
- `async def test_memory_recall_no_user_message(mock_store, mock_llm_bridge)` - *Test that recall does nothing if no user message is found.*
- `async def test_memory_recall_empty_store_passthrough(mock_llm_bridge)` - *When memory store has no memories, input should be returned unchanged.*
- `async def test_memory_recall_returns_memory_context_field(mock_store, mock_llm_bridge)` - *Recall should return _memory_context field (not inject into messages).*
- `async def test_memory_recall_multimodal_query(mock_store, mock_llm_bridge)` - *Multimodal user message content should be extracted as text for embedding.*
- `async def test_memory_save_delay_configurable(mock_store)` - *save_delay config key should be forwarded to _save_background.*
- `async def test_check_goal_no_goal_passthrough()` - *CheckGoalExecutor should return input unchanged when no goal exists.*
- `async def test_check_goal_injects_goal_into_messages()` - *CheckGoalExecutor should inject a system message with the active goal.*
- `async def test_get_executor_class_dispatcher()` - *get_executor_class should return correct class for all memory node types.*
- `async def test_pending_save_task_registered(mock_store)` - *receive() must register the background task in _pending_save_tasks.*
- `async def test_pending_save_task_removed_on_completion(mock_store)` - *Done callback must remove the task from _pending_save_tasks once it finishes.*
- `async def test_memory_shutdown_awaits_pending_tasks()` - *shutdown() in modules.memory should wait for pending save tasks to complete.*
- `async def test_memory_shutdown_noop_when_no_pending_tasks()` - *shutdown() must be a no-op when there are no pending tasks.*

---

### [`tests/test_memory_regressions.py`](tests/test_memory_regressions.py)
> *Memory regressions: upgrade migrations, consolidation, and FAISS edits.*

**Size**: `207` lines (`7224` bytes)

**Functions:**
- `def test_existing_database_receives_new_schema(tmp_path)`
- `def faiss_store(tmp_path, monkeypatch)`
- `def test_consolidation_maps_faiss_ids_to_memory_ids(faiss_store)`
- `def test_update_entry_adds_the_new_revision_to_faiss(faiss_store)`
- `def make_consolidator(tmp_path)`
- `def test_set_parent_removes_child_from_faiss(faiss_store)`
- `async def test_consolidation_timestamp_does_not_advance_on_failure(tmp_path, monkeypatch)`
- `async def test_consolidation_timestamp_advances_on_success(tmp_path, monkeypatch)`
- `def _seed_ranked_memories(store, now)` - *Four top-ranked ineligible memories ahead of two eligible 'notes' ones.*
- `def test_linear_search_fills_the_page_after_filtering(tmp_path)`
- `def test_faiss_search_fills_the_page_after_filtering(faiss_store)`

---

### [`tests/test_memory_router.py`](tests/test_memory_router.py)
**Size**: `108` lines (`3634` bytes)

**Functions:**
- `def client()`
- `def mock_store()`
- `def test_memory_stats_route(client, mock_store)` - *Test that the stats endpoint renders the correct HTML with data.*
- `def test_memory_stats_empty(client, mock_store)` - *Test the empty state of the stats endpoint.*
- `def test_consolidation_status_never_run(client)` - *GET /memory/consolidation/status returns correct shape when never run.*
- `def test_consolidation_status_after_run(client)` - *GET /memory/consolidation/status reflects updated state.*
- `def test_consolidation_status_with_error(client)` - *GET /memory/consolidation/status reports last_error.*
- `def test_consolidation_status_while_running(client)` - *GET /memory/consolidation/status shows is_running=True while active.*

---

### [`tests/test_messaging_bridge.py`](tests/test_messaging_bridge.py)
> *Tests for modules/messaging_bridge — node executors, service routing, and bridges.*

**Size**: `806` lines (`31984` bytes)

**Classes:**
- `class TestMessagingInputExecutor`
  - Method `.executor()`
  - Method `async def .test_receive_passes_through(executor)`
  - Method `async def .test_receive_blocks_repeater(executor)`
  - Method `async def .test_send_requires_messages_or_text(executor)`
  - Method `async def .test_send_passes_with_messages(executor)`
  - Method `async def .test_send_passes_with_text(executor)`
  - Method `async def .test_send_passes_repeater_through(executor)`
  - Method `async def .test_platform_filter_allows_matching(executor)`
  - Method `async def .test_platform_filter_blocks_non_matching(executor)`
  - Method `async def .test_platform_filter_empty_allows_all(executor)`
  - Method `async def .test_platform_filter_absent_allows_all(executor)`
- `class TestMessagingOutputExecutor`
  - Method `.executor()`
  - Method `async def .test_no_content_passes_through(executor)`
  - Method `async def .test_send_passthrough(executor)`
  - Method `async def .test_auto_platform_telegram(executor)`
  - Method `async def .test_auto_platform_discord(executor)`
  - Method `async def .test_auto_platform_signal(executor)`
  - Method `async def .test_missing_token_skips_send(executor)`
- `class TestTelegramBridge`
  - Method `.bridge()`
  - Method `async def .test_send_message_chunks(bridge)`
  - Method `async def .test_send_empty_returns_false(bridge)`
- `class TestDiscordBridge`
  - Method `.bridge()`
  - Method `async def .test_send_message_chunks(bridge)`
  - Method `async def .test_send_empty_returns_false(bridge)`
  - Method `async def .test_no_channel_returns_false(bridge)`
- `class TestSignalBridge`
  - Method `.bridge()`
  - Method `async def .test_send_message_success(bridge)`
  - Method `async def .test_send_chunks_long_message(bridge)`
  - Method `async def .test_send_missing_config_returns_false(bridge)`
  - Method `async def .test_receive_messages_parses_correctly(bridge)`
  - Method `async def .test_receive_skips_empty_messages(bridge)`
  - Method `async def .test_listen_stops_when_not_running(bridge)`
- `class TestTelegramHandler`
  - Method `.handler()`
  - Method `async def .test_ignores_unknown_type(handler)`
  - Method `async def .test_help_command(handler)`
  - Method `async def .test_new_session_command(handler)`
  - Method `async def .test_runs_flow_on_text(handler)`
  - Method `async def .test_sets_correct_input_source(handler)`
- `class TestDiscordHandler`
  - Method `.handler()`
  - Method `async def .test_ignores_bot_messages(handler)`
  - Method `async def .test_ignores_wrong_channel(handler)`
  - Method `async def .test_help_command(handler)`
  - Method `async def .test_runs_flow_on_text(handler)`
  - Method `async def .test_sets_correct_input_source(handler)`
- `class TestSignalHandler`
  - Method `.handler()`
  - Method `async def .test_ignores_empty_content(handler)`
  - Method `async def .test_help_command(handler)`
  - Method `async def .test_runs_flow_on_text(handler)`
  - Method `async def .test_sets_correct_input_source(handler)`
- `class TestGetExecutorClass`
  - Method `async def .test_messaging_input()`
  - Method `async def .test_messaging_output()`
  - Method `async def .test_unknown_returns_none()`
- `class TestWhatsAppBridge`
  - Method `.bridge()`
  - Method `async def .test_send_message_success(bridge)`
  - Method `async def .test_send_chunks_long_message(bridge)`
  - Method `async def .test_send_missing_config_returns_false(bridge)`
  - Method `async def .test_send_empty_text_returns_false(bridge)`
  - Method `.test_parse_webhook_text_message()`
  - Method `.test_parse_webhook_ignores_outbound()`
  - Method `.test_parse_webhook_ignores_unknown_event()`
  - Method `.test_parse_webhook_ignores_empty_text()`
  - Method `.test_parse_webhook_extended_text()`
  - Method `.test_jid_normalisation()`
- `class TestWhatsAppHandler`
  - Method `.handler()`
  - Method `async def .test_ignores_empty_text(handler)`
  - Method `async def .test_help_command(handler)`
  - Method `async def .test_runs_flow_on_text(handler)`
  - Method `async def .test_sets_correct_input_source(handler)`
  - Method `.test_handle_incoming_webhook_dispatches(handler)`
  - Method `.test_handle_incoming_webhook_ignores_non_message_events(handler)`
- `class TestMessagingPlatformsRegistry`
  - Method `.test_whatsapp_in_registry()`
  - Method `.test_all_platforms_have_id_and_label()`
- `class TestSessionStore`
  - Method `.test_set_get_delete(tmp_path)`

**Functions:**
- `def _make_tg_msg(text, chat_id, msg_type)`
- `def _make_discord_msg(content, channel_id, user_id, username, bot)`
- `def _make_signal_msg(text, sender)`

---

### [`tests/test_meta_flow.py`](tests/test_meta_flow.py)
> *Tests for composing small flows into meta-flows.*

**Size**: `167` lines (`5193` bytes)

**Functions:**
- `def member(evolution, name, prefix)`
- `def manager(tmp_path)`
- `def evolution(manager)`
- `def composer(manager)`
- `def test_compose_links_ganglia_with_a_pathway(manager, evolution, composer)`
- `def test_compose_auto_chains_single_boundary_members(evolution, composer)`
- `def test_compose_allows_the_same_member_twice(evolution, composer)`
- `def test_compose_live_uses_subflow_ganglia(manager, evolution, composer)`
- `def test_compose_live_rejects_a_cyclic_pathway(evolution, composer)`
- `def test_compose_live_rejects_an_unknown_member_pathway(evolution, composer)`
- `def test_compose_rejects_a_missing_member(composer)`
- `def test_compose_rejects_empty_members(composer)`
- `def test_compose_rejects_an_unknown_pathway_node(evolution, composer)`
- `def test_compose_rejects_a_cyclic_pathway(evolution, composer)`

---

### [`tests/test_meta_module.py`](tests/test_meta_module.py)
> *Tests for the meta-flow subflow executor.*

**Size**: `96` lines (`2881` bytes)

**Classes:**
- `class FakeRunner`
  - Method `.__init__(flow_id, flow_manager_override)`
  - Method `async def .run(data, **kwargs)`

**Functions:**
- `async def test_subflow_executes_the_referenced_flow(monkeypatch)`
- `async def test_subflow_uses_the_context_flow_manager(monkeypatch)`
- `async def test_subflow_requires_a_flow_id()`
- `async def test_subflow_detects_a_cycle()`
- `async def test_subflow_enforces_the_depth_limit()`
- `async def test_get_executor_class_dispatch()`
- `async def test_send_is_a_passthrough()`
- `def test_module_manifest_declares_the_subflow_node()`

---

### [`tests/test_module_defaults.py`](tests/test_module_defaults.py)
> *Which modules a fresh install loads: experimental autonomy stays opt-in.*

**Size**: `42` lines (`1282` bytes)

**Functions:**
- `def _enabled(module_id)`
- `def test_experimental_autonomy_modules_ship_disabled()`
- `def test_meta_flow_ships_enabled_because_saved_flows_use_its_nodes()`
- `def test_disabled_brain_is_neither_routed_nor_linked()`

---

### [`tests/test_module_manager.py`](tests/test_module_manager.py)
**Size**: `127` lines (`5518` bytes)

**Functions:**
- `def temp_modules_dir(tmp_path, monkeypatch)` - *Creates a temporary module directory structure for testing.*
- `def mock_app()` - *Provides a mock FastAPI app instance.*
- `def test_module_discovery(temp_modules_dir, mock_app)` - *Tests that the manager correctly discovers modules with metadata.*
- `def test_load_enabled_modules_at_startup(temp_modules_dir, mock_app)` - *Tests that only enabled modules are loaded on initialization.*
- `def test_hot_enable_module(temp_modules_dir, mock_app)` - *Tests that enabling a module dynamically adds its router.*
- `def test_hot_disable_module(temp_modules_dir, mock_app)` - *Tests that disabling a module dynamically removes its router.*
- `def test_update_module_config(temp_modules_dir, mock_app)` - *Tests that updating module configuration writes to the file.*
- `def test_failed_manifest_write_keeps_the_previous_manifest(temp_modules_dir, mock_app)`

---

### [`tests/test_node_retry.py`](tests/test_node_retry.py)
> *Per-node retry policy: parsing, backoff, classification and bounded attempts.*

**Size**: `323` lines (`9603` bytes)

**Functions:**
- `def test_retries_are_off_by_default()`
- `def test_policy_reads_node_config_and_caps_values()`
- `def test_malformed_config_falls_back_instead_of_crashing(bad)`
- `def test_node_timeout_is_bounded_and_opt_in()`
- `def test_transient_failures_are_retryable(exc)`
- `def test_deterministic_failures_and_cancellation_are_not_retried(exc)`
- `async def test_a_transient_failure_is_retried_and_succeeds()`
- `async def test_retries_are_bounded_and_the_last_error_surfaces()`
- `async def test_a_deterministic_error_fails_on_the_first_attempt()`
- `async def test_a_custom_on_retry_reports_the_attempt_and_delay()`
- `def _retry_flow(config, metadata)`
- `async def _run_retry_flow(flow, receive, send)`
- `async def test_a_flow_node_retries_a_transient_failure()`
- `async def test_a_flow_node_does_not_retry_a_deterministic_error()`
- `async def test_a_node_timeout_can_be_retried()`
- `async def test_retry_attempts_are_charged_to_the_run_budget()`
- `async def test_bridge_execution_uses_the_same_policy()`
- `async def test_retry_backoff_is_capped_by_the_remaining_run_time()`
- `async def test_a_typed_llm_timeout_exhausts_into_a_structured_node_error()`

---

### [`tests/test_node_stall.py`](tests/test_node_stall.py)
> *Node latency outlier detection, bounded history and the debug report (ADR-061).*

**Size**: `103` lines (`3349` bytes)

**Functions:**
- `def monitor(**overrides)`
- `def test_no_finding_before_the_minimum_sample_count_or_below_the_floor()`
- `def test_a_duration_above_both_the_floor_and_prior_p99_is_a_stall()`
- `def test_history_is_bounded_per_node_type_and_resettable()`
- `def test_report_exposes_thresholds_nodes_and_findings()`
- `def test_the_debug_route_reports_the_monitor(monkeypatch)`
- `async def test_flow_runner_records_each_attempt_latency(monkeypatch)`

---

### [`tests/test_observability.py`](tests/test_observability.py)
> *Tests for core.observability - Distributed Tracing, Metrics, and Structured Logging*

**Size**: `499` lines (`16972` bytes)

**Classes:**
- `class TestTraceContext` - *Test TraceContext functionality.*
  - Method `.test_create_trace_context()` - *Test creating a new trace context.*
  - Method `.test_add_span()` - *Test adding spans to trace context.*
  - Method `.test_span_parenting()` - *Test span parenting.*
  - Method `.test_end_span()` - *Test ending a span.*
  - Method `.test_span_duration()` - *Test span duration calculation.*
  - Method `.test_get_trace()` - *Test getting trace as list of dicts.*
- `class TestTraceContextManager` - *Test trace context manager.*
  - Method `.test_trace_context_manager()` - *Test using trace context as manager.*
  - Method `.test_trace_context_error_handling()` - *Test error handling in trace context.*
- `class TestTraceContextFunctions` - *Test trace context management functions.*
  - Method `.test_get_or_create_trace_context()` - *Test getting or creating trace context.*
  - Method `.test_trace_id_propagation()` - *Test trace ID propagation.*
- `class TestTracedDecorator` - *Test @traced decorator.*
  - Method `.sync_function()` - *Synchronous function to trace.*
  - Method `async def .async_function()` - *Asynchronous function to trace.*
  - Method `.test_traced_sync_function()` - *Test @traced on sync function.*
  - Method `async def .test_traced_async_function()` - *Test @traced on async function.*
- `class TestMetrics` - *Test Metrics collection.*
  - Method `.setup_method()` - *Reset metrics before each test.*
  - Method `.test_increment_counter()` - *Test incrementing a counter.*
  - Method `.test_increment_with_tags()` - *Test incrementing with tags.*
  - Method `.test_timing()` - *Test timing recording.*
  - Method `.test_timing_percentiles()` - *Test timing percentiles.*
  - Method `.test_gauge()` - *Test gauge setting.*
  - Method `.test_get_all_metrics()` - *Test getting all metrics.*
  - Method `.test_reset()` - *Test resetting metrics.*
  - Method `.test_thread_safety()` - *Test thread safety of metrics.*
- `class TestTimedContextManager` - *Test timed context manager.*
  - Method `.setup_method()`
  - Method `.test_timed_context_manager()` - *Test timed context manager.*
  - Method `.test_timed_decorator_sync()` - *Test @timed decorator on sync function.*
  - Method `async def .test_timed_decorator_async()` - *Test @timed decorator on async function.*
- `class TestStructuredLogger` - *Test StructuredLogger.*
  - Method `.test_structured_logger_creation()` - *Test creating a structured logger.*
  - Method `.test_log_methods()` - *Test logger log methods.*
  - Method `.test_log_flow()` - *Test flow logging.*
  - Method `.test_log_node()` - *Test node logging.*
- `class TestDashboardData` - *Test dashboard data endpoint.*
  - Method `.setup_method()`
  - Method `.test_get_dashboard_data()` - *Test getting dashboard data.*
- `class TestIntegration` - *Integration tests for observability.*
  - Method `.setup_method()`
  - Method `.test_full_trace_flow()` - *Test complete trace flow with metrics.*
  - Method `async def .test_async_trace_flow()` - *Test trace flow in async context.*
- `class TestGetTokenStats` - *Tests for get_token_stats().*
  - Method `.setup_method()`
  - Method `.test_empty_returns_zero_totals()`
  - Method `.test_flat_counters_appear_in_total()`
  - Method `.test_per_model_breakdown()`
  - Method `.test_multiple_models_tracked_independently()`
  - Method `.test_calls_failed_counted()`
- `class TestEnableTraceFileExport` - *Tests for enable_trace_file_export and is_trace_file_export_enabled.*
  - Method `.test_enable_sets_global_flag()`
  - Method `.test_disable_clears_global_flag()`
  - Method `.test_does_not_raise_when_settings_unavailable()` - *enable_trace_file_export must not raise regardless of settings state.*
- `class TestTraceCtxSharedBetweenSyncAndAsync` - *Verify trace_async uses the same ContextVar as the sync trace() context manager.*
  - Method `.test_trace_async_reuses_sync_context()`

---

### [`tests/test_packaging.py`](tests/test_packaging.py)
> *Packaging contract: pyproject.toml is the source of truth for every requirements file.*

**Size**: `48` lines (`1558` bytes)

**Functions:**
- `def _name(requirement)`
- `def _requirement_names(path)`
- `def _project()`
- `def test_requirements_txt_is_runtime_plus_optional_capabilities()`
- `def test_requirements_dev_adds_exactly_the_dev_group()`
- `def test_built_in_route_dependencies_are_core_dependencies()`

---

### [`tests/test_plan_step_tracker.py`](tests/test_plan_step_tracker.py)
> *Tests for modules/planner/node.py — PlanStepTracker*

**Size**: `326` lines (`10912` bytes)

**Functions:**
- `def executor()`
- `async def test_tracks_single_step_completion(executor)` - *Should increment current_step and mark step as completed.*
- `async def test_tracks_multi_step_progress(executor)` - *Should track progress through multiple steps.*
- `async def test_resumes_from_middle_of_plan(executor)` - *Should work correctly when starting from non-zero current_step.*
- `async def test_empty_plan_passthrough(executor)` - *Should return data unchanged when plan is empty.*
- `async def test_no_current_step_defaults_to_zero(executor)` - *Should default current_step to 0 if not provided.*
- `async def test_non_dict_input_passthrough(executor)` - *Should return non-dict input unchanged.*
- `async def test_preserves_other_data_fields(executor)` - *Should preserve all other fields in input data.*
- `async def test_send_returns_data_unchanged(executor)` - *send() should return data unchanged.*
- `async def test_get_executor_class_returns_tracker()` - *get_executor_class('plan_step_tracker') should return PlanStepTracker.*
- `async def test_plan_complete_only_when_all_steps_done(executor)` - *plan_complete should only be True when all steps are finished.*
- `async def test_step_completed_field_set_correctly(executor)` - *step_completed should always reflect the step that was just completed.*
- `async def test_dependency_aware_ordering_simple(executor)` - *Should respect simple dependencies (step 2 depends on step 1).*
- `async def test_dependency_aware_ordering_out_of_order(executor)` - *Should execute step 3 before step 2 if step 2 depends on step 3.*
- `async def test_circular_dependency_detection(executor)` - *Should detect and report circular dependencies.*
- `async def test_multiple_dependencies(executor)` - *Should handle steps with multiple dependencies.*
- `async def test_completed_steps_tracking(executor)` - *Should track completed_steps across multiple calls.*
- `async def test_next_step_field_set_correctly(executor)` - *next_step should indicate the step to execute next.*
- `async def test_dependency_with_nonexistent_step(executor)` - *Should handle dependencies on non-existent steps gracefully.*
- `async def test_complex_dependency_chain(executor)` - *Should handle complex dependency chains correctly.*

---

### [`tests/test_planner_node.py`](tests/test_planner_node.py)
> *Tests for modules/planner/node.py — PlannerExecutor*

**Size**: `566` lines (`20481` bytes)

**Functions:**
- `def make_executor()` - *Create a PlannerExecutor with LLMBridge patched out.*
- `def make_llm_response(content)`
- `def make_input(user_msg)`
- `async def test_none_input_returns_empty_dict()` - *None input should be treated as empty dict and returned with plan keys.*
- `async def test_no_user_message_passthrough()` - *Input with no user message should be returned unchanged.*
- `async def test_enabled_false_passthrough()` - *When enabled=False in config, input should be returned unchanged.*
- `async def test_valid_json_list_plan_parsed()` - *A valid JSON list response should be parsed into plan steps.*
- `async def test_dict_wrapped_plan_parsed()` - *A plan wrapped in a dict ({"plan": [...]}) should also be parsed.*
- `async def test_plan_context_formatted()` - *plan_context should be a formatted string of steps.*
- `async def test_llm_failure_returns_empty_plan_with_error()` - *When LLM raises an exception, plan should be empty and planning_error set.*
- `async def test_malformed_json_returns_empty_plan()` - *Malformed JSON in LLM response should result in empty plan (no crash).*
- `async def test_max_steps_respected()` - *Plan should be truncated to max_steps.*
- `async def test_input_data_preserved_in_output()` - *Original input_data keys should be preserved in the output.*
- `async def test_get_executor_class_dispatcher()` - *get_executor_class('planner') should return PlannerExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*
- `async def test_max_tokens_calculated_dynamically()` - *max_tokens should be calculated based on max_steps.*
- `async def test_max_tokens_minimum_enforced()` - *max_tokens should never go below minimum (500).*
- `async def test_plan_context_shows_current_step()` - *plan_context should indicate which step is current.*
- `async def test_plan_context_with_depends_on()` - *plan_context should preserve depends_on field in plan.*
- `async def test_plan_context_empty_plan()` - *plan_context should not be set for empty plans.*
- `async def test_auto_track_enabled_runs_progression()` - *When auto_track=true and plan exists, should run progression tick.*
- `async def test_auto_track_disabled_passthrough()` - *When auto_track=false (default), should create new plan from LLM.*
- `async def test_auto_track_no_plan_creates_plan()` - *When auto_track=true but no plan exists, should create new plan.*
- `async def test_auto_track_plan_complete()` - *When auto_track progresses to end of plan, should set plan_complete=True.*
- `async def test_auto_track_respects_dependencies()` - *Auto_track should respect dependencies like PlanStepTracker.*
- `async def test_auto_track_detects_circular_dependencies()` - *Auto_track should detect circular dependencies.*
- `async def test_auto_track_multiple_steps()` - *Auto_track should work correctly through multiple progression steps.*
- `async def test_auto_track_plan_context_shows_completed()` - *Auto_track should update plan_context to show completed steps.*
- `async def test_auto_track_matches_tracker_behavior()` - *Auto_track behavior should match PlanStepTracker for same inputs.*
- `async def test_planner_uses_config_model_override()` - *PlannerExecutor must pass config['model'] to the LLM, not always default_model.*
- `def test_get_executable_steps_hoist_step_num_to_idx()` - *_get_executable_steps must produce correct results regardless of plan size.*
- `def test_get_executable_steps_parallel_when_no_deps()` - *Steps with no dependencies are all executable simultaneously.*

---

### [`tests/test_reasoning_book.py`](tests/test_reasoning_book.py)
**Size**: `150` lines (`4615` bytes)

**Functions:**
- `def temp_reasoning_file()` - *Create a temporary reasoning file for testing.*
- `def reasoning_service(temp_reasoning_file)` - *Create a ReasoningBookService with temporary storage.*
- `async def test_log_thought(reasoning_service)` - *Test logging a thought.*
- `async def test_log_thought_default_source(reasoning_service)` - *Test logging with default source.*
- `def test_get_thoughts_empty(reasoning_service)` - *Test getting thoughts when none exist.*
- `async def test_get_thoughts_multiple(reasoning_service)` - *Test getting multiple thoughts.*
- `async def test_thoughts_ordered_newest_first(reasoning_service)` - *Test that thoughts are ordered newest first.*
- `async def test_clear_thoughts(reasoning_service)` - *Test clearing all thoughts.*
- `async def test_thought_has_timestamp(reasoning_service)` - *Test that thoughts have timestamps.*
- `async def test_max_thoughts_limit(reasoning_service)` - *Test that thought count is limited to MAX_THOUGHTS.*
- `async def test_thought_persistence(reasoning_service, temp_reasoning_file)` - *Test that thoughts persist to file.*
- `async def test_load_nonexistent_file(reasoning_service)` - *Test loading when file doesn't exist.*
- `async def test_multiple_sources(reasoning_service)` - *Test logging thoughts from different sources.*

---

### [`tests/test_reasoning_book_node.py`](tests/test_reasoning_book_node.py)
> *Tests for modules/reasoning_book/node.py*

**Size**: `414` lines (`18756` bytes)

**Classes:**
- `class TestReasoningSaveExecutorDefaultField` - *ReasoningSaveExecutor reads 'content' by default (matches agent_loop output).*
  - Method `async def .test_saves_from_content_field_by_default()` - *Should save from 'content' key when no source_field config is set.*
  - Method `async def .test_returns_data_unchanged()` - *receive() should always return the original data dict unchanged.*
  - Method `async def .test_skips_when_content_field_missing_and_no_fallback()` - *Should skip saving when 'content' is absent and no fallback fields exist.*
  - Method `async def .test_skips_when_content_too_short()` - *Should skip saving when content is shorter than MIN_CONTENT_LENGTH (30).*
  - Method `async def .test_skips_on_error_key_in_data()` - *Should skip saving when data contains an 'error' key.*
  - Method `async def .test_no_vague_pattern_filter()` - *Content that would have been blocked by the old vague-pattern filter should now be saved.*
  - Method `async def .test_exact_min_length_boundary_saved()` - *Content of exactly MIN_CONTENT_LENGTH (30) chars should be saved.*
  - Method `async def .test_one_below_min_length_skipped()` - *Content of 29 chars (one below MIN_CONTENT_LENGTH) should be skipped.*
- `class TestReasoningSaveExecutorFallbackFields` - *ReasoningSaveExecutor falls back to reasoning/thought/summary/conclusion/result.*
  - Method `async def .test_fallback_to_reasoning_field()` - *Should fall back to 'reasoning' when 'content' is absent.*
  - Method `async def .test_fallback_to_thought_field()` - *Should fall back to 'thought' when 'content' and 'reasoning' are absent.*
  - Method `async def .test_fallback_to_summary_field()` - *Should fall back to 'summary' when earlier fields are absent.*
  - Method `async def .test_fallback_to_conclusion_field()` - *Should fall back to 'conclusion'.*
  - Method `async def .test_fallback_to_result_field()` - *Should fall back to 'result'.*
  - Method `async def .test_content_takes_priority_over_fallback()` - *'content' should be used even when fallback fields are also present.*
- `class TestReasoningSaveExecutorConfig` - *ReasoningSaveExecutor respects source_field and source config keys.*
  - Method `async def .test_custom_source_field_config()` - *source_field config should override the default 'content' field.*
  - Method `async def .test_custom_source_label()` - *source config should set the source label passed to log_thought.*
  - Method `async def .test_custom_source_field_missing_falls_back()` - *If the configured source_field is absent, should fall back to reasoning/thought/etc.*
  - Method `async def .test_none_config_uses_defaults()` - *Passing config=None should use all defaults without error.*
- `class TestReasoningSaveExecutorSend` - *ReasoningSaveExecutor.send() passthrough.*
  - Method `async def .test_send_returns_data_unchanged()`
  - Method `async def .test_send_none_returns_none()`
- `class TestReasoningLoadExecutor` - *ReasoningLoadExecutor injects reasoning_history and reasoning_context.*
  - Method `async def .test_injects_reasoning_history_list()` - *reasoning_history should be a list of content strings.*
  - Method `async def .test_injects_reasoning_context_string()` - *reasoning_context should be a formatted string in chronological order.*
  - Method `async def .test_last_n_limits_thoughts()` - *last_n config should limit the number of thoughts loaded.*
  - Method `async def .test_default_last_n_is_5()` - *Default last_n should be 5.*
  - Method `async def .test_empty_thoughts_injects_empty_structures()` - *With no thoughts, reasoning_history should be [] and reasoning_context ''.*
  - Method `async def .test_original_keys_preserved()` - *All original input keys should be preserved in the output.*
  - Method `async def .test_non_dict_input_returned_unchanged()` - *Non-dict input should be returned as-is without modification.*
  - Method `async def .test_context_timestamp_format()` - *reasoning_context entries should include the timestamp in brackets.*
  - Method `async def .test_send_returns_data_unchanged()`
- `class TestGetExecutorClass` - *Tests for the get_executor_class module-level dispatcher.*
  - Method `async def .test_returns_save_executor_for_reasoning_save()`
  - Method `async def .test_returns_load_executor_for_reasoning_load()`
  - Method `async def .test_returns_none_for_unknown_id()`
  - Method `async def .test_save_executor_is_instantiable()`
  - Method `async def .test_load_executor_is_instantiable()`

**Functions:**
- `def make_thought(content, timestamp, source)`

---

### [`tests/test_reasoning_book_service.py`](tests/test_reasoning_book_service.py)
**Size**: `216` lines (`7054` bytes)

**Functions:**
- `def temp_data_file()`
- `def service(temp_data_file)`
- `async def test_log_thought(service)`
- `async def test_log_thought_default_source(service)`
- `async def test_get_thoughts(service)`
- `async def test_clear(service)`
- `async def test_max_thoughts_limit(service)`
- `async def test_log_thought_with_structured_fields(service)` - *Test logging with all optional structured fields.*
- `async def test_get_thought_by_id(service)` - *Test retrieving a specific thought by ID.*
- `async def test_get_thoughts_by_step(service)` - *Test retrieving thoughts by step_id.*
- `async def test_get_thoughts_by_tag(service)` - *Test retrieving thoughts by tag.*
- `async def test_get_thoughts_by_session(service)` - *Test retrieving thoughts by session_id.*
- `async def test_reload(service, temp_data_file)` - *Test reloading thoughts from disk.*
- `async def test_concurrent_writes(service)` - *Test that concurrent writes are handled safely with locking.*
- `async def test_timestamp_iso_format(service)` - *Verify timestamps are in proper ISO 8601 format with date and time.*

---

### [`tests/test_reflection_node.py`](tests/test_reflection_node.py)
> *Tests for modules/reflection/node.py*

**Size**: `792` lines (`30636` bytes)

**Classes:**
- `class TestReflectionBasic` - *Core satisfied / not-satisfied behaviour.*
  - Method `async def .test_satisfied_response_sets_satisfied_true()` - *When LLM says satisfied=True, output satisfied should be True.*
  - Method `async def .test_not_satisfied_sets_satisfied_false()` - *When LLM says satisfied=False, output satisfied should be False.*
  - Method `async def .test_no_messages_defaults_to_satisfied()` - *When there are no messages, reflection should default to satisfied=True.*
  - Method `async def .test_no_content_no_user_message_defaults_to_satisfied()` - *When there is no user message, reflection should default to satisfied=True.*
  - Method `async def .test_none_input_defaults_to_satisfied()` - *None input should be treated as empty dict and default to satisfied=True.*
  - Method `async def .test_markdown_wrapped_json_parsing()` - *When LLM response is wrapped in ```json ... ```, it should still parse correctly.*
  - Method `async def .test_reflection_key_always_present()` - *Output must always contain a 'reflection' dict.*
  - Method `async def .test_satisfied_key_present_at_top_level()` - *satisfied must be present at the top level for ConditionalRouter routing.*
  - Method `async def .test_default_prompt_includes_clarifying_question_instruction()` - *The default reflection prompt should instruct the LLM to allow clarifying questions.*
- `class TestImprovementInjection` - *Tests for improvement message injection into messages.*
  - Method `async def .test_not_satisfied_injects_improvement_message()` - *When not satisfied and needs_improvement is set, a system message should be appended.*
  - Method `async def .test_satisfied_does_not_inject_message()` - *When satisfied, no improvement message should be appended.*
  - Method `async def .test_inject_improvement_false_suppresses_injection()` - *inject_improvement=False should prevent message injection even when not satisfied.*
  - Method `async def .test_no_injection_when_needs_improvement_is_none()` - *When needs_improvement is None, no message should be injected even if not satisfied.*
  - Method `async def .test_injected_message_appended_at_end()` - *The improvement message should be the last message in the list.*
  - Method `async def .test_original_messages_not_mutated()` - *The original input messages list must not be mutated.*
  - Method `async def .test_injected_message_uses_system_role_not_user()` - *Improvement feedback should use system role, not user role.*
- `class TestReflectionRetryDepth` - *Tests for reflection retry depth counter and max_reflection_retries.*
  - Method `async def .test_reflection_retry_count_increments_on_retry()` - *When not satisfied, reflection_retry_count should increment.*
  - Method `async def .test_max_reflection_retries_enforced()` - *When reflection_retry_count >= max_reflection_retries, force satisfied=True.*
  - Method `async def .test_max_reflection_retries_default_is_3()` - *Default max_reflection_retries should be 3.*
  - Method `async def .test_custom_max_reflection_retries()` - *Custom max_reflection_retries config should be respected.*
  - Method `async def .test_below_max_retries_allows_retry()` - *When below max retries, should allow normal reflection behavior.*
  - Method `async def .test_reflection_retry_count_zero_by_default()` - *reflection_retry_count should default to 0 if not present.*
- `class TestReflectionErrorHandling` - *Tests for LLM failure and malformed response handling.*
  - Method `async def .test_llm_exception_defaults_to_satisfied()` - *An exception from the LLM should default to satisfied=True (fail-safe).*
  - Method `async def .test_malformed_json_response_uses_default()` - *Malformed JSON from LLM should fall back to default reflection_result.*
  - Method `async def .test_none_llm_response_uses_default()` - *None response from LLM should fall back to default reflection_result.*
  - Method `async def .test_empty_choices_falls_back_to_satisfied()` - *Empty choices list causes IndexError caught by exception handler → satisfied=True.*
  - Method `async def .test_js_comment_stripped_before_json_parse()` - *JavaScript-style comments in LLM response should be stripped before parsing.*
- `class TestInputDataPreservation` - *Tests that input_data keys are preserved in the output.*
  - Method `async def .test_original_keys_preserved()` - *All original input_data keys should be present in the output.*
  - Method `async def .test_content_key_preserved()` - *The 'content' key from agent_loop output should be preserved.*
  - Method `async def .test_messages_key_preserved_when_satisfied()` - *The 'messages' list should be preserved unchanged when satisfied.*
- `class TestInjectImprovementMessageDirect` - *Direct unit tests for the _inject_improvement_message helper.*
  - Method `.test_appends_system_message()` - *Should append a system message to the messages list.*
  - Method `.test_does_not_mutate_original()` - *Should not mutate the original input_data dict.*
  - Method `.test_preserves_other_keys()` - *Other keys in input_data should be preserved.*
  - Method `.test_message_contains_needs_improvement_text()` - *The injected message content should include the needs_improvement text.*
  - Method `.test_message_includes_original_user_request()` - *The injected message should include the original user request for context.*
  - Method `.test_uses_system_role_not_user()` - *The injected message should use system role, not user role.*
  - Method `.test_long_improvement_message_is_truncated()` - *A needs_improvement string longer than 2000 chars must be truncated.*
  - Method `.test_short_improvement_message_is_not_truncated()` - *A needs_improvement string within the limit must appear verbatim.*
- `class TestReflectionSend` - *Tests for the send() passthrough method.*
  - Method `async def .test_send_returns_data_unchanged()` - *send() must return the data unchanged.*
  - Method `async def .test_send_none_returns_none()` - *send() with None should return None.*
- `class TestReflectionGetExecutorClass` - *Tests for the get_executor_class module-level dispatcher.*
  - Method `async def .test_returns_reflection_executor_for_correct_id()` - *get_executor_class('reflection') should return ReflectionExecutor.*
  - Method `async def .test_returns_none_for_unknown_id()` - *get_executor_class with an unknown id should return None.*
  - Method `async def .test_executor_class_is_instantiable()` - *The returned class should be instantiable without arguments.*

**Functions:**
- `def make_reflection_llm_response(satisfied, reason, needs_improvement)` - *Build a minimal LLM response that ReflectionExecutor can parse.*
- `def make_executor(mock_bridge)` - *Create a ReflectionExecutor with a mocked LLMBridge.*
- `def make_input(user_msg, assistant_msg, extra)` - *Build a minimal input_data dict with messages and content.*
- `async def test_reflection_uses_config_model_override()` - *ReflectionExecutor must pass config['model'] to the LLM, not always default_model.*

---

### [`tests/test_research.py`](tests/test_research.py)
> *Tests for ResearchManager CRUD operations.*

**Size**: `247` lines (`9568` bytes)

**Classes:**
- `class TestHypotheses`
  - Method `.test_create_and_get(rm)`
  - Method `.test_list_all(rm)`
  - Method `.test_filter_by_status(rm)`
  - Method `.test_filter_by_domain(rm)`
  - Method `.test_update(rm)`
  - Method `.test_update_nonexistent(rm)`
  - Method `.test_delete(rm)`
  - Method `.test_delete_nonexistent(rm)`
  - Method `.test_variables_serialized(rm)`
- `class TestArticles`
  - Method `.test_create_and_get(rm)`
  - Method `.test_list_and_filter(rm)`
  - Method `.test_delete(rm)`
  - Method `.test_delete_nonexistent(rm)`
- `class TestStudyDesigns`
  - Method `.test_create_and_get(rm)`
  - Method `.test_list_and_filter_status(rm)`
  - Method `.test_update_status(rm)`
  - Method `.test_delete(rm)`
  - Method `.test_booleans_roundtrip(rm)`
- `class TestFindings`
  - Method `.test_create_and_get(rm)`
  - Method `.test_list_all(rm)`
  - Method `.test_update_status(rm)`
  - Method `.test_delete(rm)`
  - Method `.test_none_numeric_fields(rm)`
- `class TestStats`
  - Method `.test_stats_empty(rm)`
  - Method `.test_stats_populated(rm)`

**Functions:**
- `def rm(tmp_path)` - *Fresh ResearchManager backed by a temp SQLite file.*

---

### [`tests/test_research_gateway.py`](tests/test_research_gateway.py)
> *Research gateway: allowlist, SSRF policy, parsers, cache and provenance.*

**Size**: `299` lines (`11103` bytes)

**Functions:**
- `def public_resolver(host, port, **kwargs)`
- `def private_resolver(host, port, **kwargs)`
- `def test_the_source_list_is_fixed_and_explains_its_licence()`
- `def test_request_urls_are_built_from_the_allowlisted_endpoint()`
- `def test_urls_must_be_https_allowlisted_and_public(monkeypatch)`
- `def test_private_and_special_addresses_are_rejected(address)`
- `def test_wikipedia_results_are_parsed_and_markup_removed()`
- `def test_arxiv_results_are_parsed_from_atom()`
- `def test_pubmed_results_carry_a_doi_and_a_stable_url()`
- `def use_tmp_cache(monkeypatch, tmp_path)`
- `def test_a_fresh_cache_hit_skips_the_network(monkeypatch, tmp_path)`
- `def test_an_expired_cache_serves_stale_results_with_a_warning(monkeypatch, tmp_path)`
- `def test_an_unavailable_source_without_cache_raises(monkeypatch, tmp_path)`
- `def test_bad_input_is_rejected_before_any_request(monkeypatch, tmp_path)`
- `def test_persist_stores_articles_with_provenance(monkeypatch, tmp_path)`
- `def test_the_routes_list_sources_and_map_errors(monkeypatch)`
- `def test_persist_is_honoured_on_a_cache_hit_without_duplicates(monkeypatch, tmp_path)`
- `def test_the_cache_is_bounded_and_evicts_the_oldest(monkeypatch, tmp_path)`

---

### [`tests/test_runtime_paths.py`](tests/test_runtime_paths.py)
> *Runtime state must stay inside the project, whatever the launch directory is.*

**Size**: `121` lines (`4594` bytes)

**Functions:**
- `def test_runtime_paths_are_absolute_and_inside_project()`
- `def test_importing_the_app_from_another_directory_writes_nothing_there(tmp_path)`
- `def test_data_path_is_the_state_dir_when_set_else_project_data(monkeypatch, tmp_path)`
- `def test_every_data_file_follows_the_state_dir()`
- `def test_legacy_nested_hardware_id_is_adopted(monkeypatch, tmp_path)`
- `def test_recent_sessions_reads_the_dashboard_state_file(monkeypatch, tmp_path)`

---

### [`tests/test_runtime_regressions.py`](tests/test_runtime_regressions.py)
> *Runtime regressions: state directory, raise_errors, debug trigger, hardware id.*

**Size**: `150` lines (`4416` bytes)

**Classes:**
- `class BoomExecutor`
  - Method `async def .receive(input_data, config)`
  - Method `async def .send(processed_data)`
- `class _CapturedTasks`
  - Method `.__init__()`
  - Method `.add_task(fn, *args, **kwargs)`

**Functions:**
- `def test_state_path_honours_state_directory(monkeypatch, tmp_path)`
- `async def test_flow_runner_raise_errors_reraises_node_failure(monkeypatch)`
- `async def test_debug_fire_flow_fires_every_active_flow(monkeypatch)`
- `def test_hardware_id_differs_when_os_probes_are_empty(monkeypatch, tmp_path)`
- `def test_run_node_route_executes_through_the_flow_runner(monkeypatch)`

---

### [`tests/test_sandbox_escape.py`](tests/test_sandbox_escape.py)
> *Regression tests for M3: sandbox escape paths.*

**Size**: `75` lines (`2509` bytes)

**Functions:**
- `def test_bare_modules_package_is_not_passthrough()`
- `def test_sandbox_module_cannot_be_imported()`
- `def test_allowlisted_first_party_module_imports()`
- `def test_calendar_passthrough_still_works_in_sandbox()`
- `def test_importing_sandbox_at_runtime_is_refused()`
- `def test_dunder_class_attribute_is_blocked_statically()`
- `def test_dunder_traversal_is_blocked_at_runtime()`
- `def test_getattr_dunder_is_blocked()`
- `def test_subscript_dunder_is_blocked()`
- `def test_double_underscore_name_attribute_still_allowed()` - *``__name__`` is not an escape; tools rely on it (e.g. type(x).__name__).*

---

### [`tests/test_sandbox_security.py`](tests/test_sandbox_security.py)
> *Tests for sandbox security - attempting known bypass techniques.*

**Size**: `92` lines (`3426` bytes)

**Classes:**
- `class TestSandboxBypassAttempts` - *Test known sandbox bypass techniques are blocked.*
  - Method `.test_block_import_os()` - *Test that importing 'os' module is blocked.*
  - Method `.test_block_import_sys()` - *Test that importing 'sys' module is blocked.*
  - Method `.test_block_import_subprocess()` - *Test that importing 'subprocess' module is blocked.*
  - Method `.test_block_import_socket()` - *Test that importing 'socket' module is blocked.*
  - Method `.test_block_eval()` - *Test that eval() is blocked.*
  - Method `.test_block_exec()` - *Test that exec() is blocked.*
  - Method `.test_block_compile()` - *Test that compile() is blocked.*
  - Method `.test_block_getattr_builtins()` - *Test that getattr on builtins is blocked.*
  - Method `.test_block_path_traversal()` - *Test that path traversal attempts are blocked.*
  - Method `.test_block_dunder_import()` - *Test that __import__ is blocked.*
  - Method `.test_safe_code_executes()` - *Test that safe code executes correctly.*
  - Method `.test_safe_math_executes()` - *Test that safe math operations work.*
  - Method `.test_safe_json_executes()` - *Test that safe JSON operations work.*

---

### [`tests/test_secret_redaction.py`](tests/test_secret_redaction.py)
> *Regression tests for M4: secrets must never reach an export or a log in plaintext.*

**Size**: `61` lines (`2340` bytes)

**Functions:**
- `def test_redact_secrets_masks_flat_and_nested()`
- `def test_redact_secrets_keeps_empty_values()`
- `def test_strip_secrets_drops_sensitive_keys()`
- `async def test_export_config_strips_secrets_by_default(monkeypatch)`
- `async def test_export_config_includes_secrets_when_opted_in(monkeypatch)`
- `def test_debug_logger_redacts_secrets(caplog)`

---

### [`tests/test_security_binding.py`](tests/test_security_binding.py)
> *Regression tests for the M1 control-plane binding policy and trusted-host guard.*

**Size**: `76` lines (`2693` bytes)

**Functions:**
- `def _guarded_client()`
- `def test_bind_defaults_to_loopback(monkeypatch)`
- `def test_bind_host_opt_in(monkeypatch)`
- `def test_bind_port_default_and_invalid(monkeypatch)`
- `def test_reload_is_off_by_default(monkeypatch)`
- `def test_allowed_hosts_are_loopback_by_default(monkeypatch)`
- `def test_trusted_host_guard_rejects_unknown_host(monkeypatch)`
- `def test_trusted_host_guard_accepts_opt_in_host(monkeypatch)`
- `def test_main_app_carries_the_host_guard()` - *The real application must be constructed with the guard installed.*

---

### [`tests/test_security_middleware.py`](tests/test_security_middleware.py)
> *Regression tests for auth (fail-closed API token) and CSRF (browser-origin) enforcement.*

**Size**: `200` lines (`8210` bytes)

**Classes:**
- `class TestDeploymentValidation`
  - Method `.test_loopback_bind_needs_no_token(monkeypatch)`
  - Method `.test_remote_bind_without_a_token_is_refused(monkeypatch, host)`
  - Method `.test_remote_bind_with_a_token_is_allowed(monkeypatch)`
  - Method `.test_remote_bind_can_be_explicitly_unauthenticated(monkeypatch)`
- `class TestOriginComparison`
  - Method `._request(url)`
  - Method `.test_same_origin_is_allowed()`
  - Method `.test_same_host_different_port_is_rejected()`
  - Method `.test_same_host_different_scheme_is_rejected()`
  - Method `.test_explicit_default_port_matches()`
  - Method `.test_loopback_alias_is_allowed_on_the_same_port(monkeypatch)`
  - Method `.test_trusted_origin_matches_the_normalized_origin(monkeypatch)`
  - Method `.test_trusted_origin_entries_are_normalized(monkeypatch)`
  - Method `.test_malformed_origin_is_rejected()`
- `class TestTestClientHooksAreTestOnly`
  - Method `._request(host)`
  - Method `.test_client_names_are_not_loopback_outside_test_mode(monkeypatch)`
  - Method `.test_client_names_are_loopback_in_test_mode(monkeypatch)`

**Functions:**
- `def _guarded_client()`
- `def test_csrf_blocks_cross_origin_write(monkeypatch)`
- `def test_csrf_allows_same_origin_write(monkeypatch)`
- `def test_csrf_allows_trusted_origin(monkeypatch)`
- `def test_get_is_not_csrf_checked(monkeypatch)`
- `def test_api_token_required_for_non_loopback_when_set(monkeypatch)`
- `def test_remote_client_without_a_token_is_rejected_even_when_unset(monkeypatch)`
- `def test_remote_client_without_a_token_needs_the_explicit_opt_out(monkeypatch)`
- `def test_loopback_client_is_exempt_from_token()`

---

### [`tests/test_security_regressions.py`](tests/test_security_regressions.py)
> *Security regressions: HTML escaping, sandbox escapes, isolation and network policy.*

**Size**: `204` lines (`7379` bytes)

**Functions:**
- `def test_shared_escape_helper_escapes_html_entities()`
- `def test_marketplace_pages_use_the_shared_escape_helper()`
- `def test_reflective_sandbox_bypasses_are_rejected(code)`
- `def fake_dns(monkeypatch)` - *Resolve hostnames from a table instead of the network.*
- `def test_sandbox_worker_start_failure_never_runs_in_process(monkeypatch)`
- `def test_empty_allowlist_disables_network(httpx_mock)`
- `def test_mixed_public_and_private_dns_answer_is_rejected(fake_dns)`
- `def test_redirect_to_an_unlisted_host_is_rejected(fake_dns, httpx_mock)`
- `def test_redirect_to_a_private_address_is_rejected(fake_dns, httpx_mock)`
- `def test_oversized_streamed_response_is_rejected(fake_dns, httpx_mock)`
- `def test_cross_host_redirect_drops_credentials(fake_dns, httpx_mock)`
- `def test_tools_cannot_reach_the_control_plane(fake_dns, httpx_mock, monkeypatch, url)`
- `def test_tools_keep_their_internal_and_local_llm_endpoints(fake_dns, httpx_mock, monkeypatch, url)`
- `def test_allowlisted_name_resolving_to_loopback_is_rejected(fake_dns)`

---

### [`tests/test_session_manager.py`](tests/test_session_manager.py)
> *Tests for Session Manager*

**Size**: `582` lines (`19248` bytes)

**Classes:**
- `class TestSessionManager` - *Test cases for SessionManager.*
  - Method `.setup_and_teardown()` - *Setup and teardown for each test.*
  - Method `._cleanup_test_files()` - *Remove test files.*
  - Method `.test_session_creation()` - *Test creating a new session.*
  - Method `.test_session_persistence()` - *Test that session persists to disk.*
  - Method `.test_trace_writing()` - *Test append-only trace file writing.*
  - Method `.test_trace_reading()` - *Test reading trace events.*
  - Method `.test_tick_increment()` - *Test tick incrementing.*
  - Method `.test_llm_call_logging()` - *Test LLM call logging.*
  - Method `.test_agent_event_logging()` - *Test general agent event logging.*
  - Method `.test_rlm_event_logging()` - *Test RLM-specific event logging.*
  - Method `.test_tool_error_logging()` - *Test logging tool errors.*
  - Method `.test_trace_context_manager()` - *Test trace_context context manager.*
  - Method `.test_reset_session()` - *Test resetting session.*
  - Method `.test_multiple_sessions_isolation()` - *Test that multiple manager instances don't interfere.*
- `class TestTraceWriter` - *Test cases for TraceWriter.*
  - Method `.setup_and_teardown()` - *Setup and teardown for each test.*
  - Method `._cleanup_test_files()` - *Remove test files.*
  - Method `.test_append()` - *Test appending events.*
  - Method `.test_read_since()` - *Only events strictly after the cutoff are returned.*
  - Method `.test_clear()` - *Test clearing trace.*
- `class TestGetSessionManager` - *Test the get_session_manager factory function.*
  - Method `.setup_and_teardown()` - *Setup and teardown for each test.*
  - Method `.test_singleton()` - *Test that get_session_manager returns singleton.*
  - Method `.test_convenience_singleton()` - *Test that session_manager convenience singleton is available.*
  - Method `.test_get_trace_summary_empty()` - *Test get_trace_summary with no events.*
  - Method `.test_get_trace_summary_with_events()` - *Test get_trace_summary with various events.*
  - Method `.test_get_trace_summary_limit_zero()` - *Test get_trace_summary with limit=0.*
- `class TestTraceWriterMalformedLines` - *Regression tests for malformed-line handling in TraceWriter.read_all().*
  - Method `.test_valid_lines_returned_when_mixed_with_invalid(tmp_path)` - *read_all() must skip corrupt lines and return only valid events.*
  - Method `.test_malformed_line_triggers_warning(tmp_path, caplog)` - *read_all() must log a warning for each malformed line.*

---

### [`tests/test_settings.py`](tests/test_settings.py)
**Size**: `37` lines (`1222` bytes)

**Functions:**
- `def settings_manager()`
- `def test_default_settings_creation(settings_manager)`
- `def test_save_and_load_settings(settings_manager)`
- `def test_get_with_default(settings_manager)`

---

### [`tests/test_settings_gui.py`](tests/test_settings_gui.py)
**Size**: `49` lines (`1606` bytes)

**Functions:**
- `def client()`
- `def test_settings_page_accessible(client)`
- `def test_save_settings_route(client)`

---

### [`tests/test_skills_module.py`](tests/test_skills_module.py)
> *Tests for the Skills module.*

**Size**: `550` lines (`18909` bytes)

**Classes:**
- `class TestSkillBackend` - *Test SkillBackend data persistence layer.*
  - Method `.temp_storage()` - *Create temporary storage directory.*
  - Method `.backend(temp_storage)` - *Create backend with temp storage.*
  - Method `.test_create_skill(backend)` - *Test creating a new skill.*
  - Method `.test_get_skill(backend)` - *Test retrieving a skill by ID.*
  - Method `.test_get_nonexistent_skill(backend)` - *Test retrieving non-existent skill returns None.*
  - Method `.test_list_skills(backend)` - *Test listing all skills.*
  - Method `.test_list_skills_with_category_filter(backend)` - *Test filtering skills by category.*
  - Method `.test_list_skills_with_tag_filter(backend)` - *Test filtering skills by tag.*
  - Method `.test_update_skill(backend)` - *Test updating a skill.*
  - Method `.test_delete_skill(backend)` - *Test deleting a skill.*
  - Method `.test_delete_nonexistent_skill(backend)` - *Test deleting non-existent skill returns False.*
  - Method `.test_import_skill(backend)` - *Test importing a skill from content.*
  - Method `.test_get_categories(backend)` - *Test getting all unique categories from skills.*
  - Method `.test_get_all_tags(backend)` - *Test getting all unique tags.*
  - Method `.test_get_skill_content_only(backend)` - *Test getting only skill content.*
  - Method `.test_skill_exists(backend)` - *Test skill existence check.*
- `class TestSkillService` - *Test SkillService business logic layer.*
  - Method `.temp_storage()`
  - Method `.service(temp_storage)`
  - Method `.test_create_skill_validates_content(service)` - *Test that skill creation validates content format.*
  - Method `.test_update_skill_validates_content(service)` - *Test that skill update validates content.*
  - Method `.test_import_from_file_with_yaml_frontmatter(service)` - *Test importing skill with YAML frontmatter.*
  - Method `.test_import_from_file_without_frontmatter(service)` - *Test importing skill without frontmatter (fallback extraction).*
  - Method `.test_export_skill_json(service)` - *Test exporting skill as JSON.*
  - Method `.test_export_skill_markdown(service)` - *Test exporting skill as Markdown with frontmatter.*
  - Method `.test_get_enabled_skills_content(service)` - *Test getting content for multiple enabled skills.*
  - Method `.test_get_enabled_skills_content_ignores_missing(service)` - *Test that missing skill IDs are ignored.*
- `class TestSkillValidation` - *Test skill content validation.*
  - Method `.service()`
  - Method `.test_validate_skill_content_with_headers(service)` - *Test validation passes with markdown headers.*
  - Method `.test_validate_skill_content_with_lists(service)` - *Test validation passes with markdown lists.*
  - Method `.test_validate_skill_content_with_numbered_lists(service)` - *Test validation passes with numbered lists.*
  - Method `.test_validate_skill_content_rejects_empty(service)` - *Test validation rejects empty content.*
  - Method `.test_validate_skill_content_rejects_no_structure(service)` - *Test validation rejects content without structure.*
  - Method `.test_validate_skill_content_rejects_too_short(service)` - *Test validation rejects content under 100 chars.*
  - Method `.test_validate_skill_content_accepts_minimum_valid(service)` - *Test validation accepts minimum valid content (100 chars + structure).*
- `class TestSkillMetadataExtraction` - *Test metadata extraction from skill content.*
  - Method `.service()`
  - Method `.test_extract_title_from_h1(service)` - *Test extracting title from first H1.*
  - Method `.test_extract_title_returns_none_when_no_h1(service)` - *Test title extraction returns None when no H1.*
  - Method `.test_extract_description_first_paragraph(service)` - *Test extracting description from first paragraph after title.*
  - Method `.test_clean_file_name(service)` - *Test cleaning file names to skill names.*
  - Method `.test_generate_import_id(service)` - *Test generating unique import IDs.*

**Functions:**
- `def test_example_skill_loads()` - *Test that the example skill file loads correctly.*
- `def test_skill_content_sanitization()` - *Test that skill content is properly handled.*

---

### [`tests/test_skills_versions.py`](tests/test_skills_versions.py)
> *Content-addressed skill versions: history, deduplication and auditable rollback.*

**Size**: `127` lines (`5211` bytes)

**Functions:**
- `def make_backend(tmp_path)`
- `def test_create_and_update_record_distinct_versions(tmp_path)`
- `def test_updating_with_identical_content_does_not_add_a_version(tmp_path)`
- `def test_a_rollback_restores_content_and_is_itself_recorded(tmp_path)`
- `def test_a_missing_version_or_skill_is_not_a_crash(tmp_path)`
- `def test_history_is_bounded_and_keeps_the_active_version(tmp_path, monkeypatch)`
- `def test_an_unsafe_skill_id_is_rejected(tmp_path)`
- `def test_the_backend_path_helper_rejects_traversal(tmp_path)`
- `def test_a_corrupt_index_reads_as_empty_and_recovers(tmp_path)`
- `def test_versions_survive_a_new_backend_instance(tmp_path)`
- `def test_list_skills_skips_an_unsafe_legacy_id(tmp_path)`

---

### [`tests/test_sse_streaming.py`](tests/test_sse_streaming.py)
> *Tests for SSE streaming (5.1): LLMExecutor streaming path and FlowRunner queue injection.*

**Size**: `254` lines (`9813` bytes)

**Classes:**
- `class TestLLMExecutorStreaming` - *Tests for the new _receive_with_stream() path in LLMExecutor.*
  - Method `._make_chunks(tokens)` - *Helper: build fake streaming chunks from a list of token strings.*
  - Method `async def ._stream_gen(chunks)`
  - Method `async def .test_tokens_put_to_queue()` - *Each token delta is put into the queue as a {type: token} event.*
  - Method `async def .test_returns_assembled_response_dict()` - *_receive_with_stream returns a choices dict with the full assembled content.*
  - Method `async def .test_stream_error_chunk_stops_gracefully()` - *An error chunk in the stream logs a warning and returns whatever was collected.*
  - Method `async def .test_receive_uses_streaming_path_when_queue_present()` - *receive() delegates to _receive_with_stream when _stream_queue is in config.*
  - Method `async def .test_receive_uses_normal_path_without_queue()` - *receive() uses the normal (non-streaming) path when no queue is in config.*
  - Method `async def .test_receive_falls_back_to_normal_when_tools_present()` - *receive() uses normal path when tools are present (streaming + tools unsupported).*
- `class TestFlowRunnerStreamQueue` - *Tests that FlowRunner passes _stream_queue through node configs.*
  - Method `async def .test_stream_queue_injected_into_node_config()` - *Nodes receive _stream_queue in their config when stream_queue is passed to run().*
  - Method `async def .test_no_stream_queue_not_injected()` - *_stream_queue is NOT in node config when stream_queue=None.*
  - Method `async def .test_sentinel_received_after_flow_completes()` - *After run() returns, putting None into the queue signals done.*

---

### [`tests/test_statistics_engine.py`](tests/test_statistics_engine.py)
> *Tests for core/statistics_engine.py and the new ResearchManager experiment tables.*

**Size**: `413` lines (`19071` bytes)

**Classes:**
- `class TestParseData`
  - Method `.test_comma_separated()`
  - Method `.test_newline_separated()`
  - Method `.test_mixed_delimiters()`
  - Method `.test_invalid_token()`
  - Method `.test_too_few_values()`
- `class TestDescriptiveStats`
  - Method `.test_mean()`
  - Method `.test_std()`
  - Method `.test_n()`
  - Method `.test_min_max()`
  - Method `.test_normality_present()`
  - Method `.test_has_iqr()`
- `class TestConfidenceInterval`
  - Method `.test_interval_contains_mean()`
  - Method `.test_99_wider_than_95()`
- `class TestOneSampleTtest`
  - Method `.test_reject_null()`
  - Method `.test_fail_to_reject()`
  - Method `.test_cohen_d_positive_when_mean_above_mu()`
- `class TestTwoSampleTtest`
  - Method `.test_different_means_significant()`
  - Method `.test_same_data_not_significant()`
  - Method `.test_mean_difference_sign()`
  - Method `.test_levene_included()`
- `class TestPairedTtest`
  - Method `.test_before_after_significant()`
  - Method `.test_unequal_length_raises()`
- `class TestCorrelation`
  - Method `.test_perfect_positive_pearson()`
  - Method `.test_negative_correlation()`
  - Method `.test_spearman_monotone()`
  - Method `.test_unequal_length_raises()`
- `class TestChiSquare`
  - Method `.test_uniform_not_significant()`
  - Method `.test_skewed_significant()`
  - Method `.test_expected_mismatch_raises()`
- `class TestANOVA`
  - Method `.test_different_groups_significant()`
  - Method `.test_same_groups_not_significant()`
  - Method `.test_requires_two_groups()`
- `class TestMannWhitney`
  - Method `.test_clearly_different()`
  - Method `.test_same_data_not_significant()`
- `class TestBootstrapCI`
  - Method `.test_ci_excludes_zero_for_large_difference()`
  - Method `.test_ci_includes_zero_for_same_data()`
  - Method `.test_observed_diff_correct()`
- `class TestLinearTrend`
  - Method `.test_strong_upward_trend_significant()`
  - Method `.test_flat_data_not_significant()`
- `class TestInterventionAnalysis`
  - Method `.test_significant_improvement()`
  - Method `.test_no_effect()`
  - Method `.test_small_sample_warning()`
  - Method `.test_result_structure()`
- `class TestResearchManagerExperiments`
  - Method `.test_create_intervention(rm)`
  - Method `.test_update_intervention_end_date(rm)`
  - Method `.test_delete_intervention(rm)`
  - Method `.test_create_metric_unique(rm)`
  - Method `.test_log_and_get_metrics(rm)`
  - Method `.test_delete_metric_log(rm)`
  - Method `.test_log_and_get_events(rm)`
  - Method `.test_delete_event(rm)`
  - Method `.test_csv_export(rm)`

**Functions:**
- `def rm(tmp_path)`

---

### [`tests/test_structured_output.py`](tests/test_structured_output.py)
> *Tests for Structured Output Enforcement*

**Size**: `343` lines (`10804` bytes)

**Classes:**
- `class Person`
- `class SimpleResponse`
- `class TestStructuredOutputError` - *Tests for the StructuredOutputError exception.*
  - Method `.test_error_creation()` - *Test StructuredOutputError can be created with all parameters.*
  - Method `.test_error_string_representation()` - *Test error string includes all details.*
- `class TestCreateJsonSchema` - *Tests for create_json_schema function.*
  - Method `.test_create_schema_from_pydantic()` - *Test creating JSON schema from Pydantic model.*
  - Method `.test_schema_contains_required_fields()` - *Test that schema contains required field definitions.*
- `class TestStructuredCompletion` - *Tests for structured_completion function.*
  - Method `async def .test_successful_parsing()` - *Test successful JSON parsing on valid response.*
  - Method `async def .test_validation_error_triggers_retry()` - *Test that validation error triggers retry with corrected message.*
  - Method `async def .test_max_retries_exceeded_raises_error()` - *Test that exceeding max retries raises StructuredOutputError.*
  - Method `async def .test_empty_response_triggers_retry()` - *Test that empty response triggers retry.*
  - Method `async def .test_api_error_triggers_retry()` - *Test that API error triggers retry.*
- `class TestStructuredCompletionWithFallback` - *Tests for structured_completion_with_fallback function.*
  - Method `async def .test_returns_fallback_on_failure()` - *Test that fallback value is returned on failure.*
  - Method `async def .test_returns_valid_result_on_success()` - *Test that valid result is returned on success.*
- `class TestStructuredChat` - *Tests for structured_chat convenience function.*
  - Method `async def .test_prepends_system_prompt()` - *Test that system prompt is prepended to messages.*
  - Method `async def .test_without_system_prompt()` - *Test that function works without system prompt.*

---

### [`tests/test_system_prompt_node.py`](tests/test_system_prompt_node.py)
**Size**: `363` lines (`13311` bytes)

**Functions:**
- `async def test_system_prompt_injection()`
- `async def test_system_prompt_default()`
- `async def test_system_prompt_none_input()` - *Test handling of None input.*
- `async def test_system_prompt_preserves_data()` - *Test that other data fields are preserved.*
- `async def test_system_prompt_bad_messages_type()` - *Test handling when messages is not a list.*
- `async def test_system_prompt_with_enabled_tools()` - *Test that enabled tools are passed in OpenAI format (not markdown in system prompt).*
- `async def test_system_prompt_with_empty_tools()` - *Test that no tools section is added when enabled_tools is empty.*
- `async def test_system_prompt_passes_tools_in_openai_format()` - *Test that enabled tools are passed in OpenAI format via result data.*
- `async def test_system_prompt_no_tools_in_result_when_empty()` - *Test that tools are not added to result when no tools are enabled.*
- `async def test_memory_context_injected_into_system_prompt()` - *_memory_context field should be appended to the system message.*
- `async def test_knowledge_context_injected_into_system_prompt()` - *knowledge_context field should be appended to the system message.*
- `async def test_plan_context_injected_into_system_prompt()` - *plan_context field should be appended to the system message.*
- `async def test_reasoning_context_injected_into_system_prompt()` - *reasoning_context field should be appended to the system message.*
- `async def test_combined_contexts_all_injected()` - *All context fields present simultaneously should all appear in system message.*
- `async def test_get_executor_class_dispatcher()` - *get_executor_class('system_prompt') should return SystemPromptExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*
- `async def test_token_budget_enforcement()` - *Test that token budget limits context size.*
- `async def test_token_budget_priority_ordering()` - *Test that high-priority context (memory) comes before low-priority (skills).*
- `async def test_token_estimation()` - *Test the character-based token estimation fallback (tiktoken not available).*
- `async def test_low_priority_context_dropped_when_budget_exceeded()` - *Test that token budget limits total context size.*

---

### [`tests/test_tool_sandbox.py`](tests/test_tool_sandbox.py)
> *Tests for the Tool Sandbox security system.*

**Size**: `613` lines (`21923` bytes)

**Classes:**
- `class TestRestrictedImport` - *Tests for the restricted import hook.*
  - Method `.test_blocks_dangerous_modules()` - *Test that dangerous modules like os, sys are blocked.*
  - Method `.test_blocks_dangerous_submodules()` - *Test that submodules of dangerous modules are blocked.*
  - Method `.test_allows_safe_modules()` - *Test that safe modules like math, json are allowed.*
- `class TestSafeOpen` - *Tests for the restricted file open.*
  - Method `.test_blocks_access_outside_allowed_dirs()` - *Test that files outside allowed directories are blocked.*
  - Method `.test_blocks_write_access_when_read_only()` - *Test that write access is blocked in read-only mode.*
  - Method `.test_blocks_append_mode_in_read_only()` - *Test that append mode 'a' is blocked in read-only mode.*
  - Method `.test_blocks_exclusive_mode_in_read_only()` - *Test that exclusive mode 'x' is blocked in read-only mode.*
  - Method `.test_blocks_binary_read_in_read_only()` - *Test that binary read mode 'rb' is blocked in read-only mode.*
- `class TestSafeHttpxClient` - *Tests for the restricted HTTP client.*
  - Method `.test_blocks_non_whitelisted_domains()` - *Test that requests to non-whitelisted domains are blocked.*
  - Method `.test_allows_whitelisted_domains()` - *Test that requests to whitelisted domains are allowed.*
  - Method `.test_blocks_internal_ips()` - *Test that requests to internal IP addresses are blocked (SSRF protection).*
- `class TestToolSandboxStaticAnalysis` - *Tests for the sandbox static code analysis.*
  - Method `.test_blocks_dangerous_imports()` - *Test that code with dangerous imports is blocked.*
  - Method `.test_blocks_dangerous_builtins()` - *Test that code with dangerous builtins is blocked.*
  - Method `.test_blocks_path_traversal()` - *Test that code with path traversal is blocked.*
- `class TestToolSandboxExecution` - *Tests for sandboxed code execution.*
  - Method `.test_executes_safe_code()` - *Test that safe code executes correctly.*
  - Method `.test_provides_safe_modules()` - *Test that safe modules are available in sandbox.*
  - Method `.test_blocks_dangerous_code_at_runtime()` - *Test that dangerous code is blocked during execution.*
  - Method `.test_enforces_timeout()` - *Test that code exceeding timeout is terminated.*
  - Method `.test_enforces_output_size_limit()` - *Test that output exceeding size limit is blocked.*
  - Method `.test_enforces_memory_limit()` - *Test that memory limit is enforced (if supported on platform).*
- `class TestExecuteSandboxed` - *Tests for the convenience function.*
  - Method `.test_basic_execution()` - *Test basic sandboxed execution.*
  - Method `.test_security_violation()` - *Test that security violations are caught.*
- `class TestSandboxSecurityScenarios` - *Real-world security scenario tests.*
  - Method `.test_prevents_code_injection()` - *Test that code injection attempts are blocked.*
  - Method `.test_prevents_import_bypass()` - *Test that various import bypass techniques are blocked.*
  - Method `.test_calculator_tool_safety()` - *Test that calculator-like tools are safe.*
  - Method `.test_network_tool_safety()` - *Test that network tools respect domain restrictions.*
  - Method `.test_import_httpx_returns_safe_client()` - *Regression test: tool code that does `import httpx` must receive*
  - Method `.test_httpx_import_respects_domain_restriction()` - *Tool code that imports httpx and calls a blocked domain must be rejected.*
  - Method `.test_import_os_returns_safe_env()` - *import os must return SafeEnv, not the real os module.*
  - Method `.test_os_getenv_hides_the_inherited_environment()` - *A tool cannot read a host secret it was not explicitly given.*
  - Method `.test_os_getenv_reads_permitted_values()` - *os.getenv() and os.environ expose exactly the permitted values.*
  - Method `.test_os_dangerous_attrs_blocked()` - *os.system, os.path, etc. must be blocked via SafeEnv.*
  - Method `.test_import_ast_works()` - *import ast must succeed (needed by Calculator tool).*
  - Method `.test_import_zoneinfo_works()` - *from zoneinfo import ZoneInfo must succeed (needed by TimeZoneConverter).*
  - Method `.test_unknown_module_raises_import_error()` - *Unknown non-dangerous modules raise ImportError, not SecurityError.*
  - Method `.test_modules_passthrough_works()` - *from modules.* imports pass through to the real NeuroCore package.*
- `class TestToolDispatcherIntegration` - *Integration tests with the ToolDispatcherExecutor.*
  - Method `async def .test_dispatcher_uses_sandbox()` - *Test that the tool dispatcher uses the sandbox.*
  - Method `async def .test_dispatcher_catches_security_errors()` - *Test that security errors are properly caught and reported.*

---

### [`tests/test_tools_library.py`](tests/test_tools_library.py)
**Size**: `549` lines (`20004` bytes)

**Classes:**
- `class TestCalculator` - *Tests for Calculator tool.*
  - Method `.test_basic_addition()`
  - Method `.test_basic_subtraction()`
  - Method `.test_basic_multiplication()`
  - Method `.test_basic_division()`
  - Method `.test_power_operator()`
  - Method `.test_math_functions()`
  - Method `.test_math_constants()`
  - Method `.test_complex_expression()`
  - Method `.test_empty_expression()`
  - Method `.test_forbidden_keywords()`
- `class TestConversionCalculator` - *Tests for ConversionCalculator tool.*
  - Method `.test_celsius_to_fahrenheit()`
  - Method `.test_fahrenheit_to_celsius()`
  - Method `.test_meters_to_feet()`
  - Method `.test_kilometers_to_miles()`
  - Method `.test_kg_to_pounds()`
  - Method `.test_liters_to_gallons()`
  - Method `.test_unsupported_conversion()`
  - Method `.test_invalid_value()`
- `class TestCurrencyConverter` - *Tests for CurrencyConverter tool.*
  - Method `.test_same_currency(mock_get)`
  - Method `.test_successful_conversion(mock_get)`
  - Method `.test_missing_arguments(mock_get)`
  - Method `.test_api_error(mock_get)`
- `class TestTimeZoneConverter` - *Tests for TimeZoneConverter tool.*
  - Method `.test_current_time_conversion()`
  - Method `.test_specific_time_conversion()`
  - Method `.test_missing_source_tz()`
  - Method `.test_missing_target_tz()`
- `class TestFetchURL` - *Tests for FetchURL tool.*
  - Method `.test_successful_fetch(mock_get)`
  - Method `.test_missing_url(mock_get)`
  - Method `.test_html_stripping(mock_get)`
  - Method `.test_fetch_error(mock_get)`
- `class TestSystemTime` - *Tests for SystemTime tool.*
  - Method `.test_returns_current_time()`
- `class TestWeather` - *Tests for Weather tool.*
  - Method `.test_successful_weather_fetch(mock_get)`
  - Method `.test_missing_location(mock_get)`
  - Method `.test_api_error(mock_get)`
- `class TestSendEmail` - *Tests for SendEmail tool.*
  - Method `.test_successful_send(mock_smtp)`
  - Method `.test_missing_credentials()`
  - Method `.test_missing_arguments(mock_smtp)`
- `class TestSaveReminder` - *Tests for SaveReminder tool.*
  - Method `.test_save_reminder_with_time(monkeypatch)`
  - Method `.test_save_reminder_without_time(monkeypatch)`
  - Method `.test_save_reminder_missing_title()`
- `class TestCheckCalendar` - *Tests for CheckCalendar tool.*
  - Method `.test_get_upcoming_events(monkeypatch)`
  - Method `.test_get_events_by_date(monkeypatch)`
  - Method `.test_no_events_found(monkeypatch)`
- `class TestYouTubeTranscript` - *Tests for YouTubeTranscript tool.*
  - Method `.test_missing_url()`
  - Method `.test_invalid_url_no_video_id()`
  - Method `.test_youtube_watch_url_parsing()`
  - Method `.test_youtu_be_url_parsing()`
- `class TestWikipediaLookup` - *Tests for WikipediaLookup tool.*
  - Method `.test_successful_summary_lookup(mock_get)`
  - Method `.test_full_article_mode(mock_get)`
  - Method `.test_missing_query()`
  - Method `.test_no_results_found(mock_get)`
  - Method `.test_api_error(mock_get)`
- `class TestArXivSearch` - *Tests for ArXivSearch tool.*
  - Method `.test_successful_search(mock_get)`
  - Method `.test_missing_query()`
  - Method `.test_no_results_found(mock_get)`
  - Method `.test_custom_max_results(mock_get)`
  - Method `.test_api_error(mock_get)`

**Functions:**
- `def execute_tool(tool_name, args)` - *Helper function to execute a tool with given arguments.*

---

### [`tests/test_tools_node.py`](tests/test_tools_node.py)
> *Tests for modules/tools/node.py — ToolDispatcherExecutor*

**Size**: `180` lines (`7108` bytes)

**Functions:**
- `def make_input(tool_calls, messages)` - *Build a minimal input_data dict with tool_calls in OpenAI format.*
- `def make_tool_call(name, args, call_id)`
- `def make_executor(tools)` - *Create executor with _load_tools patched.*
- `async def test_no_tool_calls_passthrough()` - *Input without tool_calls should be returned unchanged.*
- `async def test_no_choices_passthrough()` - *Input without 'choices' key should be returned unchanged.*
- `async def test_tool_execution_happy_path()` - *A known tool with valid code should execute and return its result.*
- `async def test_unknown_tool_returns_error()` - *An unknown tool name should produce an error result, not raise.*
- `async def test_malformed_json_arguments_returns_error()` - *Malformed JSON in tool arguments should produce an error result, not raise.*
- `async def test_max_tools_per_turn_limits_execution()` - *Only max_tools_per_turn tools should run; the rest stored in _remaining_tool_calls.*
- `async def test_requires_continuation_true_when_tools_remain()` - *requires_continuation must be True when tool calls were truncated.*
- `async def test_requires_continuation_false_when_all_tools_run()` - *requires_continuation must be False when all tool calls fit in one turn.*
- `async def test_allowed_tools_filtering()` - *Tools not in allowed_tools list should be blocked.*
- `async def test_messages_updated_with_tool_results()` - *Output messages should include the assistant message + tool results.*
- `async def test_config_none_guard()` - *Passing config=None should not raise.*
- `async def test_get_executor_class_dispatcher()` - *get_executor_class('tool_dispatcher') should return ToolDispatcherExecutor.*
- `async def test_get_executor_class_unknown()` - *get_executor_class with unknown id should return None.*

---

### [`tests/test_ui_dynamics.py`](tests/test_ui_dynamics.py)
**Size**: `41` lines (`1589` bytes)

**Functions:**
- `def client()`
- `def test_navbar_fragment(client)` - *Tests if the navbar fragment loads correctly via HTMX route.*
- `def test_module_list_fragment(client)` - *Tests if the module list fragment loads correctly.*
- `def test_llm_status_fragment(client)` - *Tests if the LLM status fragment loads correctly.*
- `def test_chat_page_loading(client)` - *Tests if the /chat route correctly prepares the dashboard for chat.*

---

### [`tests/test_xss_escaping.py`](tests/test_xss_escaping.py)
> *Regression tests for M5: stored content must be escaped, not rendered as HTML.*

**Size**: `48` lines (`1819` bytes)

**Functions:**
- `def test_jinja_environment_autoescapes()`
- `async def test_memory_stats_type_is_html_escaped(monkeypatch)`
- `def test_marketplace_templates_escape_stored_content()`

---
