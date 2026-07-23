"""Discriminating-input search (U5).

Observed-output divergence only sees forks that some input exercises. A
null-handling fork is invisible if no sample input contains a null. This module
closes that gap: it injects ambiguity germs into the sample input, re-runs the
candidates, and keeps the variants that increase the number of behavioral
clusters -- surfacing forks the available data never triggered. Winning inputs
are then minimized toward the smallest case that still splits the ensemble, for
use as a branch's illustrative example.

Injection is structural and germ-driven, never domain-specific: insert a None,
empty the collection, duplicate an element, push a numeric edge.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.decisions import germs
from semipy.decisions.divergence import observe_pure


@dataclass
class DiscriminationResult:
    """Outcome of the search for inputs that split the candidate ensemble."""

    found: bool
    base_clusters: int
    best_clusters: int = 0
    germ: Optional[str] = None
    #: The free variable whose mutation exposed the split, when found.
    variable: Optional[str] = None
    best_input: Optional[dict[str, Any]] = None
    minimized_input: Optional[dict[str, Any]] = None
    tried: int = 0
    notes: list[str] = field(default_factory=list)


def _germ_variants(value: Any) -> list[tuple[str, Any]]:
    """Structural germ injections for one primary value. Each is (germ_id, mutated)."""
    variants: list[tuple[str, Any]] = []

    if isinstance(value, list):
        variants.append((germs.EMPTY, []))
        if value:
            variants.append((germs.TIE, value + [copy.deepcopy(value[0])]))
        dict_rows = [v for v in value if isinstance(v, dict)]
        if dict_rows:
            keys = sorted({str(k) for row in dict_rows for k in row.keys()})
            for key in keys:
                null_row = copy.deepcopy(value)
                for item in null_row:
                    if isinstance(item, dict) and key in item:
                        item[key] = None
                        break
                variants.append((germs.NULL, null_row))
                zero_row = copy.deepcopy(value)
                appended = copy.deepcopy(dict_rows[0])
                appended[key] = 0
                zero_row.append(appended)
                variants.append((germs.BOUNDARY, zero_row))
        elif value:
            variants.append((germs.NULL, value + [None]))
            variants.append((germs.BOUNDARY, value + [0]))
            variants.append((germs.TIE, value + [value[0]]))
        return variants

    if isinstance(value, dict):
        for key in sorted(value, key=str):
            null_d = copy.deepcopy(value)
            null_d[key] = None
            variants.append((germs.NULL, null_d))
        variants.append((germs.EMPTY, {}))
        return variants

    if isinstance(value, str):
        # A string variable's structural germs are its empty and null forms.
        # Applied one variable at a time, these also decouple a string input from
        # any other input it is correlated with in the observed data (e.g. a
        # ``lower`` argument that always equals ``text.lower()``): mutating one
        # while holding the other fixed breaks the equality and exposes a fork
        # that trusts the derived input vs. one that recomputes it.
        variants.append((germs.EMPTY, ""))
        variants.append((germs.NULL, None))
        return variants

    # Scalar / None seed.
    variants.append((germs.NULL, [None]))
    variants.append((germs.BOUNDARY, [0]))
    return variants


def _row_with(base_row: dict[str, Any], primary: str, mutated: Any) -> dict[str, Any]:
    row = dict(base_row)
    row[primary] = mutated
    return row


def _count_clusters(
    candidates: dict[str, str],
    free_variables: list[str],
    row: dict[str, Any],
    output_names: Optional[list[str]],
    timeout: int,
) -> int:
    res = observe_pure(
        candidates,
        free_variables=free_variables,
        sample_rows=[row],
        output_names=output_names,
        timeout=timeout,
    )
    return len(res.clusters)


def _minimize(
    candidates: dict[str, str],
    free_variables: list[str],
    var: str,
    base_row: dict[str, Any],
    winning_value: Any,
    target_clusters: int,
    output_names: Optional[list[str]],
    timeout: int,
) -> dict[str, Any]:
    """Greedily shrink a winning list input to the smallest still-splitting case."""
    if not isinstance(winning_value, list) or len(winning_value) <= 1:
        return _row_with(base_row, var, winning_value)
    current = list(winning_value)
    changed = True
    while changed and len(current) > 1:
        changed = False
        for i in range(len(current)):
            trial = current[:i] + current[i + 1:]
            if not trial:
                continue
            row = _row_with(base_row, var, trial)
            if _count_clusters(candidates, free_variables, row, output_names, timeout) >= target_clusters:
                current = trial
                changed = True
                break
    return _row_with(base_row, var, current)


def search_discriminating_inputs(
    candidates: dict[str, str],
    *,
    free_variables: list[str],
    base_rows: list[dict[str, Any]],
    output_names: Optional[list[str]] = None,
    timeout: int = 15,
) -> DiscriminationResult:
    """Search for an input that splits ``candidates`` into more branches than the
    base sample does. Returns the best (and minimized) discriminating input.

    Every free variable is mutated (one at a time, the others held at their base
    values), not just the first: a fork can hinge on a non-primary variable, and
    mutating a variable in isolation also decouples it from any correlation it has
    with the others in the observed data.
    """
    variables = [v for v in free_variables if v != "self"]
    seed_row = base_rows[0] if base_rows else {}
    base_clusters = (
        _count_clusters(candidates, free_variables, seed_row, output_names, timeout)
        if base_rows
        else 1
    )
    result = DiscriminationResult(found=False, base_clusters=base_clusters)
    if not variables:
        result.notes.append("no input variable to mutate")
        return result

    best_clusters = base_clusters
    tried = 0
    for var in variables:
        for germ_id, mutated in _germ_variants(seed_row.get(var)):
            tried += 1
            row = _row_with(seed_row, var, mutated)
            n = _count_clusters(candidates, free_variables, row, output_names, timeout)
            if n > best_clusters:
                best_clusters = n
                result.found = True
                result.germ = germ_id
                result.variable = var
                result.best_input = row
                result.best_clusters = n

    result.tried = tried
    if result.found and result.best_input is not None and result.variable is not None:
        result.minimized_input = _minimize(
            candidates,
            free_variables,
            result.variable,
            seed_row,
            result.best_input[result.variable],
            result.best_clusters,
            output_names,
            timeout,
        )
    else:
        result.notes.append("no germ injection increased the cluster count")
    return result
