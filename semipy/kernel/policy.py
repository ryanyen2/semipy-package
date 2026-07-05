"""Derived policy: the freeze cost model, search-budget math (Phase 3, §3.1),
and the routing priority cascade (Phase 6, §5).

Pure functions -- no side effects, no I/O. The freeze-cost knobs (``c_m``,
``c_e``, ``gamma_e``, ``epsilon``, ``delta``, ``gamma``) are estimates a
caller supplies or defaults; see the plan's §7 for the honest limitation that
a wrong cost input shifts *when* you freeze, not *whether* safety holds.
``decide_route`` has no knobs to estimate -- it is the existing deterministic
fingerprint/equivalence cascade (``routing.RoutingPolicy``'s old inline body),
relocated here so the *order of precedence* is one canonical, unit-testable
function instead of logic embedded in an I/O-touching class: ``RoutingPolicy``
still owns fetching the signals (locked-commit lookup, donor search, sketch
matching -- all real I/O), this owns deciding what they mean.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from semipy.types import Decision


def freeze_break_even(c_m: float, c_e: float, gamma_e: float) -> float:
    """The myopic break-even disagreement mass (§3.1):

        ε* = c_m / (γ_e · c_e)

    Freeze when the population's disagreement mass Δ(V) <= ε*. ``c_m`` is the
    per-call cost of staying molten/interpreted, ``c_e`` the one-time cost of a
    wrong commitment surfacing later, ``gamma_e`` the fraction of disagreement
    inputs that actually surface as failures.
    """
    if c_e <= 0:
        raise ValueError(f"c_e must be positive, got {c_e}")
    if gamma_e <= 0:
        raise ValueError(f"gamma_e must be positive, got {gamma_e}")
    return c_m / (gamma_e * c_e)


def counterexample_budget(epsilon: float, delta: float, gamma: float) -> int:
    """Query budget that licenses freezing at confidence ``1 - delta`` (§3.1).

    Under the detection-efficiency model (each query surfaces a disagreeing
    input w.p. >= gamma * epsilon when the true disagreement mass exceeds
    epsilon), a failed search of

        n >= log(delta) / log(1 - gamma * epsilon)

    queries rejects H0: Δ(V) > epsilon at confidence 1 - delta.
    """
    if not (0.0 < delta < 1.0):
        raise ValueError(f"delta must be in (0, 1), got {delta}")
    p = gamma * epsilon
    if not (0.0 < p < 1.0):
        raise ValueError(f"gamma * epsilon must be in (0, 1), got {p}")
    return math.ceil(math.log(delta) / math.log(1.0 - p))


# Structural description-length proxy for "stay molten/interpreted": a fixed
# small constant standing in for "call the LLM per row", since a molten node
# has no committed code to measure. Deliberately crude (§7: cost inputs are
# estimates); it only needs to be small relative to a real function body so a
# genuinely compressive residual can win and a memorized one-off cannot.
INTERPRETED_NODE_COST = 20


def mdl_compression_gain(
    candidate_source: str,
    example_outputs: list,
    *,
    match_fraction: float = 1.0,
) -> float:
    """ℓ(molten) + ℓ(E | molten) − (ℓ(p̂) + ℓ(E | p̂)), in characters (§3.1 MDL gate).

    Interpreted mode is a lookup table -- no compression -- so its evidence
    cost is the raw size of every example's output. The candidate's evidence
    cost is the fraction of examples it does *not* reproduce, charged at that
    same raw size (an unexplained example costs as much to record as it did
    under interpreted mode); ``match_fraction`` is 1.0 whenever the caller has
    already required perfect held-out reproduction, making that term zero.
    Positive means the candidate compresses the evidence -- freeze-eligible;
    zero or negative (a shape seen once) means it does not.
    """
    raw_cost = sum(len(repr(out)) for out in example_outputs)
    residual_cost = (1.0 - match_fraction) * raw_cost
    molten_cost = INTERPRETED_NODE_COST + raw_cost
    candidate_cost = len(candidate_source) + residual_cost
    return molten_cost - candidate_cost


def is_comparable_output(*, expected_type: object, labels: object) -> bool:
    """Declared ≈_Y availability (§2).

    A constrained label set or any concrete non-str type has a usable
    equivalence (exact match). Free-form text does not -- it is declared
    incomparable, so it is never freeze-eligible by construction: this is
    where honest non-convergence for summarize/judge comes from, not an
    accident of the held-out check happening to fail.
    """
    if labels:
        return True
    if expected_type in (None, str, type(None)):
        return False
    return True


@dataclass(frozen=True)
class RouteDecision:
    """Which of the four moves fires, and which already-fetched signal backs it.

    ``source`` tells the caller which of its own fetched objects (the locked
    commit, the head commit, the cross-slot donor, the sketch match) to attach
    to the result -- this function only decides precedence, it never fetches.
    """

    decision: Decision
    source: str  # "locked" | "head" | "donor" | "sketch" | "none"


def decide_route(
    *,
    has_slot: bool,
    is_locked: bool = False,
    force_regenerate: bool = False,
    has_head: bool = False,
    has_commits: bool = False,
    equiv_ok: bool = False,
    prior_validation_failure_kind: Optional[str] = None,
    semantic_wants_adapt: bool = False,
    donor_available: bool = False,
    sketch_available: bool = False,
) -> RouteDecision:
    """The routing priority cascade, as a pure function of resolved signals.

    Priority order (first match wins) -- unchanged from the pre-Phase-6
    ``RoutingPolicy.decide`` this replaces:
    no slot -> GENERATE; version lock -> REUSE; force_regenerate -> ADAPT from
    head/donor or GENERATE; an adapt-forcing prior validation failure -> ADAPT
    or GENERATE; a semantic recheck requesting adapt -> ADAPT or GENERATE;
    commits with equivalence mismatch -> INSTANTIATE (sketch) or ADAPT;
    commits with equivalence ok -> REUSE; no commits -> donor REUSE, else
    sketch INSTANTIATE, else GENERATE.
    """
    if not has_slot:
        return RouteDecision(Decision.GENERATE, "none")
    if is_locked:
        return RouteDecision(Decision.REUSE, "locked")
    if force_regenerate:
        if has_head:
            return RouteDecision(Decision.ADAPT, "head")
        if donor_available:
            return RouteDecision(Decision.ADAPT, "donor")
        return RouteDecision(Decision.GENERATE, "none")
    if prior_validation_failure_kind in ("type_mismatch", "empty_output", "identity_return"):
        return RouteDecision(Decision.ADAPT, "head") if has_head else RouteDecision(Decision.GENERATE, "none")
    if semantic_wants_adapt:
        return RouteDecision(Decision.ADAPT, "head") if has_head else RouteDecision(Decision.GENERATE, "none")
    if has_commits and not equiv_ok:
        if sketch_available:
            return RouteDecision(Decision.INSTANTIATE, "sketch")
        # Unlike the cases above, this ADAPT always carries a spec-hash branch
        # name even with no head to adapt from (an edge case unreachable in
        # practice: has_commits implies a head), matching the pre-Phase-6
        # RoutingPolicy exactly.
        return RouteDecision(Decision.ADAPT, "head")
    if has_commits and equiv_ok:
        return RouteDecision(Decision.REUSE, "head")
    if donor_available:
        return RouteDecision(Decision.REUSE, "donor")
    if sketch_available:
        return RouteDecision(Decision.INSTANTIATE, "sketch")
    return RouteDecision(Decision.GENERATE, "none")
