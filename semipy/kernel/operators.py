"""``freeze`` and ``melt`` -- two of the four certified moves (Phase 3-4, §3.1-3.2).

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

``melt`` (Phase 4) is local rejuvenation: blame a failing case down to the
shallowest node that reproduces it (``kernel.blame``), then splice an
already-regenerated replacement for that node back into the original source
(``kernel.tree.patch_source``) rather than discarding the whole function.
Live-wired (opt-in via ``config.melt_on_contract_failure``): when a candidate
fails an active "example" case inside ``slot_resolver``'s contract-gate retry
loop, ``_try_melt_for_example_case`` blames the failure, synthesizes a
replacement for just the blamed MAP/FILTER leaf (one small LLM call scoped to
that leaf, via ``interpreted.synthesize_residual_source``), and patches it in
-- tried before a full-function regeneration, never instead of the gate's own
re-verification. Neither the tree nor the patch is ever persisted; both are
recomputed on demand from the candidate's own source and discarded after.

``branch`` and ``merge`` (Phase 5, §3.3-3.4) round out the four moves.
``branch`` compiles LLM-proposed guard strings against the closed DSL
(``kernel.guard``) and is licensed only when at least one compiles -- an
unverified guard never dispatches. ``synthesize_separating_guard`` is the
live-wired proposal step feeding it: given two concrete, already-known
disagreement points (a contract case's own input vs. the input that drove a
conflicting regeneration), it tries a small closed template bank first, then
escalates to one scoped LLM call, and independently evaluates whatever it
gets before trusting it. Live-wired (opt-in via ``config.branch_on_quarantine``):
when the contract gate's retry budget is exhausted and a case would be
quarantined, ``slot_resolver._try_branch_split`` tries this instead --
licenses the guard, then ``kernel.tree.build_branch_wrapper`` combines the two
*whole* candidate implementations behind it (no tree/patch_source needed here;
every live slot is still one opaque node, so this operates at the whole-function
level, not a sub-node splice). ``merge`` verifies a candidate unified structure
against both branches' own evidence, a fresh separation search (reusing the
same discriminating-input searcher freeze's counterexample license uses), and
an MDL comparison, before collapsing two branches into one. Unlike branch,
merge is still additive: nothing yet produces two branch-shaped artifacts of
the same slot for it to act on.

``license_sketch`` (Phase 6, §5 library/) applies the same discipline to
cross-slot pattern reuse: ``library.merge_sketch_into_library``'s single-shot,
LLM-self-reported-confidence gate is replaced with recurrence (the pattern
must show up across more than one independently generated occurrence),
generalization (the template built from the first occurrence must actually
reproduce a later, independent one -- not just replay what it memorized), and
an MDL check (the template must compress relative to storing raw sources).
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
    # Whether the counterexample search (§3.1's hypothesis test on Δ) actually
    # ran. It can only run when the committee drew >1 distinct candidate to
    # search for disagreement among; a deterministic proposal that collapses to
    # a single candidate leaves this False, so ``budget_total`` (the theoretical
    # budget for the configured ε/δ/γ) is not misread as a search that happened.
    # A freeze with this False rests on held-out reproducibility + MDL alone.
    counterexample_evaluated: bool = False

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
            "counterexample_evaluated": self.counterexample_evaluated,
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
            counterexample_evaluated=d.get("counterexample_evaluated", False),
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
    counterexample_evaluated = False
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
            counterexample_evaluated = True
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
        counterexample_evaluated=counterexample_evaluated,
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


@dataclass
class MeltResult:
    """The outcome of one local-rejuvenation attempt."""

    patched_source: Optional[str]
    blamed_node_id: str
    blamed_kind: str
    blame_reason: str
    patch_target_id: str
    locality: float


def melt(
    original_source: str,
    root_id: str,
    *,
    free_variables: dict[str, Any],
    expected_output: Any,
    new_node_source: str,
) -> MeltResult:
    """Local rejuvenation (§3.2): blame the failing case, then splice
    *new_node_source* -- an already-regenerated replacement -- back into
    *original_source* at exactly the blamed node's position.

    The caller is responsible for shaping *new_node_source* to match what
    ``patch_target_id`` needs: a small ``def name(params): return expr``
    for a MAP/FILTER leaf (``blamed_kind in ("map", "filter")``), or a
    replacement statement list wrapped in the original function's own
    signature for anything else (a BRANCH arm, a whole segment). melt does
    not validate that the replacement's shape is sensible for the slot --
    that trust boundary belongs to whatever generates it (an LLM call scoped
    to the blamed node, in the eventual live wiring).

    ``patched_source`` is ``None`` when blame could not localize past the
    root (regenerate the whole function -- today's behavior, unchanged) or
    when the splice itself is out of ``patch_source``'s scope; either way the
    caller falls back to whole-function regeneration rather than guessing.
    """
    from semipy.kernel.blame import blame, locality_metric
    from semipy.kernel.tree import lower_source_to_tree, patch_source

    tree = lower_source_to_tree(original_source, root_id)
    result = blame(tree, free_variables=free_variables, expected_output=expected_output)
    locality = locality_metric(tree, result.node_id)

    patch_target_id = result.node_id
    if result.kind == "map":
        patch_target_id = f"{result.node_id}.map.body"
    elif result.kind == "filter":
        patch_target_id = f"{result.node_id}.filter.pred"

    patched: Optional[str] = None
    if result.node_id != root_id:
        patched = patch_source(original_source, root_id, patch_target_id, new_node_source)

    return MeltResult(
        patched_source=patched,
        blamed_node_id=result.node_id,
        blamed_kind=result.kind,
        blame_reason=result.reason,
        patch_target_id=patch_target_id,
        locality=locality,
    )


@dataclass
class BranchEvent:
    """The outcome of compiling a proposed regime split into guards."""

    guards: list[str]
    rejected_guards: list[str]
    licensed: bool
    reason: str


def branch(proposed_guards: Sequence[str]) -> BranchEvent:
    """Compile and validate LLM-proposed guard strings into the closed DSL
    (§3.3). A guard that does not compile is excluded, not patched up or
    guessed at -- the node stays molten on that regime rather than
    dispatching on an unverified predicate. Licensed once at least one guard
    compiles (a mixture needs at least one real discriminator; an all-reject
    result is indistinguishable from "no split was actually found").
    """
    from semipy.kernel.guard import compile_guard

    compiled: list[str] = []
    rejected: list[str] = []
    for g in proposed_guards:
        if compile_guard(g) is not None:
            compiled.append(g)
        else:
            rejected.append(g)
    licensed = bool(compiled)
    reason = (
        "at least one guard compiled against the closed predicate DSL" if licensed
        else "no proposed guard compiled against the closed predicate DSL; node stays molten"
    )
    return BranchEvent(guards=compiled, rejected_guards=rejected, licensed=licensed, reason=reason)


_GUARD_ELIGIBLE_TYPENAMES = frozenset(
    {"int", "float", "str", "bool", "list", "dict", "tuple", "set", "frozenset", "bytes"}
)


def _template_guard_candidates(var: str, old_value: Any, new_value: Any) -> list[str]:
    """A small, closed bank of guard shapes over one shared free variable, derived
    from a structural property of *old_value* alone -- the side the guard must
    license True for. Every candidate stays inside ``kernel.guard``'s grammar by
    construction; callers still evaluate each one before trusting it."""
    candidates: list[str] = []
    old_t, new_t = type(old_value).__name__, type(new_value).__name__
    if old_t != new_t and old_t in _GUARD_ELIGIBLE_TYPENAMES:
        candidates.append(f"isinstance({var}, {old_t})")
    if old_value is None:
        candidates.append(f"{var} is None")
    if hasattr(old_value, "__len__"):
        candidates.append(f"len({var}) == 0" if len(old_value) == 0 else f"len({var}) > 0")
    if isinstance(old_value, (int, float)) and not isinstance(old_value, bool):
        candidates.append(f"{var} == {old_value!r}" if abs(old_value) < 1e6 else f"{var} > 0")
    return candidates


def _propose_guard_via_llm(
    *, old_input: dict[str, Any], new_input: dict[str, Any], free_variables: Sequence[str]
) -> Optional[str]:
    """Escalate to one scoped LLM call for a separating predicate, reusing
    ``interpreted.synthesize_residual_source`` (the same primitive freeze uses)
    with a two-row True/False training set instead of a domain instruction. The
    result is trusted only as a *proposal*: the caller independently evaluates it
    against both concrete inputs before accepting it."""
    if not free_variables:
        return None
    from semipy.interpreted import synthesize_residual_source

    args_old = [old_input.get(v) for v in free_variables]
    args_new = [new_input.get(v) for v in free_variables]
    instruction = (
        "Return a single boolean expression (not a multi-statement function body) "
        "using only comparisons, isinstance/len/type, and and/or/not over the given "
        "variables. It must evaluate True for the first example and False for the second."
    )
    src = synthesize_residual_source(instruction, free_variables, [(args_old, True), (args_new, False)])
    if not src:
        return None
    try:
        import ast as _ast

        fn = _ast.parse(src).body[0]
        if not isinstance(fn, _ast.FunctionDef) or len(fn.body) != 1 or not isinstance(fn.body[0], _ast.Return):
            return None
        return _ast.unparse(fn.body[0].value) if fn.body[0].value is not None else None
    except SyntaxError:
        return None


def synthesize_separating_guard(
    *, old_input: dict[str, Any], new_input: dict[str, Any]
) -> Optional[str]:
    """Find a guard (§3.3's closed DSL) that separates two concrete, already-known
    disagreement points: it must evaluate True on *old_input* (the case whose
    behavior must be preserved) and False on *new_input* (the input that drove the
    conflicting regeneration). Tries a small closed template bank first (no LLM
    call, deterministic); escalates to one scoped LLM proposal only if nothing in
    the bank separates the two. Every candidate -- template or LLM-proposed -- is
    independently evaluated against both concrete inputs before being returned; an
    LLM's syntactically-valid-but-wrong proposal is caught here, not trusted.
    Returns ``None`` if no guard in scope separates them (the caller falls back to
    whatever it would have done otherwise -- e.g. quarantining a case).
    """
    from semipy.kernel.guard import compile_guard

    def _separates(guard_src: str) -> bool:
        compiled = compile_guard(guard_src)
        if compiled is None:
            return False
        return compiled.evaluate(old_input) is True and compiled.evaluate(new_input) is False

    shared_vars = [v for v in old_input if v in new_input]
    for var in shared_vars:
        for candidate in _template_guard_candidates(var, old_input[var], new_input.get(var)):
            if _separates(candidate):
                return candidate

    proposed = _propose_guard_via_llm(old_input=old_input, new_input=new_input, free_variables=shared_vars)
    if proposed and _separates(proposed):
        return proposed
    return None


@dataclass
class MergeEvent:
    """The outcome of one verified-mixture-collapse attempt."""

    licensed: bool
    reason: str
    unified_source: Optional[str] = None


def merge(
    *,
    branch_a_source: str,
    branch_b_source: str,
    candidate_unified_source: str,
    branch_a_examples: Sequence[tuple[Sequence[Any], Any]],
    branch_b_examples: Sequence[tuple[Sequence[Any], Any]],
    free_variables: Sequence[str],
    output_names: Optional[Sequence[str]] = None,
    timeout: int = 15,
) -> MergeEvent:
    """Verified mixture collapse (§3.4): merge two branches into one
    structure only if the candidate (i) reproduces both branches' own
    evidence, (ii) survives a fresh separation search against each original
    branch (the same hypothesis test as freeze's counterexample license,
    §3.1, applied to the pair), and (iii) MDL favors the union over keeping
    two branches. Merge-on-shape-congruence alone is not enough -- any one
    gate failing keeps the branches distinct.
    """
    from semipy.decisions.discriminate import search_discriminating_inputs
    from semipy.interpreted import validate_residual

    all_examples = list(branch_a_examples) + list(branch_b_examples)

    ok, frac = validate_residual(candidate_unified_source, all_examples, timeout=timeout)
    if not ok:
        return MergeEvent(
            licensed=False,
            reason=f"candidate does not reproduce both branches' evidence ({frac:.2f} match)",
        )

    candidates = {
        "unified": candidate_unified_source,
        "branch_a": branch_a_source,
        "branch_b": branch_b_source,
    }
    rows = [{fv: a for fv, a in zip(free_variables, args)} for args, _ in all_examples] or [{}]
    disc = search_discriminating_inputs(
        candidates, free_variables=list(free_variables), base_rows=rows,
        output_names=list(output_names or []), timeout=timeout,
    )
    if disc.found:
        return MergeEvent(
            licensed=False,
            reason=f"separation search found a splitting input (germ={disc.germ}); branches remain distinct",
        )

    mdl_gain = (len(branch_a_source) + len(branch_b_source)) - len(candidate_unified_source)
    if mdl_gain <= 0:
        return MergeEvent(
            licensed=False,
            reason=f"MDL gate: unified structure is not shorter than the two branches (gain={mdl_gain} <= 0)",
        )

    return MergeEvent(
        licensed=True,
        reason="candidate reproduces both branches, survives separation search, and MDL favors the union",
        unified_source=candidate_unified_source,
    )


@dataclass
class SketchLicense:
    """The outcome of one sketch cross-slot-reuse licensing attempt (Phase 6)."""

    licensed: bool
    reason: str
    recurrence: int
    mdl_gain: float


def license_sketch(
    sketch: Any,  # library.sketch.CodeSketch, already merged with this occurrence
    *,
    incoming_spec_text: str,
    incoming_source: str,
    min_recurrence: int = 2,
) -> SketchLicense:
    """License a code sketch for cross-slot matching (§5 library/).

    Replaces the single-shot LLM-confidence gate with three checks, run in the
    same spirit as ``freeze``'s: **recurrence** (the structural pattern has
    shown up across at least ``min_recurrence`` occurrences -- one occurrence
    proves nothing); **generalization** (the template built from the first
    occurrence, re-instantiated against *this* occurrence's own spec text,
    must reproduce this occurrence's independently generated source -- proof
    the template actually captures the reusable structure rather than
    memorizing instance one); and **MDL** (storing the template once plus one
    hole-value set per occurrence must be cheaper than storing every
    occurrence's raw source). Call only while the sketch is not yet licensed
    -- licensing is sticky (never re-run against a sketch already licensed),
    matching the calculus's monotone-safety discipline: a later occurrence
    that fails to fit the template does not retroactively revoke a license
    earned from evidence that still stands.
    """
    from semipy.library.sketch import instantiate_sketch_code, match_spec_to_sketch

    recurrence = len(sketch.source_commit_ids)
    if recurrence < min_recurrence:
        return SketchLicense(
            licensed=False,
            reason=f"recurrence {recurrence} < required {min_recurrence}",
            recurrence=recurrence, mdl_gain=0.0,
        )

    params_map = {p.hole_name: p for p in sketch.params}
    hole_values = match_spec_to_sketch(incoming_spec_text, sketch.spec_template, params_map)
    if hole_values is None:
        return SketchLicense(
            licensed=False,
            reason="this occurrence's spec text no longer fits the template's token pattern",
            recurrence=recurrence, mdl_gain=0.0,
        )

    reinstantiated = instantiate_sketch_code(sketch, hole_values)
    try:
        import ast as _ast
        reproduced = _ast.dump(_ast.parse(reinstantiated)) == _ast.dump(_ast.parse(incoming_source))
    except SyntaxError:
        reproduced = False
    if not reproduced:
        return SketchLicense(
            licensed=False,
            reason="template does not reproduce this independently generated occurrence",
            recurrence=recurrence, mdl_gain=0.0,
        )

    raw_cost = recurrence * len(incoming_source)
    template_cost = len(sketch.code_template) + recurrence * len(repr(hole_values))
    mdl_gain = raw_cost - template_cost
    if mdl_gain <= 0:
        return SketchLicense(
            licensed=False,
            reason=f"template does not compress evidence relative to raw sources (gain={mdl_gain} <= 0)",
            recurrence=recurrence, mdl_gain=mdl_gain,
        )

    return SketchLicense(
        licensed=True,
        reason="pattern recurred, generalized to a new occurrence, and compresses",
        recurrence=recurrence, mdl_gain=mdl_gain,
    )


# ---------------------------------------------------------------------------
# Scope (U2, R3-R4): mint a scope predicate at commit/freeze from the evidence
# ledger's input profiles, and record every out-of-scope deopt as a ledger event
# with a running frequency statistic (for a later relaxation pass -- this unit
# ships the statistic, not the relaxation).
# ---------------------------------------------------------------------------


def mint_scope(profiles: Sequence[dict]) -> Any:
    """Mint the scope predicate for a slot's accumulated input profiles.

    Thin commit/freeze-time entry point over ``kernel.guard.synthesize_scope``;
    the returned ``ScopePredicate`` is serializable and carries a stable
    ``predicate_id`` for other modules to reference."""
    from semipy.kernel.guard import synthesize_scope

    return synthesize_scope(list(profiles))


@dataclass
class ScopeDeoptEvent:
    """One out-of-scope reuse (R4): a scope predicate rejected an input, so it was
    routed to verify/adapt instead of running the artifact silently."""

    slot_id: str = ""
    commit_id: str = ""
    predicate_id: str = ""
    violated_conjunct: str = ""
    observed_profile: dict = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "commit_id": self.commit_id,
            "predicate_id": self.predicate_id,
            "violated_conjunct": self.violated_conjunct,
            "observed_profile": dict(self.observed_profile),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScopeDeoptEvent":
        return cls(
            slot_id=d.get("slot_id", ""),
            commit_id=d.get("commit_id", ""),
            predicate_id=d.get("predicate_id", ""),
            violated_conjunct=d.get("violated_conjunct", ""),
            observed_profile=dict(d.get("observed_profile") or {}),
            timestamp=d.get("timestamp", 0.0),
        )


_SCOPE_DEOPT_KEY = "scope_deopts"
_SCOPE_STATS_KEY = "scope_stats"


def _scope_advisor_state(slot: Any) -> dict:
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        adv = {}
        slot.advisor_state = adv
    return adv


def record_scope_check(slot: Any, in_scope: bool) -> None:
    """Count one scope-membership check (in or out of scope). The running
    ``deopts / checks`` ratio is the over-tight-scope statistic a later relaxation
    pass consumes."""
    stats = _scope_advisor_state(slot).setdefault(_SCOPE_STATS_KEY, {"checks": 0, "deopts": 0})
    stats["checks"] = int(stats.get("checks", 0)) + 1


def append_deopt_event(slot: Any, event: ScopeDeoptEvent) -> None:
    """Append one out-of-scope deopt to the slot's ledger (caller saves the portal)."""
    adv = _scope_advisor_state(slot)
    adv.setdefault(_SCOPE_DEOPT_KEY, []).append(event.to_dict())
    stats = adv.setdefault(_SCOPE_STATS_KEY, {"checks": 0, "deopts": 0})
    stats["deopts"] = int(stats.get("deopts", 0)) + 1


def get_deopt_events(slot: Any) -> list[ScopeDeoptEvent]:
    """Return the slot's persisted out-of-scope deopt history (oldest first)."""
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        return []
    return [ScopeDeoptEvent.from_dict(d) for d in adv.get(_SCOPE_DEOPT_KEY, [])]


def deopt_frequency(slot: Any) -> float:
    """Out-of-scope deopts as a fraction of scope checks -- the over-tight-scope
    signal. 0.0 when no scope check has run."""
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        return 0.0
    stats = adv.get(_SCOPE_STATS_KEY) or {}
    checks = int(stats.get("checks", 0))
    if checks <= 0:
        return 0.0
    return int(stats.get("deopts", 0)) / checks
