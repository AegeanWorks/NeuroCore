"""Role/task delegation contract with capability bounds (IDEA-1242).

A delegated sub-task used to be an implicit call: no expected shape, no bound on the tools the
delegatee may use, no declared context, and no limit on how deep delegation could nest. This
module makes the contract explicit, so a delegatee can refuse an out-of-contract tool with a typed
error and a parent can reject an output that does not match the promised shape.

The contract is data, not behaviour: it is validated before the delegatee runs and the caller is
expected to pass it to the delegatee. ``CapabilityRefused`` carries the Why/Fix text and is never
retried by this module.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

MAX_DELEGATION_DEPTH = 4
DEFAULT_TIMEOUT = 300.0
DEFAULT_RETRY = 0


class DelegationError(ValueError):
    """An invalid contract; the message carries Why and Fix."""


class CapabilityRefused(PermissionError):
    """A delegatee tried a tool its contract does not allow."""

    def __init__(self, role: str, tool: str, allowed: Iterable[str]) -> None:
        allowed_list = ", ".join(sorted(allowed)) or "(none)"
        super().__init__(
            f"role '{role}' may not use tool '{tool}'. Why: the delegation contract bounds the "
            f"delegatee to {allowed_list}. Fix: add '{tool}' to capability_set, or drop the call."
        )
        self.role = role
        self.tool = tool
        self.allowed = frozenset(allowed)


class DelegationDepthExceeded(DelegationError):
    def __init__(self, depth: int, limit: int) -> None:
        super().__init__(
            f"delegation depth {depth} exceeds the limit {limit}. Why: unbounded nested "
            "delegation can recurse without end. Fix: flatten the task or raise "
            "max_delegation_depth deliberately."
        )


@dataclass(frozen=True)
class DelegationContract:
    task_id: str
    delegatee_role: str
    capability_set: Sequence[str] = field(default_factory=tuple)
    expected_output_schema: Dict[str, type] = field(default_factory=dict)
    context_task_ids: Sequence[str] = field(default_factory=tuple)
    timeout: float = DEFAULT_TIMEOUT
    retry: int = DEFAULT_RETRY
    depth: int = 0

    def validate(self) -> "DelegationContract":
        if not str(self.task_id).strip():
            raise DelegationError(
                "task_id is required. Why: a contract without an id cannot be traced or retried. "
                "Fix: pass the parent's step id."
            )
        if not str(self.delegatee_role).strip():
            raise DelegationError(
                "delegatee_role is required. Why: a task must name who executes it. "
                "Fix: pass a role such as 'researcher'."
            )
        if self.depth > MAX_DELEGATION_DEPTH:
            raise DelegationDepthExceeded(self.depth, MAX_DELEGATION_DEPTH)
        if self.timeout <= 0:
            raise DelegationError(
                "timeout must be positive. Why: a zero timeout would abort immediately. "
                "Fix: use seconds, e.g. 300."
            )
        if self.retry < 0:
            raise DelegationError("retry cannot be negative. Fix: use 0 or a positive count.")
        if not isinstance(self.expected_output_schema, dict):
            raise DelegationError(
                "expected_output_schema must be a mapping of field -> type. "
                "Fix: use {\"summary\": str} or leave it empty."
            )
        for field_name, expected in self.expected_output_schema.items():
            if not isinstance(field_name, str) or not field_name:
                raise DelegationError("expected_output_schema keys must be field names.")
            if not isinstance(expected, type):
                raise DelegationError(
                    f"expected_output_schema['{field_name}'] must be a type. "
                    "Why: the acceptance check compares isinstance. Fix: use str, int, list, ..."
                )
        return self

    def allows(self, tool: str) -> bool:
        return str(tool) in set(self.capability_set)

    def require_tool(self, tool: str) -> None:
        if not self.allows(tool):
            raise CapabilityRefused(self.delegatee_role, str(tool), self.capability_set)

    def check_output(self, output: Any) -> List[str]:
        """Missing or wrongly typed fields; an empty list means the output was accepted."""
        if not self.expected_output_schema:
            return []
        if not isinstance(output, dict):
            return ["output is not a mapping"]
        problems: List[str] = []
        for field_name, expected in self.expected_output_schema.items():
            if field_name not in output:
                problems.append(f"missing field '{field_name}'")
            elif not isinstance(output[field_name], expected):
                problems.append(
                    f"field '{field_name}' is {type(output[field_name]).__name__}, "
                    f"expected {expected.__name__}"
                )
        return problems

    def child(self, task_id: str, delegatee_role: str, capability_set: Sequence[str],
              **kwargs: Any) -> "DelegationContract":
        """A nested contract one level deeper, bounded by ``MAX_DELEGATION_DEPTH``."""
        depth = self.depth + 1
        if depth > MAX_DELEGATION_DEPTH:
            raise DelegationDepthExceeded(depth, MAX_DELEGATION_DEPTH)
        return DelegationContract(
            task_id=task_id, delegatee_role=delegatee_role,
            capability_set=tuple(capability_set),
            expected_output_schema=kwargs.pop("expected_output_schema", {}),
            context_task_ids=kwargs.pop("context_task_ids", (self.task_id,)),
            timeout=float(kwargs.pop("timeout", self.timeout)),
            retry=int(kwargs.pop("retry", self.retry)),
            depth=depth,
        ).validate()

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["capability_set"] = list(self.capability_set)
        payload["context_task_ids"] = list(self.context_task_ids)
        payload["expected_output_schema"] = {
            name: expected.__name__ for name, expected in self.expected_output_schema.items()
        }
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DelegationContract":
        clean = dict(data)
        schema = clean.get("expected_output_schema") or {}
        builtin_types = {
            "str": str, "int": int, "float": float, "bool": bool,
            "list": list, "dict": dict, "tuple": tuple, "bytes": bytes,
        }
        typed_schema = {}
        for k, v in schema.items():
            if isinstance(v, type):
                typed_schema[k] = v
            elif isinstance(v, str) and v in builtin_types:
                typed_schema[k] = builtin_types[v]
            elif isinstance(v, str):
                import builtins
                t = getattr(builtins, v, None)
                if isinstance(t, type):
                    typed_schema[k] = t
        clean["expected_output_schema"] = typed_schema
        if "capability_set" in clean and isinstance(clean["capability_set"], list):
            clean["capability_set"] = tuple(clean["capability_set"])
        if "context_task_ids" in clean and isinstance(clean["context_task_ids"], list):
            clean["context_task_ids"] = tuple(clean["context_task_ids"])
        return cls(**clean).validate()
