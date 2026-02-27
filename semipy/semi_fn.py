"""
Core runtime primitive: semi() and call-site resolution.

semi(prompt) resolves to a cached or newly generated function, then runs it with
runtime arguments. semi.name(...) does the same for named calls. Standalone
semi() (without @semiformal) uses a single implementation per call site.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from typing import Any, Optional

from semipy.agents.agent import SemiAgent
from semipy.agents.config import get_config
from semipy.agents.console_io import (
    print_dag_adapt,
    print_dag_compose,
    print_dag_generate,
    print_dag_reuse,
    print_pipeline_log,
    print_reactive_cascade,
    print_reactive_mismatch,
    print_reactive_stale,
    _call_site_file_url,
    _file_link_url,
    _relative_link_path,
)
from semipy.history import (
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.decorator import get_semiformal_context
from semipy.reactivity import (
    SlotRef,
    add_dependency,
    clear_stale,
    get_downstream_requirements,
    is_stale,
    mark_downstream_stale,
    save_dependency_graph,
    update_slot_commit,
    _get_dep_graph as _load_dep_graph,
    FLOW_ATTR,
    create_flow,
    extract_flow,
    profile_output,
)
from semipy.agents.validator import count_function_positional_params
from semipy.resolver import resolve
from semipy.library import load_library
from semipy.library.injection import build_library_context
from semipy.store import (
    function_name_for_commit,
    get_dispatch_function_line_range,
    load_function_from_dispatch,
    load_portal,
    save_portal,
    write_dispatch_module,
    _dispatch_module_path,
)
from semipy.template import structural_fingerprint, template_tree_from_prompt
from semipy.types import (
    Decision,
    GenerationSpec,
    NamedCallSiteInfo,
    PromptTemplate,
    SemiCallError,
    SemiCallSite,
    TemplatePart,
    Usage,
    session_id_from_filename,
    session_module_name_from_filename,
)

_portal_cache: dict[str, Any] = {}
_dispatch_globals_cache: dict[str, dict[str, Any]] = {}


def _call_generated_fn(
    fn: Any,
    call_site: SemiCallSite,
    generated_path: str,
    line_range: tuple[int, int],
    prompt_preview: str,
    *args: Any,
    usage_hint: str = "",
    **kwargs: Any,
) -> Any:
    """Invoke generated function; on exception raise SemiCallError with debugger-style summary.
    *args must come before usage_hint so that all_values_ordered is not interpreted as usage_hint.
    """
    kwargs_for_fn = {k: v for k, v in kwargs.items() if k != "usage_hint"}
    try:
        return fn(*args, **kwargs_for_fn)
    except Exception as e:
        raise SemiCallError(
            "Generated semi() function raised at runtime",
            call_site=call_site,
            generated_path=generated_path,
            line_range=line_range,
            prompt_preview=prompt_preview,
            usage_hint=usage_hint,
            cause=e,
        ) from e


def _normalize_filename(path: str) -> str:
    if not path or path == "<unknown>":
        return path
    try:
        return str(__import__("os").path.abspath(path))
    except Exception:
        return path


def _extract_source_file_imports(filename: str) -> list[str]:
    """Extract module-level import statements from the source file as source strings."""
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


def _get_entry_script_path() -> Optional[str]:
    """Path of the script the user ran (e.g. examples/use_csv_kit.py), resolved, or None."""
    if not sys.argv or not sys.argv[0] or sys.argv[0].startswith("-"):
        return None
    try:
        return str(Path(sys.argv[0]).resolve())
    except Exception:
        return None


def _get_entry_script_path_and_line(call_site: SemiCallSite) -> Optional[tuple[str, int]]:
    """(path, lineno) of the user script frame (caller of the package file), or None. Walks stack to find first frame outside semipy and outside call_site.filename."""
    call_filename = _normalize_filename(call_site.filename)
    try:
        semipy_dir = Path(__file__).resolve().parent
    except Exception:
        semipy_dir = None
    frame = inspect.currentframe()
    try:
        f = frame
        while f is not None:
            f = f.f_back
            if f is None:
                break
            fn = f.f_code.co_filename or ""
            if not fn or fn == "<unknown>":
                continue
            try:
                fnorm = _normalize_filename(fn)
                frame_path = Path(fn).resolve()
                if semipy_dir is not None and semipy_dir in frame_path.parents:
                    continue
                if fnorm == call_filename:
                    continue
            except Exception:
                continue
            return (str(frame_path), f.f_lineno or 0)
    finally:
        del frame
    path = _get_entry_script_path()
    return (path, 0) if path else None


def _identify_call_site(depth: int = 2) -> SemiCallSite:
    """Identify the call site by walking back 'depth' frames (inline=2, named=3)."""
    frame = inspect.currentframe()
    if frame is None:
        return SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
    try:
        f = frame
        for _ in range(depth):
            if f is None:
                return SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
            f = f.f_back
        if f is None:
            return SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
        filename = _normalize_filename(f.f_code.co_filename or "<unknown>")
        lineno = f.f_lineno or 0
        func_name = f.f_code.co_name or ""
        qualname = getattr(f.f_locals.get("self"), "__class__", None)
        if qualname is not None:
            qualname = f"{qualname.__name__}.{func_name}"
        else:
            qualname = func_name
        return SemiCallSite(filename=filename, lineno=lineno, func_qualname=qualname)
    finally:
        del frame


def _find_site_info(call_site: SemiCallSite, context: Optional[Any]) -> Optional[Any]:
    if context is None or not getattr(context, "semi_call_sites", None):
        return None
    call_filename = _normalize_filename(call_site.filename)
    for info in context.semi_call_sites:
        info_filename = _normalize_filename(info.call_site.filename)
        if info.call_site.lineno == call_site.lineno and info_filename == call_filename:
            return info
    return None


def _find_named_site_info(call_site: SemiCallSite, context: Optional[Any]) -> Optional[NamedCallSiteInfo]:
    if context is None or not getattr(context, "named_call_sites", None):
        return None
    call_filename = _normalize_filename(call_site.filename)
    for info in context.named_call_sites:
        info_filename = _normalize_filename(info.call_site.filename)
        if info.call_site.lineno == call_site.lineno and info_filename == call_filename:
            return info
    return None


def _eval_expressions(expressions: list[str], frame: Any) -> list[Any]:
    values = []
    g = frame.f_globals
    l = frame.f_locals
    for expr in expressions:
        try:
            values.append(eval(expr, g, l))
        except Exception:
            values.append(None)
    return values


def _readable_function_name(call_site: SemiCallSite) -> str:
    base = (call_site.func_qualname or "fn").replace(".", "_").replace(" ", "_")
    return "".join(c if c.isalnum() or c == "_" else "_" for c in base)


def _usage_from_parts(
    call_site: SemiCallSite,
    template: PromptTemplate,
    constant_values: dict[str, Any],
    expected_type: type = type(None),
) -> Usage:
    return Usage(
        call_site=call_site,
        template=template,
        constant_values=constant_values or {},
        expected_type=expected_type,
    )


def _get_portal(cache_dir: Path, session_id: str, source_file: str, module_name: str) -> Any:
    key = session_id
    if key not in _portal_cache:
        _portal_cache[key] = load_portal(cache_dir, session_id, source_file, module_name)
    return _portal_cache[key]


def _ensure_slot(portal: Any, call_site: SemiCallSite, function_name_base: str) -> Slot:
    slot_id = call_site.site_id
    if slot_id in portal.slots:
        return portal.slots[slot_id]
    slot = Slot(
        slot_id=slot_id,
        call_site_info={
            "filename": call_site.filename,
            "lineno": call_site.lineno,
            "func_qualname": call_site.func_qualname,
        },
        function_name_base=function_name_base,
    )
    portal.slots[slot_id] = slot
    return slot


def _get_dep_graph(cache_dir: Path) -> Any:
    """Cached dependency graph for cache_dir."""
    return _load_dep_graph(cache_dir)


def _register_upstream_deps(
    graph: Any,
    input_values: list[Any],
    current_slot_ref: SlotRef,
) -> None:
    """Extract flow from input values and add edges upstream -> current."""
    for val in input_values:
        flow = extract_flow(val)
        if flow is not None:
            add_dependency(graph, flow.producing_slot, current_slot_ref)


def _check_downstream_requirements(
    graph: Any,
    slot_ref: SlotRef,
    result: Any,
) -> Optional[dict[str, Any]]:
    """
    Compare result profile to downstream requirements. Return mismatch dict if any
    requirement is not satisfied (e.g. required_columns not in profile), else None.
    Data-agnostic: compares whatever keys are in downstream_requirements.
    """
    reqs = get_downstream_requirements(graph, slot_ref)
    if not reqs:
        return None
    profile = profile_output(result)
    mismatch: dict[str, Any] = {}
    required_cols = reqs.get("required_columns")
    if required_cols is not None:
        profile_cols = profile.get("columns") or profile.get("keys") or []
        norm = lambda s: s.strip().lower() if isinstance(s, str) else str(s).strip().lower()
        profile_set = {norm(c) for c in profile_cols}
        missing = [c for c in required_cols if norm(c) not in profile_set]
        if missing:
            mismatch["missing_columns"] = missing
    return mismatch if mismatch else None


def _named_name_to_value(
    variable_names: list[str],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Build name_to_value for named call: positionals in order, then c_kw_* from kwargs."""
    result: dict[str, Any] = {}
    pos_idx = 0
    for var_name in variable_names:
        if var_name.startswith("c_kw_"):
            kw = var_name[6:]  # len("c_kw_")
            result[var_name] = kwargs.get(kw)
        else:
            if pos_idx < len(args):
                result[var_name] = args[pos_idx]
                pos_idx += 1
    return result


def _build_named_prompt(method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Build a prompt string for named call from function name and arg descriptions."""
    lines = [
        f"Implement a Python function named {method_name!r}.",
        "The function name describes what it should do.",
        "",
        "Positional arguments:",
    ]
    for i, v in enumerate(args):
        lines.append(f"  [{i}] {type(v).__name__}: {repr(v)[:200]}")
    if kwargs:
        lines.append("Keyword arguments:")
        for k, v in sorted(kwargs.items()):
            lines.append(f"  {k}: {type(v).__name__} = {repr(v)[:200]}")
    return "\n".join(lines)


def _semi_inline(
    prompt: str,
    *,
    expected_type: Optional[type] = None,
    require_tools: bool = False,
    **kwargs: Any,
) -> Any:
    """
    Inline semi(f\"...\"): at runtime, either runs a cached generated function
    or triggers LLM generation, then returns the result. When expected_type is
    set (e.g. str, float), the generated function must return that type (value-style usage).
    """
    call_site = _identify_call_site(depth=3)  # user -> SemiProxy.__call__ -> _semi_inline -> _identify_call_site
    context = get_semiformal_context()
    site_info = _find_site_info(call_site, context)

    config = get_config()
    cache_dir = Path(config.cache_dir)
    session_id = session_id_from_filename(call_site.filename)
    module_name = session_module_name_from_filename(call_site.filename)

    if site_info is not None and site_info.template.variable_expressions:
        frame = inspect.currentframe()
        if frame is None:
            return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, expected_type=expected_type, require_tools=require_tools, **kwargs)
        # User frame is 2 steps up: _semi_inline -> SemiProxy.__call__ -> user (same as call_site depth from _identify_call_site).
        caller_frame = frame
        for _ in range(2):
            caller_frame = caller_frame.f_back if caller_frame else None
        if caller_frame is None:
            return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, expected_type=expected_type, require_tools=require_tools, **kwargs)
        caller_locals_snapshot = dict(caller_frame.f_locals)
        try:
            values = _eval_expressions(site_info.template.variable_expressions, caller_frame)
        except Exception:
            return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, expected_type=expected_type, require_tools=require_tools, **kwargs)
        name_to_value = dict(zip(site_info.template.variable_names, values))
        loop_names = set(site_info.loop_variant_names)
        constant_values = {n: name_to_value[n] for n in site_info.template.variable_names if n not in loop_names}
        all_values_ordered = [name_to_value[n] for n in site_info.template.variable_names]
        effective_type = expected_type if expected_type is not None else site_info.expected_type
        usage = _usage_from_parts(call_site, site_info.template, constant_values, effective_type)
        tree = template_tree_from_prompt(site_info.template)
        fingerprint = structural_fingerprint(tree)

        portal = _get_portal(cache_dir, session_id, call_site.filename, module_name)
        force_regenerate = False
        dep_graph = None
        current_slot_ref = SlotRef(session_id=session_id, slot_id=call_site.site_id)
        if getattr(config, "reactive", True):
            dep_graph = _get_dep_graph(cache_dir)
            _register_upstream_deps(graph=dep_graph, input_values=all_values_ordered, current_slot_ref=current_slot_ref)
            if is_stale(dep_graph, current_slot_ref):
                force_regenerate = True
                if config.verbose:
                    st = dep_graph.statuses.get(current_slot_ref.key())
                    print_reactive_stale(call_site, call_site.site_id, st.stale_reason if st else "unknown")
        library = None
        spec_for_compose = None
        if getattr(config, "abstraction_discovery", True):
            try:
                library = load_library(cache_dir)
                spec_for_compose = GenerationSpec(
                    prompt=prompt,
                    call_site=call_site,
                    template=site_info.template,
                    context=context,
                    expected_type=effective_type,
                    constant_values=constant_values,
                    variable_values={n: name_to_value[n] for n in site_info.template.variable_names},
                )
            except Exception:
                pass
        resolution = resolve(
            portal, usage, fingerprint, constant_values,
            force_regenerate=force_regenerate,
            library=library,
            spec_for_compose=spec_for_compose,
        )

        need_generate = False
        if resolution.decision == Decision.REUSE and resolution.slot is not None and resolution.commit_id is not None:
            commit = resolution.slot.commits.get(resolution.commit_id)
            if commit is not None:
                required_params = len(site_info.template.variable_names)
                actual_params = count_function_positional_params(commit.generated_source)
                if actual_params < required_params:
                    need_generate = True
                else:
                    if usage.usage_id() not in resolution.slot.refs:
                        resolution.slot.refs[usage.usage_id()] = resolution.commit_id
                        save_portal(cache_dir, portal)
                        write_dispatch_module(cache_dir, portal)
                        _dispatch_globals_cache.pop(module_name, None)
                    fn_name = function_name_for_commit(resolution.slot, commit)
                    fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
                    if fn is not None:
                        dispatch_path = _dispatch_module_path(cache_dir, module_name)
                        path_str = str(dispatch_path)
                        code_line_range = get_dispatch_function_line_range(dispatch_path, fn_name)
                        if config.verbose:
                            _entry = _get_entry_script_path_and_line(call_site)
                            print_dag_reuse(
                                call_site,
                                resolution.commit_id,
                                path_str,
                                _relative_link_path(call_site.filename, call_site.lineno),
                                _file_link_url(path_str),
                                code_line_range=code_line_range if code_line_range != (0, 0) else None,
                                entry_script_path=_entry[0] if _entry else None,
                                entry_script_lineno=_entry[1] if _entry else None,
                            )
                        _kwargs = {k: v for k, v in kwargs.items() if k != "usage_hint"}
                        result = _call_generated_fn(
                            fn, call_site, path_str, code_line_range, prompt,
                            *all_values_ordered,
                            usage_hint=getattr(site_info, "usage_hint", ""),
                            **_kwargs,
                        )
                        if getattr(config, "reactive", True) and dep_graph is not None:
                            upstream_refs = [
                                (flow.producing_slot.session_id, flow.producing_slot.slot_id)
                                for val in all_values_ordered
                                for flow in (extract_flow(val),)
                                if flow is not None
                            ]
                            try:
                                setattr(
                                    result,
                                    FLOW_ATTR,
                                    create_flow(
                                        session_id,
                                        call_site.site_id,
                                        resolution.commit_id or "",
                                        upstream_chain=[SlotRef(sid, s) for sid, s in upstream_refs],
                                        output_profile=profile_output(result),
                                    ),
                                )
                            except (TypeError, AttributeError):
                                pass
                            update_slot_commit(dep_graph, current_slot_ref, resolution.commit_id or "")
                            clear_stale(dep_graph, current_slot_ref)
                        return result
                    need_generate = True

        if resolution.decision in (Decision.ADAPT, Decision.COMPOSE, Decision.GENERATE) or need_generate:
            sample_input = _sample_from_values(
                site_info.template.variable_names,
                name_to_value,
                site_info.loop_variant_names,
            )
            upstream_lineage_tuples = None
            if getattr(config, "reactive", True) and dep_graph is not None:
                upstream_lineage_tuples = [
                    (flow.producing_slot.session_id, flow.producing_slot.slot_id)
                    for val in all_values_ordered
                    for flow in (extract_flow(val),)
                    if flow is not None
                ]
            user_source_code = None
            if call_site.filename and call_site.filename != "<unknown>":
                try:
                    user_source_code = Path(call_site.filename).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            enclosing_function_source = context.source_code if context else None
            library_context_str = None
            if library is not None:
                try:
                    library_context_str = build_library_context(library, GenerationSpec(
                        prompt=prompt,
                        call_site=call_site,
                        template=site_info.template,
                        context=context,
                        expected_type=effective_type,
                        constant_values=constant_values,
                        variable_values={n: name_to_value[n] for n in site_info.template.variable_names},
                    ), max_count=5)
                except Exception:
                    pass
            spec = GenerationSpec(
                prompt=prompt,
                call_site=call_site,
                template=site_info.template,
                context=context,
                expected_type=effective_type,
                sample_input=sample_input,
                constant_values=constant_values,
                variable_values={n: name_to_value[n] for n in site_info.template.variable_names},
                require_external_tools=require_tools,
                decision=resolution.decision,
                parent_sources=resolution.parent_sources,
                parent_commit_ids=resolution.parent_commit_ids,
                lineage_summary=resolution.lineage_summary,
                usage_hint=getattr(site_info, "usage_hint", ""),
                caller_locals=caller_locals_snapshot,
                source_file_imports=_extract_source_file_imports(call_site.filename),
                upstream_lineage=upstream_lineage_tuples,
                downstream_requirements=get_downstream_requirements(dep_graph, current_slot_ref) if (getattr(config, "reactive", True) and dep_graph is not None) else None,
                user_source_code=user_source_code,
                enclosing_function_source=enclosing_function_source,
                library_context=library_context_str,
            )
            entry = SemiAgent().generate(spec)
            constants_snapshot = freeze_constants(constant_values)
            function_name_base = _readable_function_name(call_site)
            slot = resolution.slot if resolution.slot is not None else _ensure_slot(portal, call_site, function_name_base)
            if resolution.decision == Decision.ADAPT and resolution.branch_name:
                branch_name = resolution.branch_name
                parent_ids = tuple(resolution.parent_commit_ids)
                decision_str = "ADAPT"
            elif resolution.decision == Decision.COMPOSE:
                branch_name = "main" if not slot.branches else f"b_{fingerprint[:8]}"
                parent_ids = ()
                decision_str = "COMPOSE"
            else:
                if not slot.branches:
                    branch_name = "main"
                else:
                    branch_name = f"b_{fingerprint[:8]}"
                parent_ids = ()
                decision_str = "GENERATE"
            commit = create_commit(
                parent_ids,
                entry.generated_source,
                fingerprint,
                constants_snapshot,
                prompt,
                decision_str,
                usage_id=usage.usage_id(),
            )
            add_commit_to_slot(slot, commit, branch_name, usage.usage_id())
            if getattr(config, "reactive", True) and dep_graph is not None:
                n_marked = mark_downstream_stale(dep_graph, current_slot_ref, "upstream implementation changed")
                save_dependency_graph(cache_dir, dep_graph)
                if config.verbose and n_marked > 0:
                    print_reactive_cascade(call_site.site_id, n_marked)
            save_portal(cache_dir, portal)
            _dispatch_path, fn_line_map = write_dispatch_module(cache_dir, portal)
            _dispatch_globals_cache.pop(module_name, None)
            fn_name = function_name_for_commit(slot, commit)
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is None:
                fn = entry.compiled_fn
            path_str = str(_dispatch_path)
            code_line_range = fn_line_map.get(fn_name, (0, 0))
            if config.verbose:
                _entry = _get_entry_script_path_and_line(call_site)
                _ep, _el = (_entry[0], _entry[1]) if _entry else (None, None)
                if resolution.decision == Decision.ADAPT and resolution.parent_commit_ids:
                    print_dag_adapt(
                        call_site,
                        commit.commit_id,
                        resolution.parent_commit_ids[0],
                        path_str,
                        _relative_link_path(call_site.filename, call_site.lineno),
                        _file_link_url(path_str),
                        code_line_range=code_line_range if code_line_range != (0, 0) else None,
                        entry_script_path=_ep,
                        entry_script_lineno=_el,
                    )
                elif resolution.decision == Decision.COMPOSE:
                    print_dag_compose(
                        call_site,
                        commit.commit_id,
                        path_str,
                        _relative_link_path(call_site.filename, call_site.lineno),
                        _file_link_url(path_str),
                        code_line_range=code_line_range if code_line_range != (0, 0) else None,
                        entry_script_path=_ep,
                        entry_script_lineno=_el,
                    )
                else:
                    print_dag_generate(
                        call_site,
                        commit.commit_id,
                        path_str,
                        _relative_link_path(call_site.filename, call_site.lineno),
                        _file_link_url(path_str),
                        code_line_range=code_line_range if code_line_range != (0, 0) else None,
                        entry_script_path=_ep,
                        entry_script_lineno=_el,
                    )
            _kwargs = {k: v for k, v in kwargs.items() if k != "usage_hint"}
            result = _call_generated_fn(
                fn, call_site, path_str, code_line_range, prompt,
                *all_values_ordered,
                usage_hint=getattr(site_info, "usage_hint", ""),
                **_kwargs,
            )
            if getattr(config, "reactive", True) and dep_graph is not None:
                mismatch = _check_downstream_requirements(dep_graph, current_slot_ref, result)
                if mismatch and config.verbose:
                    print_reactive_mismatch(call_site.site_id, get_downstream_requirements(dep_graph, current_slot_ref), profile_output(result))
                upstream_refs = [
                    (flow.producing_slot.session_id, flow.producing_slot.slot_id)
                    for val in all_values_ordered
                    for flow in (extract_flow(val),)
                    if flow is not None
                ]
                try:
                    setattr(
                        result,
                        FLOW_ATTR,
                        create_flow(
                            session_id,
                            call_site.site_id,
                            commit.commit_id,
                            upstream_chain=[SlotRef(sid, s) for sid, s in upstream_refs],
                            output_profile=profile_output(result),
                        ),
                    )
                except (TypeError, AttributeError):
                    pass
                update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
                clear_stale(dep_graph, current_slot_ref)
            return result

    return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, expected_type=expected_type, require_tools=require_tools, **kwargs)


def _semi_named(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Named semi.name(...): resolve or generate from method name and args/kwargs, then call."""
    call_site = _identify_call_site(depth=3)
    context = get_semiformal_context()
    site_info = _find_named_site_info(call_site, context)

    config = get_config()
    cache_dir = Path(config.cache_dir)
    session_id = session_id_from_filename(call_site.filename)
    module_name = session_module_name_from_filename(call_site.filename)

    if site_info is not None:
        # Build name_to_value in template.variable_names order: positionals first, then kwargs
        name_to_value = _named_name_to_value(site_info.template.variable_names, args, kwargs)
        loop_names = set(site_info.loop_variant_names)
        constant_values = {n: name_to_value[n] for n in site_info.template.variable_names if n in name_to_value and n not in loop_names}
        template = site_info.template
        expected_type = site_info.expected_type
        loop_variant_names = site_info.loop_variant_names
    else:
        # Standalone: all positionals loop-variant, all kwargs constants
        parts = [TemplatePart(is_literal=True, value=f"@named:{name}")]
        variable_names = []
        for i in range(len(args)):
            var_name = f"v{i}"
            variable_names.append(var_name)
            parts.append(TemplatePart(is_literal=False, value=var_name))
        for k in sorted(kwargs.keys()):
            var_name = f"c_kw_{k}"
            variable_names.append(var_name)
            parts.append(TemplatePart(is_literal=False, value=var_name))
        template = PromptTemplate(
            template_parts=parts,
            variable_names=variable_names,
            variable_expressions=[""] * len(variable_names),
        )
        name_to_value = {f"v{i}": args[i] for i in range(len(args))}
        for k, v in kwargs.items():
            name_to_value[f"c_kw_{k}"] = v
        constant_values = {f"c_kw_{k}": v for k, v in kwargs.items()}
        expected_type = type(None)
        loop_variant_names = [f"v{i}" for i in range(len(args))]

    usage = _usage_from_parts(call_site, template, constant_values, expected_type)
    tree = template_tree_from_prompt(template)
    fingerprint = structural_fingerprint(tree)
    portal = _get_portal(cache_dir, session_id, call_site.filename, module_name)
    force_regenerate = False
    dep_graph = None
    current_slot_ref = SlotRef(session_id=session_id, slot_id=call_site.site_id)
    if getattr(config, "reactive", True):
        dep_graph = _get_dep_graph(cache_dir)
        _register_upstream_deps(graph=dep_graph, input_values=list(args), current_slot_ref=current_slot_ref)
        if is_stale(dep_graph, current_slot_ref):
            force_regenerate = True
            if config.verbose:
                st = dep_graph.statuses.get(current_slot_ref.key())
                print_reactive_stale(call_site, call_site.site_id, st.stale_reason if st else "unknown")
    resolution = resolve(portal, usage, fingerprint, constant_values, force_regenerate=force_regenerate)

    need_generate = False
    if resolution.decision == Decision.REUSE and resolution.slot is not None and resolution.commit_id is not None:
        commit = resolution.slot.commits.get(resolution.commit_id)
        if commit is not None:
            if usage.usage_id() not in resolution.slot.refs:
                resolution.slot.refs[usage.usage_id()] = resolution.commit_id
                save_portal(cache_dir, portal)
                write_dispatch_module(cache_dir, portal)
                _dispatch_globals_cache.pop(module_name, None)
            fn_name = function_name_for_commit(resolution.slot, commit)
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is not None:
                dispatch_path = _dispatch_module_path(cache_dir, module_name)
                path_str = str(dispatch_path)
                code_line_range = get_dispatch_function_line_range(dispatch_path, fn_name)
                if config.verbose:
                    _entry = _get_entry_script_path_and_line(call_site)
                    print_dag_reuse(
                        call_site,
                        resolution.commit_id,
                        path_str,
                        _relative_link_path(call_site.filename, call_site.lineno),
                        _file_link_url(path_str),
                        code_line_range=code_line_range if code_line_range != (0, 0) else None,
                        entry_script_path=_entry[0] if _entry else None,
                        entry_script_lineno=_entry[1] if _entry else None,
                    )
                prompt_preview = f"semi.{name}(...)"
                _kwargs = {k: v for k, v in kwargs.items() if k != "usage_hint"}
                result = _call_generated_fn(
                    fn, call_site, path_str, code_line_range, prompt_preview,
                    *args,
                    usage_hint=getattr(site_info, "usage_hint", "") if site_info is not None else "",
                    **_kwargs,
                )
                if getattr(config, "reactive", True) and dep_graph is not None:
                    upstream_refs = [
                        (flow.producing_slot.session_id, flow.producing_slot.slot_id)
                        for val in args
                        for flow in (extract_flow(val),)
                        if flow is not None
                    ]
                    try:
                        setattr(
                            result,
                            FLOW_ATTR,
                            create_flow(
                                session_id,
                                call_site.site_id,
                                resolution.commit_id or "",
                                upstream_chain=[SlotRef(sid, s) for sid, s in upstream_refs],
                                output_profile=profile_output(result),
                            ),
                        )
                    except (TypeError, AttributeError):
                        pass
                    update_slot_commit(dep_graph, current_slot_ref, resolution.commit_id or "")
                    clear_stale(dep_graph, current_slot_ref)
                return result
            need_generate = True

    if resolution.decision in (Decision.ADAPT, Decision.GENERATE) or need_generate:
        frame = inspect.currentframe()
        caller_frame = frame
        for _ in range(3):
            if caller_frame is None:
                break
            caller_frame = caller_frame.f_back
        caller_locals_snapshot = dict(caller_frame.f_locals) if caller_frame else None
        prompt = _build_named_prompt(name, args, kwargs)
        sample_input = _sample_from_values(
            template.variable_names,
            name_to_value,
            loop_variant_names,
        )
        upstream_lineage_tuples = None
        if getattr(config, "reactive", True) and dep_graph is not None:
            upstream_lineage_tuples = [
                (flow.producing_slot.session_id, flow.producing_slot.slot_id)
                for val in args
                for flow in (extract_flow(val),)
                if flow is not None
            ]
        user_source_code = None
        if call_site.filename and call_site.filename != "<unknown>":
            try:
                user_source_code = Path(call_site.filename).read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        enclosing_function_source = context.source_code if context else None
        spec = GenerationSpec(
            prompt=prompt,
            call_site=call_site,
            template=template,
            context=context,
            expected_type=expected_type,
            sample_input=sample_input,
            constant_values=constant_values,
            variable_values=name_to_value,
            require_external_tools=False,
            decision=resolution.decision,
            parent_sources=resolution.parent_sources,
            parent_commit_ids=resolution.parent_commit_ids,
            lineage_summary=resolution.lineage_summary,
            method_name=name,
            usage_hint=getattr(site_info, "usage_hint", "") if site_info is not None else "",
            caller_locals=caller_locals_snapshot,
            source_file_imports=_extract_source_file_imports(call_site.filename),
            upstream_lineage=upstream_lineage_tuples,
            downstream_requirements=get_downstream_requirements(dep_graph, current_slot_ref) if (getattr(config, "reactive", True) and dep_graph is not None) else None,
            user_source_code=user_source_code,
            enclosing_function_source=enclosing_function_source,
        )
        entry = SemiAgent().generate(spec)
        constants_snapshot = freeze_constants(constant_values)
        function_name_base = _readable_function_name(call_site)
        slot = resolution.slot if resolution.slot is not None else _ensure_slot(portal, call_site, function_name_base)
        if resolution.decision == Decision.ADAPT and resolution.branch_name:
            branch_name = resolution.branch_name
            parent_ids = tuple(resolution.parent_commit_ids)
            decision_str = "ADAPT"
        else:
            branch_name = f"b_{fingerprint[:8]}" if resolution.slot and resolution.slot.branches else "main"
            parent_ids = ()
            decision_str = "GENERATE"
        commit = create_commit(
            parent_ids,
            entry.generated_source,
            fingerprint,
            constants_snapshot,
            prompt,
            decision_str,
            usage_id=usage.usage_id(),
        )
        add_commit_to_slot(slot, commit, branch_name, usage.usage_id())
        if getattr(config, "reactive", True) and dep_graph is not None:
            n_marked = mark_downstream_stale(dep_graph, current_slot_ref, "upstream implementation changed")
            save_dependency_graph(cache_dir, dep_graph)
            if config.verbose and n_marked > 0:
                print_reactive_cascade(call_site.site_id, n_marked)
        save_portal(cache_dir, portal)
        _dispatch_path, fn_line_map = write_dispatch_module(cache_dir, portal)
        _dispatch_globals_cache.pop(module_name, None)
        fn_name = function_name_for_commit(slot, commit)
        fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
        if fn is None:
            fn = entry.compiled_fn
        path_str = str(_dispatch_path)
        code_line_range = fn_line_map.get(fn_name, (0, 0))
        if config.verbose:
            _entry = _get_entry_script_path_and_line(call_site)
            _ep, _el = (_entry[0], _entry[1]) if _entry else (None, None)
            if resolution.decision == Decision.ADAPT and resolution.parent_commit_ids:
                print_dag_adapt(
                    call_site,
                    commit.commit_id,
                    resolution.parent_commit_ids[0],
                    path_str,
                    _relative_link_path(call_site.filename, call_site.lineno),
                    _file_link_url(path_str),
                    code_line_range=code_line_range if code_line_range != (0, 0) else None,
                    entry_script_path=_ep,
                    entry_script_lineno=_el,
                )
            else:
                print_dag_generate(
                    call_site,
                    commit.commit_id,
                    path_str,
                    _relative_link_path(call_site.filename, call_site.lineno),
                    _file_link_url(path_str),
                    code_line_range=code_line_range if code_line_range != (0, 0) else None,
                    entry_script_path=_ep,
                    entry_script_lineno=_el,
                )
        _kwargs = {k: v for k, v in kwargs.items() if k != "usage_hint"}
        result = _call_generated_fn(
            fn, call_site, path_str, code_line_range, prompt,
            *args,
            usage_hint=getattr(site_info, "usage_hint", "") if site_info is not None else "",
            **_kwargs,
        )
        if getattr(config, "reactive", True) and dep_graph is not None:
            upstream_refs = [
                (flow.producing_slot.session_id, flow.producing_slot.slot_id)
                for val in args
                for flow in (extract_flow(val),)
                if flow is not None
            ]
            try:
                setattr(
                    result,
                    FLOW_ATTR,
                    create_flow(
                        session_id,
                        call_site.site_id,
                        commit.commit_id,
                        upstream_chain=[SlotRef(sid, s) for sid, s in upstream_refs],
                        output_profile=profile_output(result),
                    ),
                )
            except (TypeError, AttributeError):
                pass
            update_slot_commit(dep_graph, current_slot_ref, commit.commit_id)
            clear_stale(dep_graph, current_slot_ref)
        return result


def _sample_from_values(
    variable_names: list[str],
    name_to_value: dict[str, Any],
    loop_variant_names: list[str],
) -> dict[str, Any]:
    args = tuple(name_to_value.get(n) for n in variable_names)
    return {"args": args, "kwargs": {}}


def _semi_fallback(
    prompt: str,
    call_site: SemiCallSite,
    cache_dir: Path,
    session_id: str,
    module_name: str,
    *,
    expected_type: Optional[type] = None,
    require_tools: bool = False,
    **kwargs: Any,
) -> Any:
    """Standalone semi() without @semiformal context: one implementation per call site, reused for every prompt. Use expected_type for value-style return (e.g. str, float)."""
    fallback_template = PromptTemplate(
        template_parts=[TemplatePart(is_literal=True, value="<fallback>")],
        variable_names=[],
        variable_expressions=[],
    )
    usage = _usage_from_parts(
        call_site,
        fallback_template,
        {},
        expected_type=expected_type if expected_type is not None else type(None),
    )
    tree = template_tree_from_prompt(fallback_template)
    fingerprint = structural_fingerprint(tree)
    portal = _get_portal(cache_dir, session_id, call_site.filename, module_name)
    resolution = resolve(portal, usage, fingerprint, {})

    if resolution.decision == Decision.REUSE and resolution.slot is not None and resolution.commit_id is not None:
        commit = resolution.slot.commits.get(resolution.commit_id)
        if commit is not None:
            if usage.usage_id() not in resolution.slot.refs:
                resolution.slot.refs[usage.usage_id()] = resolution.commit_id
                save_portal(cache_dir, portal)
                write_dispatch_module(cache_dir, portal)
                _dispatch_globals_cache.pop(module_name, None)
            fn_name = function_name_for_commit(resolution.slot, commit)
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is not None:
                config = get_config()
                dispatch_path = _dispatch_module_path(cache_dir, module_name)
                path_str = str(dispatch_path)
                code_line_range = get_dispatch_function_line_range(dispatch_path, fn_name)
                if config.verbose:
                    _entry = _get_entry_script_path_and_line(call_site)
                    print_dag_reuse(
                        call_site,
                        resolution.commit_id,
                        path_str,
                        _relative_link_path(call_site.filename, call_site.lineno),
                        _file_link_url(path_str),
                        code_line_range=code_line_range if code_line_range != (0, 0) else None,
                        entry_script_path=_entry[0] if _entry else None,
                        entry_script_lineno=_entry[1] if _entry else None,
                    )
                _kwargs = {k: v for k, v in kwargs.items() if k != "usage_hint"}
                return _call_generated_fn(
                    fn, call_site, path_str, code_line_range, prompt,
                    *(prompt,),
                    usage_hint="",
                    **_kwargs
                )

    spec = GenerationSpec(
        prompt=prompt,
        call_site=call_site,
        template=None,
        context=None,
        expected_type=expected_type if expected_type is not None else type(None),
        sample_input=None,
        constant_values=None,
        variable_values=None,
        require_external_tools=require_tools,
        decision=Decision.GENERATE,
    )
    entry = SemiAgent().generate(spec)
    function_name_base = _readable_function_name(call_site)
    slot = _ensure_slot(portal, call_site, function_name_base)
    constants_snapshot = freeze_constants({})
    commit = create_commit(
        (), entry.generated_source, fingerprint, constants_snapshot, prompt, "GENERATE", usage_id=usage.usage_id()
    )
    add_commit_to_slot(slot, commit, "main", usage.usage_id())
    save_portal(cache_dir, portal)
    _dispatch_path, fn_line_map = write_dispatch_module(cache_dir, portal)
    _dispatch_globals_cache.pop(module_name, None)
    fn_name = function_name_for_commit(slot, commit)
    fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
    if fn is None:
        fn = entry.compiled_fn
    path_str = str(_dispatch_path)
    code_line_range = fn_line_map.get(fn_name, (0, 0))
    config = get_config()
    if config.verbose:
        _entry = _get_entry_script_path_and_line(call_site)
        print_dag_generate(
            call_site,
            commit.commit_id,
            path_str,
            _relative_link_path(call_site.filename, call_site.lineno),
            _file_link_url(path_str),
            code_line_range=code_line_range if code_line_range != (0, 0) else None,
            entry_script_path=_entry[0] if _entry else None,
            entry_script_lineno=_entry[1] if _entry else None,
        )
    _kwargs = {k: v for k, v in kwargs.items() if k != "usage_hint"}
    return _call_generated_fn(fn, call_site, path_str, code_line_range, prompt, *(prompt,), usage_hint="", **_kwargs)


class _SemiMethod:
    """Callable proxy for semi.<name>; invokes _semi_named(name, args, kwargs)."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _semi_named(self._name, args, kwargs)


class SemiProxy:
    """
    Proxy for semi: semi(prompt) runs inline semiformal; semi.<name>(*args, **kwargs) runs named call.
    """

    def __call__(
        self,
        prompt: str,
        *,
        expected_type: Optional[type] = None,
        require_tools: bool = False,
        **kwargs: Any,
    ) -> Any:
        return _semi_inline(prompt, expected_type=expected_type, require_tools=require_tools, **kwargs)

    def __getattr__(self, name: str) -> _SemiMethod:
        if name.startswith("_"):
            raise AttributeError(name)
        return _SemiMethod(name)


semi = SemiProxy()