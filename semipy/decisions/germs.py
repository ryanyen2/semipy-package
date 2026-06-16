"""Ambiguity-germ taxonomy and detectors (U1).

A *germ* is a structural source of ambiguity in a runtime value -- the place
where an underspecified spec forces the model to guess. The taxonomy is small,
reusable, and general-purpose: decisions in data programming cluster around
these germs, which is what makes the surfaced fork legible ("null reading")
instead of a raw statement diff.

Detection is purely structural and data-agnostic: it inspects value *shape*
(None presence, empty containers, NaN, mixed element types, duplicate grouping
values), never literal/keyword/domain patterns. It is intentionally permissive
-- germs *seed* the discriminating-input search (U5); the divergence engine
(U3) and classifier (U6) decide which germs correspond to a real decision. A
false-positive germ costs a probe; a missed germ hides a fork.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# -- Germ identifiers: the reusable taxonomy --------------------------------
NULL = "null"                 # a None inside a value the spec must reduce over
EMPTY = "empty"               # an empty collection (group, list, string)
TIE = "tie"                   # duplicate values where order/selection is forced
BOUNDARY = "boundary"         # an inclusive-vs-exclusive edge (zero, negative)
ORDERING = "ordering"         # a sequence whose order may or may not be significant
COERCION = "coercion"         # mixed element types in one collection
PRECISION = "precision"       # float / NaN / inf where rounding policy is unstated
UNIT = "unit"                 # unit/timezone/encoding ambiguity (not structurally detectable)
GROUPING_KEY = "grouping-key" # a key with repeated values -- a candidate group axis

GERMS: tuple[str, ...] = (
    NULL,
    EMPTY,
    TIE,
    BOUNDARY,
    ORDERING,
    COERCION,
    PRECISION,
    UNIT,
    GROUPING_KEY,
)

# Bound the structural walk so a pathological nested value cannot stall detection.
_MAX_DEPTH = 6


@dataclass(frozen=True)
class GermHit:
    """One detected ambiguity source: which germ, where, and a short note."""

    germ: str
    path: str = "$"
    note: str = ""


def detect_germs(value: Any) -> list[GermHit]:
    """Return the ambiguity germs present in ``value``, with locations.

    Deterministic and offline; depends only on value structure.
    """
    hits: list[GermHit] = []
    _walk(value, "$", 0, hits)
    # De-duplicate identical (germ, path) hits while preserving order.
    seen: set[tuple[str, str]] = set()
    unique: list[GermHit] = []
    for h in hits:
        key = (h.germ, h.path)
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


def detect_germ_ids(value: Any) -> set[str]:
    """Return just the set of germ identifiers present in ``value``."""
    return {h.germ for h in detect_germs(value)}


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _walk(value: Any, path: str, depth: int, hits: list[GermHit]) -> None:
    if depth > _MAX_DEPTH:
        return

    if value is None:
        hits.append(GermHit(NULL, path, "value is None"))
        return

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            hits.append(GermHit(PRECISION, path, "NaN or infinity"))
        elif not value.is_integer():
            hits.append(GermHit(PRECISION, path, "non-integer float (rounding unstated)"))
        if _is_number(value) and value <= 0:
            hits.append(GermHit(BOUNDARY, path, "zero or negative edge"))
        return

    if isinstance(value, bool):
        return

    if isinstance(value, int):
        if value <= 0:
            hits.append(GermHit(BOUNDARY, path, "zero or negative edge"))
        return

    if isinstance(value, str):
        if value == "":
            hits.append(GermHit(EMPTY, path, "empty string"))
        return

    if isinstance(value, dict):
        if len(value) == 0:
            hits.append(GermHit(EMPTY, path, "empty mapping"))
        for k, v in value.items():
            _walk(v, f"{path}.{k}", depth + 1, hits)
        return

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if len(items) == 0:
            hits.append(GermHit(EMPTY, path, "empty collection"))
            return
        if len(items) >= 2:
            hits.append(GermHit(ORDERING, path, "sequence order may be significant"))
            _detect_mixed_types(items, path, hits)
            _detect_duplicates(items, path, hits)
        _detect_grouping_keys(items, path, hits)
        for i, v in enumerate(items):
            _walk(v, f"{path}[{i}]", depth + 1, hits)
        return


def _detect_mixed_types(items: list[Any], path: str, hits: list[GermHit]) -> None:
    """Mixed scalar types in one collection -> coercion ambiguity."""
    kinds: set[str] = set()
    for v in items:
        if v is None:
            continue
        if _is_number(v):
            kinds.add("number")
        elif isinstance(v, str):
            kinds.add("str")
        elif isinstance(v, bool):
            kinds.add("bool")
        else:
            kinds.add(type(v).__name__)
    if len(kinds) >= 2:
        hits.append(GermHit(COERCION, path, f"mixed element types: {sorted(kinds)}"))


def _detect_duplicates(items: list[Any], path: str, hits: list[GermHit]) -> None:
    """Duplicate hashable scalars -> a tie the model must break somehow."""
    seen: set[Any] = set()
    for v in items:
        if isinstance(v, (dict, list, set, tuple)):
            continue
        try:
            if v in seen:
                hits.append(GermHit(TIE, path, "duplicate values present"))
                return
            seen.add(v)
        except TypeError:
            continue


def _detect_grouping_keys(items: list[Any], path: str, hits: list[GermHit]) -> None:
    """A dict key whose values repeat across rows is a candidate grouping axis;
    repeated values also imply a tie when a representative must be chosen."""
    if not all(isinstance(v, dict) for v in items):
        return
    keys: set[str] = set()
    for row in items:
        keys.update(str(k) for k in row.keys())
    for key in sorted(keys):
        values = [row.get(key) for row in items if key in row]
        hashable = [v for v in values if not isinstance(v, (dict, list, set, tuple))]
        if len(hashable) < len(values):
            continue
        try:
            if len(set(hashable)) < len(hashable):
                hits.append(GermHit(GROUPING_KEY, f"{path}.{key}", "key with repeated values (candidate group axis)"))
        except TypeError:
            continue
