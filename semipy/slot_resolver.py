from __future__ import annotations

import ast
import json
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
from semipy.history.version_control import (
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.session_anchor import resolve_portal_anchor
from semipy.reactivity import (
    SlotRef,
    _get_dep_graph,
    add_dependency,
    attach_producer_flow,
    clear_stale,
    create_flow,
    extract_flow,
    get_downstream_requirements,
    is_stale,
    mark_downstream_stale,
    profile_output,
    save_dependency_graph,
    update_slot_commit,
)
from semipy.resolver import resolve
import inspect as _inspect

from semipy.agents.profiler import _is_collection_like
from semipy.documents import materialize_runtime_document_inputs
from semipy.library.binding import extract_binding_async
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
    save_portal,
    write_dispatch_module,
    _dispatch_module_path,
)
from semipy.runtime_fingerprint import compute_runtime_input_fingerprint
from semipy.types import (
    Decision,
    GenerationSpec,
    SemiCallError,
    SemiCallSite,
    SlotCategory,
    SlotSpec,
    equivalence_key_from_stored_snapshot,
    session_id_from_filename,
    session_module_name_from_filename,
)

_dispatch_globals_cache: dict[str, dict[str, Any]] = {}

_OBSERVATION_MAX_PER_KEY = 100
_REUSE_VERIFY_MAX_SAMPLES = 50
_REUSE_VERIFY_MAX_PER_KEY = 40


def _sample_input_signature(sample_input: dict[str, Any]) -> str:
    try:
        return json.dumps(
            {"args": sample_input.get("args"), "kwargs": sample_input.get("kwargs")},
            default=str,
            sort_keys=True,
        )
    except Exception:
        return repr(sample_input)


def _reuse_verify_sample_inputs(
    slot_spec: SlotSpec,
    slot: Any,
    runtime_values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build distinct sample_input dicts: current invocation plus recorded observations per free variable."""
    primary = _runtime_sample_input(slot_spec, runtime_values)
    samples: list[dict[str, Any]] = [primary]
    seen: set[str] = {_sample_input_signature(primary)}
    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return samples
    for name in slot_spec.free_variables:
        if name not in obs:
            continue
        vals = obs[name]
        if not isinstance(vals, list):
            continue
        for v in vals[:_REUSE_VERIFY_MAX_PER_KEY]:
            rv = dict(runtime_values)
            rv[name] = v
            si = _runtime_sample_input(slot_spec, rv)
            sig = _sample_input_signature(si)
            if sig not in seen:
                seen.add(sig)
                samples.append(si)
                if len(samples) >= _REUSE_VERIFY_MAX_SAMPLES:
                    return samples
    return samples


def _runtime_value_observation_str(val: Any) -> str:
    try:
        if isinstance(val, str):
            return val
        return repr(val)
    except Exception:
        return ""


def _record_slot_input_observations(slot: Any, runtime_values: dict[str, Any]) -> None:
    """Append current runtime values to per-slot observation lists (bounded, distinct)."""
    if not hasattr(slot, "input_observation_samples"):
        slot.input_observation_samples = {}
    d: dict[str, list[str]] = slot.input_observation_samples
    for k, v in runtime_values.items():
        if isinstance(k, str) and k.startswith("_"):
            continue
        s = _runtime_value_observation_str(v)
        if not s:
            continue
        lst = d.setdefault(k, [])
        if s not in lst:
            lst.append(s)
        while len(lst) > _OBSERVATION_MAX_PER_KEY:
            lst.pop(0)


def _runtime_profile_is_scalar_only(runtime_values: dict[str, Any]) -> bool:
    if not runtime_values:
        return True
    for v in runtime_values.values():
        if _is_collection_like(v):
            return False
    return True


def _absorb_samples(slot: Any, key: str, raw_values: list) -> None:
    """Record distinct observation strings into the slot for one parameter key."""
    obs = slot.input_observation_samples.setdefault(key, [])
    for rv in raw_values:
        s = _runtime_value_observation_str(rv)
        if s and s not in obs and len(obs) < _OBSERVATION_MAX_PER_KEY:
            obs.append(s)


def _harvest_caller_series_samples(
    runtime_values: dict[str, Any],
    slot: Any,
    *,
    max_samples: int = 50,
    stack_depth: int = 12,
) -> None:
    """Walk up the call stack looking for a Series/list from which the current scalar came.

    When found, record a sample of its unique values into the slot's observation list
    so the first GENERATE prompt already knows about input variety.
    """
    if not runtime_values:
        return
    scalar_keys = [
        k for k, v in runtime_values.items()
        if isinstance(v, (str, int, float, bool)) and not isinstance(v, type)
    ]
    if not scalar_keys:
        return

    _SKIP_INTERNAL_FRAMES = 3
    frame = _inspect.currentframe()
    try:
        f = frame
        for _ in range(_SKIP_INTERNAL_FRAMES):
            if f is None or f.f_back is None:
                break
            f = f.f_back
        for _ in range(stack_depth):
            if f is None:
                break
            loc = f.f_locals
            for k in scalar_keys:
                cur_val = runtime_values[k]
                for _vname, v in loc.items():
                    if _vname.startswith("_"):
                        continue
                    try:
                        if hasattr(v, "unique") and callable(v.unique) and not isinstance(v, (str, bytes)):
                            _absorb_samples(slot, k, list(v.unique())[:max_samples])
                            return
                        if isinstance(v, list) and len(v) > 1 and any(isinstance(x, type(cur_val)) for x in v[:5]):
                            _absorb_samples(slot, k, v[:max_samples])
                            return
                    except Exception:
                        continue
            f = f.f_back
    except Exception:
        pass
    finally:
        del frame


def _slot_session_observations(slot: Any) -> dict[str, list[str]] | None:
    raw = getattr(slot, "input_observation_samples", None)
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = [str(x) for x in v]
    return out if out else None


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


_semantic_reuse_counters: dict[str, int] = {}
_semantic_last_fingerprints: dict[str, str] = {}


def _has_diverse_observations(slot: Any) -> bool:
    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return False
    for k, v in obs.items():
        if isinstance(k, str) and (k.startswith("_") or k == "self"):
            continue
        if isinstance(v, list) and len(v) > 1:
            return True
    return False


def _obs_content_fingerprint(slot: Any) -> str:
    """Coarse fingerprint of the observation patterns for a slot.

    Normalises each observation value by replacing digit sequences with
    ``N`` and truncating to a short prefix so that inputs differing only
    in numeric IDs or IP addresses (e.g. ``Found child 25792`` vs
    ``Found child 6765``, or ``[client 1.2.3.4]`` vs ``[client 5.6.7.8]``)
    map to the same bucket.  The fingerprint only changes when a
    genuinely new input *pattern* appears.
    """
    import hashlib as _hl
    import re as _re

    _PREFIX_LEN = 24
    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return ""
    parts: list[str] = []
    for k in sorted(obs.keys()):
        if isinstance(k, str) and (k.startswith("_") or k == "self"):
            continue
        v = obs.get(k)
        if isinstance(v, list) and len(v) > 1:
            prefixes = sorted(
                {_re.sub(r"\d+", "N", str(x))[:_PREFIX_LEN] for x in v}
            )
            parts.append(f"{k}:{','.join(prefixes)}")
    return _hl.sha256("|".join(parts).encode()).hexdigest()[:16]


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

    threshold = getattr(config, "semantic_verify_threshold", 3)
    reuse_count = _semantic_reuse_counters.get(counter_key, 0)
    return reuse_count >= threshold


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
) -> None:
    """Persist semantic check result in slot advisor_state and reset counters."""
    adv = getattr(slot, "advisor_state", None) or {}
    if not isinstance(adv, dict):
        adv = {}
    adv["semantic_last_commit_id"] = commit_id
    fp = _obs_content_fingerprint(slot)
    adv["semantic_obs_fingerprint"] = fp
    adv["semantic_decision"] = decision
    slot.advisor_state = adv
    save_portal(cache_dir, portal)

    counter_key = f"{slot.slot_id}:{commit_id}"
    _semantic_reuse_counters[counter_key] = 0
    _semantic_last_fingerprints[counter_key] = fp


def _call_site_from_slot(slot_spec: SlotSpec) -> SemiCallSite:
    filename, lineno, _end = slot_spec.source_span
    return SemiCallSite(filename=filename, lineno=lineno, func_qualname=slot_spec.enclosing_function_qualname)


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
    }


def _ensure_slot(portal: Any, slot_spec: SlotSpec) -> Slot:
    slot = portal.slots.get(slot_spec.slot_id)
    if slot is not None:
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

    exec_ns = _execution_namespace_for_generation(slot_spec.source_span[0])

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
        user_source_code=None,
        execution_namespace=exec_ns or None,
        session_input_observations=session_obs,
        runtime_profile_scalar_only=_runtime_profile_is_scalar_only(runtime_values),
        verify_failure_context=verify_failure_context,
        sketch_context=sketch_context,
    )


def _runtime_sample_input(slot_spec: SlotSpec, runtime_values: dict[str, Any]) -> dict[str, Any]:
    if runtime_values:
        args = [runtime_values.get(n) for n in slot_spec.free_variables]
        return {"args": tuple(args), "kwargs": {}, "runtime_values": dict(runtime_values)}
    return {"args": tuple(), "kwargs": {}, "runtime_values": {}}


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
                    "[semipy] Sketch library: no binding extracted "
                    "(model unavailable, parse failure, or empty response).",
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
        merge_sketch_into_library(lib, sketch, binding)
        save_sketch_library(cache_dir, lib)
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

            print(f"[semipy] Sketch library: extraction failed: {ex}", file=sys.stderr)


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


def _call_generated_fn(
    *,
    fn: Callable[..., Any],
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    prompt_preview: str,
    generated_path: str,
    cache_dir: Path,
) -> Any:
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
            usage_hint="",
            cause=e,
        )
        try:
            from semipy.diagnostics_export import export_from_semi_call_error

            export_from_semi_call_error(cache_dir, slot_spec, err)
        except Exception:
            pass
        raise err from e


def execute_slot(
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    source_file: str,
    cache_dir: Path,
) -> Any:
    """
    Execute one slot:
    - load portal + dependency graph
    - infer/update dependency edges from runtime_values flows
    - detect spec_hash change and stale flags
    - reuse/adapt/generate implementation via resolver + agent
    - call the resulting implementation with runtime_values
    - attach DataFlow to result for downstream inference
    """
    config = get_config()
    runtime_values = materialize_runtime_document_inputs(dict(runtime_values))

    portal_anchor = resolve_portal_anchor(source_file)
    session_id = session_id_from_filename(portal_anchor)
    module_name = session_module_name_from_filename(portal_anchor)

    portal = load_portal(cache_dir, session_id, portal_anchor, module_name)
    try:
        from semipy.diagnostics_export import clear_diagnostics

        clear_diagnostics(cache_dir, slot_spec.slot_id)
    except Exception:
        pass
    slot = _ensure_slot(portal, slot_spec)
    _record_slot_input_observations(slot, runtime_values)
    if _runtime_profile_is_scalar_only(runtime_values):
        _harvest_caller_series_samples(runtime_values, slot)
    save_portal(cache_dir, portal)

    dep_graph = _get_dep_graph(cache_dir)
    current_slot_ref = SlotRef(session_id=session_id, slot_id=slot_spec.slot_id)

    # Register observed cross-slot dependency edges.
    if dep_graph is not None:
        for val in runtime_values.values():
            flow = extract_flow(val)
            if flow is not None:
                add_dependency(dep_graph, upstream=flow.producing_slot, downstream=current_slot_ref)

    force_regenerate = False
    old_snap = slot.slot_spec if isinstance(slot.slot_spec, dict) else {}
    old_eq = equivalence_key_from_stored_snapshot(old_snap) if old_snap else None
    if old_eq is not None:
        spec_changed = old_eq != slot_spec.spec_equivalence_key
    else:
        spec_changed = bool(slot.spec_hash) and slot.spec_hash != slot_spec.spec_hash
    if spec_changed:
        force_regenerate = True
        if dep_graph is not None:
            mark_downstream_stale(dep_graph, current_slot_ref, "spec changed")
    if dep_graph is not None and is_stale(dep_graph, current_slot_ref):
        force_regenerate = True
    adv = getattr(slot, "advisor_state", None) or {}
    if isinstance(adv, dict) and adv.get("force_regenerate_next"):
        force_regenerate = True
        adv.pop("force_regenerate_next", None)
        slot.advisor_state = adv
        save_portal(cache_dir, portal)

    sketch_library = load_sketch_library(cache_dir)
    resolution = resolve(
        portal,
        slot_spec,
        force_regenerate=force_regenerate,
        sketch_library=sketch_library,
    )

    source_file_imports = _extract_source_file_imports(source_file)

    _verify_failure_msg: str | None = None
    _sketch_context: str | None = None

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
            resolution = resolve(
                portal,
                slot_spec,
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
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is None:
                _dispatch_globals_cache.pop(module_name, None)
                fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is None:
                fn = load_function_from_dispatch_by_slot_id(
                    cache_dir,
                    module_name,
                    dispatch_slot_id,
                    _dispatch_globals_cache,
                )
            if fn is None:
                force_regenerate = True
            else:
                call_site = _call_site_from_slot(slot_spec)
                current_fp = compute_runtime_input_fingerprint(runtime_values)
                do_verify = True
                stored_fp = getattr(commit, "runtime_input_fingerprint", "") or ""
                skip_verify = bool(stored_fp) and stored_fp == current_fp

                if do_verify and not skip_verify:
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
                        # Type check passed. Run semantic check if threshold
                        # crossed to catch implementations that are type-correct
                        # but semantically inadequate for new input patterns.
                        _increment_semantic_reuse_counter(slot, commit.commit_id)
                        if _should_semantic_check(slot, commit.commit_id, config):
                            from semipy.agents.decision import evaluate_reuse_semantics

                            session_obs = _slot_session_observations(slot)
                            impl_source = getattr(commit, "generated_source", "") or ""
                            if config.verbose:
                                print_pipeline_log(
                                    call_site,
                                    "semantic_check",
                                    "New input patterns detected; running batch test and evaluating...",
                                )
                            sem = evaluate_reuse_semantics(
                                slot_spec=slot_spec,
                                implementation_source=impl_source,
                                session_observations=session_obs,
                            )
                            _update_semantic_state(
                                slot, commit.commit_id, sem.decision,
                                portal=portal, cache_dir=cache_dir,
                            )
                            if sem.decision == "adapt":
                                _verify_failure_msg = (
                                    f"Semantic check: {sem.reasoning}"
                                )
                                if sem.problematic_inputs:
                                    examples = "; ".join(
                                        s[:120] for s in sem.problematic_inputs[:3]
                                    )
                                    _verify_failure_msg += (
                                        f" Problematic inputs: {examples}"
                                    )
                                if config.verbose:
                                    print_pipeline_log(
                                        call_site,
                                        "semantic_check",
                                        f"Implementation gaps detected; adapting. {sem.reasoning}",
                                    )
                                force_regenerate = True
                            elif config.verbose:
                                print_pipeline_log(
                                    call_site,
                                    "semantic_check",
                                    "Implementation covers observed input diversity; proceeding with reuse.",
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
                    else:
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
                        if resolution.reuse_dispatch_slot_id:
                            from semipy.resolver import list_equivalence_donors

                            donors = list_equivalence_donors(portal, slot_spec, slot_spec.slot_id)
                            summaries: list[str] = []
                            for s, c in donors[:12]:
                                ci = s.call_site_info or {}
                                summaries.append(
                                    f"commit_id={c.commit_id} slot_id={s.slot_id} "
                                    f"file={ci.get('filename', '')} line={ci.get('lineno', 0)}"
                                )
                        return result

        if force_regenerate:
            resolution = resolve(
                portal,
                slot_spec,
                force_regenerate=True,
                sketch_library=sketch_library,
            )

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
    entry = SemiAgent().generate(generation_spec)

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
    )
    add_commit_to_slot(slot, commit, branch_name, usage_id=slot_spec.slot_id)

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
        from semipy.agents.skeleton_writer import surface_skeleton as _surface_skeleton

        # Run synchronously so script termination cannot drop the surface write.
        _surface_skeleton(slot_spec, entry, portal.source_file)
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
    _dispatch_globals_cache.pop(module_name, None)

    fn_name = function_name_for_commit(slot, commit)
    dispatch_path = _dispatch_module_path(cache_dir, module_name)
    fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
    if fn is None:
        fn = load_function_from_dispatch_by_slot_id(cache_dir, module_name, slot.slot_id, _dispatch_globals_cache)
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
        result = execute_slot(
            slot_spec=slot_spec,
            runtime_values=kwargs,
            source_file=source_file,
            cache_dir=effective_cache_dir,
        )
        if slot_spec.expected_category == SlotCategory.STATEMENT_BLOCK:
            if len(slot_spec.output_names) == 1 and isinstance(result, dict):
                return result.get(slot_spec.output_names[0])
            return result
        return result

    return __slot_proxy__

