"""Population-of-candidates: a node's molten representation (Phase 2).

The multi-candidate draw (``semipy.decisions.draw``/``divergence``) already *is*
the particle population the plan describes -- proposal sampling plus a
behavioral quotient. What Phase 2 adds is the one new piece: an
execution-based head-selection score (type validity + contract-pass fraction
+ cluster agreement), replacing pure majority-vote head selection
(``_default_head``'s "heaviest cluster wins", which ignores whether a
candidate actually runs cleanly or satisfies the slot's accumulated
contract). See docs/plans/2026-07-04-001-refactor-frontier-kernel-plan.md
Phase 2.

This module only re-ranks candidates a draw already produced. It never
triggers additional generation, and it is only reached on the existing
``decisions_enabled`` opt-in path -- the population still initializes with a
single particle by default (today's single-generation cost), unchanged here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from semipy.decisions.cluster import UNRUNNABLE
from semipy.decisions.divergence import DivergenceResult


@dataclass(frozen=True)
class Particle:
    """One candidate's execution-based standing within its population."""

    candidate_id: str
    source: str
    type_ok: bool
    contract_pass_fraction: Optional[float]
    cluster_weight: float

    def rank_key(self) -> tuple[int, float, float, str]:
        """Higher sorts better. Priority order matches the plan: type validity
        is a hard gate, contract-pass fraction (when there's a contract to
        check against) outranks cluster agreement, and candidate id breaks
        ties deterministically."""
        return (
            1 if self.type_ok else 0,
            self.contract_pass_fraction if self.contract_pass_fraction is not None else -1.0,
            self.cluster_weight,
            self.candidate_id,
        )


def build_population(
    divergence: DivergenceResult,
    *,
    contract_pass_fractions: Optional[dict[str, float]] = None,
) -> list[Particle]:
    """Assemble one particle per candidate from a draw's clustering."""
    weight_by_candidate: dict[str, float] = {}
    for cluster in divergence.clusters:
        for cid in cluster.candidate_ids:
            weight_by_candidate[cid] = cluster.weight

    fractions = contract_pass_fractions or {}
    particles: list[Particle] = []
    for cid, run in divergence.runs.items():
        type_ok = run.error is None and run.signature != (UNRUNNABLE,)
        particles.append(
            Particle(
                candidate_id=cid,
                source=run.source,
                type_ok=type_ok,
                contract_pass_fraction=fractions.get(cid),
                cluster_weight=weight_by_candidate.get(cid, 0.0),
            )
        )
    return particles


def select_head(
    divergence: DivergenceResult,
    *,
    contract_pass_fractions: Optional[dict[str, float]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Execution-ranked head: the highest-scoring particle wins.

    Returns ``(None, None)`` only when the draw produced no candidates at all.
    """
    particles = build_population(divergence, contract_pass_fractions=contract_pass_fractions)
    if not particles:
        return None, None
    best = max(particles, key=Particle.rank_key)
    return best.candidate_id, best.source


def score_candidates_against_contract(
    candidates: dict[str, str],
    *,
    slot: Any,
    slot_spec: Any,
    config: Any,
) -> Optional[dict[str, float]]:
    """Per-candidate pass fraction over the slot's active contract cases.

    Returns ``None`` when contracts are disabled or the slot has no active
    cases yet -- callers then fall back to cluster agreement alone (today's
    behavior). A candidate whose gist could not be replayed at all against the
    cases (as opposed to replaying and failing some) is left out of the
    returned mapping, so it ranks below any candidate that was actually
    checked, rather than being credited with an unearned pass.
    """
    if not getattr(config, "contract_enabled", True):
        return None
    from semipy.contract.access import load_active_cases
    from semipy.contract.runner import run_contract

    active = load_active_cases(slot)
    if not active:
        return None
    cap = int(getattr(config, "contract_max_cases", 25))
    cases = active[:cap]

    fractions: dict[str, float] = {}
    for cid, source in candidates.items():
        cr = run_contract(
            implementation_source=source,
            slot_spec=slot_spec,
            cases=cases,
            scaffold_source=getattr(slot_spec, "enclosing_function_source", None),
        )
        total = len(cr.evaluated_case_ids)
        if total == 0:
            continue
        fractions[cid] = 1.0 - (len(cr.failing_case_ids()) / total)
    return fractions
