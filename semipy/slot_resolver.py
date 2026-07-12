from __future__ import annotations

import ast
import inspect as _inspect
import time
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from semipy.agents.agent import SemiAgent
from semipy.agents.slot_call import invoke_slot
from semipy.agents.config import get_config
from semipy.agents.console_io import print_pipeline_log
from semipy.agents.compiler import _compile_source
from semipy.agents.validator import verify_runtime_execution
from semipy.distribution.floor_gate import adapt_from_shipped_floor, installed_floor_for
from semipy.distribution.runtime import FALL_THROUGH, try_resolve as _try_package_data_resolve
from semipy.history.version_control import (
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.session_anchor import resolve_project
from semipy.reactivity import (
    SlotRef,
    _get_dep_graph,
    attach_producer_flow,
    clear_stale,
    create_flow,
    extract_flow,
    get_downstream_requirements,
    mark_downstream_stale,
    profile_output,
    record_consumed,
    save_dependency_graph,
    set_incoming_edges,
    stale_against_inputs,
    update_slot_commit,
)
from semipy._slot_region import expand_zone
from semipy.routing import RoutingPolicy
from semipy.orchestration.orchestrator import Orchestrator

from semipy.documents import materialize_runtime_document_inputs
from semipy.library.binding import evaluate_binding_clarity, extract_binding_async
from semipy.library.sketch import (
    build_code_sketch_from_commit,
    instantiate_sketch_code,
    merge_sketch_into_library,
    validate_instantiated_source,
)
from semipy.library.sketch_store import load_sketch_library, save_sketch_library
from semipy.store import (
    function_name_for_commit,
    load_function_from_dispatch_by_slot_id,
    load_function_from_dispatch,
    load_portal,
    migrate_legacy_portals,
    save_portal,
    write_dispatch_module,
    _dispatch_module_path,
)
from semipy.slot_observations import (
    _reuse_verify_sample_inputs,
    _record_slot_input_observations,
    _runtime_profile_is_scalar_only,
    _harvest_caller_series_samples,
    _slot_session_observations,
    _has_diverse_observations,
    _obs_content_fingerprint,
    _record_call_outcome,
    _get_recent_call_outcomes,
    _check_intent_judge_pre_filters,
    _get_batch_summary_from_outcomes,
)
from semipy.runtime_fingerprint import compute_runtime_input_fingerprint
from semipy.types import (
    CallOutcome,
    Decision,
    GenerationSpec,
    SemiCallError,
    SemiCallSite,
    SlotCategory,
    SlotSpec,
    ValidationResult,
    equivalence_key_from_stored_snapshot,
    module_name_for_project,
    session_id_for_project,
)

_dispatch_globals_cache: dict[str, dict[str, Any]] = {}


def _sanitize_identifier(s: str) -> str:
    # Ensure generated dispatch function names are valid Python identifiers.
    # Replace common placeholders like "<lambda>".
    if not s:
        return "slot"
    s = s.strip()
    s = s.replace("<", "").replace(">", "")
    out_chars: list[str] = []
    for ch in s:
        if ch.isalnum() or ch == "_":
            out_chars.append(ch)
        else:
            out_chars.append("_")
    out = "".join(out_chars)
    if not out:
        out = "slot"
    if out[0].isdigit():
        out = "_" + out
    return out


def _extract_source_file_imports(filename: str) -> list[str]:
    if not filename or filename == "<unknown>":
        return []
    try:
        path = Path(filename).resolve()
        if not path.exists():
            return []
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        lines: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                seg = ast.get_source_segment(source, node)
                if seg:
                    lines.append(seg.strip())
        return lines
    except Exception:
        return []


def _read_user_source_for_context(filename: str, *, max_chars: int = 12000) -> str | None:
    """Read the user's source file to provide downstream usage context.

    The LLM can see how the function output is consumed (key accesses,
    passed to other functions, etc.) to infer the expected output shape
    even when the return type annotation is generic (e.g. ``list[dict]``).
    """
    if not filename or filename == "<unknown>":
        return None
    try:
        path = Path(filename).resolve()
        if not path.exists() or not path.is_file():
            return None
        source = path.read_text(encoding="utf-8", errors="replace")
        if len(source) > max_chars:
            source = source[:max_chars] + "\n# ... (truncated)"
        return source
    except Exception:
        return None


def _capture_slot_source_snapshot(slot_spec: SlotSpec) -> dict[str, Any]:
    """Snapshot the user source region around the slot so the commit can
    restore the #> spec and #< surface if the user rewinds to it.
    """
    source_span = getattr(slot_spec, "source_span", None)
    if not source_span or len(source_span) < 3:
        return {}
    filename, start1, end1 = source_span[0], int(source_span[1]), int(source_span[2])
    if not filename or start1 < 1 or end1 < start1:
        return {}
    try:
        path = Path(filename)
        if not path.exists() or not path.is_file():
            return {}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return {}
    if not lines:
        return {}
    region_start, region_end = expand_zone(lines, start1 - 1, end1 - 1)
    return {
        "slot_region_text": "\n".join(lines[region_start : region_end + 1]),
        "slot_region_start_line": region_start + 1,
        "slot_region_end_line": region_end + 1,
        "source_file": filename,
    }


# ---------------------------------------------------------------------------
# Scope predicates for the reuse fast path (U2, R3-R4). Input profiles accumulate
# per slot; a scope predicate is minted at commit time and stored keyed by commit
# id; the reuse gate checks scope *membership* rather than fingerprint equality,
# records an out-of-scope deopt as a ledger event, and hands large inputs to
# sampled verify. Scope state lives in ``slot.advisor_state`` (persisted) so no
# history-layer schema change is needed.
# ---------------------------------------------------------------------------

_SCOPE_PROFILE_MAX = 50


def _record_input_profile(slot: Any, runtime_values: dict[str, Any]) -> None:
    """Accumulate a bounded, structurally-deduplicated ledger of input profiles on
    the slot -- the evidence a scope predicate is minted from (R3)."""
    try:
        from semipy.runtime_fingerprint import compute_input_profile

        profile = compute_input_profile(runtime_values)
    except Exception:
        return
    if not profile:
        return
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        adv = {}
        slot.advisor_state = adv
    ledger = adv.setdefault("scope_profiles", [])
    keys = adv.setdefault("scope_profile_keys", [])
    import json as _json

    key = _json.dumps(profile, sort_keys=True, default=str)
    if key in keys:
        return
    ledger.append(profile)
    keys.append(key)
    while len(ledger) > _SCOPE_PROFILE_MAX:
        ledger.pop(0)
        keys.pop(0)


def _mint_and_store_scope(slot: Any, commit_id: str) -> None:
    """Mint the scope predicate at commit time (R3) from the slot's accumulated
    input profiles and store it keyed by commit id, referenceable by predicate id."""
    try:
        from semipy.kernel.operators import mint_scope

        adv = getattr(slot, "advisor_state", None)
        profiles = adv.get("scope_profiles", []) if isinstance(adv, dict) else []
        predicate = mint_scope(profiles)
        if not isinstance(adv, dict):
            adv = {}
            slot.advisor_state = adv
        adv.setdefault("scope_predicates", {})[commit_id] = predicate.to_dict()
    except Exception:
        pass


def _stored_scope_for_commit(slot: Any, commit_id: str) -> dict | None:
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        return None
    return (adv.get("scope_predicates") or {}).get(commit_id)


def _reuse_input_is_large(runtime_values: dict[str, Any], config: Any) -> bool:
    from semipy.agents.validator import sampleable_length

    threshold = int(getattr(config, "sampled_verify_row_threshold", 10000) or 10000)
    for v in runtime_values.values():
        n = sampleable_length(v)
        if n is not None and n > threshold:
            return True
    return False


def _reuse_scope_decision(
    slot: Any,
    commit: Any,
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    *,
    current_fp: str,
    stored_fp: str,
    portal: Any,
    cache_dir: Path,
    config: Any,
    call_site: Any,
) -> tuple[bool, bool]:
    """R3/R4 reuse gate. Returns ``(skip_verify, input_is_large)``.

    Equal fingerprint is a fast pre-check (equal => in scope => skip verify). An
    unequal fingerprint falls through to the commit's minted scope predicate: an
    out-of-scope input records a deopt ledger event (never runs silently) and
    routes to verify/adapt; an in-scope input skips verify only below the sampled-
    verify size threshold (a large in-scope input still gets a cheap sampled verify
    rather than a blind skip -- the D5 tail-blindness fix). With no minted scope
    (legacy/first commit) or a scalar-only slot (empty predicate), behavior is
    exactly today's fingerprint gate (verify on mismatch)."""
    from semipy.kernel.guard import ScopePredicate
    from semipy.kernel.operators import (
        ScopeDeoptEvent,
        append_deopt_event,
        record_scope_check,
    )
    from semipy.runtime_fingerprint import compute_input_profile

    is_large = _reuse_input_is_large(runtime_values, config)
    if bool(stored_fp) and stored_fp == current_fp:
        return True, is_large

    scope_dict = _stored_scope_for_commit(slot, commit.commit_id)
    if not scope_dict:
        return False, is_large
    predicate = ScopePredicate.from_dict(scope_dict)
    if predicate.is_empty():
        return False, is_large

    profile = compute_input_profile(runtime_values)
    check = predicate.check(profile)
    record_scope_check(slot, check.in_scope)
    if not check.in_scope:
        import time as _time

        append_deopt_event(slot, ScopeDeoptEvent(
            slot_id=slot_spec.slot_id,
            commit_id=commit.commit_id,
            predicate_id=predicate.predicate_id,
            violated_conjunct=check.violated or "",
            observed_profile=profile.get(check.violated_var or "", {}),
            timestamp=_time.time(),
        ))
        save_portal(cache_dir, portal)
        if getattr(config, "verbose", False):
            print_pipeline_log(
                call_site, "scope_deopt",
                f"Input out of scope; deopt (violated: {check.violated}). Routing to verify/adapt.",
            )
        return False, is_large

    # In scope: skip verify only when small. A large in-scope input still verifies,
    # but on a row sample (with recorded power), not the whole input.
    return (not is_large), is_large


def _sampled_reuse_verify(
    fn: Any, slot: Any, slot_spec: SlotSpec, runtime_values: dict[str, Any], config: Any
) -> Any:
    """Run sampled verify for a large reuse input (R4) and fold its aggregate
    output-sanity check into the returned ValidationResult."""
    from semipy.agents.validator import sampled_verify_runtime_execution

    result = sampled_verify_runtime_execution(
        fn=fn, slot_spec=slot_spec, runtime_values=runtime_values,
        epsilon=float(getattr(config, "sampled_verify_epsilon", 0.05)),
        delta=float(getattr(config, "sampled_verify_delta", 0.1)),
        gamma=float(getattr(config, "sampled_verify_gamma", 1.0)),
        size_threshold=int(getattr(config, "sampled_verify_row_threshold", 10000) or 10000),
    )
    try:
        adv = getattr(slot, "advisor_state", None)
        if isinstance(adv, dict):
            adv["last_sampled_verify"] = {
                "sampled": result.sampled, "sample_size": result.sample_size,
                "population_size": result.population_size, "power": result.power,
                "aggregate_ok": result.aggregate_ok,
            }
    except Exception:
        pass
    if result.validation.passed and not result.aggregate_ok:
        return ValidationResult(
            passed=False, ast_valid=True, type_correct=True, execution_ok=True,
            error_message=f"Sampled aggregate sanity: {result.aggregate_detail}",
            failure_kind="aggregate_sanity",
        )
    return result.validation


_semantic_reuse_counters: dict[str, int] = {}
_semantic_last_fingerprints: dict[str, str] = {}

# Single-flight: one lock per slot_id so concurrent callers of the SAME slot do not
# each invoke the LLM (a thundering herd of redundant generations) and do not race on
# the portal / dispatch-module writes. The first caller generates; the rest block,
# then re-enter and REUSE the just-created implementation. Different slots use
# different locks, so unrelated slots still run concurrently. For the common case --
# a CPU-bound generated function -- this adds ~nothing over the GIL, which already
# serializes pure-Python execution across threads.
_slot_singleflight_locks: dict[str, threading.Lock] = {}
_slot_singleflight_guard = threading.Lock()


def _slot_singleflight_lock(slot_id: str) -> threading.Lock:
    with _slot_singleflight_guard:
        lk = _slot_singleflight_locks.get(slot_id)
        if lk is None:
            lk = threading.Lock()
            _slot_singleflight_locks[slot_id] = lk
        return lk


# Per-portal lock: with one portal per PROJECT, two slots in DIFFERENT files now
# share one portal file, so their read-modify-write (load_portal -> add slot ->
# save_portal) would race and lose updates. One reentrant lock per (cache_dir,
# session_id) serializes portal mutation within a project; different projects still
# run concurrently. The per-slot single-flight lock in _make_slot_proxy is acquired
# first (outer), this one second (inner) -- a fixed order, so no deadlock.
#
# This lock is held across _call_generated_fn (the generated/cached implementation
# runs while it is held). That is safe because generated functions are pure data
# transformations over their inputs and never call back into a semiformal slot --
# so there is no nested slot_lock<->portal_lock acquisition that could invert the
# order across threads. (The RLock also makes same-thread re-entry a no-op.)
_portal_locks: dict[str, threading.RLock] = {}
_portal_lock_guard = threading.Lock()


def _portal_lock(cache_dir: Path, session_id: str) -> threading.RLock:
    key = f"{cache_dir}:{session_id}"
    with _portal_lock_guard:
        lk = _portal_locks.get(key)
        if lk is None:
            lk = threading.RLock()
            _portal_locks[key] = lk
        return lk


def _should_semantic_check(
    slot: Any,
    commit_id: str,
    config: Any,
) -> bool:
    """Decide whether to run an LLM-based semantic reuse check.

    Triggers when:
    1. The commit changed (new generation) and observations exist --
       always check once so the very first commit gets evaluated.
    2. The observation content fingerprint has changed since the last
       check AND enough REUSE calls have accumulated (rate limiter).

    The fingerprint gate is the primary mechanism: if the observation
    set hasn't changed since the last successful check, there is no
    need to re-evaluate.  When it *has* changed, the reuse-count
    threshold rate-limits the LLM calls.
    """
    if not getattr(config, "semantic_verify", True):
        return False
    snap = getattr(slot, "slot_spec", None)
    if isinstance(snap, dict):
        cat = str(snap.get("expected_category", "") or "")
        outs = snap.get("output_names", [])
        if cat == SlotCategory.STATEMENT_BLOCK.value and isinstance(outs, list) and len(outs) == 0:
            # Pure side-effect blocks are noisy for semantic checks and often produce
            # redundant adapt loops without improving correctness.
            return False
    if not _has_diverse_observations(slot):
        return False

    adv = getattr(slot, "advisor_state", None) or {}
    if not isinstance(adv, dict):
        adv = {}

    # Convergence cap: once a slot has been adapted by the semantic recheck this many
    # times, stop re-checking. An inherently-semantic slot compiles to a static
    # function the judge can reject on every new free-text input; without this cap the
    # slot regenerates on essentially every call (unbounded cost/latency at scale).
    cap = int(getattr(config, "semantic_verify_max_adapts", 2) or 0)
    if cap and int(adv.get("semantic_adapt_count", 0) or 0) >= cap:
        return False

    last_commit = adv.get("semantic_last_commit_id")
    if last_commit != commit_id:
        return True

    counter_key = f"{slot.slot_id}:{commit_id}"
    current_fp = _obs_content_fingerprint(slot)
    stored_fp = (
        _semantic_last_fingerprints.get(counter_key)
        or adv.get("semantic_obs_fingerprint", "")
    )
    if current_fp == stored_fp:
        return False

    # New input patterns detected (fingerprint changed).  Trigger the
    # semantic check after just 1 additional REUSE call so genuinely new
    # patterns are evaluated quickly.  The full rate-limiter threshold
    # only gates re-checks when observations haven't changed.
    reuse_count = _semantic_reuse_counters.get(counter_key, 0)
    return reuse_count >= 1


def _increment_semantic_reuse_counter(
    slot: Any,
    commit_id: str,
) -> None:
    """In-memory counter of REUSE calls since last semantic check."""
    counter_key = f"{slot.slot_id}:{commit_id}"
    _semantic_reuse_counters[counter_key] = _semantic_reuse_counters.get(counter_key, 0) + 1


def _update_semantic_state(
    slot: Any,
    commit_id: str,
    decision: str,
    *,
    portal: Any,
    cache_dir: Any,
    reasoning: str = "",
) -> None:
    """Persist semantic check result in slot advisor_state and reset counters."""
    adv = getattr(slot, "advisor_state", None) or {}
    if not isinstance(adv, dict):
        adv = {}
    adv["semantic_last_commit_id"] = commit_id
    fp = _obs_content_fingerprint(slot)
    adv["semantic_obs_fingerprint"] = fp
    adv["semantic_decision"] = decision
    # Persist the semantic reasoning so the steering synthesizer's `because`
    # key can ground its phrase in the actual judgment reason.
    adv["last_semantic_reasoning"] = reasoning or ""
    slot.advisor_state = adv
    save_portal(cache_dir, portal)

    counter_key = f"{slot.slot_id}:{commit_id}"
    _semantic_reuse_counters[counter_key] = 0
    _semantic_last_fingerprints[counter_key] = fp


def _call_site_from_slot(slot_spec: SlotSpec) -> SemiCallSite:
    filename, lineno, _end = slot_spec.source_span
    return SemiCallSite(filename=filename, lineno=lineno, func_qualname=slot_spec.enclosing_function_qualname)


def _head_commit(slot: Any) -> Any:
    """Return the newest commit across all branches of the slot, or ``None``."""
    try:
        from semipy.history import most_recent_branch_head
    except Exception:
        return None
    try:
        return most_recent_branch_head(slot)
    except Exception:
        return None


def _get_prior_steering(slot: Any, slot_spec: SlotSpec) -> Any:
    """Load the :class:`SteeringBlock` from the most recent commit on this slot.

    Returns ``None`` when no prior commit exists or when the stored payload
    cannot be coerced back into a :class:`SteeringBlock`.
    """
    del slot_spec  # retained for future per-slot context expansion
    head = _head_commit(slot)
    if head is None:
        return None
    cr = getattr(head, "commitment_record", None) or {}
    if not isinstance(cr, dict):
        return None
    raw = cr.get("steering")
    if raw is None:
        return None
    try:
        from semipy.models import SteeringBlock
        return SteeringBlock.model_validate(raw)
    except Exception:
        return None


def _function_name_base(slot_spec: SlotSpec) -> str:
    base = (slot_spec.enclosing_function_qualname or "slot").replace(".", "_")
    base = _sanitize_identifier(base)
    return f"{base}_slot_{slot_spec.slot_id[:8]}"


def _slot_spec_snapshot(slot_spec: SlotSpec) -> dict[str, Any]:
    # SlotSpec may include types; store a repr so JSON serialization stays safe.
    return {
        "source_span": slot_spec.source_span,
        "spec_text": slot_spec.spec_text,
        "spec_hash": slot_spec.spec_hash,
        "spec_equivalence_key": slot_spec.spec_equivalence_key,
        "free_variables": slot_spec.free_variables,
        "control_context": slot_spec.control_context,
        "expected_category": slot_spec.expected_category.value,
        "expected_type": repr(slot_spec.expected_type),
        "output_names": slot_spec.output_names,
        "formal_constraints": slot_spec.formal_constraints,
        "usage_hints": slot_spec.usage_hints,
        "enclosing_function_qualname": slot_spec.enclosing_function_qualname,
        "enclosing_function_span": list(slot_spec.enclosing_function_span),
        "interpreted": getattr(slot_spec, "interpreted", False),
        "mode": getattr(slot_spec, "mode", "adaptive"),
    }


def _sync_slot_mode(slot: Slot, slot_spec: SlotSpec) -> None:
    """U7 (R12/R13): keep a pre-existing slot's persisted ``mode``/
    ``interpreted`` in sync with the decorator's current authoring, without
    touching anything else in ``slot.slot_spec`` -- mode is metadata, not spec
    identity (it is deliberately excluded from ``spec_equivalence_key``), so a
    mode-only change must not trigger regeneration."""
    if not isinstance(slot.slot_spec, dict):
        return
    slot.slot_spec["mode"] = getattr(slot_spec, "mode", "adaptive")
    slot.slot_spec["interpreted"] = getattr(slot_spec, "interpreted", False)


def _ensure_slot(portal: Any, slot_spec: SlotSpec) -> Slot:
    slot = portal.slots.get(slot_spec.slot_id)
    if slot is not None:
        _sync_slot_mode(slot, slot_spec)
        return slot

    slot = Slot(
        slot_id=slot_spec.slot_id,
        call_site_info={
            "filename": slot_spec.source_span[0],
            "lineno": slot_spec.source_span[1],
            "func_qualname": slot_spec.enclosing_function_qualname,
        },
        function_name_base=_function_name_base(slot_spec),
        spec_hash=slot_spec.spec_hash,
        slot_spec=_slot_spec_snapshot(slot_spec),
        enclosing_function_site_id=None,
    )
    portal.slots[slot.slot_id] = slot

    portal.enclosing_function_slots.setdefault(slot_spec.enclosing_function_qualname, [])
    if slot.slot_id not in portal.enclosing_function_slots[slot_spec.enclosing_function_qualname]:
        portal.enclosing_function_slots[slot_spec.enclosing_function_qualname].append(slot.slot_id)
        portal.enclosing_function_slots[slot_spec.enclosing_function_qualname].sort()
    return slot


def _execution_namespace_for_generation(source_file: str) -> dict[str, Any]:
    """
    Globals from the user's module for validating generated code and building gists.

    Prefer the @semiformal defining module; otherwise match stack frames to source_file.
    """
    try:
        from semipy.decorator import get_semiformal_context

        ctx = get_semiformal_context()
        if ctx is not None and getattr(ctx, "defining_globals", None):
            return dict(ctx.defining_globals)
    except Exception:
        pass
    try:
        import os

        target = os.path.abspath(os.path.normpath(source_file))
        for fr in _inspect.stack()[2:]:
            try:
                cand = os.path.abspath(os.path.normpath(fr.filename))
            except Exception:
                continue
            if cand == target:
                return dict(fr.frame.f_globals)
    except Exception:
        pass
    return {}


def _examples_from_slot(slot_obj: Any) -> list[dict[str, Any]] | None:
    """Render the slot's active contract ``example`` cases for the generation prompt.

    Pinned input->output examples ground the model on canonical behavior (the
    example-driven generation half of the design). Effectful slots also merge in
    effect-case summaries + the most recent applied EffectScript (added in Stage 4).
    Best-effort: any failure yields ``None`` and the prompt simply omits examples.
    """
    if slot_obj is None:
        return None
    try:
        from semipy.contract.access import load_active_cases
    except Exception:
        return None
    try:
        cases = load_active_cases(slot_obj)
    except Exception:
        return None
    examples: list[dict[str, Any]] = []
    for c in cases or []:
        if getattr(c, "kind", "") != "example":
            continue
        raw = getattr(c, "input_sample", {}) or {}
        inp = {
            k: v
            for k, v in raw.items()
            if not (isinstance(k, str) and (k == "self" or k.startswith("_")))
        }
        examples.append(
            {
                "input": inp,
                "output_repr": getattr(c, "expected_repr", ""),
                "effect_summary": "",
                "reason": getattr(c, "reason", ""),
            }
        )
    return examples or None


def build_generation_spec(
    *,
    slot_spec: SlotSpec,
    portal: Any,
    resolution: Any,
    runtime_values: dict[str, Any],
    dep_graph: Any | None,
    current_slot_ref: SlotRef,
    source_file_imports: list[str],
    verify_failure_context: str | None = None,
    sketch_context: str | None = None,
) -> GenerationSpec:
    call_site = _call_site_from_slot(slot_spec)

    sibling_slot_ids = portal.enclosing_function_slots.get(slot_spec.enclosing_function_qualname, [])
    if runtime_values:
        args = [runtime_values.get(n) for n in slot_spec.free_variables]
        sample_input = {"args": tuple(args), "kwargs": {}, "runtime_values": dict(runtime_values)}
    else:
        sample_input = {"args": tuple(), "kwargs": {}, "runtime_values": {}}

    upstream_lineage: list[tuple[str, str]] = []
    for val in runtime_values.values():
        flow = extract_flow(val)
        if flow is not None:
            upstream_lineage.append((flow.producing_slot.session_id, flow.producing_slot.slot_id))

    downstream_requirements = (
        get_downstream_requirements(dep_graph, current_slot_ref) if dep_graph is not None else None
    )

    slot_obj = portal.slots.get(slot_spec.slot_id)
    session_obs = _slot_session_observations(slot_obj) if slot_obj else None
    contract_examples = _examples_from_slot(slot_obj)

    exec_ns = _execution_namespace_for_generation(slot_spec.source_span[0])

    user_source_code = _read_user_source_for_context(slot_spec.source_span[0])

    steering_overrides: dict[str, str] = {}
    if slot_obj is not None:
        adv = getattr(slot_obj, "advisor_state", None) or {}
        if isinstance(adv, dict):
            raw_overrides = adv.get("steering_overrides") or {}
            if isinstance(raw_overrides, dict):
                steering_overrides = {
                    str(k): str(v) for k, v in raw_overrides.items() if v
                }

    return GenerationSpec(
        prompt=slot_spec.spec_text,
        call_site=call_site,
        expected_type=slot_spec.expected_type,
        decision=resolution.decision,
        parent_sources=resolution.parent_sources,
        parent_commit_ids=resolution.parent_commit_ids,
        lineage_summary=resolution.lineage_summary,
        slot_spec=slot_spec,
        scaffold_source=slot_spec.enclosing_function_source,
        sibling_slot_ids=sibling_slot_ids,
        sample_input=sample_input,
        source_file_imports=source_file_imports,
        upstream_lineage=upstream_lineage or None,
        downstream_requirements=downstream_requirements,
        enclosing_function_source=slot_spec.enclosing_function_source,
        user_source_code=user_source_code,
        execution_namespace=exec_ns or None,
        session_input_observations=session_obs,
        runtime_profile_scalar_only=_runtime_profile_is_scalar_only(runtime_values),
        verify_failure_context=verify_failure_context,
        sketch_context=sketch_context,
        steering_overrides=steering_overrides,
        contract_examples=contract_examples,
    )


def _run_sketch_binding_extraction(
    *,
    spec_text: str,
    generated_source: str,
    commit_id: str,
    slot_spec: SlotSpec,
    cache_dir: Path,
    session_id: str,
    portal_anchor: str,
    module_name: str,
    slot_id: str,
) -> None:
    """LLM binding extraction and sketch library update; failures are ignored."""
    try:
        import asyncio
        import sys

        binding = asyncio.run(extract_binding_async(spec_text, generated_source))
        if binding is None:
            if get_config().verbose:
                print(
                    "  Pattern learning: no reusable pattern from this generation.",
                    file=sys.stderr,
                )
            return
        cfg = get_config()
        min_conf = float(getattr(cfg, "sketch_library_min_confidence", 0.6))
        is_clear, reject_reason = evaluate_binding_clarity(
            binding, min_confidence=min_conf
        )
        if not is_clear:
            if cfg.verbose:
                print(
                    "  Pattern learning: skipped (no confident reusable pattern).",
                    file=sys.stderr,
                )
            return
        lib = load_sketch_library(cache_dir)
        sketch = build_code_sketch_from_commit(
            binding,
            generated_source,
            commit_id,
            slot_spec.expected_category.value,
            tuple(slot_spec.free_variables),
        )
        merged = merge_sketch_into_library(lib, sketch, binding)
        if not merged.licensed:
            from semipy.kernel.operators import license_sketch

            lic = license_sketch(
                merged,
                incoming_spec_text=spec_text,
                incoming_source=generated_source,
                min_recurrence=int(getattr(cfg, "sketch_library_min_recurrence", 2)),
            )
            merged.licensed = lic.licensed
            if cfg.verbose:
                print(
                    f"  Pattern learning: {'licensed' if lic.licensed else 'not yet licensed'} "
                    f"({lic.reason}).",
                    file=sys.stderr,
                )
        save_sketch_library(cache_dir, lib)
        # Hold the per-portal lock around the portal read-modify-write: when this
        # runs in a background thread (sketch_library_learning_async), it must not
        # race a main-thread execute_slot writing the same project portal. The
        # synchronous path already holds this RLock on the same thread (reentrant).
        with _portal_lock(cache_dir, session_id):
            portal = load_portal(cache_dir, session_id, portal_anchor, module_name)
            sl = portal.slots.get(slot_id)
            if sl and commit_id in sl.commits:
                c = sl.commits[commit_id]
                sl.commits[commit_id] = replace(c, binding_id=binding.binding_id)
                save_portal(cache_dir, portal)
                write_dispatch_module(cache_dir, portal, sketch_library=lib)
    except Exception as ex:
        if get_config().verbose:
            import sys

            print(f"  Pattern learning: extraction failed ({ex}).", file=sys.stderr)


def _schedule_sketch_binding_extraction(
    *,
    spec_text: str,
    generated_source: str,
    commit_id: str,
    slot_spec: SlotSpec,
    cache_dir: Path,
    session_id: str,
    portal_anchor: str,
    module_name: str,
    slot_id: str,
) -> None:
    cfg = get_config()
    if not cfg.sketch_library_learning:
        return
    kwargs = dict(
        spec_text=spec_text,
        generated_source=generated_source,
        commit_id=commit_id,
        slot_spec=slot_spec,
        cache_dir=cache_dir,
        session_id=session_id,
        portal_anchor=portal_anchor,
        module_name=module_name,
        slot_id=slot_id,
    )
    if cfg.sketch_library_learning_async:
        threading.Thread(
            target=lambda: _run_sketch_binding_extraction(**kwargs),
            daemon=True,
            name="semipy-sketch-binding",
        ).start()
    else:
        _run_sketch_binding_extraction(**kwargs)


def _run_contract_maintenance(
    *,
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    new_source: str,
    change_record: dict,
    decision: str,
    commit_id: str,
    cache_dir: Path,
    session_id: str,
    portal_anchor: str,
    module_name: str,
    slot_id: str,
) -> None:
    try:
        from semipy.contract.maintainer import maintain_contract

        # maintain_contract does its own portal read-modify-write; hold the
        # per-portal lock so an async (contract_maintainer_async) run cannot race a
        # main-thread execute_slot on the same project portal. Reentrant for the
        # synchronous path, which already holds it on this thread.
        with _portal_lock(cache_dir, session_id):
            maintain_contract(
                slot_spec=slot_spec,
                runtime_values=runtime_values,
                new_source=new_source,
                change_record=change_record,
                decision=decision,
                commit_id=commit_id,
                cache_dir=cache_dir,
                session_id=session_id,
                portal_anchor=portal_anchor,
                module_name=module_name,
                slot_id=slot_id,
            )
    except Exception as ex:
        if get_config().verbose:
            import sys

            print(f"[semipy] Contract maintenance failed: {ex}", file=sys.stderr)


def _schedule_contract_maintenance(**kwargs: Any) -> None:
    cfg = get_config()
    if not getattr(cfg, "contract_enabled", True):
        return
    if getattr(cfg, "contract_maintainer_async", False):
        threading.Thread(
            target=lambda: _run_contract_maintenance(**kwargs),
            daemon=True,
            name="semipy-contract-maintainer",
        ).start()
    else:
        _run_contract_maintenance(**kwargs)


def _run_reuse_contract_gate(
    slot: Any,
    slot_spec: SlotSpec,
    commit: Any,
    config: Any,
    call_site: Any,
) -> tuple[str | None, Any]:
    """Run the behavioral contract against a reuse candidate.

    Returns ``(failure_message, validation_result)`` when a carried-forward case
    is violated (so the caller forces ADAPT), else ``(None, None)``. Never raises;
    an inability to run the contract is treated as "no objection".
    """
    if not getattr(config, "contract_gate", False):
        return None, None
    try:
        from semipy.contract.access import load_active_cases, record_case_outcomes
        from semipy.contract.runner import run_contract

        active = load_active_cases(slot)
        if not active:
            return None, None
        cap = int(getattr(config, "contract_max_cases", 25))
        cases = active[:cap]
        cr = run_contract(
            implementation_source=getattr(commit, "generated_source", "") or "",
            slot_spec=slot_spec,
            cases=cases,
            scaffold_source=slot_spec.enclosing_function_source,
        )
        record_case_outcomes(slot, cases, cr, commit_id=getattr(commit, "commit_id", ""))
        if cr.passed:
            return None, None
        msg = cr.first_failure_message()
        if config.verbose:
            err = msg.replace("\n", " ")
            if len(err) > 160:
                err = err[:157] + "..."
            print_pipeline_log(
                call_site,
                "contract_gate",
                f"Contract case violated; adapting. {err}",
            )
        return msg, cr.as_validation_result()
    except Exception:
        return None, None


def _try_melt_for_example_case(
    *, case: Any, candidate_source: str, slot_spec: SlotSpec,
) -> str | None:
    """Attempt melt's local rejuvenation (frontier-kernel Phase 4) for one
    ``case`` the contract gate's *candidate_source* just failed: blame the
    failure to the shallowest node in the candidate's own tree, synthesize a
    replacement for just that node, and splice it back in. Returns the patched
    source, or ``None`` when melt is out of scope for this case (not an
    "example" case, its expected value doesn't literal_eval, blame can't
    localize past the root, or the localized node isn't a MAP/FILTER leaf) --
    the caller falls back to whole-function regeneration, unchanged.
    """
    if getattr(case, "kind", None) != "example":
        return None
    try:
        expected_output = ast.literal_eval(case.expected_repr)
    except Exception:
        return None

    from semipy.interpreted import synthesize_residual_source
    from semipy.kernel.blame import blame
    from semipy.kernel.operators import melt
    from semipy.kernel.tree import lower_source_to_tree

    root_id = slot_spec.slot_id
    tree = lower_source_to_tree(candidate_source, root_id)
    result = blame(tree, free_variables=case.input_sample, expected_output=expected_output)
    if result.node_id == root_id or result.kind not in ("map", "filter"):
        return None
    if result.offending_input is None and result.offending_target is None:
        return None

    leaf_node = next((n for n in tree.walk() if n.node_id == result.node_id), None)
    leaf = leaf_node.children[0] if leaf_node and leaf_node.children else None
    if leaf is None or not leaf.artifact:
        return None
    try:
        leaf_fn = ast.parse(leaf.artifact).body[0]
        item_name = leaf_fn.args.args[0].arg  # type: ignore[union-attr]
    except Exception:
        return None

    instruction = (
        f"{slot_spec.spec_text}\n\n"
        + (
            "This is the per-element transform applied inside a map over one item; "
            "write it as a function of that single item."
            if result.kind == "map"
            else "This is the per-element boolean predicate applied inside a filter over "
            "one item; write it as a function of that single item returning True/False."
        )
    )
    new_leaf_source = synthesize_residual_source(
        instruction, [item_name], [([result.offending_input], result.offending_target)],
    )
    if not new_leaf_source:
        return None

    melt_result = melt(
        candidate_source, root_id,
        free_variables=case.input_sample, expected_output=expected_output,
        new_node_source=new_leaf_source,
    )
    return melt_result.patched_source


def _try_branch_split(
    *, case: Any, runtime_values: dict[str, Any], parent_source: str, candidate_source: str,
) -> str | None:
    """Attempt to preserve ``case`` behind a guard instead of quarantining it
    (frontier-kernel Phase 5, branch): find a predicate that routes case's own
    input to *parent_source* (which already satisfies it -- it was active
    before this regeneration) and everything else to *candidate_source* (which
    satisfies the new evidence that conflicts with it), license it, and wrap
    both bodies behind it. Returns the wrapped source, or ``None`` when no
    guard separates the two inputs or the wrap fails to construct -- the
    caller falls back to quarantining the case, unchanged.
    """
    if getattr(case, "kind", None) != "example":
        return None
    from semipy.kernel.operators import branch, synthesize_separating_guard
    from semipy.kernel.tree import build_branch_wrapper

    guard = synthesize_separating_guard(old_input=case.input_sample, new_input=runtime_values)
    if guard is None:
        return None
    event = branch([guard])
    if not event.licensed:
        return None
    return build_branch_wrapper(event.guards[0], old_source=parent_source, new_source=candidate_source)


def _run_generate_contract_gate(
    slot: Any,
    slot_spec: SlotSpec,
    entry: Any,
    generation_spec: Any,
    resolution: Any,
    runtime_values: dict[str, Any],
    config: Any,
    call_site: Any,
) -> tuple[Any, set[str], dict]:
    """Acceptance gate + effect tracing for a freshly generated/adapted candidate.

    1. The candidate must satisfy the slot's carried-forward cases (when the gate
       is enabled). 2. Its EFFECT — the behavior diff vs the parent over the union
       of contract inputs — is always traced when the contract is enabled.
       3. An unintended diff (a regression on an input the parent handled) also
       fails the gate. On any violation, regenerate up to ``contract_gate_max_retries``
       with the specific problem appended to the failure context -- unless melt
       (opt-in, ``config.melt_on_contract_failure``) can patch just the blamed
       node instead; tried first each retry, falls straight through to full
       regeneration when it's out of scope or doesn't fix the failure.

    Returns ``(entry, unresolved_case_ids, change_record_dict)``. Unresolved ids are
    quarantined by the caller, unless branch (opt-in, ``config.branch_on_quarantine``)
    finds a guard that preserves the case's own behavior alongside the new evidence
    instead -- tried once, against the first still-failing case, before quarantining;
    on success the case is dropped from the returned ids. Either way the change record
    is attached to the commit as the real "what changed" provenance.
    """
    if not getattr(config, "contract_enabled", True):
        return entry, set(), {}
    try:
        from semipy.contract.access import load_active_cases
        from semipy.contract.change import (
            change_record_to_dict,
            compute_effect_diff,
            regression_summary,
        )
        from semipy.contract.fingerprint import structural_input_fingerprint
        from semipy.contract.runner import ContractRunResult, run_contract
    except Exception:
        return entry, set(), {}

    cap = int(getattr(config, "contract_max_cases", 25))
    active = load_active_cases(slot)[:cap]
    gate_on = bool(getattr(config, "contract_gate", False))
    block = bool(getattr(config, "contract_block_regressions", True))
    max_retries = int(getattr(config, "contract_gate_max_retries", 1))

    parent_sources = resolution.parent_sources or []
    parent_source = parent_sources[0] if parent_sources else None
    parent_commit_id = (resolution.parent_commit_ids or [""])[0] if resolution.parent_commit_ids else ""
    decision = resolution.decision.name if resolution.decision else "GENERATE"
    triggering_fp = structural_input_fingerprint(
        runtime_values, free_variables=list(slot_spec.free_variables)
    )
    scaffold = slot_spec.enclosing_function_source

    def _assess(src: str) -> tuple[Any, Any]:
        cr = (
            run_contract(
                implementation_source=src,
                slot_spec=slot_spec,
                cases=active,
                scaffold_source=scaffold,
            )
            if (gate_on and active)
            else ContractRunResult(passed=True)
        )
        change = compute_effect_diff(
            parent_source=parent_source,
            new_source=src,
            slot_spec=slot_spec,
            cases=active,
            triggering_fp=triggering_fp,
            scaffold_source=scaffold,
            reason=(generation_spec.verify_failure_context or "").strip(),
            decision=decision,
            parent_commit_id=parent_commit_id,
        )
        return cr, change

    case_by_id = {c.case_id: c for c in active}
    melt_enabled = bool(getattr(config, "melt_on_contract_failure", False))

    cr, change = _assess(entry.generated_source)
    attempt = 0
    while gate_on and attempt < max_retries and (
        (not cr.passed) or (block and change.unintended_count > 0)
    ):
        attempt += 1

        if melt_enabled and not cr.passed and cr.failures:
            case = case_by_id.get(cr.failures[0].case_id)
            patched = (
                _try_melt_for_example_case(case=case, candidate_source=entry.generated_source, slot_spec=slot_spec)
                if case is not None else None
            )
            if patched is not None:
                melted_entry = replace(entry, generated_source=patched)
                melted_cr, melted_change = _assess(melted_entry.generated_source)
                if melted_cr.passed and not (block and melted_change.unintended_count > 0):
                    entry, cr, change = melted_entry, melted_cr, melted_change
                    if config.verbose:
                        print_pipeline_log(
                            call_site, "melt",
                            f"Locally patched node for case {case.case_id[:8]}; skipped full regeneration.",
                        )
                    continue

        if not cr.passed:
            extra = cr.first_failure_message()
        else:
            extra = regression_summary(change)
        if not extra:
            break
        base = generation_spec.verify_failure_context or ""
        generation_spec.verify_failure_context = f"{base}\n{extra}".strip()
        if config.verbose:
            print_pipeline_log(
                call_site,
                "contract_gate",
                f"Candidate conflicts with a prior decision; regenerating ({attempt}/{max_retries}). {extra[:120]}",
            )
        try:
            entry = SemiAgent().generate(generation_spec)
        except Exception:
            break
        cr, change = _assess(entry.generated_source)

    ids: set[str] = set()
    if gate_on and not cr.passed:
        ids = cr.failing_case_ids()
        if bool(getattr(config, "branch_on_quarantine", False)) and parent_source and cr.failures:
            case = case_by_id.get(cr.failures[0].case_id)
            branched = (
                _try_branch_split(
                    case=case, runtime_values=runtime_values,
                    parent_source=parent_source, candidate_source=entry.generated_source,
                )
                if case is not None else None
            )
            if branched is not None:
                branched_cr, branched_change = _assess(branched)
                if branched_cr.passed and not (block and branched_change.unintended_count > 0):
                    entry, cr, change = replace(entry, generated_source=branched), branched_cr, branched_change
                    ids = set()
                    if config.verbose:
                        print_pipeline_log(
                            call_site, "branch",
                            f"Preserved case {case.case_id[:8]} behind a guard instead of quarantining it.",
                        )
        if ids and config.verbose:
            print_pipeline_log(
                call_site,
                "contract_gate",
                f"Quarantining {len(ids)} unsatisfiable case(s) after regeneration budget.",
            )
    return entry, ids, change_record_to_dict(change)


def _run_generate_effect_gate(
    slot: Any,
    slot_spec: SlotSpec,
    entry: Any,
    generation_spec: Any,
    resolution: Any,
    runtime_values: dict[str, Any],
    config: Any,
    call_site: Any,
) -> Any:
    """Effect acceptance gate for a freshly generated/adapted effectful candidate.

    Stages the candidate's EffectScript in a shadow over the current input and
    runs the static effect-invariant checks (reversible + unbounded-blast-radius
    guard). On violation, append the reason to the failure context and regenerate
    up to ``effect_gate_max_retries`` -- the same control flow as the contract
    gate. Pure (non-fx) candidates pass through untouched. Stage 1 is dry-run: the
    shadow is always discarded; nothing is applied to the real artifact.
    """
    if not getattr(config, "effects_enabled", False):
        return entry
    if not getattr(config, "effect_gate", False):
        return entry
    try:
        from semipy.effects.diff import compute_effect_state_diff
        from semipy.effects.inject import fn_is_effectful
        from semipy.effects.shadow import compile_source, run_effectful_source
        from semipy.effects.verify import verify_static
    except Exception:
        return entry

    ns = getattr(generation_spec, "execution_namespace", None) or None
    fn = compile_source(entry.generated_source, ns)
    if fn is None or not fn_is_effectful(fn):
        return entry  # pure slot (or uncompilable -> the validator owns that)

    free_vars = list(slot_spec.free_variables)
    prov = {"slot_id": slot_spec.slot_id}
    max_retries = int(getattr(config, "effect_gate_max_retries", 1))
    block = bool(getattr(config, "effect_block_regressions", True))
    blast = int(getattr(config, "effect_default_blast_radius", 1))
    smt = bool(getattr(config, "effect_smt", False))
    parent_sources = getattr(resolution, "parent_sources", None) or []
    parent_source = parent_sources[0] if parent_sources else None

    def _prove_bounded(script: Any) -> str | None:
        """Schema-grounded forall-inputs blast-radius proof; returns a reason if unproven."""
        try:
            from semipy.effects.backends import resolve_backend
            from semipy.effects.prove import prove_bounded_blast_radius

            pr = prove_bounded_blast_radius(
                script, lambda t: resolve_backend(t).schema(t)
            )
            return None if pr.status == "proved" else pr.detail
        except Exception:
            return None  # missing backend/schema -> defer to sample checks

    def _assess(src: str) -> tuple[bool, str]:
        """Return ``(ok, message)``: static invariants, optional forall-inputs proof,
        and a blast-radius regression check."""
        script, world, err = run_effectful_source(
            src, free_variables=free_vars, runtime_values=runtime_values,
            provenance=prov, namespace=ns,
        )
        world.discard_all()
        if err is not None:
            return False, f"effect execution error: {err}"
        vr = verify_static(script)
        if not vr.passed:
            return False, vr.first_message()
        if smt:
            unproven = _prove_bounded(script)
            if unproven:
                return False, "Blast radius not provably bounded for all inputs: " + unproven
        if block and parent_source:
            sdiff = compute_effect_state_diff(
                parent_source=parent_source, new_script=script,
                free_variables=free_vars, runtime_values=runtime_values,
                namespace=ns, provenance=prov, default_blast_radius=blast,
            )
            if sdiff.regression:
                return False, sdiff.summary
        return True, ""

    ok, msg = _assess(entry.generated_source)
    attempt = 0
    while attempt < max_retries and not ok:
        attempt += 1
        if not msg:
            break
        base = generation_spec.verify_failure_context or ""
        generation_spec.verify_failure_context = f"{base}\n{msg}".strip()
        if config.verbose:
            print_pipeline_log(
                call_site,
                "effect_gate",
                f"Effect check failed; regenerating ({attempt}/{max_retries}). {msg[:120]}",
            )
        try:
            entry = SemiAgent().generate(generation_spec)
        except Exception:
            break
        ok, msg = _assess(entry.generated_source)

    if not ok and config.verbose:
        print_pipeline_log(
            call_site,
            "effect_gate",
            f"Effect check still failing after budget; accepting candidate. {msg[:120]}",
        )
    return entry


def _run_floor_gate(
    slot: Any,
    slot_spec: SlotSpec,
    entry: Any,
    generation_spec: Any,
    resolution: Any,
    runtime_values: dict[str, Any],
    config: Any,
    call_site: Any,
) -> Any:
    """U9/R16: no consumer-site candidate commits without replay-passing the
    shipped floor. A no-op when no baseline is installed for this slot's call
    site (an ordinary developer slot). Otherwise every shipped ``ship=True``
    case is replayed against the candidate; on violation, append the reason to
    the failure context and regenerate up to ``floor_gate_max_retries`` -- the
    same control flow as the contract/effect gates. Unlike those two, this gate
    is not behind a feature flag (KTD-1: load-bearing safety infrastructure,
    not optional) and never quarantines: on budget exhaustion it raises
    ``FloorViolation`` naming the offending case, because the shipped floor is
    immutable at the consumer site (pitfall-preservation). Floors compose with
    (never replace) the local contract gate -- both run independently on the
    same candidate.
    """
    try:
        from semipy.distribution.floor_gate import FloorViolation, run_floor_contract
    except Exception:
        return entry

    floor = installed_floor_for(slot)
    if floor is None or not floor.cases:
        return entry

    max_retries = int(getattr(config, "floor_gate_max_retries", 1))
    scaffold = slot_spec.enclosing_function_source

    def _assess(src: str) -> Any:
        return run_floor_contract(
            implementation_source=src,
            slot_spec=slot_spec,
            floor_cases=floor.cases,
            scaffold_source=scaffold,
        )

    cr = _assess(entry.generated_source)
    attempt = 0
    n_replays = 1
    while attempt < max_retries and not cr.passed:
        attempt += 1
        extra = cr.first_failure_message()
        if not extra:
            break
        base = generation_spec.verify_failure_context or ""
        generation_spec.verify_failure_context = f"{base}\n{extra}".strip()
        if config.verbose:
            print_pipeline_log(
                call_site,
                "floor_gate",
                f"Candidate regressed a shipped floor case; regenerating ({attempt}/{max_retries}). {extra[:120]}",
            )
        try:
            entry = SemiAgent().generate(generation_spec)
        except Exception:
            break
        cr = _assess(entry.generated_source)
        n_replays += 1

    if config.verbose:
        print_pipeline_log(
            call_site,
            "floor_gate",
            f"Replayed {len(floor.cases)} shipped floor case(s) x {n_replays} attempt(s).",
        )

    if not cr.passed:
        failure = cr.failures[0]
        raise FloorViolation(
            slot_id=slot_spec.slot_id,
            case_id=failure.case_id,
            message=failure.message,
        )
    return entry


def _run_reuse_effect_gate(
    slot: Any,
    slot_spec: SlotSpec,
    commit: Any,
    runtime_values: dict[str, Any],
    config: Any,
    call_site: Any,
) -> tuple[str | None, Any]:
    """Effect gate for a reuse candidate: a reused effectful impl may emit an
    invariant-violating script on a *new* input shape (e.g. a selector that is now
    empty). Re-stage + verify over the current input; on violation return
    ``(message, validation_result)`` so the caller forces ADAPT. Never raises."""
    if not getattr(config, "effects_enabled", False):
        return None, None
    if not getattr(config, "effect_gate", False):
        return None, None
    try:
        from semipy.effects.inject import fn_is_effectful
        from semipy.effects.shadow import compile_source, run_effectful_source
        from semipy.effects.verify import verify_static

        src = getattr(commit, "generated_source", "") or ""
        ns = _execution_namespace_for_generation(slot_spec.source_span[0])
        fn = compile_source(src, ns)
        if fn is None or not fn_is_effectful(fn):
            return None, None
        script, world, err = run_effectful_source(
            src, free_variables=list(slot_spec.free_variables),
            runtime_values=runtime_values, provenance={"slot_id": slot_spec.slot_id},
            namespace=ns,
        )
        world.discard_all()
        if err is not None:
            return err, ValidationResult(
                passed=False, ast_valid=True, type_correct=True, execution_ok=False,
                error_message=err, failure_kind="execution_error",
            )
        vr = verify_static(script)
        if vr.passed:
            return None, None
        msg = vr.first_message()
        if config.verbose:
            print_pipeline_log(
                call_site, "effect_gate",
                f"Reused effect violates an invariant on this input; adapting. {msg[:140]}",
            )
        return msg, ValidationResult(
            passed=False, ast_valid=True, type_correct=False, execution_ok=True,
            error_message=msg, failure_kind=vr.failures[0].failure_kind,
        )
    except Exception:
        return None, None


def _should_surface_skeleton(slot_spec: SlotSpec) -> bool:
    """Whether to write ``#<`` skeleton lines into the user's source after generation.

    Never for standalone ``semi()``: there is no ``#>`` block to annotate, and
    rewriting the source inserts lines that shift line numbers -- which breaks the
    source-line template extraction (and therefore reuse) on the next call to the
    same call site (see the standalone-reuse bug fixed alongside this guard).
    """
    return slot_spec.expected_category != SlotCategory.EXPRESSION_STANDALONE


def _call_generated_fn(
    *,
    fn: Callable[..., Any],
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    prompt_preview: str,
    generated_path: str,
    cache_dir: Path,
    slot: Any = None,
    commit: Any = None,
    portal: Any = None,
) -> Any:
    # Effectful slot (its generated function declares an ``fx`` parameter): the
    # effects subsystem owns execution -- it binds a shadow, runs with the recording
    # ``fx``, and either commits + ledgers (auto-apply) or dry-runs. Pure slots take
    # the unchanged in-process path below.
    effectful = False
    try:
        from semipy.effects.inject import fn_is_effectful

        if getattr(get_config(), "effects_enabled", False):
            effectful = fn_is_effectful(fn)
    except Exception:
        effectful = False

    if effectful:
        from semipy.effects.apply import execute_effectful

        return execute_effectful(
            fn=fn, slot_spec=slot_spec, runtime_values=runtime_values, config=get_config(),
            slot=slot, commit=commit, portal=portal, cache_dir=cache_dir,
            prompt_preview=prompt_preview, generated_path=generated_path,
        )

    args = tuple(runtime_values.get(n) for n in slot_spec.free_variables)
    try:
        return invoke_slot(fn, list(slot_spec.free_variables), args)
    except Exception as e:
        err = SemiCallError(
            "Generated slot function raised at runtime",
            call_site=_call_site_from_slot(slot_spec),
            generated_path=generated_path,
            line_range=(0, 0),
            prompt_preview=prompt_preview,
            cause=e,
        )
        try:
            from semipy.diagnostics_export import export_from_semi_call_error

            export_from_semi_call_error(cache_dir, slot_spec, err)
        except Exception:
            pass
        raise err from e


_MAX_INTERP_EXAMPLES = 80


def _promote_interpreted_commit(
    src: str,
    slot_spec: SlotSpec,
    slot: Any,
    portal: Any,
    dep_graph: Any,
    current_slot_ref: Any,
    cache_dir: Path,
    module_name: str,
    runtime_values: dict[str, Any],
) -> None:
    """Mint a normal commit holding the synthesized residual so subsequent calls
    REUSE it via the standard dispatch path (no more LLM)."""
    commit = create_commit(
        (),
        src,
        slot_spec.spec_hash,
        freeze_constants({}),
        slot_spec.spec_text,
        "PROMOTE",
        usage_id=slot_spec.slot_id,
        runtime_input_fingerprint=compute_runtime_input_fingerprint(runtime_values),
        source_snapshot=_capture_slot_source_snapshot(slot_spec),
    )
    add_commit_to_slot(slot, commit, "main", usage_id=slot_spec.slot_id)
    slot.spec_hash = slot_spec.spec_hash
    slot.slot_spec = _slot_spec_snapshot(slot_spec)
    if dep_graph is not None:
        update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
        clear_stale(dep_graph, current_slot_ref)
        save_dependency_graph(cache_dir, dep_graph)
    write_dispatch_module(cache_dir, portal, sketch_library=load_sketch_library(cache_dir))
    save_portal(cache_dir, portal)
    _dispatch_globals_cache.pop(module_name, None)


def _execute_interpreted_slot(
    *,
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    slot: Any,
    portal: Any,
    dep_graph: Any,
    current_slot_ref: Any,
    cache_dir: Path,
    session_id: str,
    module_name: str,
    config: Any,
    promote_after: int = 6,
) -> Any:
    """Interpret-until-shape-stable execution for one interpreted slot.

    Calls the LLM per input (memoized by runtime fingerprint), accumulates
    (args -> output) examples in ``slot.advisor_state``, and once enough have
    accumulated attempts ``kernel.operators.freeze`` (frontier-kernel Phase 3):
    held-out reproducibility, an MDL compression check, and a counterexample
    license across the residual candidates drawn. On a licensed freeze the
    slot promotes to a normal cached commit and this branch is never taken
    again; on refusal it stays interpreted and retries later. Every attempt
    (licensed or not) is appended to ``slot.freeze_events``.
    """
    from semipy.interpreted import extract_label_set, interpret_call
    from semipy.kernel.operators import append_freeze_event, freeze

    output_names = list(slot_spec.output_names or [])
    labels = extract_label_set(slot_spec.expected_type)
    call_site = _call_site_from_slot(slot_spec)
    adv = slot.advisor_state if isinstance(slot.advisor_state, dict) else {}
    slot.advisor_state = adv
    examples: list[dict[str, Any]] = adv.setdefault("interpreted_examples", [])
    memo: dict[str, Any] = adv.setdefault("interpreted_memo", {})

    import json

    def _jsonsafe(v: Any) -> Any:
        try:
            json.dumps(v)
            return v
        except Exception:
            return repr(v)

    fp = compute_runtime_input_fingerprint(runtime_values)
    args = [_jsonsafe(runtime_values.get(n)) for n in slot_spec.free_variables]

    if fp in memo:
        out = memo[fp]
    else:
        if config.verbose:
            print_pipeline_log(
                call_site, "interpret",
                "Interpreting via LLM (not yet shape-stable)...",
            )
        out = _jsonsafe(interpret_call(
            slot_spec.spec_text, runtime_values,
            expected_type=slot_spec.expected_type, output_names=output_names, labels=labels,
        ))
        examples.append({"args": args, "output": out})
        if len(examples) > _MAX_INTERP_EXAMPLES:
            del examples[: len(examples) - _MAX_INTERP_EXAMPLES]
        memo[fp] = out
        if len(memo) > _MAX_INTERP_EXAMPLES * 2:
            for k in list(memo)[: len(memo) - _MAX_INTERP_EXAMPLES]:
                memo.pop(k, None)
    save_portal(cache_dir, portal)

    n = len(examples)
    if n >= promote_after and n >= int(adv.get("interpreted_next_attempt", promote_after)):
        adv["interpreted_attempts"] = int(adv.get("interpreted_attempts", 0)) + 1
        pairs = [(e["args"], e["output"]) for e in examples]
        # Derive the freeze threshold ε* from the cost model (§3.1) rather than
        # the static default, so the certificate's ε is an auditable function of
        # three costs. A misconfigured (nonpositive) cost falls back to the
        # static 0.05 instead of crashing slot execution.
        from semipy.kernel.policy import freeze_break_even

        try:
            epsilon = freeze_break_even(
                getattr(config, "freeze_cost_molten", 1.0),
                getattr(config, "freeze_cost_error", 20.0),
                getattr(config, "freeze_gamma_e", 1.0),
            )
        except ValueError:
            epsilon = 0.05
        src, freeze_event = freeze(
            instruction=slot_spec.spec_text, free_variables=slot_spec.free_variables,
            examples=pairs, expected_type=slot_spec.expected_type,
            output_names=output_names, labels=labels,
            epsilon=epsilon,
            timeout=getattr(config, "gist_timeout", 30),
            e2b_api_key=getattr(config, "e2b_api_key", None),
            node_id=slot_spec.slot_id,
        )
        frac = freeze_event.certificate.held_out_pass_fraction
        append_freeze_event(slot, freeze_event)
        promoted = False
        if src:
            _promote_interpreted_commit(
                src, slot_spec, slot, portal, dep_graph, current_slot_ref,
                cache_dir, module_name, runtime_values,
            )
            adv["interpreted_promoted"] = True
            promoted = True
            if config.verbose:
                print_pipeline_log(
                    call_site, "promote",
                    f"Promoted to LLM-free residual (held-out match {frac:.2f}); "
                    f"future calls reuse cached code.",
                )
        adv["interpreted_holdout_match"] = frac
        if not promoted:
            adv["interpreted_next_attempt"] = n + promote_after
            if config.verbose:
                print_pipeline_log(
                    call_site, "interpret",
                    f"Staying interpreted (held-out match {frac:.2f}; "
                    f"codegen {'failed' if not src else 'did not generalize'}).",
                )
        slot.advisor_state = adv
        save_portal(cache_dir, portal)
    return out


def execute_slot(
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    source_file: str,
    cache_dir: Path,
) -> Any:
    """
    Execute one slot:
    - resolve the project (one portal per project: the folder rooted at the
      nearest ancestor ``.semiformal/``) and take the per-portal lock
    - load portal + dependency graph
    - infer/update dependency edges from runtime_values flows
    - detect spec_hash change and stale flags
    - reuse/adapt/generate implementation via resolver + agent
    - call the resulting implementation with runtime_values
    - attach DataFlow to result for downstream inference
    """
    config = get_config()
    runtime_values = materialize_runtime_document_inputs(dict(runtime_values))

    # U6/KTD-7: an installed library may ship package data (``semipy build``)
    # next to its modules -- try resolving against that before ever touching a
    # cache dir or portal, so an in-scope consumer call needs no cache dir and
    # no key. Falls through to the pipeline below when no package data applies.
    package_result = _try_package_data_resolve(slot_spec, runtime_values, source_file, config)
    if package_result is not FALL_THROUGH:
        return package_result

    cache_dir, project_root = resolve_project(source_file, cache_dir)
    session_id = session_id_for_project(project_root)
    module_name = module_name_for_project(project_root)
    portal_anchor = str(project_root)

    with _portal_lock(cache_dir, session_id):
        return _execute_slot_locked(
            slot_spec,
            runtime_values,
            config,
            cache_dir,
            source_file,
            portal_anchor,
            session_id,
            module_name,
        )


def _resolve_slot_with_decisions(
    slot_spec: SlotSpec,
    generation_spec: Any,
    runtime_values: dict[str, Any],
    slot: Any = None,
) -> tuple[Any, Any]:
    """Draw N candidates and surface the model's silent fork (F0, opt-in).

    Gated by ``config.decisions_enabled`` at the call site. Wraps the normal
    single generation in a multi-candidate draw: the same ``generation_spec`` is
    generated up to ``decision_max_candidates`` times, the candidates are
    clustered by observed behavior, and -- when they diverge -- a ``DecisionSet``
    is built so the fork can be surfaced and steered.

    Returns ``(head_entry, decision_set)``. ``head_entry`` is the CacheEntry whose
    source backs the execution-ranked head (frontier-kernel Phase 2, see
    ``kernel/population.py``): type validity + the slot's contract-pass fraction
    (when ``slot`` has active cases) + cluster agreement, falling back to the
    heaviest runnable cluster when no contract signal is available -- today's
    behavior, unchanged. If every candidate fails generation, falls back to a
    single ``SemiAgent().generate`` so error behavior is preserved.
    """
    from semipy.decisions.draw import resolve_with_decisions

    cfg = get_config()
    free_vars = [v for v in (slot_spec.free_variables or []) if v != "self"]

    # Memoize by draw index so a pre-draw (for effectful detection) and the
    # resolver's own draws share generations, and so the chosen head source maps
    # back to its compiled CacheEntry.
    by_index: dict[int, str | None] = {}
    entry_by_source: dict[str, Any] = {}

    def generate_candidate(i: int) -> str | None:
        if i in by_index:
            return by_index[i]
        try:
            entry = SemiAgent().generate(generation_spec)
            src = entry.generated_source
            by_index[i] = src
            if src and src not in entry_by_source:
                entry_by_source[src] = entry
            return src
        except Exception:
            # A failed candidate is dropped (resolve_with_decisions filters None);
            # never fabricate a candidate to make a fork appear.
            by_index[i] = None
            return None

    # Detect effectfulness from the first candidate's compiled fn (conservative:
    # pure path unless clearly effectful), so divergence is observed in the right
    # mode -- reified EffectScript for effectful slots, return value otherwise.
    effectful_runtime_values = None
    first_src = generate_candidate(0)
    if getattr(cfg, "effects_enabled", False) and first_src:
        try:
            from semipy.effects.inject import fn_is_effectful

            first_entry = entry_by_source.get(first_src)
            fn = getattr(first_entry, "compiled_fn", None)
            if fn is not None and fn_is_effectful(fn):
                effectful_runtime_values = dict(runtime_values)
        except Exception:
            effectful_runtime_values = None

    sample_rows = (
        None
        if effectful_runtime_values is not None
        else [{v: runtime_values.get(v) for v in free_vars}]
    )

    outcome = resolve_with_decisions(
        generate_candidate=generate_candidate,
        free_variables=free_vars,
        sample_rows=sample_rows,
        output_names=list(slot_spec.output_names or []) or None,
        effectful_runtime_values=effectful_runtime_values,
        slot_id=slot_spec.slot_id,
        initial_candidates=getattr(cfg, "decision_initial_candidates", 3),
        max_candidates=getattr(cfg, "decision_max_candidates", 5),
        use_llm=True,
        timeout=getattr(cfg, "decision_cost_budget_s", 15),
    )

    head_source = outcome.head_source
    if outcome.divergence is not None:
        from semipy.kernel.population import score_candidates_against_contract, select_head

        contract_scores = None
        if slot is not None:
            candidates = {cid: run.source for cid, run in outcome.divergence.runs.items()}
            contract_scores = score_candidates_against_contract(
                candidates, slot=slot, slot_spec=slot_spec, config=cfg,
            )
        _head_id, ranked_source = select_head(
            outcome.divergence, contract_pass_fractions=contract_scores,
        )
        if ranked_source is not None:
            head_source = ranked_source

    head_entry = entry_by_source.get(head_source or "")
    if head_entry is None:
        # All candidates failed, or the head source was not captured -> fall back
        # to the unchanged single-generation path (raises as before on failure).
        head_entry = SemiAgent().generate(generation_spec)
    return head_entry, outcome.decision_set


def _execute_slot_locked(
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    config: Any,
    cache_dir: Path,
    source_file: str,
    portal_anchor: str,
    session_id: str,
    module_name: str,
) -> Any:
    """Body of :func:`execute_slot`, run while holding the per-portal lock."""
    portal = load_portal(cache_dir, session_id, portal_anchor, module_name)
    if not portal.slots:
        migrated = migrate_legacy_portals(cache_dir, session_id, portal_anchor, module_name)
        if migrated is not None:
            portal = migrated
    try:
        from semipy.diagnostics_export import clear_diagnostics

        clear_diagnostics(cache_dir, slot_spec.slot_id)
    except Exception:
        pass
    slot = _ensure_slot(portal, slot_spec)
    _record_slot_input_observations(slot, runtime_values)
    _record_input_profile(slot, runtime_values)
    if _runtime_profile_is_scalar_only(runtime_values):
        _harvest_caller_series_samples(runtime_values, slot)
    save_portal(cache_dir, portal)

    dep_graph = _get_dep_graph(cache_dir)
    current_slot_ref = SlotRef(session_id=session_id, slot_id=slot_spec.slot_id)

    # Observe this call's upstreams from the flow each input value carries. Edges are
    # refreshed to exactly these producers (so a dropped dependency leaves no ghost
    # edge), and the producing commit ids drive pull-based input-staleness below.
    _observed_upstreams: dict[str, str] = {}
    if dep_graph is not None:
        _obs_refs = []
        for val in runtime_values.values():
            flow = extract_flow(val)
            if flow is not None:
                _obs_refs.append(flow.producing_slot)
                _pcid = getattr(flow, "producing_commit_id", "") or ""
                if _pcid:
                    _observed_upstreams[flow.producing_slot.key()] = _pcid
        set_incoming_edges(dep_graph, current_slot_ref, _obs_refs)

    force_regenerate = False
    old_snap = slot.slot_spec if isinstance(slot.slot_spec, dict) else {}
    old_eq = equivalence_key_from_stored_snapshot(old_snap) if old_snap else None
    if old_eq is not None:
        spec_changed = old_eq != slot_spec.spec_equivalence_key
    else:
        spec_changed = bool(slot.spec_hash) and slot.spec_hash != slot_spec.spec_hash
    if spec_changed:
        force_regenerate = True
        # The slot's meaning changed: retire cases derived under the old meaning so
        # the acceptance gate / effect-diff don't fight the user's intent. The
        # maintainer re-seeds under the new spec; still-valid invariants reactivate.
        if getattr(config, "contract_enabled", True):
            try:
                from semipy.contract.access import retire_active_cases

                n_retired = retire_active_cases(slot, "spec changed")
                if n_retired:
                    save_portal(cache_dir, portal)
                    if config.verbose:
                        print_pipeline_log(
                            _call_site_from_slot(slot_spec),
                            "contract",
                            f"Spec changed; retired {n_retired} prior case(s) (re-seeding under new spec).",
                        )
            except Exception:
                pass
        if dep_graph is not None:
            mark_downstream_stale(dep_graph, current_slot_ref, "spec changed")
    # Pull-based input staleness: regenerate iff an upstream this slot actually
    # consumed before now presents a different commit. Compared against the current
    # call's inputs only, so dropped deps never over-invalidate and mutual deps are
    # caught without a graph cycle; recording the new set lets it settle (no churn).
    if dep_graph is not None:
        if stale_against_inputs(dep_graph, current_slot_ref, _observed_upstreams):
            force_regenerate = True
            clear_stale(dep_graph, current_slot_ref)
        record_consumed(dep_graph, current_slot_ref, _observed_upstreams)
        save_dependency_graph(cache_dir, dep_graph)
    adv = getattr(slot, "advisor_state", None) or {}
    if isinstance(adv, dict) and adv.get("force_regenerate_next"):
        force_regenerate = True
        adv.pop("force_regenerate_next", None)
        slot.advisor_state = adv
        save_portal(cache_dir, portal)

    # Interpret-until-shape-stable: while the slot is interpreted and has not yet
    # promoted, the LLM runs per call (memoized) and the slot tries to compile a
    # residual from accumulated examples. Once promoted, a normal commit exists and
    # we fall through to REUSE it. See semipy/interpreted.py.
    if getattr(slot_spec, "interpreted", False) and not (
        isinstance(adv, dict) and adv.get("interpreted_promoted")
    ):
        return _execute_interpreted_slot(
            slot_spec=slot_spec,
            runtime_values=runtime_values,
            slot=slot,
            portal=portal,
            dep_graph=dep_graph,
            current_slot_ref=current_slot_ref,
            cache_dir=cache_dir,
            session_id=session_id,
            module_name=module_name,
            config=config,
        )

    sketch_library = load_sketch_library(cache_dir)
    # Routing is owned by the Orchestrator seam (KTD2: code-driven, not autonomous).
    # Orchestrator.route delegates to resolve(), so behavior is identical.
    _orchestrator = Orchestrator(cache_dir=cache_dir, session_id=session_id)
    resolution = _orchestrator.route(
        portal,
        slot_spec,
        force_regenerate=force_regenerate,
        sketch_library=sketch_library,
    )

    # Compute user module globals once here for dispatch module loading and validation.
    # This ensures user-defined types referenced by generated functions are available
    # in the dispatch exec namespace on both REUSE and GENERATE paths.
    _slot_exec_ns: dict[str, Any] | None = _execution_namespace_for_generation(slot_spec.source_span[0]) or None

    source_file_imports = _extract_source_file_imports(source_file)

    _verify_failure_msg: str | None = None
    _sketch_context: str | None = None
    _post_reuse_validation: Any = None
    _post_reuse_semantic: Any = None

    if resolution.decision == Decision.INSTANTIATE and resolution.sketch_id:
        sk = sketch_library.sketches.get(resolution.sketch_id)
        hv = resolution.sketch_hole_values or {}
        src = ""
        instant_ok = False
        fn_try = None
        if sk is not None:
            src = instantiate_sketch_code(sk, hv)
            if validate_instantiated_source(src):
                try:
                    fn_try = _compile_source(src)
                except Exception as ex:
                    _verify_failure_msg = str(ex).strip()
                if fn_try is not None:
                    vr_last = None
                    for sample_in in _reuse_verify_sample_inputs(
                        slot_spec, slot, runtime_values
                    ):
                        vr_last = verify_runtime_execution(
                            fn=fn_try,
                            expected_type=slot_spec.expected_type,
                            sample_input=sample_in,
                            slot_category=slot_spec.expected_category,
                            output_names=list(slot_spec.output_names or []),
                            enable_execution=True,
                            free_variables=list(slot_spec.free_variables),
                            usage_hints=list(slot_spec.usage_hints or []),
                        )
                        if not vr_last.passed:
                            break
                    if vr_last is not None and vr_last.passed:
                        instant_ok = True
                    elif vr_last is not None:
                        _verify_failure_msg = (vr_last.error_message or "").strip()
            else:
                _verify_failure_msg = "instantiated sketch source failed syntax validation"
        if instant_ok and sk is not None and fn_try is not None:
            dispatch_path = _dispatch_module_path(cache_dir, module_name)
            try:
                result = _call_generated_fn(
                    fn=fn_try,
                    slot_spec=slot_spec,
                    runtime_values=runtime_values,
                    prompt_preview=slot_spec.spec_text,
                    generated_path=str(dispatch_path),
                    cache_dir=cache_dir,
                    slot=slot,
                    commit=None,  # INSTANTIATE: no commit exists yet (created after a successful call)
                    portal=portal,
                )
            except SemiCallError as e:
                cause = e.__cause__
                if cause is not None:
                    _verify_failure_msg = "".join(
                        traceback.format_exception_only(type(cause), cause)
                    ).strip()
                else:
                    _verify_failure_msg = str(e)
                instant_ok = False
            else:
                parent_ids = tuple(sk.source_commit_ids[-1:]) if sk.source_commit_ids else ()
                branch_name = "main" if not slot.commits else f"b_{slot_spec.spec_hash[:8]}"
                commit = create_commit(
                    parent_ids,
                    src,
                    slot_spec.spec_hash,
                    freeze_constants({}),
                    slot_spec.spec_text,
                    Decision.INSTANTIATE.name,
                    usage_id=slot_spec.slot_id,
                    runtime_input_fingerprint=compute_runtime_input_fingerprint(runtime_values),
                    binding_id=sk.binding_id or "",
                    source_snapshot=_capture_slot_source_snapshot(slot_spec),
                )
                add_commit_to_slot(slot, commit, branch_name, usage_id=slot_spec.slot_id)
                slot.spec_hash = slot_spec.spec_hash
                slot.slot_spec = _slot_spec_snapshot(slot_spec)
                sk2 = sketch_library.sketches.get(sk.sketch_id)
                if sk2 is not None:
                    sk2.instantiation_count += 1
                    sk2.validated = True
                save_sketch_library(cache_dir, sketch_library)
                if dep_graph is not None:
                    update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
                    clear_stale(dep_graph, current_slot_ref)
                    save_dependency_graph(cache_dir, dep_graph)
                write_dispatch_module(cache_dir, portal, sketch_library=sketch_library)
                save_portal(cache_dir, portal)
                _dispatch_globals_cache.pop(module_name, None)
                if config.verbose:
                    print_pipeline_log(
                        _call_site_from_slot(slot_spec),
                        "instantiate",
                        "Reusing learned pattern with parameter substitution; no generation needed.",
                    )
                if dep_graph is not None:
                    upstream_chain = []
                    for val in runtime_values.values():
                        flow = extract_flow(val)
                        if flow is not None:
                            upstream_chain.append(flow.producing_slot)
                    result = attach_producer_flow(
                        result,
                        create_flow(
                            session_id=session_id,
                            slot_id=slot_spec.slot_id,
                            commit_id=commit.commit_id,
                            upstream_chain=upstream_chain,
                            output_profile=profile_output(result),
                        ),
                    )
                return result
        if not instant_ok:
            tmpl = getattr(sk, "spec_template", "") if sk is not None else ""
            _sketch_context = (
                f"Sketch spec template: {tmpl}\n"
                f"Instantiated source:\n{src[:8000]}"
            )
            resolution = RoutingPolicy(portal).decide(
                slot_spec,
                slot,
                force_regenerate=True,
                sketch_library=sketch_library,
            )

    if resolution.decision == Decision.REUSE and resolution.commit_id is not None:
        dispatch_slot_id = resolution.reuse_dispatch_slot_id or slot_spec.slot_id
        commit_holder = portal.slots.get(dispatch_slot_id) or slot
        commit = commit_holder.commits.get(resolution.commit_id) if commit_holder else None
        if commit is None:
            force_regenerate = True
        else:
            fn_name = function_name_for_commit(commit_holder, commit)
            dispatch_path = _dispatch_module_path(cache_dir, module_name)
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache, globals_seed=_slot_exec_ns)
            if fn is None:
                _dispatch_globals_cache.pop(module_name, None)
                fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache, globals_seed=_slot_exec_ns)
            if fn is None:
                fn = load_function_from_dispatch_by_slot_id(
                    cache_dir,
                    module_name,
                    dispatch_slot_id,
                    _dispatch_globals_cache,
                    globals_seed=_slot_exec_ns,
                )
            if fn is None:
                force_regenerate = True
            else:
                call_site = _call_site_from_slot(slot_spec)
                current_fp = compute_runtime_input_fingerprint(runtime_values)
                stored_fp = getattr(commit, "runtime_input_fingerprint", "") or ""
                # R3/R4: scope membership replaces fingerprint equality. Equal
                # fingerprint stays a fast pre-check (equal => in scope); an unequal
                # fingerprint falls through to the compiled scope predicate instead
                # of forcing verify, and an out-of-scope input deopts (ledgered).
                skip_verify, _scope_large = _reuse_scope_decision(
                    slot, commit, slot_spec, runtime_values,
                    current_fp=current_fp, stored_fp=stored_fp,
                    portal=portal, cache_dir=cache_dir, config=config, call_site=call_site,
                )

                if not skip_verify:
                    if _scope_large:
                        # Above the size threshold: sampled verify (recorded power)
                        # replaces whole-input verify (R4, D5).
                        vr_last = _sampled_reuse_verify(fn, slot, slot_spec, runtime_values, config)
                    else:
                        vr_last = None
                        for sample_in in _reuse_verify_sample_inputs(
                            slot_spec, slot, runtime_values
                        ):
                            vr_last = verify_runtime_execution(
                                fn=fn,
                                expected_type=slot_spec.expected_type,
                                sample_input=sample_in,
                                slot_category=slot_spec.expected_category,
                                output_names=list(slot_spec.output_names or []),
                                enable_execution=True,
                                free_variables=list(slot_spec.free_variables),
                                usage_hints=list(slot_spec.usage_hints or []),
                            )
                            if not vr_last.passed:
                                break
                    if vr_last is None or not vr_last.passed:
                        _verify_failure_msg = (
                            (vr_last.error_message or "").strip() if vr_last else ""
                        )
                        _post_reuse_validation = vr_last
                        if config.verbose:
                            err = _verify_failure_msg.replace("\n", " ")
                            if len(err) > 160:
                                err = err[:157] + "..."
                            print_pipeline_log(
                                call_site,
                                "reuse_verify",
                                f"Runtime check failed; adapting. {err}",
                            )
                        force_regenerate = True
                    else:
                        # Type check passed. First enforce the behavioral contract:
                        # a reused impl that violates a previously-recorded decision
                        # is rejected and the violated case's reason drives the ADAPT.
                        _c_msg, _c_val = _run_reuse_contract_gate(
                            slot, slot_spec, commit, config, call_site
                        )
                        if _c_msg is not None:
                            _verify_failure_msg = _c_msg
                            _post_reuse_validation = _c_val
                            force_regenerate = True
                        # Effect gate: a reused effectful impl may emit an
                        # invariant-violating script on this input shape.
                        if not force_regenerate:
                            _e_msg, _e_val = _run_reuse_effect_gate(
                                slot, slot_spec, commit, runtime_values, config, call_site
                            )
                            if _e_msg is not None:
                                _verify_failure_msg = _e_msg
                                _post_reuse_validation = _e_val
                                force_regenerate = True
                        # Then run semantic check if threshold crossed to catch
                        # implementations that are type-correct but semantically
                        # inadequate for new input patterns.
                        _increment_semantic_reuse_counter(slot, commit.commit_id)
                        _adaptive = getattr(config, "adaptive_mode", False)
                        _pre_filter_triggered, _pre_filter_signals = (
                            _check_intent_judge_pre_filters(slot, commit.commit_id)
                            if _adaptive and not force_regenerate else (False, [])
                        )
                        # The semantic judge evaluates whether the impl's OUTPUT fits
                        # the intent; an effectful slot returns an EffectScript, not a
                        # domain value, so the judge would misfire and force ADAPT on
                        # every reuse. Reuse verification for effectful slots is owned
                        # by the effect gate (already run above), so skip the judge here.
                        _is_effectful_reuse = False
                        try:
                            from semipy.effects.inject import fn_is_effectful

                            _is_effectful_reuse = (
                                getattr(config, "effects_enabled", False) and fn_is_effectful(fn)
                            )
                        except Exception:
                            _is_effectful_reuse = False
                        _run_check = (not force_regenerate) and (not _is_effectful_reuse) and (
                            _should_semantic_check(slot, commit.commit_id, config) or (
                                _adaptive and _pre_filter_triggered
                            )
                        )
                        if _run_check:
                            from semipy.agents.decision import evaluate_reuse_semantics

                            session_obs = _slot_session_observations(slot)
                            impl_source = getattr(commit, "generated_source", "") or ""

                            _real_outcomes: list[dict] | None = None
                            _batch_summary: dict | None = None
                            if _adaptive:
                                _real_outcomes = _get_recent_call_outcomes(slot, n=20) or None
                                _batch_summary = _get_batch_summary_from_outcomes(slot)

                            if config.verbose:
                                _ctx_parts = []
                                if _pre_filter_signals:
                                    _ctx_parts.extend(_pre_filter_signals)
                                if not _ctx_parts:
                                    _ctx_parts.append("new input patterns")
                                _ctx_str = ", ".join(_ctx_parts)
                                print_pipeline_log(
                                    call_site,
                                    "context_change",
                                    f"Context shift detected ({_ctx_str}); evaluating intent-fit...",
                                )
                            sem = evaluate_reuse_semantics(
                                slot_spec=slot_spec,
                                implementation_source=impl_source,
                                session_observations=session_obs,
                                call_outcomes=_real_outcomes,
                                batch_summary=_batch_summary,
                            )
                            # Mark where we left off so pre-filter doesn't retrigger immediately.
                            if _adaptive:
                                _adv = getattr(slot, "advisor_state", None) or {}
                                _ring = _adv.get("call_outcomes", [])
                                _adv["intent_judge_last_outcome_count"] = len(_ring)
                                slot.advisor_state = _adv
                            _update_semantic_state(
                                slot, commit.commit_id, sem.decision,
                                portal=portal, cache_dir=cache_dir,
                                reasoning=getattr(sem, "reasoning", "") or "",
                            )
                            if sem.decision == "adapt":
                                _verify_failure_msg = (
                                    f"Intent check: {sem.reasoning}"
                                )
                                if sem.problematic_inputs:
                                    examples = "; ".join(
                                        s[:120] for s in sem.problematic_inputs[:3]
                                    )
                                    _verify_failure_msg += (
                                        f" Problematic inputs: {examples}"
                                    )
                                if _adaptive and getattr(sem, "ambiguous_inputs", None):
                                    ambi_strs = [
                                        f"{a.get('input', '?')} (picked: {a.get('picked_output', '?')}, why: {a.get('why', '')})"
                                        for a in sem.ambiguous_inputs[:3]
                                    ]
                                    _verify_failure_msg += (
                                        f" Ambiguous inputs: {'; '.join(ambi_strs)}"
                                    )
                                if _batch_summary:
                                    _verify_failure_msg += (
                                        f" Batch: {_batch_summary.get('n_in',0)} calls, "
                                        f"{_batch_summary.get('n_raised',0)} raised, "
                                        f"{_batch_summary.get('n_unique_outputs',0)} unique outputs."
                                    )
                                _post_reuse_semantic = sem
                                # Count semantic-driven adapts so the convergence cap
                                # in _should_semantic_check can stop perpetual churn.
                                _adv_sc = getattr(slot, "advisor_state", None) or {}
                                if not isinstance(_adv_sc, dict):
                                    _adv_sc = {}
                                _sc_n = int(_adv_sc.get("semantic_adapt_count", 0) or 0) + 1
                                _adv_sc["semantic_adapt_count"] = _sc_n
                                slot.advisor_state = _adv_sc
                                _sc_cap = int(getattr(config, "semantic_verify_max_adapts", 2) or 0)
                                if config.verbose:
                                    print_pipeline_log(
                                        call_site,
                                        "semantic_check",
                                        f"Implementation does not satisfy intent; adapting. {sem.reasoning}",
                                    )
                                    if _sc_cap and _sc_n >= _sc_cap:
                                        print_pipeline_log(
                                            call_site,
                                            "semantic_check",
                                            f"Intent rechecks capped at {_sc_cap} for this slot; "
                                            "trusting the implementation hereafter to avoid "
                                            "regenerating on every new input.",
                                        )
                                force_regenerate = True
                            else:
                                if _adaptive and config.verbose and getattr(sem, "ambiguous_inputs", None):
                                    ambi_count = len(sem.ambiguous_inputs)
                                    print_pipeline_log(
                                        call_site,
                                        "intent_warn",
                                        f"[WARN] {ambi_count} ambiguous input(s) detected; implementation chose one interpretation. {sem.reasoning}",
                                    )
                                elif config.verbose:
                                    print_pipeline_log(
                                        call_site,
                                        "semantic_check",
                                        "Implementation satisfies intent for observed inputs; proceeding with reuse.",
                                    )
                        if not force_regenerate:
                            if config.verbose:
                                is_donor = bool(resolution.reuse_dispatch_slot_id)
                                donor_note = " (from donor slot)" if is_donor else ""
                                print_pipeline_log(
                                    call_site,
                                    "reuse",
                                    f"Reusing cached implementation{donor_note}; runtime verify passed.",
                                )
                elif skip_verify:
                    _increment_semantic_reuse_counter(slot, commit.commit_id)
                    if config.verbose:
                        is_donor = bool(resolution.reuse_dispatch_slot_id)
                        donor_note = " (from donor slot)" if is_donor else ""
                        print_pipeline_log(
                            call_site,
                            "reuse",
                            f"Reusing cached implementation{donor_note}; same input fingerprint.",
                        )

                if not force_regenerate:
                    try:
                        result = _call_generated_fn(
                            fn=fn,
                            slot_spec=slot_spec,
                            runtime_values=runtime_values,
                            prompt_preview=slot_spec.spec_text,
                            generated_path=str(dispatch_path),
                            cache_dir=cache_dir,
                            slot=slot,
                            commit=commit,
                            portal=portal,
                        )
                    except SemiCallError as e:
                        cause = e.__cause__
                        if cause is not None:
                            _verify_failure_msg = "".join(
                                traceback.format_exception_only(type(cause), cause)
                            ).strip()
                        else:
                            _verify_failure_msg = str(e)
                        if config.verbose:
                            err = _verify_failure_msg.replace("\n", " ")
                            if len(err) > 160:
                                err = err[:157] + "..."
                            print_pipeline_log(
                                call_site,
                                "reuse_verify",
                                f"Cached implementation raised at runtime; adapting. {err}",
                            )
                        force_regenerate = True
                        if getattr(config, "adaptive_mode", False):
                            _exc_cause = e.__cause__
                            _record_call_outcome(slot, CallOutcome(
                                ts=time.time(),
                                runtime_input_fingerprint=current_fp,
                                input_repr_short=repr(list(runtime_values.values())[:1])[:80],
                                returned_type="",
                                returned_repr_short="",
                                raised=True,
                                exception_type=type(_exc_cause).__name__ if _exc_cause else "SemiCallError",
                            ))
                    else:
                        if getattr(config, "adaptive_mode", False):
                            _record_call_outcome(slot, CallOutcome(
                                ts=time.time(),
                                runtime_input_fingerprint=current_fp,
                                input_repr_short=repr(list(runtime_values.values())[:1])[:80],
                                returned_type=type(result).__name__,
                                returned_repr_short=repr(result)[:80],
                                raised=False,
                            ))
                        if dep_graph is not None:
                            upstream_chain = []
                            for val in runtime_values.values():
                                flow = extract_flow(val)
                                if flow is not None:
                                    upstream_chain.append(flow.producing_slot)
                            result = attach_producer_flow(
                                result,
                                create_flow(
                                    session_id=session_id,
                                    slot_id=slot_spec.slot_id,
                                    commit_id=commit.commit_id,
                                    upstream_chain=upstream_chain,
                                    output_profile=profile_output(result),
                                ),
                            )
                            update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
                            clear_stale(dep_graph, current_slot_ref)
                            save_dependency_graph(cache_dir, dep_graph)
                        return result

        if force_regenerate:
            resolution = RoutingPolicy(portal).decide(
                slot_spec,
                slot,
                force_regenerate=True,
                sketch_library=sketch_library,
                prior_validation=_post_reuse_validation,
                semantic_result=_post_reuse_semantic,
            )

    # U9/R16: adapt from the shipped floor instead of generating from scratch
    # while a baseline exists for this call site (never touches a slot that
    # already has a local parent -- the local overlay wins, per U8).
    _floor_baseline_version = adapt_from_shipped_floor(slot, resolution)

    # ADAPT / GENERATE
    generation_spec = build_generation_spec(
        slot_spec=slot_spec,
        portal=portal,
        resolution=resolution,
        runtime_values=runtime_values,
        dep_graph=dep_graph,
        current_slot_ref=current_slot_ref,
        source_file_imports=source_file_imports,
        verify_failure_context=_verify_failure_msg,
        sketch_context=_sketch_context,
    )
    # Multi-candidate draw to surface the model's silent fork (F0). Opt-in via
    # decisions_enabled and only on a genuine generation (GENERATE/ADAPT); REUSE /
    # INSTANTIATE never reach here. Off by default -> the single generation below.
    _pending_decision_set = None
    if config.decisions_enabled and resolution.decision in (Decision.GENERATE, Decision.ADAPT):
        entry, _pending_decision_set = _resolve_slot_with_decisions(
            slot_spec, generation_spec, runtime_values, slot=slot
        )
    else:
        entry = SemiAgent().generate(generation_spec)

    # Behavioral-contract acceptance gate + effect tracing: the candidate must satisfy
    # carried-forward prior decisions before commit (enforces "don't forget"), and its
    # behavior diff vs the parent is recorded as the change's traced effect.
    entry, _gate_quarantine_ids, _change_record = _run_generate_contract_gate(
        slot, slot_spec, entry, generation_spec, resolution, runtime_values,
        config, _call_site_from_slot(slot_spec),
    )

    # Effect acceptance gate: for an effectful candidate, stage its EffectScript in
    # a shadow and enforce the effect invariants (reversible + bounded blast radius)
    # before commit, regenerating on violation. No-op for pure slots.
    entry = _run_generate_effect_gate(
        slot, slot_spec, entry, generation_spec, resolution, runtime_values,
        config, _call_site_from_slot(slot_spec),
    )

    # Floor gate: replay the shipped floor (when a baseline is installed for
    # this call site) against the candidate before it can commit. Composes
    # with the contract gate above rather than replacing it; raises (never
    # quarantines) on budget exhaustion.
    entry = _run_floor_gate(
        slot, slot_spec, entry, generation_spec, resolution, runtime_values,
        config, _call_site_from_slot(slot_spec),
    )

    # History identity fields: the new model keys by spec_hash, but existing Commit schema still
    # requires these fields. Keep them stable per spec.
    template_fingerprint = slot_spec.spec_hash
    constants_snapshot = freeze_constants({})
    parent_ids = tuple(resolution.parent_commit_ids or ())

    decision_str = resolution.decision.name if resolution.decision else "GENERATE"
    branch_name = resolution.branch_name or ("main" if resolution.decision == Decision.GENERATE else f"b_{slot_spec.spec_hash[:8]}")

    commit = create_commit(
        parent_ids,
        entry.generated_source,
        template_fingerprint,
        constants_snapshot,
        slot_spec.spec_text,
        decision_str,
        usage_id=slot_spec.slot_id,
        runtime_input_fingerprint=compute_runtime_input_fingerprint(runtime_values),
        source_snapshot=_capture_slot_source_snapshot(slot_spec),
        change_record=_change_record,
    )
    add_commit_to_slot(slot, commit, branch_name, usage_id=slot_spec.slot_id)
    # Mint the scope predicate for this commit (R3) from the accumulated input
    # profiles, so the reuse fast path checks membership rather than fingerprint
    # equality on the next call.
    _mint_and_store_scope(slot, commit.commit_id)

    # U8/U9: stamp which installed baseline this commit was adapted against,
    # so a later package upgrade can demote it to needs-revalidation
    # (distribution.baseline.is_stale_overlay_commit) without deleting it.
    if _floor_baseline_version is not None:
        cr_existing = commit.commitment_record or {}
        cr_new = dict(cr_existing)
        cr_new["baseline_version"] = _floor_baseline_version
        slot.commits[commit.commit_id] = replace(commit, commitment_record=cr_new)
        commit = slot.commits[commit.commit_id]

    # Surface the silent fork (F0): persist the DecisionSet on the slot so the #?
    # surface + steering can render it. Empty/agreeing draws attach nothing.
    if _pending_decision_set is not None and not _pending_decision_set.is_empty():
        try:
            from semipy.decisions.persistence import attach_decision_set

            attach_decision_set(slot, _pending_decision_set)
        except Exception:
            pass

    # Quarantine any carried-forward cases the regeneration budget could not satisfy,
    # so the system makes forward progress and surfaces the conflict rather than livelocking.
    if _gate_quarantine_ids:
        try:
            from semipy.contract.access import quarantine_cases

            quarantine_cases(
                slot,
                list(_gate_quarantine_ids),
                "unsatisfiable after regeneration budget",
            )
        except Exception:
            pass

    # Update stored slot snapshot after regeneration.
    slot.spec_hash = slot_spec.spec_hash
    slot.slot_spec = _slot_spec_snapshot(slot_spec)

    if dep_graph is not None:
        update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
        clear_stale(dep_graph, current_slot_ref)
        save_dependency_graph(cache_dir, dep_graph)

    write_dispatch_module(cache_dir, portal, sketch_library=sketch_library)
    save_portal(cache_dir, portal)

    if resolution.decision in (Decision.GENERATE, Decision.ADAPT):
        from semipy.agents.skeleton_writer import (
            surface_skeleton as _surface_skeleton,
            detect_promoted_keys as _detect_promoted_keys,
        )
        from semipy.agents.steering import synthesize_steering as _synthesize_steering

        prior_steering = _get_prior_steering(slot, slot_spec)
        promoted_keys = _detect_promoted_keys(slot_spec)
        # Ground steering synthesis in the change's traced reason/effect.
        try:
            from semipy.contract.change import change_record_from_dict as _crfd

            _cr_obj = _crfd(_change_record)
            _summary_bits = []
            if _cr_obj.reason:
                _summary_bits.append(_cr_obj.reason.splitlines()[0][:160])
            if _cr_obj.effect_diff or _cr_obj.n_compared:
                _summary_bits.append(_cr_obj.summary())
            generation_spec.change_summary = " | ".join(_summary_bits) or None
        except Exception:
            generation_spec.change_summary = None
        try:
            entry.steering = _synthesize_steering(
                generation_spec,
                entry,
                slot,
                prior_steering,
                promoted_keys=promoted_keys,
            )
        except Exception:
            entry.steering = None

        # Persist the SteeringBlock onto the commit's commitment_record so prior
        # values are available on the next run for signature-based carry-forward.
        if entry.steering is not None:
            cr_existing = commit.commitment_record or {}
            cr_new = dict(cr_existing)
            try:
                cr_new["steering"] = entry.steering.model_dump()
            except Exception:
                cr_new["steering"] = None
            slot.commits[commit.commit_id] = replace(commit, commitment_record=cr_new)
            commit = slot.commits[commit.commit_id]
            save_portal(cache_dir, portal)
            write_dispatch_module(cache_dir, portal, sketch_library=sketch_library)

        # Run synchronously so script termination cannot drop the surface write.
        if _should_surface_skeleton(slot_spec):
            surface_overrides = _surface_skeleton(slot_spec, entry) or {}
        else:
            surface_overrides = {}
        # Re-snapshot the slot region NOW so the commit captures the freshly
        # written #< surface lines in addition to the user's #> spec.
        try:
            post_surface_snapshot = _capture_slot_source_snapshot(slot_spec)
        except Exception:
            post_surface_snapshot = {}
        if post_surface_snapshot:
            slot.commits[commit.commit_id] = replace(
                slot.commits[commit.commit_id],
                source_snapshot=post_surface_snapshot,
            )
            commit = slot.commits[commit.commit_id]
            save_portal(cache_dir, portal)
        # Merge promoted-from-spec keys with surface-reported overrides; promoted
        # `#>` lines take precedence since those are hard contracts.
        combined_overrides: dict[str, str] = {}
        combined_overrides.update(surface_overrides)
        combined_overrides.update(promoted_keys)
        if combined_overrides:
            adv = getattr(slot, "advisor_state", None) or {}
            if not isinstance(adv, dict):
                adv = {}
            existing = adv.get("steering_overrides") or {}
            if not isinstance(existing, dict):
                existing = {}
            existing_dict = {str(k): str(v) for k, v in existing.items() if v}
            existing_dict.update({
                str(k): str(v) for k, v in combined_overrides.items() if v
            })
            adv["steering_overrides"] = existing_dict
            slot.advisor_state = adv
            save_portal(cache_dir, portal)
        _schedule_sketch_binding_extraction(
            spec_text=slot_spec.spec_text,
            generated_source=entry.generated_source,
            commit_id=commit.commit_id,
            slot_spec=slot_spec,
            cache_dir=cache_dir,
            session_id=session_id,
            portal_anchor=portal_anchor,
            module_name=module_name,
            slot_id=slot_spec.slot_id,
        )
        # Maintain the behavioral contract (deterministic invariant seeding + optional
        # LLM pass): records reason-tagged cases so future iterations cannot forget.
        # Runs after sketch extraction so it reloads the latest persisted portal.
        _schedule_contract_maintenance(
            slot_spec=slot_spec,
            runtime_values=runtime_values,
            new_source=entry.generated_source,
            change_record=dict(getattr(commit, "change_record", {}) or {}),
            decision=decision_str,
            commit_id=commit.commit_id,
            cache_dir=cache_dir,
            session_id=session_id,
            portal_anchor=portal_anchor,
            module_name=module_name,
            slot_id=slot_spec.slot_id,
        )
    _dispatch_globals_cache.pop(module_name, None)

    fn_name = function_name_for_commit(slot, commit)
    dispatch_path = _dispatch_module_path(cache_dir, module_name)
    fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache, globals_seed=_slot_exec_ns)
    if fn is None:
        fn = load_function_from_dispatch_by_slot_id(cache_dir, module_name, slot.slot_id, _dispatch_globals_cache, globals_seed=_slot_exec_ns)
    if fn is None:
        fn = entry.compiled_fn
    if fn is None:
        raise RuntimeError(f"Generated function missing for slot {slot_spec.slot_id}")

    result = _call_generated_fn(
        fn=fn,
        slot_spec=slot_spec,
        runtime_values=runtime_values,
        prompt_preview=slot_spec.spec_text,
        generated_path=str(dispatch_path),
        cache_dir=cache_dir,
        slot=slot,
        commit=commit,
        portal=portal,
    )

    if dep_graph is not None:
        upstream_chain: list[SlotRef] = []
        for val in runtime_values.values():
            flow = extract_flow(val)
            if flow is not None:
                upstream_chain.append(flow.producing_slot)
        result = attach_producer_flow(
            result,
            create_flow(
                session_id=session_id,
                slot_id=slot_spec.slot_id,
                commit_id=commit.commit_id,
                upstream_chain=upstream_chain,
                output_profile=profile_output(result),
            ),
        )

    return result


def _make_slot_proxy(slot_spec: SlotSpec, source_file: str, cache_dir: Path | None) -> Callable[..., Any]:
    """
    Build a callable used by the scaffold:
    - for STATEMENT_BLOCK: return scalar when output_names has 1 element; otherwise return dict
    - for EXPRESSION/FUNCTION_BODY: return scalar
    """

    cache_dir_path: Path | None = cache_dir

    def __slot_proxy__(**kwargs: Any) -> Any:
        # Cache dir is read dynamically from config so users can call
        # `semipy.configure(cache_dir=...)` after importing this module.
        effective_cache_dir = cache_dir_path if cache_dir_path is not None else Path(get_config().cache_dir)
        # Single-flight per slot: under concurrent access (a threaded pipeline or a
        # server handling parallel requests) this ensures only one thread generates a
        # given slot while the rest wait and then REUSE it, instead of all firing the
        # LLM at once and racing the portal writes.
        with _slot_singleflight_lock(slot_spec.slot_id):
            result = execute_slot(
                slot_spec=slot_spec,
                runtime_values=kwargs,
                source_file=source_file,
                cache_dir=effective_cache_dir,
            )
        if slot_spec.expected_category == SlotCategory.STATEMENT_BLOCK:
            if len(slot_spec.output_names) == 1 and isinstance(result, dict):
                inner = result.get(slot_spec.output_names[0])
                # Carry producer flow across the single-output unwrap so reactivity
                # wires for the canonical #> form (the dict wrapper is discarded; the
                # flow would be lost otherwise). Re-profile against the inner value so
                # downstream shape inference sees the real columns, not {'keys':[name]}.
                flow = extract_flow(result)
                if flow is not None:
                    inner = attach_producer_flow(
                        inner,
                        create_flow(
                            session_id=flow.producing_slot.session_id,
                            slot_id=flow.producing_slot.slot_id,
                            commit_id=flow.producing_commit_id,
                            upstream_chain=list(flow.upstream_chain),
                            output_profile=profile_output(inner),
                        ),
                    )
                return inner
            return result
        return result

    return __slot_proxy__

