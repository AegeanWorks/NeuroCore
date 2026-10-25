"""Validated, versioned self-editing for NeuroCore flows.

The autonomous brain may rewrite its executable flow graph, but only through this module:
every proposal is checked for structure, endpoints and size before it reaches FlowManager,
and FlowManager snapshots the previous state so an edit can be rolled back. Agent edits are
acyclic by default; inline meta-flow composition may opt into bounded loops, which
FlowRunner already supports for normal flows.
"""

import copy
import uuid
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.flow_manager import FlowManager


class FlowEvolutionError(ValueError):
    """Raised when an evolution proposal would produce an invalid flow."""

    def __init__(self, message: str, errors: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.errors = list(errors or [])


class FlowEvolution:
    """Apply validated, versioned mutations to NeuroCore flows."""

    MAX_NODES = 200
    MAX_CONNECTIONS = 500

    def __init__(self, flow_manager: Optional[FlowManager], max_nodes: int = MAX_NODES,
                 max_connections: int = MAX_CONNECTIONS,
                 allowed_node_types: Optional[Set[Tuple[str, str]]] = None,
                 allowed_tools: Optional[Set[str]] = None):
        self.flow_manager = flow_manager
        self.max_nodes = max_nodes
        self.max_connections = max_connections
        self.allowed_node_types = (
            set(allowed_node_types) if allowed_node_types is not None else None
        )
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, name: str, nodes: Sequence[Dict[str, Any]],
                 connections: Sequence[Dict[str, Any]],
                 bridges: Optional[Sequence[Dict[str, Any]]] = None,
                 allowed_node_types: Optional[Set[Tuple[str, str]]] = None,
                 allow_cycles: bool = False) -> List[str]:
        """Return every reason the proposed flow must not be saved.

        ``allow_cycles`` is only for composing existing graphs (the default chat flow and
        user-created flows contain bounded loops). Agent-created and agent-edited flows keep
        the acyclic policy.
        """
        allowed = (
            self.allowed_node_types if allowed_node_types is None
            else set(allowed_node_types)
        )
        errors: List[str] = []
        if not isinstance(name, str) or not name.strip():
            errors.append("flow name must be a non-empty string")
        if not isinstance(nodes, (list, tuple)):
            errors.append("nodes must be a list or tuple")
            nodes = []
        if not nodes:
            errors.append("flow must have at least one node")
        if len(nodes) > self.max_nodes:
            errors.append(f"flow has {len(nodes)} nodes, above the max of {self.max_nodes}")

        node_ids = set()
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                errors.append(f"node {index} must be an object")
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id.strip():
                errors.append(f"node {index} needs a non-empty string id")
            elif node_id in node_ids:
                errors.append(f"duplicate node id '{node_id}'")
            else:
                node_ids.add(node_id)
            module_id = node.get("moduleId")
            node_type = node.get("nodeTypeId")
            for key, value in (("moduleId", module_id), ("nodeTypeId", node_type)):
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"node '{node_id or index}' needs a non-empty {key}")
            if (allowed is not None
                    and isinstance(module_id, str) and isinstance(node_type, str)
                    and (module_id, node_type) not in allowed):
                errors.append(
                    f"node '{node_id or index}' type {module_id}:{node_type} is not allowed"
                )
            if "config" in node and not isinstance(node["config"], dict):
                errors.append(f"node '{node_id or index}' config must be an object")
            if self.allowed_tools is not None and node_type == "tool_dispatcher":
                config = node.get("config") if isinstance(node.get("config"), dict) else {}
                configured = config.get("allowed_tools")
                if not isinstance(configured, list):
                    errors.append(
                        f"node '{node_id or index}' tool_dispatcher needs an allowed_tools list"
                    )
                else:
                    disallowed = sorted(set(configured) - self.allowed_tools)
                    if disallowed:
                        errors.append(
                            f"node '{node_id or index}' uses disallowed tools: "
                            f"{', '.join(str(item) for item in disallowed)}"
                        )

        if not isinstance(connections, (list, tuple)):
            errors.append("connections must be a list or tuple")
            connections = []
        if len(connections) > self.max_connections:
            errors.append(
                f"flow has {len(connections)} connections, above the max of "
                f"{self.max_connections}"
            )
        errors.extend(self._validate_edges("connection", connections, node_ids))

        if bridges is None:
            bridges = []
        if not isinstance(bridges, (list, tuple)):
            errors.append("bridges must be a list or tuple")
            bridges = []
        errors.extend(self._validate_edges("bridge", bridges, node_ids))

        if not errors and not allow_cycles:
            cycle = self._find_cycle(node_ids, list(connections) + list(bridges))
            if cycle:
                errors.append(f"flow graph contains a cycle among: {', '.join(cycle)}")
        return errors

    def _validate_edges(self, label: str, edges: Sequence[Any], node_ids: set) -> List[str]:
        errors: List[str] = []
        seen = set()
        if not isinstance(edges, (list, tuple)):
            return [f"{label}s must be a list or tuple"]
        for index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(f"{label} {index} must be an object")
                continue
            source = edge.get("from")
            target = edge.get("to")
            if not isinstance(source, str) or not source.strip():
                errors.append(f"{label} {index} needs a non-empty 'from'")
            if not isinstance(target, str) or not target.strip():
                errors.append(f"{label} {index} needs a non-empty 'to'")
            if source == target and source:
                errors.append(f"{label} {index} is a self-loop on '{source}'")
            for endpoint in (source, target):
                if isinstance(endpoint, str) and endpoint and endpoint not in node_ids:
                    errors.append(f"{label} {index} references unknown node '{endpoint}'")
            key = (source, target)
            if key in seen:
                errors.append(f"{label} {index} duplicates {source} -> {target}")
            seen.add(key)
        return errors

    def _find_cycle(self, node_ids: set, edges: Sequence[Dict[str, Any]]) -> Optional[List[str]]:
        graph = {node_id: [] for node_id in node_ids}
        indegree = {node_id: 0 for node_id in node_ids}
        for edge in edges:
            source = edge.get("from")
            target = edge.get("to")
            if source in graph and target in graph:
                graph[source].append(target)
                indegree[target] += 1
        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = []
        while ready:
            node_id = ready.pop()
            visited.append(node_id)
            for target in graph[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if len(visited) == len(graph):
            return None
        return sorted(node_id for node_id, degree in indegree.items() if degree > 0)

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def create(self, name: str, nodes: Sequence[Dict[str, Any]],
               connections: Sequence[Dict[str, Any]],
               bridges: Optional[Sequence[Dict[str, Any]]] = None,
               metadata: Optional[Dict[str, Any]] = None,
               flow_id: Optional[str] = None,
               allow_cycles: bool = False) -> Dict[str, Any]:
        """Validate and persist a new flow."""
        if self.flow_manager is None:
            raise FlowEvolutionError("a flow manager is required to create a flow", [])
        errors = self.validate(
            name, nodes, connections, bridges, allow_cycles=allow_cycles
        )
        if errors:
            raise FlowEvolutionError("flow validation failed", errors)
        with self.flow_manager.lock:
            if flow_id is not None and self.flow_manager.get_flow(flow_id) is not None:
                raise FlowEvolutionError(f"flow '{flow_id}' already exists", [])
            return self.flow_manager.save_flow(
                name.strip(),
                copy.deepcopy(list(nodes)),
                copy.deepcopy(list(connections)),
                copy.deepcopy(list(bridges or [])),
                flow_id=flow_id,
                metadata=metadata,
            )

    def preview(self, flow_id: str,
                operations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply operations to a copy and validate without touching the saved flow."""
        with self.flow_manager.lock:
            flow = self.flow_manager.get_flow(flow_id)
            if flow is None:
                raise FlowEvolutionError(f"flow '{flow_id}' does not exist", [])
            prepared = self._prepare_mutation(flow, operations)
            return {
                "flow_id": flow_id,
                "name": prepared["name"],
                "nodes": prepared["nodes"],
                "connections": prepared["connections"],
                "bridges": prepared["bridges"],
            }

    def _prepare_mutation(self, flow: Dict[str, Any],
                          operations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        name = flow.get("name", "")
        nodes = copy.deepcopy(flow.get("nodes", []))
        connections = copy.deepcopy(flow.get("connections", []))
        bridges = copy.deepcopy(flow.get("bridges", []))
        for operation in operations:
            name = self._apply_operation(name, nodes, connections, bridges, operation)

        allowed = self.allowed_node_types
        if allowed is not None:
            allowed = set(allowed)
            for node in flow.get("nodes", []):
                pair = (node.get("moduleId"), node.get("nodeTypeId"))
                if isinstance(pair[0], str) and isinstance(pair[1], str):
                    allowed.add(pair)

        # Acyclic agent flows stay acyclic. A composed meta-flow may already contain bounded
        # loops and must remain editable; otherwise compose() could create a flow the owning
        # agent is forbidden to update.
        existing_node_ids = {
            node.get("id") for node in flow.get("nodes", [])
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        had_cycle = bool(self._find_cycle(
            existing_node_ids,
            list(flow.get("connections", [])) + list(flow.get("bridges", [])),
        ))
        errors = self.validate(
            name, nodes, connections, bridges, allowed_node_types=allowed,
            allow_cycles=had_cycle,
        )
        if errors:
            raise FlowEvolutionError("flow validation failed", errors)
        return {
            "name": name,
            "nodes": nodes,
            "connections": connections,
            "bridges": bridges,
        }

    def mutate(self, flow_id: str, operations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply operations atomically; the previous state becomes a version snapshot."""
        with self.flow_manager.lock:
            flow = self.flow_manager.get_flow(flow_id)
            if flow is None:
                raise FlowEvolutionError(f"flow '{flow_id}' does not exist", [])
            prepared = self._prepare_mutation(flow, operations)
            return self.flow_manager.save_flow(
                prepared["name"],
                prepared["nodes"],
                prepared["connections"],
                prepared["bridges"],
                flow_id=flow_id,
                metadata=copy.deepcopy(flow.get("metadata")),
                reverted_nodes=copy.deepcopy(flow.get("reverted_nodes")),
                validate=False,
            )

    def _apply_operation(self, name: str, nodes: List[Dict[str, Any]],
                         connections: List[Dict[str, Any]],
                         bridges: List[Dict[str, Any]],
                         operation: Any) -> str:
        if not isinstance(operation, dict):
            raise FlowEvolutionError("each operation must be an object", [])
        kind = operation.get("op")
        if kind == "add_node":
            node = copy.deepcopy(operation.get("node"))
            if not isinstance(node, dict):
                raise FlowEvolutionError("add_node needs a node object", [])
            if not node.get("id"):
                node["id"] = f"node-{uuid.uuid4().hex[:8]}"
            nodes.append(node)
        elif kind == "update_node":
            node_id = operation.get("node_id")
            changes = operation.get("changes")
            if not isinstance(changes, dict):
                raise FlowEvolutionError("update_node needs a changes object", [])
            if "id" in changes and changes["id"] != node_id:
                raise FlowEvolutionError("update_node cannot change a node id", [])
            target = next((node for node in nodes if node.get("id") == node_id), None)
            if target is None:
                raise FlowEvolutionError(f"node '{node_id}' does not exist", [])
            target.update({key: copy.deepcopy(value) for key, value in changes.items()
                           if key != "id"})
        elif kind == "remove_node":
            node_id = operation.get("node_id")
            if not any(node.get("id") == node_id for node in nodes):
                raise FlowEvolutionError(f"node '{node_id}' does not exist", [])
            nodes[:] = [node for node in nodes if node.get("id") != node_id]
            connections[:] = [edge for edge in connections
                              if node_id not in (edge.get("from"), edge.get("to"))]
            bridges[:] = [edge for edge in bridges
                          if node_id not in (edge.get("from"), edge.get("to"))]
        elif kind == "connect":
            connections.append({
                "from": operation.get("from"),
                "to": operation.get("to"),
            })
        elif kind == "disconnect":
            source = operation.get("from")
            target = operation.get("to")
            remaining = [edge for edge in connections
                         if not (edge.get("from") == source and edge.get("to") == target)]
            if len(remaining) == len(connections):
                raise FlowEvolutionError(f"connection {source} -> {target} does not exist", [])
            connections[:] = remaining
        elif kind == "rename":
            new_name = operation.get("name")
            if not isinstance(new_name, str) or not new_name.strip():
                raise FlowEvolutionError("rename needs a non-empty name", [])
            name = new_name.strip()
        else:
            message = f"unknown operation '{kind}'"
            raise FlowEvolutionError(message, [message])
        return name

    def history(self, flow_id: str) -> List[Dict[str, Any]]:
        """Version snapshots for a flow, newest first."""
        return self.flow_manager.get_versions(flow_id)

    def rollback(self, flow_id: str, version: int) -> Dict[str, Any]:
        """Restore a snapshot; raises when the flow or version is missing."""
        restored = self.flow_manager.rollback_version(flow_id, version)
        if restored is None:
            raise FlowEvolutionError(
                f"flow '{flow_id}' has no version {version}", []
            )
        return restored
