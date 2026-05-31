"""Forall-inputs proofs over reified effects -- the practical realization of
"verify it, do not just sample it".

For semipy's fixed selector vocabulary (AND-of-field-equalities), the genuine
proofs are decidable and dependency-free:

- ``bounded_blast_radius`` is a **schema theorem**: an ``update``/``delete`` whose
  selector contains a unique key pins at most one record, for all inputs and all
  artifact states; ``create``/``append`` insert exactly one. No SAT solver needed
  (a selector cardinality question over equalities reduces to a superkey lookup).
- ``append_only`` and ``target_whitelist`` are **structural theorems** over the
  generated source: if ``fx.delete`` never appears syntactically, no input can
  delete; if every literal target is in the allowlist (and none is computed), no
  input can escape it.

When a property cannot be settled statically (a computed target, a dynamic op),
the result is ``unknown`` and the caller falls back to the Stage-1 sample checks --
proofs never *weaken* safety, they only strengthen it. A concolic backend
(e.g. CrossHair) could later refine ``unknown`` into a concrete-input counterexample;
the :class:`ProofResult` shape leaves room for that without shipping untested code.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from semipy.effects.models import EffectScript

ProofStatus = Literal["proved", "refuted", "unknown"]

# Effect-emitting capability methods, split by whether they mutate state.
_MUTATING_METHODS = {"create", "update", "delete", "append"}
_DESTRUCTIVE_METHODS = {"delete"}


@dataclass
class ProofResult:
    invariant: str
    status: ProofStatus
    detail: str = ""
    counterexample: Optional[dict[str, Any]] = None
    per_effect: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # A gate treats only a clear refutation as a hard failure; ``unknown`` defers
        # to sample checks. ``bounded_blast_radius`` is the exception (see prove_*).
        return self.status != "refuted"


# --------------------------------------------------------------------------
# Schema-grounded: bounded blast radius (forall inputs)
# --------------------------------------------------------------------------
def prove_bounded_blast_radius(
    script: EffectScript,
    schema_for: Callable[[str], Any],
) -> ProofResult:
    """Prove every mutating effect affects at most one record.

    ``create``/``append`` insert exactly one. ``update``/``delete`` are bounded iff
    the selector contains a unique key of the target's schema. Returns ``proved``
    when all mutating effects are bounded, else ``unknown`` listing the effects
    whose cardinality could not be proven (the gate treats those as actionable).
    """
    unproven: list[str] = []
    for e in script.mutating():
        # create/append are a single insert; call is an opaque external op governed
        # by the approval gate, not record cardinality. Only update/delete need a key.
        if e.op not in ("update", "delete"):
            continue
        sel_keys = set((e.selector or {}).keys())
        sch = None
        try:
            sch = schema_for(e.target)
        except Exception:
            sch = None
        if sch is None or not sch.has_unique_subset(sel_keys):
            keys_uk = getattr(sch, "unique_keys", None)
            hint = ""
            if keys_uk:
                cols = sorted({c for uk in keys_uk for c in uk})
                hint = f" Use a selector that includes a unique key ({', '.join(cols)})."
            unproven.append(
                f"{e.short()}: selector {sorted(sel_keys) or '{}'} is not a unique key of "
                f"{e.target}, so it is not provably bounded to one record.{hint}"
            )
    if not unproven:
        return ProofResult("bounded_blast_radius", "proved",
                           detail="every mutating effect provably affects <= 1 record")
    return ProofResult("bounded_blast_radius", "unknown",
                       detail="; ".join(unproven), per_effect=unproven)


# --------------------------------------------------------------------------
# AST-structural: append_only / target_whitelist (forall inputs)
# --------------------------------------------------------------------------
def _fx_calls(source: str) -> tuple[list[tuple[str, ast.Call]], bool]:
    """Return ``([(method, call_node)], has_dynamic_dispatch)`` for fx.<method>(...) calls.

    ``has_dynamic_dispatch`` is True when the code reaches the capability through a
    computed attribute (e.g. ``getattr(fx, op)``), which defeats the static proof.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], True
    calls: list[tuple[str, ast.Call]] = []
    dynamic = False
    for node in ast.walk(tree):
        # getattr(fx, <expr>) anywhere -> dynamic dispatch
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "fx":
                dynamic = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Name) and val.id == "fx":
                calls.append((node.func.attr, node))
    return calls, dynamic


def prove_append_only(source: str) -> ProofResult:
    """Prove the function never deletes, for any input (no reachable fx.delete)."""
    calls, dynamic = _fx_calls(source)
    if dynamic:
        return ProofResult("append_only", "unknown",
                           detail="capability method is computed (getattr); cannot prove statically")
    if any(m in _DESTRUCTIVE_METHODS for m, _ in calls):
        return ProofResult("append_only", "refuted",
                           detail="the implementation contains a delete; it is not append-only")
    return ProofResult("append_only", "proved",
                       detail="no delete is reachable in the implementation")


def prove_target_whitelist(source: str, whitelist: set[str]) -> ProofResult:
    """Prove every emitted target is in ``whitelist`` (for any input).

    A target given as a string literal is checked directly; a computed target makes
    the property ``unknown``.
    """
    calls, _dynamic = _fx_calls(source)
    offending: list[str] = []
    saw_dynamic_target = False
    for method, node in calls:
        if method not in (_MUTATING_METHODS | {"read", "call"}):
            continue
        target_node = node.args[0] if node.args else None
        if isinstance(target_node, ast.Constant) and isinstance(target_node.value, str):
            if target_node.value not in whitelist:
                offending.append(target_node.value)
        elif target_node is not None:
            saw_dynamic_target = True
    if offending:
        return ProofResult("target_whitelist", "refuted",
                           detail=f"targets not in the allowlist: {sorted(set(offending))}")
    if saw_dynamic_target:
        return ProofResult("target_whitelist", "unknown",
                           detail="a target is computed at runtime; cannot prove statically")
    return ProofResult("target_whitelist", "proved",
                       detail="all emitted targets are in the allowlist")
