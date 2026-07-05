"""``freeze`` -- certified posterior collapse (Phase 3, §3.1).

Generalizes ``interpreted.attempt_promotion``'s single held-out check into the
three gates the plan requires: held-out reproducibility (reusing
``interpreted``'s existing synthesize/validate primitives unchanged), an MDL
gate (does the candidate actually compress the evidence, or did it just
memorize a handful of examples), and a counterexample license (a budgeted
search across the residual candidates this synthesis pass drew, reusing
``decisions.discriminate.search_discriminating_inputs`` -- the same searcher
the molten-candidate pipeline already uses). A free-text output (no usable
``≈_Y``) is refused outright before any gate runs: summarize/judge-style
slots are never freeze-eligible, by construction (§2, §4 Prop 4).

``slot_resolver._execute_interpreted_slot`` (the live, portal-integrated
promotion path) calls this directly and persists the resulting ``FreezeEvent``
on the slot. ``interpreted.attempt_promotion`` itself is unchanged and still
backs the standalone ``InterpretedOp`` (experiments/tests, not portal-wired);
the three-gate freeze here is strictly more conservative, so some promotions
the old single-gate check granted (e.g. a residual matched from only 1-2
examples, which cannot compress the evidence) are now correctly refused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from semipy.kernel.policy import (
    counterexample_budget,
    is_comparable_output,
    mdl_compression_gain,
)


@dataclass
class FreezeCertificate:
    """The recorded license (or refusal) for one freeze attempt (§3.1)."""

    epsilon: float
    delta: float
    gamma: float
    budget_total: int
    budget_spent: int
    held_out_pass_fraction: float
    mdl_gain: float
    licensed: bool
    refusal_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "epsilon": self.epsilon,
            "delta": self.delta,
            "gamma": self.gamma,
            "budget_total": self.budget_total,
            "budget_spent": self.budget_spent,
            "held_out_pass_fraction": self.held_out_pass_fraction,
            "mdl_gain": self.mdl_gain,
            "licensed": self.licensed,
            "refusal_reasons": list(self.refusal_reasons),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreezeCertificate":
        return cls(
            epsilon=d.get("epsilon", 0.0),
            delta=d.get("delta", 0.0),
            gamma=d.get("gamma", 0.0),
            budget_total=d.get("budget_total", 0),
            budget_spent=d.get("budget_spent", 0),
            held_out_pass_fraction=d.get("held_out_pass_fraction", 0.0),
            mdl_gain=d.get("mdl_gain", 0.0),
            licensed=d.get("licensed", False),
            refusal_reasons=list(d.get("refusal_reasons", [])),
        )


@dataclass
class FreezeEvent:
    """One freeze attempt: its certificate plus which node it targeted."""

    certificate: FreezeCertificate
    node_id: str = ""
    source_len: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "certificate": self.certificate.to_dict(),
            "node_id": self.node_id,
            "source_len": self.source_len,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreezeEvent":
        return cls(
            certificate=FreezeCertificate.from_dict(d.get("certificate", {})),
            node_id=d.get("node_id", ""),
            source_len=d.get("source_len", 0),
            timestamp=d.get("timestamp", 0.0),
        )


def _refused(
    reasons: list[str],
    *,
    epsilon: float,
    delta: float,
    gamma: float,
    budget_total: int = 0,
    budget_spent: int = 0,
    held_out_pass_fraction: float = 0.0,
    mdl_gain: float = 0.0,
    node_id: str = "",
) -> tuple[None, FreezeEvent]:
    import time

    cert = FreezeCertificate(
        epsilon=epsilon, delta=delta, gamma=gamma,
        budget_total=budget_total, budget_spent=budget_spent,
        held_out_pass_fraction=held_out_pass_fraction, mdl_gain=mdl_gain,
        licensed=False, refusal_reasons=reasons,
    )
    return None, FreezeEvent(certificate=cert, node_id=node_id, source_len=0, timestamp=time.time())


def freeze_eligibility_floor(cases: Sequence[Any]) -> tuple[bool, list[str]]:
    """The freeze-eligibility floor (Phase 4; §4 Prop 2's side condition, "no
    contract, no blame"): a node needs at least one discriminating case (a
    concrete example/invariant assertion) *and* at least one non-vacuous
    metamorphic relation in its active contract before its evidence is
    trusted enough to license a freeze -- otherwise the floor is satisfied
    only vacuously (see ``contract.relations.is_relation_nonvacuous``).
    Returns ``(met, reasons)``.
    """
    from semipy.contract.relations import is_relation_nonvacuous

    active = [c for c in cases if getattr(c, "status", "active") == "active"]
    has_discriminating = any(getattr(c, "kind", "") in ("example", "invariant") for c in active)
    has_nonvacuous_relation = any(
        getattr(c, "kind", "") == "metamorphic"
        and is_relation_nonvacuous(getattr(c, "relation", ""), getattr(c, "primary_input", None))
        for c in active
    )
    reasons: list[str] = []
    if not has_discriminating:
        reasons.append("freeze-eligibility floor: no discriminating case (example/invariant) in the active contract")
    if not has_nonvacuous_relation:
        reasons.append("freeze-eligibility floor: no non-vacuous metamorphic relation in the active contract")
    return (has_discriminating and has_nonvacuous_relation), reasons


def freeze(
    *,
    instruction: str,
    free_variables: Sequence[str],
    examples: Sequence[tuple[Sequence[Any], Any]],
    expected_type: Any = None,
    output_names: Optional[Sequence[str]] = None,
    labels: Optional[Sequence[str]] = None,
    epsilon: float = 0.05,
    delta: float = 0.1,
    gamma: float = 1.0,
    timeout: int = 30,
    e2b_api_key: Optional[str] = None,
    samples: int = 2,
    node_id: str = "",
    cases: Optional[Sequence[Any]] = None,
) -> tuple[Optional[str], FreezeEvent]:
    """Attempt certified posterior collapse for one interpreted node.

    Returns ``(residual_source, event)``. ``residual_source`` is ``None`` unless
    all gates license the freeze; ``event`` always carries a certificate
    (licensed or not) with the reasons, for the evidence ledger.

    ``cases`` is optional: when supplied (a slot's active ``ContractCase``
    list), the Phase 4 freeze-eligibility floor is enforced before anything
    else runs. Interpreted-mode slots have no contract, so the live
    ``slot_resolver`` call site does not pass it -- the floor only applies
    once a caller has real contract-backed evidence to check.
    """
    import time

    from semipy.decisions.discriminate import search_discriminating_inputs
    from semipy.interpreted import split_holdout, synthesize_residual_source, validate_residual

    if not is_comparable_output(expected_type=expected_type, labels=labels):
        return _refused(
            ["output type has no usable ≈_Y (free text); never freeze-eligible"],
            epsilon=epsilon, delta=delta, gamma=gamma, node_id=node_id,
        )

    if cases is not None:
        floor_met, floor_reasons = freeze_eligibility_floor(cases)
        if not floor_met:
            return _refused(floor_reasons, epsilon=epsilon, delta=delta, gamma=gamma, node_id=node_id)

    budget_total = counterexample_budget(epsilon, delta, gamma)

    train, holdout = split_holdout(examples)
    candidates: dict[str, str] = {}
    for i in range(max(1, samples)):
        src = synthesize_residual_source(
            instruction, free_variables, train, output_names=output_names, labels=labels,
        )
        if src and src not in candidates.values():
            candidates[f"r{i}"] = src

    if not candidates:
        return _refused(
            ["no residual candidate compiled"],
            epsilon=epsilon, delta=delta, gamma=gamma, budget_total=budget_total, node_id=node_id,
        )

    # Gate 1: held-out reproducibility -- the best of the candidates drawn.
    best_src: Optional[str] = None
    best_frac = 0.0
    for src in candidates.values():
        _ok, frac = validate_residual(src, holdout, timeout=timeout, e2b_api_key=e2b_api_key)
        if best_src is None or frac > best_frac:
            best_src, best_frac = src, frac
    held_out_licensed = best_frac >= 1.0
    reasons: list[str] = []
    if not held_out_licensed:
        reasons.append(f"held-out reproducibility failed ({best_frac:.2f} < 1.00)")

    # Gate 2: counterexample license -- search for disagreement among the
    # residual committee this synthesis pass actually drew.
    budget_spent = 0
    disagreement_found = False
    if held_out_licensed:
        if len(candidates) > 1:
            rows = [
                {fv: a for fv, a in zip(free_variables, args)} for args, _ in examples
            ]
            disc = search_discriminating_inputs(
                candidates, free_variables=list(free_variables), base_rows=rows,
                output_names=list(output_names or []), timeout=timeout,
            )
            budget_spent = disc.tried
            disagreement_found = disc.found
            if disagreement_found:
                reasons.append(f"counterexample search found disagreement (germ={disc.germ})")
        else:
            reasons.append("counterexample license not evaluated: only one residual candidate drawn")

    # Gate 3: MDL -- the compact rule must actually compress the evidence.
    mdl_gain = 0.0
    mdl_licensed = False
    if held_out_licensed and not disagreement_found:
        mdl_gain = mdl_compression_gain(
            best_src or "", [out for _, out in examples], match_fraction=1.0,
        )
        mdl_licensed = mdl_gain > 0
        if not mdl_licensed:
            reasons.append(f"MDL gate: candidate does not compress the evidence (gain={mdl_gain:.1f} <= 0)")

    licensed = held_out_licensed and not disagreement_found and mdl_licensed
    cert = FreezeCertificate(
        epsilon=epsilon, delta=delta, gamma=gamma,
        budget_total=budget_total, budget_spent=budget_spent,
        held_out_pass_fraction=best_frac, mdl_gain=mdl_gain,
        licensed=licensed, refusal_reasons=reasons,
    )
    event = FreezeEvent(
        certificate=cert, node_id=node_id,
        source_len=len(best_src or "") if licensed else 0,
        timestamp=time.time(),
    )
    return (best_src if licensed else None), event


def get_freeze_events(slot: Any) -> list[FreezeEvent]:
    """Return the slot's persisted freeze-attempt history (oldest first)."""
    raw = getattr(slot, "freeze_events", None) or []
    return [FreezeEvent.from_dict(d) for d in raw]


def append_freeze_event(slot: Any, event: FreezeEvent) -> None:
    """Append one freeze attempt to the slot's ledger (caller saves the portal)."""
    raw = list(getattr(slot, "freeze_events", None) or [])
    raw.append(event.to_dict())
    slot.freeze_events = raw


def frozen_fraction(slot: Any) -> float:
    """F(slot): 1.0 if the slot's most recent freeze attempt was licensed, else
    0.0 (molten by default, including "never attempted").

    Scoped honestly: Phase 1's trees are degenerate (single opaque node) for
    every slot today, so freezing is currently a whole-slot, binary event, not
    yet a per-node fraction -- that generalization needs the tree to actually
    back execution (Phase 6), not just be computed alongside it.
    """
    events = get_freeze_events(slot)
    if not events:
        return 0.0
    return 1.0 if events[-1].certificate.licensed else 0.0
