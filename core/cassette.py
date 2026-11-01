"""Recorded provider cassettes with deterministic replay (IDEA-619).

A cassette keeps, for each provider exchange, a digest of the full request body, a structural
shape (endpoint, model, message roles, tool schema digest, stream flag) and the response. The
prompt text itself is never stored, so a credential in a prompt cannot leak through a fixture;
the response passes through the shipped credential redaction before it is written.

Replay walks the recorded exchanges in order. ``strict`` compares the whole body, ``structural``
compares the shape, and ``loose`` compares only the endpoint. A mismatch raises ``CassetteMiss``
with the differing keys, so prompt drift is a named test failure rather than a confusing answer.
"""

import copy
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.errors import NeuroCoreError
from core.security import redact_secrets as redact_mapping_secrets

logger = logging.getLogger(__name__)

SCHEMA = 1
STRICT = "strict"
STRUCTURAL = "structural"
LOOSE = "loose"
MODES = (STRICT, STRUCTURAL, LOOSE)


class CassetteError(NeuroCoreError):
    """A cassette could not be built, saved or replayed."""


class CassetteSchemaError(CassetteError):
    """A cassette file is unreadable or declares a schema this build does not know."""


class CassetteMiss(CassetteError):  # noqa: N818 - the replay blueprint names the miss
    """A request did not match the next recorded exchange."""

    def __init__(self, position: int, mode: str, diff: Sequence[str]):
        self.position = position
        self.mode = mode
        self.diff = list(diff)
        detail = ", ".join(self.diff) if self.diff else "no visible field"
        super().__init__(
            f"cassette replay missed at exchange {position + 1} in {mode} mode: {detail}",
            details={"position": position, "mode": mode, "diff": self.diff},
        )


class CassetteTemplateMismatch(CassetteError):  # noqa: N818 - the recorded template differs
    """The cassette was recorded against a different prompt template."""

    def __init__(self, recorded: str, current: str):
        self.recorded = recorded
        self.current = current
        super().__init__(
            "cassette was recorded against template "
            f"{recorded} but the current template is {current}",
            details={"recorded": recorded, "current": current},
        )


@dataclass(frozen=True)
class Exchange:
    """One recorded provider call: the request fingerprint and the replayed response."""

    request_digest: str
    request_shape: Dict[str, Any]
    response: Any
    recorded_at: str
    template_digest: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_digest": self.request_digest,
            "request_shape": self.request_shape,
            "response": self.response,
            "recorded_at": self.recorded_at,
            "template_digest": self.template_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Exchange":
        if not isinstance(raw, Mapping):
            raise CassetteSchemaError("a cassette exchange must be an object")
        missing = [
            key for key in ("request_digest", "request_shape", "response", "recorded_at")
            if key not in raw
        ]
        if missing:
            raise CassetteSchemaError(
                f"a cassette exchange is missing {', '.join(missing)}"
            )
        template = raw.get("template_digest")
        if template is not None and not isinstance(template, str):
            raise CassetteSchemaError("a cassette template digest must be a string or null")
        if not isinstance(raw["request_shape"], Mapping):
            raise CassetteSchemaError("a cassette request shape must be an object")
        return cls(
            request_digest=str(raw["request_digest"]),
            request_shape=dict(raw["request_shape"]),
            response=raw["response"],
            recorded_at=str(raw["recorded_at"]),
            template_digest=template,
        )


class Cassette:
    """An ordered set of recorded exchanges with one matching mode."""

    def __init__(self, *, mode: str = STRUCTURAL, template_digest: Optional[str] = None,
                 exchanges: Iterable[Exchange] = ()):
        if mode not in MODES:
            raise ValueError(
                f"unknown cassette mode {mode!r}; choose one of {', '.join(MODES)}"
            )
        self.mode = mode
        self.template_digest = template_digest
        self._exchanges: List[Exchange] = list(exchanges)
        self._cursor = 0

    @property
    def exchanges(self) -> Tuple[Exchange, ...]:
        return tuple(self._exchanges)

    @property
    def position(self) -> int:
        """How many exchanges replay has consumed."""
        return self._cursor

    def record(self, request: Mapping[str, Any], response: Any, *, endpoint: Optional[str] = None,
               template_digest: Optional[str] = None) -> Exchange:
        """Append one exchange; the request is stored as a digest and shape only."""
        if not isinstance(request, Mapping):
            raise CassetteError("a cassette request must be a mapping")
        self._check_template(template_digest)
        if self.template_digest is None and template_digest:
            self.template_digest = template_digest
        exchange = Exchange(
            request_digest=_digest(request),
            request_shape=_shape(request, endpoint),
            response=_redact(copy.deepcopy(response)),
            recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            template_digest=template_digest or self.template_digest,
        )
        self._exchanges.append(exchange)
        return exchange

    def replay(self, request: Mapping[str, Any], *, endpoint: Optional[str] = None,
               template_digest: Optional[str] = None) -> Any:
        """Return the response of the next exchange, or raise ``CassetteMiss``."""
        if not isinstance(request, Mapping):
            raise CassetteError("a cassette request must be a mapping")
        self._check_template(template_digest)
        if self._cursor >= len(self._exchanges):
            raise CassetteMiss(self._cursor, self.mode, ["end"])
        exchange = self._exchanges[self._cursor]
        actual_shape = _shape(request, endpoint)
        diff: List[str] = []
        if self.mode == STRICT:
            if exchange.request_digest != _digest(request):
                diff.append("body")
            if exchange.request_shape.get("endpoint") != actual_shape.get("endpoint"):
                diff.append("endpoint")
        elif self.mode == STRUCTURAL:
            diff = _shape_diff(exchange.request_shape, actual_shape)
        else:
            if exchange.request_shape.get("endpoint") != actual_shape.get("endpoint"):
                diff.append("endpoint")
        if diff:
            raise CassetteMiss(self._cursor, self.mode, diff)
        self._cursor += 1
        return copy.deepcopy(exchange.response)

    def save(self, path: Any) -> None:
        """Write the cassette atomically; responses are redacted before the write."""
        target = Path(path)
        payload = {
            "schema": SCHEMA,
            "mode": self.mode,
            "template_digest": self.template_digest,
            "exchanges": [exchange.to_dict() for exchange in self._exchanges],
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_path = tempfile.mkstemp(
                prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(payload, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, target)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except (OSError, TypeError, ValueError) as exc:
            raise CassetteError(
                f"could not save cassette {target}: {exc}. "
                "Why: the target is unwritable or the response is not JSON. "
                "Fix: choose a writable path and record a JSON-serialisable response.",
                details={"path": str(target)},
            ) from exc

    @classmethod
    def load(cls, path: Any, *, mode: Optional[str] = None) -> "Cassette":
        """Read a cassette; an unknown schema or mode is refused, never guessed."""
        target = Path(path)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CassetteSchemaError(
                f"cassette {target} is not readable JSON: {exc}. "
                "Why: the file is missing, truncated or not a cassette. "
                "Fix: re-record it with cassette_mode='record'."
            ) from exc
        if not isinstance(raw, Mapping):
            raise CassetteSchemaError(f"cassette {target} must be a JSON object")
        schema = raw.get("schema")
        if type(schema) is not int or schema != SCHEMA:
            raise CassetteSchemaError(
                f"cassette schema {schema!r} is not supported; this build reads {SCHEMA}. "
                "Why: the file was written by an incompatible version. "
                "Fix: re-record it with cassette_mode='record'."
            )
        recorded_mode = raw.get("mode")
        if recorded_mode not in MODES:
            raise CassetteSchemaError(f"cassette mode {recorded_mode!r} is not known")
        chosen_mode = mode or recorded_mode
        if chosen_mode not in MODES:
            raise ValueError(
                f"unknown cassette mode {chosen_mode!r}; choose one of {', '.join(MODES)}"
            )
        template = raw.get("template_digest")
        if template is not None and not isinstance(template, str):
            raise CassetteSchemaError("a cassette template digest must be a string or null")
        exchanges = [Exchange.from_dict(item) for item in raw.get("exchanges", [])]
        return cls(mode=chosen_mode, template_digest=template, exchanges=exchanges)

    def _check_template(self, current: Optional[str]) -> None:
        if current and self.template_digest and current != self.template_digest:
            raise CassetteTemplateMismatch(self.template_digest, current)


def _digest(value: Any) -> str:
    """A stable SHA-256 over a JSON value, independent of key order."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _shape(request: Mapping[str, Any], endpoint: Optional[str]) -> Dict[str, Any]:
    """The structural fingerprint compared in structural mode."""
    roles = []
    for message in request.get("messages") or []:
        if isinstance(message, Mapping):
            roles.append(str(message.get("role", "")))
    tools = request.get("tools") or []
    return {
        "endpoint": endpoint,
        "model": request.get("model"),
        "roles": roles,
        "tools_digest": _digest(tools),
        "stream": bool(request.get("stream")),
    }


def _shape_diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> List[str]:
    keys = sorted(set(expected) | set(actual))
    return [key for key in keys if expected.get(key) != actual.get(key)]


def _redact(value: Any) -> Any:
    """Mask named secrets and credential-shaped runs before anything is stored."""
    return _scrub_strings(redact_mapping_secrets(value))


def _scrub_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, Mapping):
        return {key: _scrub_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_strings(item) for item in value]
    return value


def _scrub_text(text: str) -> str:
    """Reuse the shipped guardrail patterns instead of duplicating a credential list."""
    try:
        from modules.brain.guardrails import redact_secrets as scrub
    except Exception as exc:  # a missing scrubber must never mean an unredacted cassette
        raise CassetteError(
            "credential redaction is unavailable, so the cassette was not recorded. "
            "Why: modules.brain.guardrails could not be imported. "
            f"Fix: restore the guardrails module ({exc})."
        ) from exc
    return scrub(text)[0]
