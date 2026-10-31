"""Deterministic pre-flight flow analysis: structure, effects and a cost envelope.

The simulator never executes a node, calls a module or touches the network. It answers the
questions an operator should ask before a run: is every connection real, is every node
reachable, which nodes leave the process, and can this flow exceed the per-run budget? Findings
are stable and machine-readable so the editor and the improvement engine can gate on them
(ADR-043).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.execution_budget import budget_from_flow

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_BLOCKING = "blocking"

LLM_MODULES = {"llm_module"}
SUBSET_MODULES = {"flow", "subflow"}
EXTERNAL_MODULES = {
    "tools", "messaging_bridge", "calendar", "email", "memory", "knowledge_base",
    "research", "autonomous_agent",
}
_WRITE_HINTS = ("save", "write", "add", "create", "send", "delete", "update", "upload", "post")
_READ_HINTS = ("recall", "search", "query", "read", "get", "list", "input", "output")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    node_id: Optional[str] = None
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
            "fix": self.fix,
        }


@dataclass
class SimulationReport:
    flow_id: str
    findings: List[Finding] = field(default_factory=list)
    cost: Dict[str, Any] = field(default_factory=dict)
    effects: List[Dict[str, str]] = field(default_factory=list)
    cycles: List[List[str]] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(finding.severity == SEVERITY_BLOCKING for finding in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        score = 100 if self.blocking else min(
            100, 10 * sum(1 for f in self.findings if f.severity == SEVERITY_WARNING)
            + 2 * len(self.effects),
        )
        return {
            "flow_id": self.flow_id,
            "blocking": self.blocking,
            "score": score,
            "summary": (
                f"{len(self.findings)} finding(s); "
                f"{'blocked' if self.blocking else 'runnable'}"
            ),
            "findings": [finding.to_dict() for finding in self.findings],
            "cost": self.cost,
            "effects": self.effects,
            "cycles": self.cycles,
        }


def _node_effects(node: Dict[str, Any]) -> Set[str]:
    module_id = str(node.get("moduleId") or "")
    node_type = str(node.get("nodeTypeId") or "").lower()
    if module_id in LLM_MODULES or "llm" in node_type:
        return {"llm"}
    if module_id in SUBSET_MODULES or "subflow" in node_type:
        return {"subflow"}
    if module_id in EXTERNAL_MODULES:
        if any(hint in node_type for hint in _READ_HINTS) and not any(
                hint in node_type for hint in _WRITE_HINTS):
            return {"read"}
        return {"write"}
    if any(hint in node_type for hint in _WRITE_HINTS):
        return {"write"}
    return set()


def _strongly_connected(nodes: Iterable[str], adjacency: Dict[str, List[str]]) -> List[List[str]]:
    """Tarjan's SCCs with at least two nodes, or one node pointing at itself."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    counter = [0]
    result: List[List[str]] = []

    def visit(start: str) -> None:
        work = [(start, 0)]
        while work:
            node, child_index = work.pop()
            if child_index == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            children = adjacency.get(node, [])
            if child_index < len(children):
                child = children[child_index]
                work.append((node, child_index + 1))
                if child not in index:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                component.sort()
                if len(component) > 1 or node in adjacency.get(node, []):
                    result.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    for node in sorted(set(nodes) | set(adjacency)):
        if node not in index:
            visit(node)
    return result


def _settings_int(key: str, default: int) -> int:
    try:
        from core.settings import settings

        return max(1, int(settings.get(key, default)))
    except (TypeError, ValueError, ImportError, OSError):
        return default


def node_type_catalogue(modules: Any) -> Set[Tuple[str, str]]:
    """Installed (module_id, node_type_id) pairs from module manifests.

    Manifests expose ``provides_nodes``; the older ``node_types`` key is accepted for callers with
    a custom catalogue. Reading only one of them silently disabled the unknown-type check
    (ADR-053).
    """
    catalogue: Set[Tuple[str, str]] = set()
    if not isinstance(modules, dict):
        return catalogue
    for module_id, manifest in modules.items():
        if not isinstance(manifest, dict):
            continue
        if not manifest.get("enabled", False):
            # The loader only starts enabled modules; a disabled manifest's node types are not
            # installed even though its file still declares them.
            continue
        provided = manifest.get("provides_nodes") or manifest.get("node_types") or []
        if manifest.get("is_flow_node"):
            # Single-node modules (llm_module, system_prompt) declare no provides_nodes and use
            # their own id as the node type; omitting them made every real flow look unknown.
            catalogue.add((str(module_id), str(module_id)))
        for node_type in provided:
            node_id = node_type.get("id") if isinstance(node_type, dict) else node_type
            if node_id:
                catalogue.add((str(module_id), str(node_id)))
    return catalogue


def simulate_flow(flow: Dict[str, Any],
                  known_node_types: Optional[Set[Tuple[str, str]]] = None) -> Dict[str, Any]:
    """Analyse a flow without running it and return a JSON-safe report."""
    report = SimulationReport(flow_id=str((flow or {}).get("id") or ""))
    nodes = {
        str(node.get("id")): node
        for node in ((flow or {}).get("nodes") or [])
        if isinstance(node, dict) and node.get("id")
    }
    connections = [
        connection for connection in ((flow or {}).get("connections") or [])
        if isinstance(connection, dict)
    ]
    bridges = [bridge for bridge in ((flow or {}).get("bridges") or [])
               if isinstance(bridge, dict)]

    if not nodes:
        report.findings.append(Finding(
            SEVERITY_BLOCKING, "empty_flow", "the flow has no nodes",
            fix="add at least one input and one output node",
        ))
        return report.to_dict()

    adjacency: Dict[str, List[str]] = {node_id: [] for node_id in nodes}
    for connection in connections:
        source, target = str(connection.get("from")), str(connection.get("to"))
        if source not in nodes or target not in nodes:
            report.findings.append(Finding(
                SEVERITY_BLOCKING, "dangling_connection",
                f"connection {source!r} -> {target!r} names a missing node",
                node_id=source if source not in nodes else target,
                fix="remove the connection or restore the node",
            ))
            continue
        adjacency.setdefault(source, []).append(target)
    for bridge in bridges:
        source, target = str(bridge.get("from")), str(bridge.get("to"))
        if source in nodes and target in nodes:
            adjacency.setdefault(source, []).append(target)
        else:
            missing = source if source not in nodes else target
            report.findings.append(Finding(
                SEVERITY_BLOCKING, "dangling_bridge",
                f"bridge {source!r} -> {target!r} names a missing node",
                node_id=missing,
                fix="remove the bridge or restore the node",
            ))

    if known_node_types:
        for node_id, node in nodes.items():
            pair = (str(node.get("moduleId") or ""), str(node.get("nodeTypeId") or ""))
            if pair not in known_node_types:
                report.findings.append(Finding(
                    SEVERITY_BLOCKING, "unknown_node_type",
                    f"node {node_id!r} uses {pair[0]}/{pair[1]}, which is not installed",
                    node_id=node_id,
                    fix="install the module or choose an available node type",
                ))

    incoming = {node_id: 0 for node_id in nodes}
    for targets in adjacency.values():
        for target in targets:
            if target in incoming:
                incoming[target] += 1
    for node_id in sorted(nodes):
        has_incoming = incoming.get(node_id, 0) > 0
        has_outgoing = bool(adjacency.get(node_id))
        if not has_incoming and not has_outgoing:
            report.findings.append(Finding(
                SEVERITY_WARNING, "isolated_node",
                f"node {node_id!r} has no connections",
                node_id=node_id,
                fix="connect it or remove it",
            ))

    report.cycles = _strongly_connected(nodes, adjacency)
    for cycle in report.cycles:
        report.findings.append(Finding(
            SEVERITY_WARNING, "cycle",
            f"cycle through {', '.join(cycle)}; the runtime caps each node's executions",
            node_id=cycle[0],
            fix="confirm the loop has a conditional exit and the budget covers it",
        ))

    effects = []
    llm_nodes = tool_nodes = 0
    for node_id, node in nodes.items():
        kinds = _node_effects(node)
        if "llm" in kinds:
            llm_nodes += 1
            effects.append({"node_id": node_id, "effect": "llm"})
        if "write" in kinds:
            if str(node.get("moduleId") or "") == "tools":
                tool_nodes += 1
            effects.append({"node_id": node_id, "effect": "write"})
        if "subflow" in kinds:
            effects.append({"node_id": node_id, "effect": "subflow"})
    report.effects = effects

    repeats = _settings_int("max_node_loops", 1000) if report.cycles else 1
    max_tokens = _settings_int("max_tokens", 4096)
    envelope = {
        "nodes": len(nodes) * repeats,
        "llm_calls": llm_nodes * repeats,
        "tokens": llm_nodes * repeats * max_tokens,
        "tool_calls": tool_nodes * repeats,
        "loop_repeats_assumed": repeats,
    }
    single_pass = {
        "nodes": len(nodes),
        "llm_calls": llm_nodes,
        "tokens": llm_nodes * max_tokens,
        "tool_calls": tool_nodes,
    }
    limits = budget_from_flow(flow).limits
    checks = (
        ("max_nodes", "nodes", "node executions"),
        ("max_llm_calls", "llm_calls", "LLM calls"),
        ("max_tokens", "tokens", "tokens"),
        ("max_tool_calls", "tool_calls", "tool calls"),
    )
    # One pass must fit; otherwise the flow cannot run at all. A cycle only *may* repeat into
    # the cap, which the runtime stops and the operator should see as a warning.
    for limit_key, cost_key, label in checks:
        if single_pass[cost_key] > limits[limit_key]:
            report.findings.append(Finding(
                SEVERITY_BLOCKING, f"budget_{cost_key}",
                f"one pass needs {single_pass[cost_key]} {label}, above the limit of "
                f"{limits[limit_key]}",
                fix="split the flow, reduce its nodes, or raise the flow budget",
            ))
        elif envelope[cost_key] > limits[limit_key]:
            report.findings.append(Finding(
                SEVERITY_WARNING, f"cycle_budget_{cost_key}",
                f"a full loop could reach {envelope[cost_key]} {label}; the run budget will "
                f"stop it at {limits[limit_key]}",
                fix="add a conditional exit or lower max_node_loops for this flow",
            ))
    report.cost = {"envelope": envelope, "single_pass": single_pass, "limits": limits}
    return report.to_dict()
