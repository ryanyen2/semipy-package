"""U9: the floor gate (R16) -- no consumer-site candidate commits without
replay-passing the shipped floor.

The shipped floor (``_semiformal/contracts/<key>.json``, U6/R14) is the
library author's own accumulated evidence: every ``ship=True`` active case,
each one a prior decision that eliminated a known-bad behavior. A consumer
who adapts against an installed baseline (U8) must keep passing that floor --
it composes with (never replaces) whatever local overlay cases the consumer's
own contract gate (``slot_resolver._run_generate_contract_gate``) enforces.

Two things live here:
  - ``installed_floor_for``: load the shipped artifact + floor cases for a
    slot's call site, so ``slot_resolver`` can (a) parent an adaptation on the
    shipped artifact instead of generating from scratch, and (b) replay the
    floor against the candidate before it commits.
  - ``run_floor_contract``: replay those cases against a candidate. Pure
    candidates' example/invariant/other-metamorphic cases delegate to
    ``contract.runner.run_contract`` unchanged; D3 containment cases (U11's
    ``ContainmentRelation`` -- never wired into that runner) are evaluated
    directly (``_run_containment_cases``) against the candidate's real
    output. An effectful candidate (declares ``fx``) cannot run through
    ``run_contract`` at all (its gist calls the function directly, with no
    ``fx`` to bind) -- its idempotent-invariant floor cases are instead
    replayed twice against a *shared* shadow world (never the consumer's
    real resources): if repeating the shipped operation changes the world
    further, idempotence broke. Other case kinds on an effectful slot are
    skipped (not failed) for now -- the same "cannot test -> skip" rule
    ``run_contract`` already follows, rather than inventing a new effectful
    evaluator for every case kind.

``slot_resolver._run_floor_gate`` is the seam that calls this on every
GENERATE/ADAPT of a slot with an installed baseline; unlike the two existing
acceptance gates it is not behind a feature flag (KTD-1: load-bearing safety
infrastructure, not optional) and it never quarantines a failing floor case --
it raises ``FloorViolation`` when the regeneration budget is exhausted. The
shipped floor is immutable at the consumer site.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from semipy.contract.models import ContractCase
from semipy.contract.runner import CaseFailure, ContractRunResult, run_contract
from semipy.contract.serialize import case_from_dict
from semipy.contract.surface import surface_from_dict
from semipy.distribution.runtime import PACKAGE_DATA_DIRNAME, _load_manifest, find_package_root


class FloorViolation(Exception):
    """R16: a candidate failed to replay-pass a shipped floor case after the
    regeneration budget was exhausted. Names the offending case so the caller
    can report exactly what would have regressed."""

    def __init__(self, *, slot_id: str, case_id: str, message: str) -> None:
        self.slot_id = slot_id
        self.case_id = case_id
        self.message = message
        super().__init__(
            f"floor violation for slot {slot_id!r}: shipped case {case_id!r} did not "
            f"replay-pass ({message})"
        )


@dataclass
class ShippedFloor:
    """What's installed for a slot's call site: the raw artifact source (to
    parent an adaptation on) and the floor cases it must keep passing."""

    baseline_version: str
    artifact_source: str
    cases: list[ContractCase]


def installed_floor_for(slot: Any) -> Optional[ShippedFloor]:
    """Load the shipped floor for *slot*'s call site, or ``None`` when no
    baseline applies -- no package data installed alongside this call site,
    or this call site was not part of the last build (e.g. an ordinary
    developer slot)."""
    call_site_info = getattr(slot, "call_site_info", None) or {}
    source_file = call_site_info.get("filename")
    if not source_file:
        return None
    package_root = find_package_root(source_file)
    if package_root is None:
        return None
    slot_spec = slot.slot_spec if isinstance(getattr(slot, "slot_spec", None), dict) else {}
    key = slot_spec.get("spec_equivalence_key")
    if not key:
        return None
    manifest = _load_manifest(package_root)
    entry = manifest.entries.get(key)
    if entry is None:
        return None
    artifact_path = package_root / PACKAGE_DATA_DIRNAME / entry.artifact_module
    contract_path = package_root / PACKAGE_DATA_DIRNAME / entry.contract_path
    try:
        artifact_source = artifact_path.read_text(encoding="utf-8")
        contract_dict = json.loads(contract_path.read_text(encoding="utf-8"))
    except OSError:
        return None
    surface = surface_from_dict(contract_dict.get("surface") or {})
    cases = [case_from_dict(c) for c in surface.cases.values()]
    return ShippedFloor(
        baseline_version=manifest.baseline_hash,
        artifact_source=artifact_source,
        cases=cases,
    )


def adapt_from_shipped_floor(slot: Any, resolution: Any) -> Optional[str]:
    """R16: "adaptation parents from the shipped artifact (never generates
    from scratch while a baseline exists)."

    A bare GENERATE (no local parent at all) becomes an ADAPT parented on the
    shipped artifact; a slot that already has a local parent (an overlay
    commit, or a REUSE that fell through to ``force_regenerate``) keeps that
    lineage untouched -- the local overlay wins, per the layered-portal model
    (U8). Mutates *resolution* in place (a plain, non-frozen dataclass) and
    returns the baseline version to stamp on the eventual commit, or ``None``
    when no baseline applies or there was already a local parent.
    """
    from semipy.types import Decision

    if resolution.decision != Decision.GENERATE or resolution.parent_sources:
        return None
    floor = installed_floor_for(slot)
    if floor is None:
        return None
    resolution.decision = Decision.ADAPT
    resolution.parent_sources = [floor.artifact_source]
    return floor.baseline_version


def _containment_failure(case: ContractCase, message: str) -> CaseFailure:
    reason = case.reason or "(prior decision)"
    return CaseFailure(
        case_id=case.case_id,
        kind=case.kind,
        label=case.relation,
        reason=reason,
        observed="",
        message=f"Shipped floor case [containment] violated: {message}. This case exists because: {reason}",
        failure_kind="type_mismatch",
    )


def _run_containment_cases(
    *, implementation_source: str, free_variables: list[str], containment_cases: list[ContractCase],
) -> list[CaseFailure]:
    """Evaluate D3 ``containment`` floor cases directly against the candidate.

    ``contract.runner.run_contract`` dispatches metamorphic cases through
    ``contract.relations.get_relation``, whose small ``_REGISTRY`` never held
    ``"containment"`` (U11 shipped ``ContainmentRelation`` as a standalone,
    label-free predicate over a single (input, output) pair -- its own test
    file notes the runner integration "lands later"). Reusing that dispatch
    would also need re-plumbing: containment checks the real output object
    (a dataclass/model/dict), not the JSON-projected record ``run_contract``'s
    gist produces. So this calls the compiled candidate directly and hands its
    raw return value to ``ContainmentRelation.evaluate`` -- the "cannot test ->
    skip" rule ``run_contract`` already follows applies here too: a candidate
    that raises is skipped, not failed (that's the existing example/invariant
    gate's job), and only whether the output text-traces is checked.
    """
    from semipy.contract.relations import ContainmentRelation
    from semipy.effects.shadow import compile_source

    fn = compile_source(implementation_source)
    if fn is None:
        return []
    failures: list[CaseFailure] = []
    for case in containment_cases:
        try:
            relation = ContainmentRelation.from_dict(case.relation_param or {})
        except Exception:
            continue
        input_sample = case.input_sample or {}
        args = tuple(input_sample.get(v) for v in free_variables if v != "self")
        try:
            output = fn(*args)
        except Exception:
            continue
        result = relation.evaluate(input_sample, output)
        if not result.holds:
            failures.append(_containment_failure(case, result.message()))
    return failures


def _effect_failure(case: ContractCase, message: str) -> CaseFailure:
    reason = case.reason or "(prior decision)"
    label = case.invariant or case.relation or case.kind
    return CaseFailure(
        case_id=case.case_id,
        kind=case.kind,
        label=label,
        reason=reason,
        observed="",
        message=f"Shipped floor case [{label}] violated: {message}. This case exists because: {reason}",
        failure_kind="effect_nonidempotent",
    )


def _run_floor_contract_effectful(
    *, implementation_source: str, slot_spec: Any, floor_cases: list[ContractCase],
) -> ContractRunResult:
    """Replay floor cases against an effectful candidate in a shadow world.

    Only the ``idempotent`` invariant kind is checked today: run the
    candidate's ``fx`` script once, snapshot the shadow, run it again against
    the SAME world with the SAME input, and snapshot again. Idempotent means
    the second run left the world exactly where the first run did -- checked
    via ``world.diff(snapshot1, snapshot2)`` being empty on every touched
    target, not via snapshot-ref equality: at least the memory backend's
    ``snapshot()`` returns an ever-incrementing sequence tag, not a content
    hash, so two refs differ even when the underlying data doesn't. Other case
    kinds are skipped -- there is no effectful evaluator for them yet."""
    from semipy.effects.shadow import ShadowWorld, run_effectful_source

    free_variables = list(getattr(slot_spec, "free_variables", []) or [])
    prov = {"slot_id": getattr(slot_spec, "slot_id", "")}
    failures: list[CaseFailure] = []
    evaluated = 0
    skipped = 0
    evaluated_case_ids: set[str] = set()

    for case in floor_cases:
        if not (case.kind == "invariant" and case.invariant == "idempotent"):
            skipped += 1
            continue
        runtime_values = dict(case.input_sample or {})
        world = ShadowWorld()
        _script1, world, err1 = run_effectful_source(
            implementation_source, free_variables=free_variables,
            runtime_values=runtime_values, provenance=prov, world=world,
        )
        if err1 is not None:
            world.discard_all()
            evaluated += 1
            evaluated_case_ids.add(case.case_id)
            failures.append(_effect_failure(case, f"raised {err1} while replaying the shipped floor in a shadow"))
            continue
        snapshot1 = world.snapshot()
        _script2, world, err2 = run_effectful_source(
            implementation_source, free_variables=free_variables,
            runtime_values=runtime_values, provenance=prov, world=world,
        )
        snapshot2 = world.snapshot()
        deltas = world.diff(snapshot1, snapshot2)
        world.discard_all()
        evaluated += 1
        evaluated_case_ids.add(case.case_id)
        if err2 is not None:
            failures.append(_effect_failure(case, f"raised {err2} on a repeat shadow replay"))
            continue
        if any(not d.is_empty() for d in deltas):
            failures.append(_effect_failure(
                case, "not idempotent: repeating the shipped operation in a shadow changed the world further"
            ))

    return ContractRunResult(
        passed=not failures,
        failures=failures,
        n_evaluated=evaluated,
        n_skipped=skipped,
        evaluated_case_ids=evaluated_case_ids,
    )


def run_floor_contract(
    *,
    implementation_source: str,
    slot_spec: Any,
    floor_cases: list[ContractCase],
    scaffold_source: Optional[str] = None,
) -> ContractRunResult:
    """Replay the shipped floor against a candidate.

    Dispatches on whether the candidate is effectful: a pure candidate's
    example/invariant/other-metamorphic cases delegate to
    ``contract.runner.run_contract`` unchanged; its D3 containment cases (not
    wired into that runner -- see ``_run_containment_cases``) are evaluated
    directly against the candidate's raw output and merged in. An effectful
    candidate can't run through that path at all (missing ``fx``), so its
    floor cases are replayed in an isolated shadow world instead -- never
    against the consumer's real resources.
    """
    if not floor_cases:
        return ContractRunResult(passed=True)

    from semipy.effects.inject import fn_is_effectful
    from semipy.effects.shadow import compile_source

    fn = compile_source(implementation_source)
    if fn is None or not fn_is_effectful(fn):
        containment_ids = {
            c.case_id for c in floor_cases if c.kind == "metamorphic" and c.relation == "containment"
        }
        containment_cases = [c for c in floor_cases if c.case_id in containment_ids]
        other_cases = [c for c in floor_cases if c.case_id not in containment_ids]
        result = run_contract(
            implementation_source=implementation_source,
            slot_spec=slot_spec,
            cases=other_cases,
            scaffold_source=scaffold_source,
        )
        if not containment_cases:
            return result
        free_variables = list(getattr(slot_spec, "free_variables", []) or [])
        extra_failures = _run_containment_cases(
            implementation_source=implementation_source,
            free_variables=free_variables,
            containment_cases=containment_cases,
        )
        return ContractRunResult(
            passed=result.passed and not extra_failures,
            failures=result.failures + extra_failures,
            n_evaluated=result.n_evaluated + len(containment_cases),
            n_skipped=result.n_skipped,
            evaluated_case_ids=result.evaluated_case_ids | {c.case_id for c in containment_cases},
        )
    return _run_floor_contract_effectful(
        implementation_source=implementation_source,
        slot_spec=slot_spec,
        floor_cases=floor_cases,
    )
