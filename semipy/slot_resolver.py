from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable

from semipy.agents.agent import SemiAgent
from semipy.agents.config import get_config
from semipy.history.version_control import (
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.reactivity import (
    FLOW_ATTR,
    SlotRef,
    _get_dep_graph,
    add_dependency,
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
from semipy.store import (
    function_name_for_commit,
    load_function_from_dispatch_by_slot_id,
    load_function_from_dispatch,
    load_portal,
    save_portal,
    write_dispatch_module,
    _dispatch_module_path,
)
from semipy.types import (
    Decision,
    GenerationSpec,
    SemiCallError,
    SemiCallSite,
    SlotCategory,
    SlotSpec,
    session_id_from_filename,
    session_module_name_from_filename,
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
    )


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
    session_id = session_id_from_filename(source_file)
    module_name = session_module_name_from_filename(source_file)

    portal = load_portal(cache_dir, session_id, source_file, module_name)
    slot = _ensure_slot(portal, slot_spec)

    dep_graph = _get_dep_graph(cache_dir) if getattr(config, "reactive", True) else None
    current_slot_ref = SlotRef(session_id=session_id, slot_id=slot_spec.slot_id)

    # Register observed cross-slot dependency edges.
    if dep_graph is not None:
        for val in runtime_values.values():
            flow = extract_flow(val)
            if flow is not None:
                add_dependency(dep_graph, upstream=flow.producing_slot, downstream=current_slot_ref)

    force_regenerate = False
    spec_changed = slot.spec_hash and slot.spec_hash != slot_spec.spec_hash
    if spec_changed:
        force_regenerate = True
        if dep_graph is not None:
            mark_downstream_stale(dep_graph, current_slot_ref, "spec changed")
    if dep_graph is not None and is_stale(dep_graph, current_slot_ref):
        force_regenerate = True

    resolution = resolve(portal, slot_spec, force_regenerate=force_regenerate)

    source_file_imports = _extract_source_file_imports(source_file)

    if resolution.decision == Decision.REUSE and resolution.slot is not None and resolution.commit_id is not None:
        commit = resolution.slot.commits.get(resolution.commit_id)
        if commit is None:
            # If we lost the commit, re-enter generation by forcing regenerate.
            force_regenerate = True
        else:
            fn_name = function_name_for_commit(resolution.slot, commit)
            dispatch_path = _dispatch_module_path(cache_dir, module_name)
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is None:
                _dispatch_globals_cache.pop(module_name, None)
                fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is None:
                # Fall back to resolving by DISPATCH[slot_id] mapping.
                fn = load_function_from_dispatch_by_slot_id(
                    cache_dir,
                    module_name,
                    slot_spec.slot_id,
                    _dispatch_globals_cache,
                )
            if fn is None:
                # Cache inconsistency: dispatch module can't be executed (e.g. invalid function names).
                # Force regeneration so we rewrite dispatch with the current naming/sanitization.
                force_regenerate = True
            else:
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
                    try:
                        setattr(
                            result,
                            FLOW_ATTR,
                            create_flow(
                                session_id=session_id,
                                slot_id=slot_spec.slot_id,
                                commit_id=commit.commit_id,
                                upstream_chain=upstream_chain,
                                output_profile=profile_output(result),
                            ),
                        )
                    except (TypeError, AttributeError):
                        pass
                    update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
                    clear_stale(dep_graph, current_slot_ref)
                    save_dependency_graph(cache_dir, dep_graph)
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
        try:
            setattr(
                result,
                FLOW_ATTR,
                create_flow(
                    session_id=session_id,
                    slot_id=slot_spec.slot_id,
                    commit_id=commit.commit_id,
                    upstream_chain=upstream_chain,
                    output_profile=profile_output(result),
                ),
            )
        except (TypeError, AttributeError):
            pass

    return result


def _make_slot_proxy(slot_spec: SlotSpec, source_file: str, cache_dir: Path) -> Callable[..., Any]:
    """
    Build a callable used by the scaffold:
    - for STATEMENT_BLOCK: return scalar when output_names has 1 element; otherwise return dict
    - for EXPRESSION/FUNCTION_BODY: return scalar
    """

    def __slot_proxy__(**kwargs: Any) -> Any:
        result = execute_slot(
            slot_spec=slot_spec,
            runtime_values=kwargs,
            source_file=source_file,
            cache_dir=cache_dir,
        )
        if slot_spec.expected_category == SlotCategory.STATEMENT_BLOCK:
            if len(slot_spec.output_names) == 1 and isinstance(result, dict):
                return result.get(slot_spec.output_names[0])
            return result
        return result

    return __slot_proxy__

