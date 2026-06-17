"""Adaptive multi-candidate draw and resolve (U2).

The orchestration spine that turns "generate one implementation" into "surface
the fork when the model is guessing." It is written as a pure function over a
``generate_candidate`` callable so it is testable offline with a fake generator;
the real wiring binds ``SemiAgent``'s draw to it, gated by ``decisions_enabled``.

Adaptive policy (KTD7): draw a small initial set; if they agree -- even after a
discriminating-input search that probes for hidden forks (R7) -- return the
single head exactly as today (no decisions, no persistence). Only when a real
fork exists does it escalate the draw, classify, and build a ``DecisionSet``.
Cost scales with real ambiguity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from semipy.decisions import germs
from semipy.decisions.cluster import UNRUNNABLE
from semipy.decisions.divergence import (
    DivergenceResult,
    observe_effectful,
    observe_pure,
)
from semipy.decisions.model import DecisionSet
from semipy.orchestration.roles.decision_classifier import (
    classify_divergence,
    rank_decisions,
)

# A generator maps a draw index to a candidate source (or None on failure).
CandidateGenerator = Callable[[int], Optional[str]]

# Priority order when naming the germ from a base sample (most-consequential first).
_GERM_PRIORITY = (
    germs.NULL,
    germs.EMPTY,
    germs.TIE,
    germs.GROUPING_KEY,
    germs.COERCION,
    germs.PRECISION,
    germs.BOUNDARY,
    germs.ORDERING,
)


@dataclass
class DecisionOutcome:
    """Result of an adaptive resolve."""

    diverged: bool
    head_candidate_id: Optional[str]
    head_source: Optional[str]
    decision_set: DecisionSet = field(default_factory=DecisionSet)
    divergence: Optional[DivergenceResult] = None

    @property
    def has_decisions(self) -> bool:
        return not self.decision_set.is_empty()


def _draw(generate: CandidateGenerator, start: int, count: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(start, start + count):
        src = generate(i)
        if src:
            out[f"c{i}"] = src
    return out


def _primary_var(free_variables: list[str]) -> Optional[str]:
    return next((v for v in free_variables if v != "self"), None)


def _germ_from_sample(sample_rows: Optional[list[dict[str, Any]]], free_variables: list[str]) -> str:
    """Name the most-consequential germ present in the base sample, else 'output'."""
    if not sample_rows:
        return "output"
    primary = _primary_var(free_variables)
    value = sample_rows[0].get(primary) if primary else sample_rows[0]
    present = germs.detect_germ_ids(value)
    for g in _GERM_PRIORITY:
        if g in present:
            return g
    return "output"


def _observe(
    candidates: dict[str, str],
    *,
    free_variables: list[str],
    sample_rows: Optional[list[dict[str, Any]]],
    output_names: Optional[list[str]],
    effectful_runtime_values: Optional[dict[str, Any]],
    timeout: int,
) -> DivergenceResult:
    if effectful_runtime_values is not None:
        return observe_effectful(
            candidates,
            free_variables=free_variables,
            runtime_values=effectful_runtime_values,
        )
    return observe_pure(
        candidates,
        free_variables=free_variables,
        sample_rows=sample_rows or [],
        output_names=output_names,
        timeout=timeout,
    )


def _default_head(divergence: DivergenceResult) -> tuple[Optional[str], Optional[str]]:
    """Head from the heaviest *runnable* cluster (clusters are weight-ordered)."""
    for cluster in divergence.clusters:
        if cluster.signature != (UNRUNNABLE,):
            cid = cluster.representative_id
            return cid, divergence.runs[cid].source
    if divergence.runs:
        any_id = next(iter(divergence.runs))
        return any_id, divergence.runs[any_id].source
    return None, None


def _single_head(divergence: DivergenceResult) -> DecisionOutcome:
    head_id, head_src = _default_head(divergence)
    return DecisionOutcome(
        diverged=False,
        head_candidate_id=head_id,
        head_source=head_src,
        divergence=divergence,
    )


def resolve_with_decisions(
    *,
    generate_candidate: CandidateGenerator,
    free_variables: list[str],
    sample_rows: Optional[list[dict[str, Any]]] = None,
    output_names: Optional[list[str]] = None,
    effectful_runtime_values: Optional[dict[str, Any]] = None,
    slot_id: str = "",
    initial_candidates: int = 3,
    max_candidates: int = 5,
    use_llm: bool = True,
    timeout: int = 15,
) -> DecisionOutcome:
    """Draw candidates adaptively and resolve to a head plus (if forked) a DecisionSet.

    When candidates agree -- including after a discriminating-input probe -- returns
    a single head with no decisions (the unchanged path). When a fork exists,
    escalates the draw, classifies, and returns a populated ``DecisionSet``.
    """
    candidates = _draw(generate_candidate, 0, max(1, initial_candidates))
    if not candidates:
        return DecisionOutcome(diverged=False, head_candidate_id=None, head_source=None)

    pure = effectful_runtime_values is None
    divergence = _observe(
        candidates,
        free_variables=free_variables,
        sample_rows=sample_rows,
        output_names=output_names,
        effectful_runtime_values=effectful_runtime_values,
        timeout=timeout,
    )

    # Decide what input set to cluster over and which germ names the fork.
    observe_rows = sample_rows
    germ = _germ_from_sample(sample_rows, free_variables) if pure else "output"
    example_in = sample_rows[0] if sample_rows else None

    if pure and sample_rows:
        from semipy.decisions.discriminate import search_discriminating_inputs

        disc = search_discriminating_inputs(
            candidates,
            free_variables=free_variables,
            base_rows=sample_rows,
            output_names=output_names,
            timeout=timeout,
        )
        if disc.found and disc.germ:
            # A hidden (or stronger) fork: cluster over the minimal splitting input.
            observe_rows = [disc.minimized_input or disc.best_input]
            germ = disc.germ
            example_in = observe_rows[0]
            divergence = _observe(
                candidates,
                free_variables=free_variables,
                sample_rows=observe_rows,
                output_names=output_names,
                effectful_runtime_values=None,
                timeout=timeout,
            )

    if not divergence.diverged():
        return _single_head(divergence)

    # Real fork: escalate the draw, then re-observe over the splitting input set.
    if max_candidates > len(candidates):
        candidates.update(
            _draw(generate_candidate, len(candidates), max_candidates - len(candidates))
        )
        divergence = _observe(
            candidates,
            free_variables=free_variables,
            sample_rows=observe_rows,
            output_names=output_names,
            effectful_runtime_values=effectful_runtime_values,
            timeout=timeout,
        )

    decisions = rank_decisions(
        classify_divergence(divergence, germ=germ, example_in=example_in, use_llm=use_llm)
    )
    head_id, head_src = _default_head(divergence)
    if not decisions:
        return _single_head(divergence)
    return DecisionOutcome(
        diverged=True,
        head_candidate_id=head_id,
        head_source=head_src,
        decision_set=DecisionSet(slot_id=slot_id, decisions=decisions, candidates=dict(candidates)),
        divergence=divergence,
    )
