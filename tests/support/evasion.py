"""Mechanical evasion variants for guardrail stability tests (IDEA-625).

The generators are pure and deterministic. A guard that claims encoding insensitivity is run
over every declared variant; a guard that does not is listed as skipped so the exemption is
visible rather than silent.
"""

import html
import urllib.parse
from typing import Callable, Dict, Iterable, List, Sequence

VARIANTS = ("case", "whitespace", "zero_width", "interleave", "percent", "html_entity")


def _case(payload: str) -> str:
    return payload.swapcase()


def _whitespace(payload: str) -> str:
    return payload.replace(" ", "\t ")


def _zero_width(payload: str) -> str:
    return "\u200b".join(payload)


def _interleave(payload: str) -> str:
    return ".".join(payload)


def _percent(payload: str) -> str:
    return "".join(f"%{ord(char):02X}" for char in payload)


def _html_entity(payload: str) -> str:
    return "".join(f"&#{ord(char)};" for char in payload)


_GENERATORS: Dict[str, Callable[[str], str]] = {
    "case": _case,
    "whitespace": _whitespace,
    "zero_width": _zero_width,
    "interleave": _interleave,
    "percent": _percent,
    "html_entity": _html_entity,
}


def variants(payload: str, names: Sequence[str] = VARIANTS) -> Dict[str, str]:
    """Every requested variant of one payload; each is guaranteed to differ from the source."""
    result = {}
    for name in names:
        generator = _GENERATORS.get(name)
        if generator is None:
            raise ValueError(f"unknown evasion variant {name!r}")
        value = generator(payload)
        if value == payload:
            raise ValueError(f"variant {name!r} did not change the payload")
        result[name] = value
    return result


def assert_guard_stable(guard: Callable[[str], bool], payloads: Iterable[str],
                        names: Sequence[str] = VARIANTS,
                        skipped: Sequence[str] = ()) -> List[Dict[str, object]]:
    """Return one record per absolute miss, variant miss, or explicitly skipped variant."""
    records: List[Dict[str, object]] = []
    skipped_set = set(skipped)
    for payload in payloads:
        if not guard(payload):
            records.append({
                "payload": payload, "variant": "canonical",
                "expected": True, "actual": False,
            })
        for name, value in variants(payload, names).items():
            if name in skipped_set:
                records.append({
                    "payload": payload, "variant": name,
                    "expected": True, "actual": None, "skipped": True,
                })
            elif not guard(value):
                records.append({
                    "payload": payload, "variant": name,
                    "expected": True, "actual": False,
                })
    return records


def normalize_variant(value: str) -> str:
    """Decode the transformations a normalising guard is expected to handle."""
    text = urllib.parse.unquote(html.unescape(value))
    return text.replace("\u200b", "").replace("\t", " ")
