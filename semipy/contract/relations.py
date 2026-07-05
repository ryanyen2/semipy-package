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


def _t_dict_key_order_reversed(value: Any) -> Any:
    """Reverse a dict's key insertion order (same keys and values, same set --
    only iteration order differs). A function that reads fields by name, not
    by iteration position, must produce the same output either way."""
    if isinstance(value, dict) and len(value) > 1:
        return dict(reversed(list(value.items())))
    return value


def _t_list_reversed(value: Any) -> Any:
    """Reverse a list's element order. Only meaningful for a slot the proposer
    judges order-INSENSITIVE (an aggregate, a set-like lookup) -- proposing it
    for an order-sensitive slot (sort, "first match", concatenation) is a
    proposer error, not something this transform can detect on its own."""
    if isinstance(value, list) and len(value) > 1:
        return list(reversed(value))
    return value


_REGISTRY: dict[str, dict[str, Any]] = {
    "whitespace_invariance": {"transform": _t_whitespace, "relation": "equal"},
    "trailing_newline_invariance": {"transform": _t_trailing_newline, "relation": "equal"},
    "dict_key_order_invariance": {"transform": _t_dict_key_order_reversed, "relation": "equal"},
    "list_permutation_invariance": {"transform": _t_list_reversed, "relation": "equal"},
}


def relation_names() -> tuple[str, ...]:
    return tuple(_REGISTRY.keys())


def get_relation(name: str) -> tuple[Callable[[Any], Any], OutputRelation] | None:
    entry = _REGISTRY.get(name)
    if entry is None:
        return None
    return entry["transform"], entry["relation"]


def is_relation_nonvacuous(name: str, value: Any) -> bool:
    """True iff transforming *value* under relation *name* actually perturbs
    it (a differently-shaped or differently-ordered input), as opposed to
    returning it unchanged because the value's shape doesn't match what this
    relation transforms (e.g. a dict-shape relation applied to a string, or a
    1-element collection with nothing to reorder). A vacuous relation still
    "passes" every time -- trivially, since the input never actually changed --
    so it carries no evidence and must not count toward a freeze-eligibility
    floor that requires a genuine metamorphic check.
    """
    rel = get_relation(name)
    if rel is None:
        return False
    transform, _ = rel
    try:
        # repr(), not ==: a dict-key-order reversal is == to the original (dict
        # equality ignores insertion order) but is a genuinely different input
        # to anything order-sensitive (json.dumps without sort_keys, next(iter(d)),
        # list.pop(0)) -- exactly the kind of divergence this is meant to catch.
        return repr(transform(value)) != repr(value)
    except Exception:
        return False
