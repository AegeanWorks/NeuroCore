"""Per-model request limits with explicit, recorded clamping (IDEA-588).

A model is only clamped when ``settings.json`` declares ``model_limits`` for it. With no
limits configured the registry is disabled and every request passes through unchanged, so
existing local deployments keep their current behaviour. An unknown model is refused only
when ``strict_model_limits`` is true; otherwise it is treated as unconfigured.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


class UnknownModelError(ValueError):
    """A model id is not in the registry while strict limits are enabled."""


class ModelLimitExceeded(ValueError):
    """A request cannot be represented within the model's declared limits."""


@dataclass(frozen=True)
class ModelLimits:
    """The two ceilings every request for one model must respect."""

    max_output_tokens: int
    max_context_tokens: int
    aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClampField:
    """One field whose requested value was reduced to an applied value."""

    field: str
    requested: int
    applied: int


@dataclass(frozen=True)
class ClampResult:
    """The applied request values and every field that had to change."""

    output_tokens: int
    context_tokens: int
    clamped: Tuple[ClampField, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output_tokens": self.output_tokens,
            "context_tokens": self.context_tokens,
            "clamped": [
                {"field": item.field, "requested": item.requested, "applied": item.applied}
                for item in self.clamped
            ],
        }


class ModelRegistry:
    """Resolve model ids and clamp request values against declared limits."""

    def __init__(self, limits: Optional[Mapping[str, Any]] = None, *, strict: bool = False):
        self._by_id: Dict[str, ModelLimits] = {}
        self._aliases: Dict[str, str] = {}
        self.strict = bool(strict)
        for model_id, raw in (limits or {}).items():
            parsed = self._parse(model_id, raw)
            self._by_id[model_id] = parsed
            for alias in parsed.aliases:
                if alias in self._by_id or alias in self._aliases:
                    raise ValueError(f"model alias {alias!r} is declared more than once")
                self._aliases[alias] = model_id
        for alias in self._aliases:
            if alias in self._by_id:
                raise ValueError(f"model alias {alias!r} is also a canonical model id")

    @property
    def enabled(self) -> bool:
        return bool(self._by_id)

    @staticmethod
    def _positive(value: Any, field: str, model_id: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{model_id}: {field} must be a positive integer")
        return value

    @classmethod
    def _parse(cls, model_id: str, raw: Any) -> ModelLimits:
        if isinstance(raw, ModelLimits):
            limits = raw
        elif isinstance(raw, Mapping):
            aliases = raw.get("aliases", ())
            if not isinstance(aliases, (list, tuple)) or not all(
                isinstance(alias, str) and alias for alias in aliases
            ):
                raise ValueError(f"{model_id}: aliases must be a list of non-empty strings")
            limits = ModelLimits(
                max_output_tokens=cls._positive(
                    raw.get("max_output_tokens"), "max_output_tokens", model_id
                ),
                max_context_tokens=cls._positive(
                    raw.get("max_context_tokens"), "max_context_tokens", model_id
                ),
                aliases=tuple(aliases),
            )
        else:
            raise ValueError(f"{model_id}: limits must be a mapping")
        return limits

    @classmethod
    def from_settings(cls, values: Optional[Mapping[str, Any]]) -> "ModelRegistry":
        """Build the registry from the settings dict; empty limits disable it."""
        values = values or {}
        raw = values.get("model_limits")
        if raw is None:
            return cls({}, strict=bool(values.get("strict_model_limits", False)))
        if not isinstance(raw, Mapping):
            raise ValueError("model_limits must be a mapping of model id to limits")
        return cls(raw, strict=bool(values.get("strict_model_limits", False)))

    def resolve(self, model_id: str) -> Optional[ModelLimits]:
        """Exact id first, then a declared alias, then refusal only in strict mode."""
        if model_id in self._by_id:
            return self._by_id[model_id]
        canonical = self._aliases.get(model_id)
        if canonical is not None:
            return self._by_id[canonical]
        if self.strict:
            known = ", ".join(sorted(self._by_id)) or "none"
            raise UnknownModelError(
                f"Unknown model {model_id!r}; known models: {known}. "
                "Why: strict model limits are enabled. Fix: add the model to settings.json "
                "or disable strict_model_limits."
            )
        return None

    def clamp(self, model_id: str, *, output_tokens: int, context_tokens: int,
              require_exact: bool = False) -> ClampResult:
        """Apply the model's ceilings without mutating the caller's values."""
        limits = self.resolve(model_id)
        if limits is None:
            return ClampResult(output_tokens=output_tokens, context_tokens=context_tokens)

        clamped = []
        applied_output = output_tokens
        if output_tokens > limits.max_output_tokens:
            applied_output = limits.max_output_tokens
            clamped.append(ClampField("output_tokens", output_tokens, applied_output))
        if context_tokens > limits.max_context_tokens:
            if not self.strict:
                # H-1: non-strict mode warns instead of refusing, so a default
                # install with image content keeps working; strict mode still
                # refuses (unknown models and over-limit requests).
                return ClampResult(
                    output_tokens=applied_output,
                    context_tokens=context_tokens,
                    clamped=tuple(clamped),
                )
            raise ModelLimitExceeded(
                f"Model {model_id!r} accepts at most {limits.max_context_tokens} context "
                f"tokens, but {context_tokens} were assembled. "
                "Why: the prompt cannot be truncated without changing its meaning. "
                "Fix: reduce the context or raise max_context_tokens in settings.json."
            )
        if require_exact and clamped:
            first = clamped[0]
            raise ModelLimitExceeded(
                f"Model {model_id!r} clamps {first.field} from {first.requested} to "
                f"{first.applied}, but require_exact=True was requested."
            )
        return ClampResult(
            output_tokens=applied_output,
            context_tokens=context_tokens,
            clamped=tuple(clamped),
        )
