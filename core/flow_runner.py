import asyncio
import copy
import importlib
import json
import logging
import sys
import time
import types
from collections import deque, defaultdict
from .flow_manager import flow_manager
from core.settings import settings
from core.execution_budget import (
    budget_from_flow,
    current_budget,
    pop_budget,
    push_budget,
    spend,
)
from core.debug import debug_logger
from core.errors import FlowError, NodeExecutionError
from core.node_retry import RetryPolicy, node_timeout, run_with_retry

logger = logging.getLogger(__name__)

class FlowRunner:
    _executor_cache = {}
    _max_cache_size = 100  # Maximum number of cached executor classes
    _cache_lock = None  # Lazy-initialized lock for thread-safe cache operations
    
    @classmethod
    def clear_cache(cls):
        cls._executor_cache.clear()
    
    @classmethod
    def _get_cache_lock(cls):
        """Return an asyncio.Lock scoped to the currently running event loop.

        Storing a single lock at the class level is unsafe when the event loop
        changes between test runs or server reloads (Python 3.10+ silently uses
        the wrong loop scheduler). Instead we key by loop identity so each loop
        gets its own lock while still sharing the executor cache dict.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — fall back to a fresh lock (import-time access).
            if cls._cache_lock is None:
                cls._cache_lock = asyncio.Lock()
            return cls._cache_lock

        loop_id = id(loop)
        # _cache_lock doubles as a per-loop dict when inside an event loop.
        if not isinstance(cls._cache_lock, dict):
            cls._cache_lock = {}
        if loop_id not in cls._cache_lock:
            cls._cache_lock[loop_id] = asyncio.Lock()
        return cls._cache_lock[loop_id]
    
    @classmethod
    async def _get_executor_class(cls, module_id: str, node_type_id: str):
        """Thread-safe method to get or create an executor class from the cache."""
        cache_key = f"{module_id}.{node_type_id}"
        
        async with cls._get_cache_lock():
            # Check if already in cache (after acquiring lock)
            if cache_key in cls._executor_cache:
                return cls._executor_cache[cache_key]
            
            # Manage cache size before adding new entry
            cls._manage_cache_size()
            
            # Dynamically import the module's node logic dispatcher
            node_dispatcher = importlib.import_module(f"modules.{module_id}.node")
            # Only reload in debug mode to pick up hot-code changes;
            # in production this is wasteful and can cause state issues.
            if settings.get("debug_mode") and isinstance(node_dispatcher, types.ModuleType) and node_dispatcher.__name__ in sys.modules:
                # Invalidate cache before reloading to ensure fresh class is used
                cls._executor_cache.pop(cache_key, None)
                importlib.reload(node_dispatcher)
            # Get the specific executor class for this node type.
            # get_executor_class must be async; guard against sync variants to
            # produce a clear error instead of the cryptic "object type can't
            # be used in 'await' expression" TypeError.
            get_fn = node_dispatcher.get_executor_class
            if not asyncio.iscoroutinefunction(get_fn):
                raise RuntimeError(
                    f"modules.{module_id}.node.get_executor_class must be async def "
                    f"(found sync function). Did a module reload produce a stale version?"
                )
            executor_class = await get_fn(node_type_id)
            cls._executor_cache[cache_key] = executor_class
            return executor_class
    
    @classmethod
    def _manage_cache_size(cls):
        """Ensure cache doesn't grow indefinitely by removing oldest entries when limit reached."""
        if len(cls._executor_cache) >= cls._max_cache_size:
            # Remove oldest 20% of entries (FIFO eviction)
            # Cap at len(cache) // 5 to avoid issues when cache is small
            num_to_remove = max(1, len(cls._executor_cache) // 5)
            keys_to_remove = list(cls._executor_cache.keys())[:num_to_remove]
            for key in keys_to_remove:
                del cls._executor_cache[key]
            if settings.get("debug_mode"):
                logger.debug(f"[FlowRunner] Cache size limit reached. Removed {len(keys_to_remove)} oldest entries.")

    def __init__(self, flow_id: str, flow_override: dict = None,
                 flow_manager_override=None, capture_outcome: bool = True):
        self.flow_id = flow_id
        self.flow_manager = flow_manager_override or flow_manager
        self.capture_outcome = bool(capture_outcome)
        if flow_override:
            self.flow = flow_override
        else:
            self.flow = self.flow_manager.get_flow(flow_id)
            
        if not self.flow:
            raise ValueError(f"Flow with id {flow_id} not found.")
        
        self.nodes = {node['id']: node for node in self.flow['nodes']}
        self.connections = self.flow['connections']
        self.bridges = self.flow.get('bridges', [])
        self.bridge_groups = self._build_bridge_groups()
        
        # Precompute incoming edges adjacency dict for O(1) lookups
        self.incoming_edges = defaultdict(list)
        for c in self.connections:
            self.incoming_edges[c['to']].append(c)
        
        # Precompute downstream nodes for O(1) lookups
        self.downstream_nodes = defaultdict(list)
        for c in self.connections:
            self.downstream_nodes[c['from']].append(c['to'])
        
        self.execution_order = self._compute_execution_order()
        
        if settings.get("debug_mode"):
            logger.debug(f"[FlowRunner] Initialized for flow {flow_id}")

    def _build_bridge_groups(self):
        """
        Identifies groups of bridged nodes using BFS to find connected components.
        
        Bridges are bidirectional connections that create implicit execution dependencies.
        When nodes A-B-C are bridged together, they form a "bridge group" where:
        - All nodes in the group share the same input data
        - The group executes in upstream-to-downstream order
        - Outputs from earlier nodes in the chain are merged into later nodes
        
        Algorithm:
        1. Build adjacency list from bridge connections (undirected graph)
        2. Use BFS to find all connected components
        3. Map each node to its full component group
        
        Returns:
            dict: Mapping of node_id -> list of all node_ids in the same bridge group
        """
        # Build undirected adjacency list: bridges connect nodes bidirectionally
        adj = {node_id: [] for node_id in self.nodes}
        for b in self.bridges:
            if b['from'] in adj and b['to'] in adj:
                adj[b['from']].append(b['to'])
                adj[b['to']].append(b['from'])
        
        groups = {}
        visited = set()
        for node_id in self.nodes:
            if node_id in visited: 
                continue
            if not adj[node_id]: 
                continue
            
            # BFS to find all nodes in this connected component
            component = []
            queue = deque([node_id])
            visited.add(node_id)
            while queue:
                curr = queue.popleft()
                component.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            
            # Each node in the component maps to the full component list
            for member in component:
                groups[member] = component
        return groups

    
    def _get_bridge_order(self, node_id):
        """
        Get bridge chain nodes in execution order (upstream to downstream).
        
        Bridges have a direction: from_node executes BEFORE to_node. This method
        performs a topological sort within the bridge group to determine the
        correct execution order.
        
        For example, if bridges are A->B and B->C, the order is [A, B, C].
        This ensures that when node C executes, it has access to outputs from A and B.
        
        Args:
            node_id: The ID of a node in the bridge group
            
        Returns:
            list: Ordered list of node IDs from furthest upstream to downstream
        """
        if node_id not in self.bridge_groups:
            return []
        
        # Get all nodes in this bridge group
        group = self.bridge_groups[node_id]
        ordered = []
        visited = set()
        
        # DFS to build topological order (visit dependencies first)
        def visit(nid):
            if nid in visited:
                return
            visited.add(nid)
            # Visit all upstream nodes first (nodes that have edges TO this node)
            for b in self.bridges:
                if b['to'] == nid and b['from'] in group:
                    visit(b['from'])
            ordered.append(nid)
        
        # Visit all nodes in the group to build complete ordering
        for nid in group:
            visit(nid)
        
        return ordered


    def _get_bridge_levels(self, node_id: str) -> list:
        """Group bridge-group nodes into parallel execution levels using Kahn's algorithm.

        Nodes in the same level have no bridge dependency on each other and can
        therefore run concurrently via ``asyncio.gather``.

        Example — bridges A→C, B→C, C→D, target node D:
            level 0: [A, B]   ← independent, run in parallel
            level 1: [C]      ← depends on A and B, runs after level 0
            (D is the target node and is excluded)

        Returns:
            list[list[str]]: Ordered levels, each a sorted list of node IDs.
        """
        if node_id not in self.bridge_groups:
            return []

        group = set(self.bridge_groups[node_id])

        # Build directed adjacency + in-degree within the group
        in_degree = {n: 0 for n in group}
        adj = {n: [] for n in group}
        for b in self.bridges:
            fn, tn = b['from'], b['to']
            if fn in adj and tn in adj:
                adj[fn].append(tn)
                in_degree[tn] += 1

        # Kahn's level extraction
        levels = []
        current = sorted(n for n in group if in_degree[n] == 0)
        visited = set(current)

        while current:
            levels.append(current)
            nxt = []
            for n in current:
                for nbr in adj[n]:
                    in_degree[nbr] -= 1
                    if in_degree[nbr] == 0 and nbr not in visited:
                        visited.add(nbr)
                        nxt.append(nbr)
            current = sorted(nxt)

        return levels

    async def _run_node(self, node_id: str, node_input, stream_queue: asyncio.Queue = None):
        """Execute one node under its configured timeout and retry policy (ADR-060).

        Returns ``("ok", output)``, ``("stop", None)`` when the node intentionally stops the
        branch, or ``("missing", None)`` when no executor exists. Each attempt spends one node
        from the run budget, so a retry cannot escape the per-run ceiling.
        """
        node_meta = self.nodes[node_id]
        module_id = node_meta['moduleId']
        node_type_id = node_meta['nodeTypeId']
        source_config = node_meta.get('config') or {}
        policy = RetryPolicy.from_config(source_config)
        timeout = node_timeout(source_config)

        async def attempt():
            spend("nodes")
            executor_class = await self._get_executor_class(module_id, node_type_id)
            if not executor_class:
                return "missing", None
            executor = executor_class()
            node_config = source_config.copy()
            node_config['_flow_id'] = self.flow_id
            node_config['_node_id'] = node_id
            if stream_queue:
                node_config['_stream_queue'] = stream_queue
            processed_data = await executor.receive(node_input, config=node_config)
            if processed_data is None:
                return "stop", None
            return "ok", await executor.send(processed_data)

        async def call():
            if timeout > 0:
                return await asyncio.wait_for(attempt(), timeout=timeout)
            return await attempt()

        def on_retry(retry: int, delay: float, exc: BaseException) -> None:
            logger.warning(
                "Node '%s' attempt %s failed (%s: %s); retrying in %.2fs",
                node_meta.get('name', node_id), retry, type(exc).__name__, exc, delay,
            )
            if settings.get("debug_mode"):
                debug_logger.log(self.flow_id, node_id, node_meta.get('name', node_id),
                                 "node_retry", {
                                     "attempt": retry,
                                     "delay": delay,
                                     "error": str(exc),
                                     "error_type": type(exc).__name__,
                                 })

        return await run_with_retry(call, policy, on_retry=on_retry)

    async def _run_bridge_node(self, bridge_node_id: str, bridge_input: dict, stream_queue: asyncio.Queue = None):
        """Execute a single bridge node and return its output dict, or None to stop the chain.

        Raises any non-cancellation exception so that ``asyncio.gather(
        return_exceptions=True)`` can capture it without killing sibling tasks.
        """
        bridge_meta = self.nodes[bridge_node_id]
        status, bridge_output = await self._run_node(
            bridge_node_id, bridge_input, stream_queue=stream_queue
        )
        if status == "missing":
            return bridge_input  # pass-through when executor is missing
        if status == "stop":
            return None  # intentional stop signal

        if settings.get("debug_mode") and bridge_output is not None:
            debug_logger.log(
                self.flow_id, bridge_node_id,
                bridge_meta.get('name', bridge_node_id),
                "bridge_output", {"output": bridge_output},
            )

        return bridge_output  # may be None (stop signal from send)

    def _compute_execution_order(self):
        """
        Performs a topological sort (Kahn's algorithm) to find the execution order.
        
        This method builds a directed acyclic graph (DAG) from:
        1. Bridge connections (implicit dependencies between bridged nodes)
        2. Regular connections (explicit user-defined edges)
        
        Bridge handling: When a node is bridged, all nodes in its bridge group
        receive the same input. The bridge edges define execution order within
        the group, while connections to any group member feed the entire group.
        
        Cycle handling: cycles (e.g. the LLM -> router -> tool -> LLM loop) are bounded loops,
        not errors: execution re-queues nodes whose inputs change, capped by max_node_loops. The
        initial order enters each cycle at its entry node (one fed from outside the cycle), with
        node ID order as the deterministic tie-break. Set ``strict_cycle_mode`` on a flow to
        reject cycles instead.
        
        Returns:
            list: Topologically sorted list of node IDs for execution
        """
        # Build adjacency list and in-degree count for Kahn's algorithm
        adj = {node_id: [] for node_id in self.nodes}
        in_degree = {node_id: 0 for node_id in self.nodes}

        # Step 1: Add bridge dependencies to the graph
        # Bridges are directed: from_node must execute before to_node
        for b in self.bridges:
            from_node = b['from']
            to_node = b['to']
            if from_node not in self.nodes or to_node not in self.nodes:
                continue
            # Add bridge edge: from_node -> to_node
            if to_node not in adj[from_node]:
                adj[from_node].append(to_node)
                in_degree[to_node] += 1

        # Step 2: Add regular connections to the graph
        for conn in self.connections:
            source = conn['from']
            target = conn['to']
            
            # Skip orphaned connections (connections to non-existent nodes)
            if source not in self.nodes or target not in self.nodes:
                continue
            
            # If target is bridged, the source feeds the entire bridge group
            targets = [target]
            if target in self.bridge_groups:
                targets = self.bridge_groups[target]
            
            for t in targets:
                # Avoid self-loops if user connected bridged nodes explicitly
                if t != source:
                    # Check if edge already exists to avoid double counting
                    if t not in adj[source]:
                        adj[source].append(t)
                        in_degree[t] += 1

        # Step 3: Kahn's algorithm - start with nodes that have no dependencies
        queue = deque([node_id for node_id in self.nodes if in_degree[node_id] == 0])
        sorted_order = []

        while queue:
            u = queue.popleft()
            sorted_order.append(u)

            # Reduce in-degree for all neighbors
            for v in adj[u]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        
        # Step 4: Handle cycles - if not all nodes were sorted, we have a cycle
        if len(sorted_order) != len(self.nodes):
            # Cycle detected. Check if strict mode is enabled
            strict_cycle_mode = self.flow.get('strict_cycle_mode', False)
            
            remaining_nodes = list(set(self.nodes.keys()) - set(sorted_order))
            
            if strict_cycle_mode:
                # In strict mode, raise an error for cycles
                cycle_nodes = self._identify_cycle_nodes(adj, sorted_order, remaining_nodes)
                raise ValueError(
                    f"[FlowRunner] Cycle detected in flow '{self.flow_id}'. "
                    f"Cyclic nodes: {cycle_nodes}. "
                    f"Enable non-strict mode or fix the flow graph to remove cycles."
                )
            
            # Log warning about cycle detection - always log in production
            logger.warning(f"[FlowRunner] Cycle detected in flow. Breaking by adding remaining nodes: {remaining_nodes}")
            
            # Check for intentional loops (nodes with isReverted flag indicating loop nodes)
            loop_nodes = [nid for nid, node in self.nodes.items() if node.get('isReverted', False)]
            if loop_nodes:
                logger.warning(f"[FlowRunner] Note: Flow contains {len(loop_nodes)} node(s) marked as loop (isReverted): {loop_nodes}")
            
            predecessors = {node_id: set() for node_id in self.nodes}
            for source, targets in adj.items():
                for target in targets:
                    predecessors[target].add(source)

            while len(sorted_order) < len(self.nodes):
                # Break the deadlock at a cycle's entry: a remaining node already fed by a sorted
                # node (e.g. the LLM in an LLM -> router -> tool -> LLM loop). Starting at an
                # arbitrary member would run it before anything upstream produced its input.
                # Ties and entry-less cycles fall back to node ID order, so it stays deterministic.
                candidates = sorted([n for n in self.nodes if n not in sorted_order])
                if not candidates:
                    break

                placed = set(sorted_order)
                entries = [n for n in candidates if predecessors[n] & placed]
                next_node = (entries or candidates)[0]
                sorted_order.append(next_node)
                
                # Simulate processing this node to free up its neighbors
                for v in adj[next_node]:
                    in_degree[v] -= 1
                    if in_degree[v] == 0 and v not in sorted_order:
                        queue.append(v)
                
                # Process any newly freed nodes
                while queue:
                    u = queue.popleft()
                    if u not in sorted_order:
                        sorted_order.append(u)
                        for v in adj[u]:
                            in_degree[v] -= 1
                            if in_degree[v] == 0:
                                queue.append(v)

        return sorted_order


    def _identify_cycle_nodes(self, adj: dict, sorted_order: list, remaining_nodes: list) -> list:
        """
        Identify the nodes that form the cycle in the flow graph.
        
        Uses DFS to find nodes that are part of cycles.
        
        Args:
            adj: Adjacency list representing the graph
            sorted_order: Nodes that were successfully topologically sorted
            remaining_nodes: Nodes that could not be sorted (part of cycle)
            
        Returns:
            list: Node IDs that are part of the cycle
        """
        cycle_nodes = []
        
        # Build reverse adjacency for cycle detection
        # For remaining nodes, find which ones reference each other
        for node in remaining_nodes:
            # Check if this node has edges to other remaining nodes
            neighbors = adj.get(node, [])
            for neighbor in neighbors:
                if neighbor in remaining_nodes:
                    # This is a cycle edge
                    if node not in cycle_nodes:
                        cycle_nodes.append(node)
                    if neighbor not in cycle_nodes:
                        cycle_nodes.append(neighbor)
        
        # If we couldn't find cycle nodes via adjacency, return the remaining nodes
        if not cycle_nodes:
            cycle_nodes = remaining_nodes[:3]  # Return first few for brevity
        
        return cycle_nodes


    def validate(self, module_manager) -> dict:
        """Validates the flow for potential issues before execution."""
        issues = []
        warnings = []
        
        # Get enabled modules
        enabled_modules = {m['id']: m for m in module_manager.get_all_modules() if m.get('enabled', False)}
        
        # Check 1: Nodes referencing disabled or non-existent modules
        for node_id, node in self.nodes.items():
            module_id = node.get('moduleId')
            if module_id not in enabled_modules:
                issues.append({
                    'type': 'disabled_module',
                    'node_id': node_id,
                    'node_name': node.get('name'),
                    'module_id': module_id,
                    'message': f"Node '{node.get('name')}' references disabled module '{module_id}'"
                })
        
        # Check 2: Orphaned connections (referencing non-existent nodes)
        node_ids = set(self.nodes.keys())
        for conn in self.connections:
            if conn['from'] not in node_ids:
                issues.append({
                    'type': 'orphaned_connection',
                    'from_id': conn['from'],
                    'to_id': conn['to'],
                    'message': f"Connection from non-existent node '{conn['from']}'"
                })
            if conn['to'] not in node_ids:
                issues.append({
                    'type': 'orphaned_connection',
                    'from_id': conn['from'],
                    'to_id': conn['to'],
                    'message': f"Connection to non-existent node '{conn['to']}'"
                })
        
        # Check 3: Tools enabled in System Prompt that don't exist or are disabled
        # Load tools once outside the loop to avoid O(nodes × tools) disk reads
        tools = {}
        try:
            from modules.tools.router import load_tools
            tools = load_tools()
        except Exception as e:
            warnings.append({
                'type': 'tool_load_error',
                'node_id': None,
                'node_name': None,
                'tool_name': None,
                'message': f"Failed to load tools: {str(e)}"
            })
        
        for node_id, node in self.nodes.items():
            if node.get('nodeTypeId') == 'system_prompt':
                enabled_tools = node.get('config', {}).get('enabled_tools', [])
                for tool_name in enabled_tools:
                    # Check if tool exists in tools.json (using cached tools)
                    tool_config = tools.get(tool_name, {})
                    if tool_name not in tools:
                        warnings.append({
                            'type': 'missing_tool',
                            'node_id': node_id,
                            'node_name': node.get('name'),
                            'tool_name': tool_name,
                            'message': f"Node '{node.get('name')}' enables non-existent tool '{tool_name}'"
                        })
                    elif tool_config.get('enabled', True) == False:
                        warnings.append({
                            'type': 'disabled_tool',
                            'node_id': node_id,
                            'node_name': node.get('name'),
                            'tool_name': tool_name,
                            'message': f"Node '{node.get('name')}' enables disabled tool '{tool_name}'"
                        })
        
        # Check 4: Check for nodes without any connections (might be unintentional)
        connected_nodes = set()
        for conn in self.connections:
            connected_nodes.add(conn['from'])
            connected_nodes.add(conn['to'])
        
        # Add bridged nodes to connected set
        for bridge in self.bridges:
            if bridge.get('from') in self.nodes:
                connected_nodes.add(bridge.get('from'))
            if bridge.get('to') in self.nodes:
                connected_nodes.add(bridge.get('to'))
        
        # Nodes that don't require connections (check various possible node type IDs)
        no_connection_required = [
            'trigger_node', 
            'repeater_node', 
            'annotation', 
            'comment', 
            'scheduled_start',
            'annotation_node',
            'comment_node'
        ]
        
        for node_id, node in self.nodes.items():
            node_type = node.get('nodeTypeId', '').lower()
            node_name = node.get('name', '').lower()
            
            # Check if it's an annotation/comment by name or type
            is_annotation = (node_type in no_connection_required or 
                           'annotation' in node_name or 
                           'comment' in node_name)
            
            if node_id not in connected_nodes and not is_annotation:
                warnings.append({
                    'type': 'unconnected_node',
                    'node_id': node_id,
                    'node_name': node.get('name'),
                    'message': f"Node '{node.get('name')}' has no connections"
                })
        
        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'warnings': warnings
        }

    async def run(self, initial_input: dict, start_node_id: str = None, timeout: float = None, raise_errors: bool = False, episode_id: str = None, stream_queue: asyncio.Queue = None):
        """Run a flow and emit one outcome record (unless capture is disabled)."""
        started = time.perf_counter()
        result = None
        error = None
        outer_budget = current_budget()
        token = None
        budget = outer_budget
        if budget is None:
            budget = budget_from_flow(self.flow)
            token = push_budget(budget)
        try:
            remaining = budget.remaining("seconds")
            if timeout is None:
                timeout = remaining
            elif timeout > 0:
                # An explicit timeout may shorten the wall clock, never extend it.
                timeout = min(timeout, remaining)
            # timeout == 0 keeps its documented meaning: no asyncio deadline. The budget still
            # enforces max_seconds on every spend (ADR-055).
            result = await self._run(
                initial_input, start_node_id=start_node_id, timeout=timeout,
                raise_errors=raise_errors, episode_id=episode_id,
                stream_queue=stream_queue,
            )
            if isinstance(result, dict) and result.get("error"):
                error = str(result.get("error"))
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            if token is not None:
                pop_budget(token)
            if self.capture_outcome:
                from core.outcomes import emit_outcome

                summary = result
                if isinstance(result, dict):
                    summary = (
                        result.get("content")
                        or result.get("response")
                        or result.get("error")
                        or "completed"
                    )
                emit_outcome({
                    "flow_id": self.flow_id,
                    "success": error is None,
                    "error": error,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "summary": str(summary)[:500],
                })

    async def _run(self, initial_input: dict, start_node_id: str = None, timeout: float = None, raise_errors: bool = False, episode_id: str = None, stream_queue: asyncio.Queue = None):
        """
        Executes the flow in order, passing data between nodes.
        
        Args:
            initial_input: Input data for the flow
            start_node_id: Optional specific node to start from
            timeout: Optional timeout in seconds for the entire flow execution
            raise_errors: If True, raise exceptions instead of returning error dicts
            episode_id: Optional episode ID for episode persistence support
            stream_queue: Optional asyncio.Queue to emit streaming tokens into
        """
        # --- Episode Persistence Support ---
        sm = None
        episode = None
        enable_episode = episode_id is not None
        
        if enable_episode:
            try:
                from core.session_manager import get_session_manager, EpisodeState
                sm = get_session_manager()
                # Try to load the episode
                episode = sm.load_episode_by_id(episode_id)
                if episode:
                    # Restore state from episode to input
                    if episode.plan:
                        initial_input.setdefault("plan", episode.plan)
                    if episode.current_step is not None:
                        initial_input.setdefault("current_step", episode.current_step)
                    if episode.completed_steps:
                        initial_input.setdefault("completed_steps", episode.completed_steps)
                    # Update phase to executing
                    sm.save_episode_by_id(episode_id, phase=EpisodeState.PHASE_EXECUTING)
            except Exception as exc:
                logger.warning(
                    f"[FlowRunner] Failed to load/restore episode '{episode_id}': {exc}. "
                    "Continuing without episode state."
                )
        if settings.get("debug_mode"):
            debug_logger.log(self.flow_id, "SYSTEM", "FlowRunner", "flow_start", {"start_node": start_node_id, "input_source": initial_input.get("_input_source") if isinstance(initial_input, dict) else None, "timeout": timeout})
        
        async def run_impl():
            node_outputs = {}
            total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            def _accumulate_usage(item_output):
                if isinstance(item_output, dict) and "usage" in item_output:
                    u = item_output["usage"]
                    if isinstance(u, dict):
                        total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += u.get("completion_tokens", 0)
                        total_usage["total_tokens"] += u.get("total_tokens", 0)
            
            # Determine start nodes based on input source
            input_source = initial_input.get("_input_source") if isinstance(initial_input, dict) else None
            explicit_start_nodes = set()
            
            if start_node_id:
                # Explicit start node specified
                if start_node_id not in self.nodes:
                    raise ValueError(f"Start node {start_node_id} not found in flow.")
                execution_queue = deque([start_node_id])
                explicit_start_nodes.add(start_node_id)
            elif input_source:
                # Auto-detect which input node to start from based on source
                input_node_map = {
                    "chat": "chat_input",
                    "telegram": "telegram_input",
                    "discord": "discord_input",
                    "messaging": "messaging_input",
                }
                target_node_type = input_node_map.get(input_source)
                if target_node_type:
                    # Find all nodes of this type
                    start_nodes = [nid for nid, n in self.nodes.items() if n.get("nodeTypeId") == target_node_type]
                    if start_nodes:
                        execution_queue = deque(start_nodes)
                        explicit_start_nodes.update(start_nodes)
                    else:
                        # Fallback: find nodes with no incoming edges
                        execution_queue = deque([nid for nid in self.nodes if not any(c['to'] == nid for c in self.connections)])
                        explicit_start_nodes.update(execution_queue)
                else:
                    execution_queue = deque(self.execution_order)
            else:
                # No source specified, use topological order
                execution_queue = deque(self.execution_order)
            
            # Track how many times a node has run to prevent infinite loops
            node_run_counts = {node_id: 0 for node_id in self.nodes}
            max_loops = settings.get("max_node_loops", 1000)
            
            # Parallel set for O(1) membership checks instead of O(n) deque scan
            pending_nodes = set(execution_queue)
            
            while execution_queue:
                node_id = execution_queue.popleft()
                pending_nodes.discard(node_id)
                
                if max_loops > 0 and node_run_counts[node_id] >= max_loops:
                    logger.warning(f"Warning: Node {node_id} hit max execution limit ({max_loops}). Stopping branch.")
                    continue
                
                node_run_counts[node_id] += 1
                
                node_meta = self.nodes[node_id]
                module_id = node_meta['moduleId']
                node_type_id = node_meta['nodeTypeId']

                # 1. Determine Input Data (DAG Logic) - use precomputed incoming_edges for O(1) lookup
                incoming_edges = self.incoming_edges.get(node_id, [])
                
                # Bridge Execution: Process upstream bridge nodes before this node.
                # Independent nodes in the same bridge level run concurrently via
                # asyncio.gather; dependent levels remain sequential.
                # Example: Memory Recall + KB Query (parallel) → System Prompt → LLM Core
                if node_id in self.bridge_groups and node_id not in explicit_start_nodes:
                    bridge_levels = self._get_bridge_levels(node_id)
                    # Start with initial input (excluding the internal routing marker)
                    bridge_input = {k: v for k, v in initial_input.items() if k != "_input_source"} if isinstance(initial_input, dict) else initial_input

                    chain_stopped = False
                    for level in bridge_levels:
                        if chain_stopped:
                            break

                        # Skip nodes that are the target itself or already computed
                        to_run = [n for n in level if n != node_id and n not in node_outputs]
                        if not to_run:
                            continue

                        if len(to_run) == 1:
                            # ── Serial fast-path (single node, no gather overhead) ──
                            bridge_node_id = to_run[0]
                            try:
                                result = await self._run_bridge_node(bridge_node_id, bridge_input, stream_queue=stream_queue)
                            except Exception as bridge_err:
                                import traceback
                                logger.error(f"[Bridge Error] Node {bridge_node_id} failed: {bridge_err}")
                                logger.error(f"[Bridge Error] Traceback: {traceback.format_exc()}")
                                if settings.get("debug_mode"):
                                    bridge_meta = self.nodes[bridge_node_id]
                                    debug_logger.log(self.flow_id, bridge_node_id, bridge_meta.get('name', bridge_node_id), "bridge_error", {"error": str(bridge_err), "traceback": traceback.format_exc()})
                                chain_stopped = True
                                break

                            if result is None:
                                chain_stopped = True
                                break

                            node_outputs[bridge_node_id] = result
                            _accumulate_usage(result)
                            if isinstance(result, dict):
                                bridge_input = {**bridge_input, **result}

                        else:
                            # ── Parallel fast-path (multiple independent nodes) ──
                            # Deep-copy messages so parallel nodes each get an independent copy
                            def _make_input(base):
                                inp = base.copy()
                                if "messages" in inp and isinstance(inp["messages"], list):
                                    inp["messages"] = copy.deepcopy(inp["messages"])
                                return inp

                            results = await asyncio.gather(
                                *[self._run_bridge_node(n, _make_input(bridge_input), stream_queue=stream_queue) for n in to_run],
                                return_exceptions=True,
                            )

                            level_output = bridge_input.copy()
                            any_stopped = False
                            for bridge_node_id, result in zip(to_run, results):
                                if isinstance(result, Exception):
                                    import traceback
                                    logger.error(f"[Bridge Error] Node {bridge_node_id} failed: {result}")
                                    if settings.get("debug_mode"):
                                        bridge_meta = self.nodes[bridge_node_id]
                                        debug_logger.log(self.flow_id, bridge_node_id, bridge_meta.get('name', bridge_node_id), "bridge_error", {"error": str(result)})
                                    # One parallel node failed — skip its contribution but continue
                                    continue

                                if result is None:
                                    any_stopped = True
                                    continue

                                node_outputs[bridge_node_id] = result
                                _accumulate_usage(result)
                                if isinstance(result, dict):
                                    level_output.update(result)

                            if any_stopped:
                                chain_stopped = True
                                break

                            bridge_input = level_output

                
                if node_id in explicit_start_nodes:
                    # Explicit start node receives the initial input directly
                    if isinstance(initial_input, dict):
                        node_input = {k: v for k, v in initial_input.items() if k != "_input_source"}
                    else:
                        node_input = initial_input
                elif not incoming_edges:
                    # Source node: receives global initial input (only if no explicit start nodes defined)
                    if explicit_start_nodes:
                        # Skip - we're in selective mode and this isn't a chosen start node
                        node_outputs[node_id] = None
                        continue
                    if isinstance(initial_input, dict):
                        node_input = {k: v for k, v in initial_input.items() if k != "_input_source"}
                    else:
                        node_input = initial_input
                else:
                    # Gather outputs from parents (including parents of bridged peers)
                    parent_outputs = []
                    bridge_outputs = []
                    
                    # Identify all relevant incoming edges
                    relevant_edges = list(incoming_edges)
                    if node_id in self.bridge_groups:
                        peers = self.bridge_groups[node_id]
                        # Include outputs from bridge peer nodes that have already run
                        for peer_id in peers:
                            if peer_id != node_id and peer_id in node_outputs:
                                peer_output = node_outputs.get(peer_id)
                                if peer_output is not None:
                                    bridge_outputs.append(peer_output)
                            # Also get edges to peers
                            peer_edges = [c for c in self.connections if c['to'] == peer_id]
                            relevant_edges.extend(peer_edges)
                    
                    for edge in relevant_edges:
                        if edge['from'] in node_outputs:
                            p_out = node_outputs.get(edge['from'])
                            if p_out is not None:
                                parent_outputs.append(p_out)
                    
                    # Deduplicate: Remove parent outputs that are also in bridge_outputs
                    # (This happens when a node is both a bridge peer and has a regular connection)
                    if bridge_outputs:
                        bridge_keys = set()
                        for bo in bridge_outputs:
                            if isinstance(bo, dict):
                                bridge_keys.add(id(bo))
                        parent_outputs = [po for po in parent_outputs if id(po) not in bridge_keys]

                    if not parent_outputs and not bridge_outputs:
                        # Branch stopped (condition failed upstream) or no data
                        node_outputs[node_id] = None
                        continue
                    
                    # Merge inputs: parent outputs first, then bridge outputs (bridge injects context)
                    # IMPORTANT: Merge in REVERSE order so the last bridge (closest to target) wins
                    node_input = {}
                    for po in parent_outputs:
                        if isinstance(po, dict):
                            node_input.update(po)
                    
                    # Merge bridge outputs in REVERSE order so the last bridge (closest to target) wins
                    for po in reversed(bridge_outputs):
                        if isinstance(po, dict):
                            node_input.update(po)  # Bridge outputs take precedence

                    # Deep-copy the messages list so that in-place mutations by any node
                    # executor do not corrupt the shared parent output stored in node_outputs.
                    if "messages" in node_input and isinstance(node_input["messages"], list):
                        node_input["messages"] = copy.deepcopy(node_input["messages"])

                    if settings.get("debug_mode") and bridge_outputs:
                        import json
                        try:
                            bridge_msg_preview = json.dumps(bridge_outputs[0].get("messages", [])[:2])
                        except (json.JSONDecodeError, TypeError, KeyError):
                            # json.JSONDecodeError: Failed to serialize messages
                            # TypeError: messages is not JSON serializable
                            # KeyError: messages key missing
                            bridge_msg_preview = str(bridge_outputs[0].get("messages", "N/A"))[:200]
                        debug_logger.log(self.flow_id, node_id, node_meta['name'], "bridge_merge", {
                            "bridge_count": len(bridge_outputs),
                            "bridge_msg_preview": bridge_msg_preview
                        })

                if settings.get("debug_mode"):
                    debug_logger.log(self.flow_id, node_id, node_meta['name'], "input_resolved", {"input": node_input})

                try:
                    status, output = await self._run_node(node_id, node_input, stream_queue=stream_queue)

                    if status == "missing":
                        # Fallback: Pass through if no executor found (e.g. missing module)
                        node_outputs[node_id] = node_input
                        continue

                    # If receive returns None (e.g. Condition failed), we stop this branch
                    if status == "stop":
                        if settings.get("debug_mode"):
                            debug_logger.log(self.flow_id, node_id, node_meta['name'], "branch_stop", {"reason": "Node returned None"})
                        node_outputs[node_id] = None
                        continue

                    # Automatic Context Propagation:
                    # If input had 'messages' (chat history) and output is a dict that missed it,
                    # preserve it. This ensures chains like System -> LLM -> Router -> Tools -> LLM
                    # maintain the conversation context.
                    # Nodes can opt-out by setting _strip_messages: true in their output
                    if isinstance(node_input, dict) and "messages" in node_input:
                        if isinstance(output, dict) and "messages" not in output:
                            # Check if node intentionally stripped messages
                            if not output.get("_strip_messages", False):
                                output["messages"] = node_input["messages"]
                            # Remove the marker if present
                            output.pop("_strip_messages", None)

                    node_outputs[node_id] = output
                    _accumulate_usage(output)

                    if settings.get("debug_mode"):
                        debug_logger.log(self.flow_id, node_id, node_meta['name'], "end", {"output": output})
                    
                    # Routing Logic: Check if the node specified specific downstream targets
                    # Consume _route_targets at the point it's read - this ensures routing
                    # only applies to children of the node that set the routing, then clears
                    # so grandchildren run freely
                    allowed_targets = None
                    if isinstance(output, dict) and "_route_targets" in output:
                        allowed_targets = output.pop("_route_targets")  # Consume it
                        if settings.get("debug_mode"):
                            debug_logger.log(self.flow_id, node_id, node_meta['name'], "routing", {"targets": allowed_targets})
                    
                    # If successful, add downstream nodes to queue if they aren't already pending
                    # This enables loops: A -> B -> A
                    downstream_nodes = [c['to'] for c in self.connections if c['from'] == node_id]
                    
                    # Note: Bridge handling is now done in _compute_execution_order via topological sort.
                    # No need to re-add bridge peers at runtime.

                    for child_id in downstream_nodes:
                        # If routing is active, check if child is allowed
                        if allowed_targets is not None:
                            # Check if this child is in the allowed targets from the router
                            if child_id not in allowed_targets:
                                if settings.get("debug_mode"):
                                    child_name = self.nodes.get(child_id, {}).get('name', child_id)
                                    debug_logger.log(self.flow_id, node_id, node_meta['name'], "routing_skip", {"skipped": child_name})
                                continue

                        if child_id not in pending_nodes:
                            execution_queue.append(child_id)
                            pending_nodes.add(child_id)
                            if settings.get("debug_mode"):
                                child_name = self.nodes.get(child_id, {}).get('name', child_id)
                                debug_logger.log(self.flow_id, node_id, node_meta['name'], "queue_next", {"next": child_name})

                except (ImportError, AttributeError) as e:
                    # Module import issues or missing executor classes
                    if raise_errors:
                        raise
                    logger.warning(f"Warning: Could not find or use node logic for {module_id}/{node_type_id}. Error: {e}. Passing data through.")
                    node_outputs[node_id] = node_input
                except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
                    # Node execution errors: runtime failures, invalid values, type mismatches
                    # and a retryable failure whose attempts are exhausted
                    error_msg = f"Execution failed at node '{node_meta['name']}': {e}"
                    if settings.get("debug_mode"):
                        debug_logger.log(self.flow_id, node_id, node_meta['name'], "error", {"error": str(e), "error_type": type(e).__name__})
                    logger.exception(f"Error in FlowRunner: {error_msg}")
                    if raise_errors:
                        raise
                    # Return a structured error that the chat UI can display
                    return {"error": error_msg}

            
            if settings.get("debug_mode"):
                debug_logger.log(self.flow_id, "SYSTEM", "FlowRunner", "flow_complete", {})
                
            # Return the output of the last executed node in the list that isn't None
            for node_id in reversed(self.execution_order):
                out = node_outputs.get(node_id)
                if out is not None:
                    # Save episode state if tracking
                    if sm and episode_id:
                        try:
                            from core.session_manager import EpisodeState
                            # Determine final phase based on result
                            final_phase = EpisodeState.PHASE_COMPLETED
                            if isinstance(out, dict) and out.get("error"):
                                final_phase = EpisodeState.PHASE_FAILED
                            
                            sm.save_episode_by_id(
                                episode_id,
                                phase=final_phase,
                                plan=out.get("plan", []),
                                current_step=out.get("current_step", 0),
                                completed_steps=out.get("completed_steps", []),
                            )
                        except Exception as _exc:
                            logger.debug("ignored exception: %s", _exc)
                    if isinstance(out, dict) and total_usage.get("total_tokens", 0) > 0:
                        out["usage"] = total_usage
                    return out
            
            # Save episode state if tracking (no successful output)
            if sm and episode_id:
                try:
                    from core.session_manager import EpisodeState
                    sm.save_episode_by_id(
                        episode_id,
                        phase=EpisodeState.PHASE_COMPLETED,
                    )
                except Exception as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            if total_usage.get("total_tokens", 0) > 0:
                return {"usage": total_usage}
            return {}
        
        # Execute with optional timeout
        if timeout is not None and timeout > 0:
            try:
                return await asyncio.wait_for(run_impl(), timeout=timeout)
            except asyncio.TimeoutError:
                error_msg = f"Flow execution timed out after {timeout} seconds"
                if settings.get("debug_mode"):
                    debug_logger.log(self.flow_id, "SYSTEM", "FlowRunner", "flow_timeout", {"timeout": timeout})
                logger.error(f"[FlowRunner] {error_msg}")
                if raise_errors:
                    raise asyncio.TimeoutError(error_msg)
                return {"error": error_msg}
        else:
            return await run_impl()
