"""Compose small flows into meta-flows.

A ganglion is a member flow. A pathway is a cross-member connection. The composer
namespaces member nodes, rewires their internal edges, adds the pathways, and asks
FlowEvolution to validate and save one executable flow.
"""

import copy
from typing import Any, Dict, List, Optional, Sequence

from core.flow_evolution import FlowEvolution, FlowEvolutionError
from core.flow_manager import FlowManager


class MetaFlowError(ValueError):
    """Raised when member flows or pathways cannot form a valid meta-flow."""


class MetaFlowComposer:
    """Merge member flows (ganglia) into one validated flow with pathways."""

    # Config fields that store node ids and therefore need namespacing with the graph.
    NODE_REFERENCE_KEYS = frozenset({
        "true_branches", "false_branches", "rlm_branch", "standard_branch",
    })

    def __init__(self, flow_manager: FlowManager,
                 evolution: Optional[FlowEvolution] = None):
        self.flow_manager = flow_manager
        self.evolution = evolution or FlowEvolution(flow_manager)

    def compose(self, name: str, member_flow_ids: Sequence[str],
                pathways: Optional[Sequence[Dict[str, Any]]] = None,
                metadata: Optional[Dict[str, Any]] = None,
                flow_id: Optional[str] = None) -> Dict[str, Any]:
        """Compose members into a meta-flow; the result is a normal saved flow."""
        if not member_flow_ids:
            raise MetaFlowError("a meta-flow needs at least one member flow")
        if metadata is not None and not isinstance(metadata, dict):
            raise MetaFlowError("metadata must be an object")

        members = []
        for member_id in member_flow_ids:
            flow = self.flow_manager.get_flow(member_id)
            if flow is None:
                raise MetaFlowError(f"member flow '{member_id}' does not exist")
            members.append(flow)

        if pathways is None:
            pathways = self._auto_pathways(members)
        if not isinstance(pathways, (list, tuple)):
            raise MetaFlowError("pathways must be a list")

        nodes: List[Dict[str, Any]] = []
        connections: List[Dict[str, Any]] = []
        bridges: List[Dict[str, Any]] = []
        node_map: Dict[tuple, str] = {}
        ganglia: List[Dict[str, Any]] = []

        for index, flow in enumerate(members):
            namespaced = []
            member_map = {}
            for node in flow.get("nodes", []):
                original = node.get("id")
                if not isinstance(original, str) or not original:
                    raise MetaFlowError(
                        f"member flow '{member_flow_ids[index]}' has a node without an id"
                    )
                member_map[original] = f"g{index}__{original}"

            for node in flow.get("nodes", []):
                original = node["id"]
                clone = copy.deepcopy(node)
                clone["id"] = member_map[original]
                clone["ganglion"] = index
                if isinstance(clone.get("config"), dict):
                    clone["config"] = self._rewrite_config(
                        clone["config"], index, member_map
                    )
                nodes.append(clone)
                node_map[(index, original)] = clone["id"]
                namespaced.append(clone["id"])

            for edge in flow.get("connections", []):
                connections.append(self._rewire(index, edge, node_map, "connection"))
            for bridge in flow.get("bridges", []):
                bridges.append(self._rewire(index, bridge, node_map, "bridge"))
            ganglia.append({
                "index": index,
                "flow_id": member_flow_ids[index],
                "nodes": namespaced,
            })

        normalized = []
        for pathway in pathways:
            if not isinstance(pathway, dict):
                raise MetaFlowError("each pathway must be an object")
            source = node_map.get((pathway.get("from_member"), pathway.get("from_node")))
            target = node_map.get((pathway.get("to_member"), pathway.get("to_node")))
            if source is None or target is None:
                raise MetaFlowError(
                    "pathway references a member or node that does not exist"
                )
            connections.append({"from": source, "to": target})
            normalized.append({
                "from_member": pathway["from_member"],
                "from_node": pathway["from_node"],
                "to_member": pathway["to_member"],
                "to_node": pathway["to_node"],
            })

        self._assert_acyclic_pathways(normalized, len(members))

        combined_metadata = dict(metadata or {})
        combined_metadata.update({
            "kind": "meta_flow",
            "ganglia": ganglia,
            "pathways": normalized,
        })
        try:
            return self.evolution.create(
                name,
                nodes,
                connections,
                bridges,
                metadata=combined_metadata,
                flow_id=flow_id,
                allow_cycles=True,
            )
        except FlowEvolutionError as exc:
            raise MetaFlowError(f"meta-flow validation failed: {exc.errors}") from exc

    def compose_live(self, name: str, member_flow_ids: Sequence[str],
                     pathways: Optional[Sequence[Dict[str, Any]]] = None,
                     metadata: Optional[Dict[str, Any]] = None,
                     flow_id: Optional[str] = None) -> Dict[str, Any]:
        """Compose live references: one meta:subflow node per ganglion."""
        if not member_flow_ids:
            raise MetaFlowError("a meta-flow needs at least one member flow")
        if metadata is not None and not isinstance(metadata, dict):
            raise MetaFlowError("metadata must be an object")

        members = []
        for member_id in member_flow_ids:
            flow = self.flow_manager.get_flow(member_id)
            if flow is None:
                raise MetaFlowError(f"member flow '{member_id}' does not exist")
            members.append(flow)

        if pathways is None:
            pathways = [
                {"from_member": index, "to_member": index + 1}
                for index in range(len(members) - 1)
            ]
        if not isinstance(pathways, (list, tuple)):
            raise MetaFlowError("pathways must be a list")

        nodes = [
            {
                "id": f"g{index}",
                "moduleId": "meta",
                "nodeTypeId": "subflow",
                "name": members[index].get("name", f"Ganglion {index}"),
                "config": {"flow_id": member_id},
            }
            for index, member_id in enumerate(member_flow_ids)
        ]
        connections = []
        normalized = []
        for pathway in pathways:
            if not isinstance(pathway, dict):
                raise MetaFlowError("each pathway must be an object")
            source = pathway.get("from_member")
            target = pathway.get("to_member")
            if (not isinstance(source, int) or not isinstance(target, int)
                    or source < 0 or target < 0
                    or source >= len(members) or target >= len(members)):
                raise MetaFlowError("pathway references a member that does not exist")
            if source == target:
                raise MetaFlowError("a pathway cannot self-loop")
            connections.append({"from": f"g{source}", "to": f"g{target}"})
            normalized.append({"from_member": source, "to_member": target})

        combined_metadata = dict(metadata or {})
        combined_metadata.update({
            "kind": "meta_flow",
            "mode": "live",
            "ganglia": [
                {"index": index, "flow_id": flow_id_value, "node_id": f"g{index}"}
                for index, flow_id_value in enumerate(member_flow_ids)
            ],
            "pathways": normalized,
        })
        try:
            return self.evolution.create(
                name, nodes, connections, [], metadata=combined_metadata,
                flow_id=flow_id,
            )
        except FlowEvolutionError as exc:
            raise MetaFlowError(f"meta-flow validation failed: {exc.errors}") from exc

    @staticmethod
    def _assert_acyclic_pathways(pathways: Sequence[Dict[str, Any]],
                                 member_count: int) -> None:
        """Members may loop internally, but pathways between ganglia must stay acyclic."""
        graph = {index: [] for index in range(member_count)}
        indegree = {index: 0 for index in range(member_count)}
        for pathway in pathways:
            source = pathway["from_member"]
            target = pathway["to_member"]
            graph[source].append(target)
            indegree[target] += 1

        ready = [index for index, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            index = ready.pop()
            visited += 1
            for target in graph[index]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != member_count:
            raise MetaFlowError("pathway graph contains a cycle")

    def _rewrite_config(self, value: Any, member_index: int,
                        member_map: Dict[str, str]) -> Any:
        """Namespace node ids referenced from node configuration."""
        if isinstance(value, dict):
            rewritten = {}
            for key, item in value.items():
                if key in self.NODE_REFERENCE_KEYS:
                    rewritten[key] = self._rewrite_reference(item, member_map)
                else:
                    rewritten[key] = self._rewrite_config(
                        item, member_index, member_map
                    )
            return rewritten
        if isinstance(value, list):
            return [
                self._rewrite_config(item, member_index, member_map)
                for item in value
            ]
        return value

    @staticmethod
    def _rewrite_reference(value: Any, member_map: Dict[str, str]) -> Any:
        if isinstance(value, str):
            return member_map.get(value, value)
        if isinstance(value, list):
            return [
                member_map.get(item, item) if isinstance(item, str) else item
                for item in value
            ]
        return value

    def _rewire(self, member_index: int, edge: Any,
                node_map: Dict[tuple, str], label: str) -> Dict[str, Any]:
        if not isinstance(edge, dict):
            raise MetaFlowError(f"{label} in member {member_index} must be an object")
        source = node_map.get((member_index, edge.get("from")))
        target = node_map.get((member_index, edge.get("to")))
        if source is None or target is None:
            raise MetaFlowError(f"{label} in member {member_index} has unknown endpoints")
        return {"from": source, "to": target}

    def _auto_pathways(self, members: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        pathways = []
        for index in range(len(members) - 1):
            _, exits = self._boundaries(members[index])
            entries, _ = self._boundaries(members[index + 1])
            if len(exits) != 1 or len(entries) != 1:
                raise MetaFlowError(
                    "members with multiple entry/exit nodes need explicit pathways"
                )
            pathways.append({
                "from_member": index,
                "from_node": exits[0],
                "to_member": index + 1,
                "to_node": entries[0],
            })
        return pathways

    @staticmethod
    def _boundaries(flow: Dict[str, Any]):
        ids = [node.get("id") for node in flow.get("nodes", [])
               if isinstance(node.get("id"), str) and node["id"]]
        incoming = {edge.get("to") for edge in flow.get("connections", [])}
        outgoing = {edge.get("from") for edge in flow.get("connections", [])}
        entries = [node_id for node_id in ids if node_id not in incoming]
        exits = [node_id for node_id in ids if node_id not in outgoing]
        return entries, exits
