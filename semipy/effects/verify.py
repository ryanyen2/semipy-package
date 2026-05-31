"""Static verification of an EffectScript against effect invariants.

Stage 1 enforces the two invariants that are useful with zero per-slot
declaration -- decidable by structural analysis of the reified script plus the
compensations the shadow filled in:

- ``reversible``: every mutating effect carries a compensation, so the change can
  be undone. A missing inverse (e.g. a multi-record delete the backend cannot
  invert with one effect) fails here, before anything is applied for real.
- ``bounded_blast_radius`` (unbounded guard): a mutating ``update``/``delete`` must
  carry a selector. A selectorless mutation targets every record -- the "wipe the
  table" catastrophe -- and is rejected.

Richer, learned invariants (target_whitelist, append_only, an observed record
bound) and the precise parent-vs-new blast-radius *regression* arrive with the
ledger (Stage 4) and the effect-state diff (Stage 2). SMT/concolic proofs over all
inputs arrive in Stage 3. Each failure carries a descriptive ``failure_kind`` used
in the gate's regeneration message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from semipy.effects.models import EffectScript

# Descriptive failure-kind labels (used in regeneration messages / logs).
FAILURE_KINDS = {
    "append_only": "effect_destructive",
    "bounded_blast_radius": "effect_blast_radius",
    "target_whitelist": "effect_target_violation",
    "reversible": "effect_irreversible",
    "idempotent_effect": "effect_nonidempotent",
}


@dataclass
class EffectFailure:
    invariant: str
    failure_kind: str
    message: str


@dataclass
class EffectVerifyResult:
    passed: bool
    failures: list[EffectFailure] = field(default_factory=list)

    def first_message(self) -> str:
        return self.failures[0].message if self.failures else ""

    def summary(self) -> str:
        return "; ".join(f.message for f in self.failures)


def verify_static(
    script: EffectScript,
    is_external: Optional[Callable[[str], bool]] = None,
) -> EffectVerifyResult:
    """Check ``reversible`` and the unbounded-blast-radius guard over ``script``.

    Assumes the script was produced against a bound shadow world, so each mutating
    effect's ``compensation`` is populated when the backend could invert it.
    ``is_external`` (when given) marks targets whose backend is non-shadowable; those
    effects are exempt from the reversible check (they are governed by the approval
    gate + idempotency, not shadow-revert).
    """
    failures: list[EffectFailure] = []
    for eff in script.mutating():
        external = bool(is_external(eff.target)) if is_external else False
        if eff.op in ("update", "delete") and not eff.selector:
            failures.append(
                EffectFailure(
                    "bounded_blast_radius",
                    FAILURE_KINDS["bounded_blast_radius"],
                    f"{eff.short()} has no selector, so it would affect EVERY record in "
                    f"{eff.target} (unbounded). Add a selector that targets only the "
                    f"intended record(s).",
                )
            )
        if not external and eff.compensation is None:
            failures.append(
                EffectFailure(
                    "reversible",
                    FAILURE_KINDS["reversible"],
                    f"{eff.short()} is not reversible: no compensation could be derived "
                    f"(it cannot be undone). Narrow it to an invertible, record-level "
                    f"change (e.g. a single-record update/delete by key).",
                )
            )
    return EffectVerifyResult(passed=not failures, failures=failures)
