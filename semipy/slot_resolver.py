from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable

from semipy.agents.agent import SemiAgent
from semipy.agents.config import get_config
from semipy.agents.console_io import print_pipeline_log
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


def build_generation_spec(
    *,
    slot_spec: SlotSpec,
    portal: Any,
    resolution: Any,
    runtime_values: dict[str, Any],
    dep_graph: Any | None,
    current_slot_ref: SlotRef,
    source_file_imports: list[str],
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
        session_input_observations=session_obs,
        runtime_profile_scalar_only=_runtime_profile_is_scalar_only(runtime_values),
    )


def _runtime_sample_input(slot_spec: SlotSpec, runtime_values: dict[str, Any]) -> dict[str, Any]:
    if runtime_values:
        args = [runtime_values.get(n) for n in slot_spec.free_variables]
        return {"args": tuple(args), "kwargs": {}, "runtime_values": dict(runtime_values)}
    return {"args": tuple(), "kwargs": {}, "runtime_values": {}}


def _call_generated_fn(
    *,
    fn: Callable[..., Any],
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    prompt_preview: str,
    generated_path: str,
) -> Any:
    args = tuple(runtime_values.get(n) for n in slot_spec.free_variables)
    try:
        return fn(*args)
    except Exception as e:
        raise SemiCallError(
            "Generated slot function raised at runtime",
            call_site=_call_site_from_slot(slot_spec),
            generated_path=generated_path,
            line_range=(0, 0),
            prompt_preview=prompt_preview,
            usage_hint="",
            cause=e,
        ) from e


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
    slot = _ensure_slot(portal, slot_spec)
    _record_slot_input_observations(slot, runtime_values)
    if not slot.commits and _runtime_profile_is_scalar_only(runtime_values):
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

    resolution = resolve(portal, slot_spec, force_regenerate=force_regenerate)

    source_file_imports = _extract_source_file_imports(source_file)

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
                # Cache inconsistency: dispatch module can't be executed (e.g. invalid function names).
                # Force regeneration so we rewrite dispatch with the current naming/sanitization.
                force_regenerate = True
            else:
                call_site = _call_site_from_slot(slot_spec)
                current_fp = compute_runtime_input_fingerprint(runtime_values)
                do_verify = True
                stored_fp = getattr(commit, "runtime_input_fingerprint", "") or ""
                skip_verify = bool(stored_fp) and stored_fp == current_fp

                if do_verify and not skip_verify:
                    sample_in = _runtime_sample_input(slot_spec, runtime_values)
                    vr = verify_runtime_execution(
                        fn=fn,
                        expected_type=slot_spec.expected_type,
                        sample_input=sample_in,
                        slot_category=slot_spec.expected_category,
                        output_names=list(slot_spec.output_names or []),
                        enable_execution=True,
                    )
                    if not vr.passed:
                        if config.verbose:
                            err = (vr.error_message or "").strip().replace("\n", " ")
                            if len(err) > 160:
                                err = err[:157] + "..."
                            print_pipeline_log(
                                call_site,
                                "reuse_verify",
                                f"Runtime check failed; adapting. {err}",
                            )
                        force_regenerate = True

                if not force_regenerate:
                    result = _call_generated_fn(
                        fn=fn,
                        slot_spec=slot_spec,
                        runtime_values=runtime_values,
                        prompt_preview=slot_spec.spec_text,
                        generated_path=str(dispatch_path),
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
            resolution = resolve(portal, slot_spec, force_regenerate=True)

    # ADAPT / GENERATE
    generation_spec = build_generation_spec(
        slot_spec=slot_spec,
        portal=portal,
        resolution=resolution,
        runtime_values=runtime_values,
        dep_graph=dep_graph,
        current_slot_ref=current_slot_ref,
        source_file_imports=source_file_imports,
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

    write_dispatch_module(cache_dir, portal)
    save_portal(cache_dir, portal)
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

