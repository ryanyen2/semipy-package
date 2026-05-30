"""Fixed registry of metamorphic relations.

Each relation names a data-agnostic input transformation and the output relation
that must hold between the original and transformed runs. Relations are the
no-oracle way to assert behavior: we do not know the *correct* output, but we
know how the output must (not) change when the input is transformed in a
meaning-preserving way. The registry is intentionally small and closed — no
per-dataset logic, nothing case-sensitive (CLAUDE.md case-independence rule).
"""
from __future__ import annotations

from typing import Any, Callable

# Output relation kinds evaluated by the runner.
#   "equal"     : transformed output must equal the original output
#   "unchanged" : alias of "equal" (kept for readability of intent)
OutputRelation = str


def _t_whitespace(value: Any) -> Any:
    """Pad with surrounding whitespace; a robust parser should ignore it."""
    if isinstance(value, str):
        return "  " + value + "  "
    return value


def _t_trailing_newline(value: Any) -> Any:
    """Append a trailing newline; should not change a line-oriented result."""
    if isinstance(value, str):
        return value + "\n"
    return value


_REGISTRY: dict[str, dict[str, Any]] = {
    "whitespace_invariance": {"transform": _t_whitespace, "relation": "equal"},
    "trailing_newline_invariance": {"transform": _t_trailing_newline, "relation": "equal"},
}


def relation_names() -> tuple[str, ...]:
    return tuple(_REGISTRY.keys())


def get_relation(name: str) -> tuple[Callable[[Any], Any], OutputRelation] | None:
    entry = _REGISTRY.get(name)
    if entry is None:
        return None
    return entry["transform"], entry["relation"]
