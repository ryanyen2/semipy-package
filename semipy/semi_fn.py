"""Core runtime primitive: semi() function and call-site resolution."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Optional

from semipy.agent import SemiAgent
from semipy.config import get_config
from semipy.console_io import (
    print_dag_advance,
    print_dag_generate,
    print_dag_reuse,
    _call_site_file_url,
    _file_link_url,
    _format_location,
)
from semipy.dag import (
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.decorator import get_semiformal_context
from semipy.resolver import resolve
from semipy.store import (
    function_name_for_commit,
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
    PromptTemplate,
    SemiCallSite,
    TemplatePart,
    Usage,
    session_id_from_filename,
    session_module_name_from_filename,
)

_portal_cache: dict[str, Any] = {}
_dispatch_globals_cache: dict[str, dict[str, Any]] = {}


def _normalize_filename(path: str) -> str:
    if not path or path == "<unknown>":
        return path
    try:
        return str(__import__("os").path.abspath(path))
    except Exception:
        return path


def _identify_call_site() -> SemiCallSite:
    frame = inspect.currentframe()
    if frame is None:
        return SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
    try:
        f = frame.f_back
        if f is not None:
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


def _usage_from_parts(call_site: SemiCallSite, template: PromptTemplate, constant_values: dict[str, Any]) -> Usage:
    return Usage(call_site=call_site, template=template, constant_values=constant_values or {})


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


def semi(prompt: str, require_tools: bool = False, **kwargs: Any) -> Any:
    """
    Semiformal expression: at runtime, either runs a cached generated function
    or triggers LLM generation, then returns the result.
    """
    call_site = _identify_call_site()
    context = get_semiformal_context()
    site_info = _find_site_info(call_site, context)

    config = get_config()
    cache_dir = Path(config.cache_dir)
    session_id = session_id_from_filename(call_site.filename)
    module_name = session_module_name_from_filename(call_site.filename)

    if site_info is not None and site_info.template.variable_expressions:
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None:
            return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, require_tools=require_tools, **kwargs)
        caller_frame = frame.f_back
        try:
            values = _eval_expressions(site_info.template.variable_expressions, caller_frame)
        except Exception:
            return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, require_tools=require_tools, **kwargs)
        name_to_value = dict(zip(site_info.template.variable_names, values))
        loop_names = set(site_info.loop_variant_names)
        constant_values = {n: name_to_value[n] for n in site_info.template.variable_names if n not in loop_names}
        all_values_ordered = [name_to_value[n] for n in site_info.template.variable_names]
        usage = _usage_from_parts(call_site, site_info.template, constant_values)
        tree = template_tree_from_prompt(site_info.template)
        fingerprint = structural_fingerprint(tree)

        portal = _get_portal(cache_dir, session_id, call_site.filename, module_name)
        resolution = resolve(portal, usage, fingerprint, constant_values)

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
                    if config.verbose:
                        loc = _format_location(call_site.filename, call_site.lineno, call_site.func_qualname or "")
                        path = str(_dispatch_module_path(cache_dir, module_name))
                        print_dag_reuse(
                            resolution.slot.function_name_base,
                            None,
                            resolution.commit_id,
                            loc,
                            path,
                            loc_link=_call_site_file_url(call_site.filename, call_site.lineno),
                            path_link=_file_link_url(path),
                        )
                    return fn(*all_values_ordered, **kwargs)
                need_generate = True

        if resolution.decision in (Decision.ADVANCE, Decision.GENERATE) or need_generate:
            sample_input = _sample_from_values(
                site_info.template.variable_names,
                name_to_value,
                site_info.loop_variant_names,
            )
            spec = GenerationSpec(
                prompt=prompt,
                call_site=call_site,
                template=site_info.template,
                context=context,
                expected_type=site_info.expected_type,
                sample_input=sample_input,
                constant_values=constant_values,
                variable_values={n: name_to_value[n] for n in site_info.template.variable_names},
                require_external_tools=require_tools,
                decision=resolution.decision,
                parent_sources=resolution.parent_sources,
                parent_commit_ids=resolution.parent_commit_ids,
                lineage_summary=resolution.lineage_summary,
            )
            entry = SemiAgent().generate(spec)
            constants_snapshot = freeze_constants(constant_values)
            function_name_base = _readable_function_name(call_site)
            slot = resolution.slot if resolution.slot is not None else _ensure_slot(portal, call_site, function_name_base)
            if resolution.decision == Decision.ADVANCE and resolution.branch_name:
                branch_name = resolution.branch_name
                parent_ids = tuple(resolution.parent_commit_ids)
                decision_str = "ADVANCE"
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
            )
            add_commit_to_slot(slot, commit, branch_name, usage.usage_id())
            save_portal(cache_dir, portal)
            write_dispatch_module(cache_dir, portal)
            _dispatch_globals_cache.pop(module_name, None)
            fn_name = function_name_for_commit(slot, commit)
            fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
            if fn is None:
                fn = entry.compiled_fn
            if config.verbose:
                loc = _format_location(call_site.filename, call_site.lineno, call_site.func_qualname or "")
                path = str(_dispatch_module_path(cache_dir, module_name))
                if resolution.decision == Decision.ADVANCE and resolution.parent_commit_ids:
                    print_dag_advance(
                        slot.function_name_base,
                        branch_name,
                        commit.commit_id,
                        resolution.parent_commit_ids[0],
                        loc,
                        path,
                        loc_link=_call_site_file_url(call_site.filename, call_site.lineno),
                        path_link=_file_link_url(path),
                    )
                else:
                    print_dag_generate(
                        slot.function_name_base,
                        branch_name,
                        commit.commit_id,
                        loc,
                        path,
                        loc_link=_call_site_file_url(call_site.filename, call_site.lineno),
                        path_link=_file_link_url(path),
                    )
            return fn(*all_values_ordered, **kwargs)

    return _semi_fallback(prompt, call_site, cache_dir, session_id, module_name, require_tools=require_tools, **kwargs)


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
    require_tools: bool = False,
    **kwargs: Any,
) -> Any:
    """Standalone semi() without @semiformal context: one implementation per call site, reused for every prompt."""
    fallback_template = PromptTemplate(
        template_parts=[TemplatePart(is_literal=True, value="<fallback>")],
        variable_names=[],
        variable_expressions=[],
    )
    usage = _usage_from_parts(call_site, fallback_template, {})
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
                if config.verbose:
                    loc = _format_location(call_site.filename, call_site.lineno, call_site.func_qualname or "")
                    path = str(_dispatch_module_path(cache_dir, module_name))
                    print_dag_reuse(
                        resolution.slot.function_name_base,
                        None,
                        resolution.commit_id,
                        loc,
                        path,
                        loc_link=_call_site_file_url(call_site.filename, call_site.lineno),
                        path_link=_file_link_url(path),
                    )
                return fn(prompt, **kwargs)

    spec = GenerationSpec(
        prompt=prompt,
        call_site=call_site,
        template=None,
        context=None,
        expected_type=type(None),
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
    commit = create_commit((), entry.generated_source, fingerprint, constants_snapshot, prompt, "GENERATE")
    add_commit_to_slot(slot, commit, "main", usage.usage_id())
    save_portal(cache_dir, portal)
    write_dispatch_module(cache_dir, portal)
    _dispatch_globals_cache.pop(module_name, None)
    fn_name = function_name_for_commit(slot, commit)
    fn = load_function_from_dispatch(cache_dir, module_name, fn_name, _dispatch_globals_cache)
    if fn is None:
        fn = entry.compiled_fn
    config = get_config()
    if config.verbose:
        loc = _format_location(call_site.filename, call_site.lineno, call_site.func_qualname or "")
        path = str(_dispatch_module_path(cache_dir, module_name))
        print_dag_generate(
            slot.function_name_base,
            "main",
            commit.commit_id,
            loc,
            path,
            loc_link=_call_site_file_url(call_site.filename, call_site.lineno),
            path_link=_file_link_url(path),
        )
    return fn(prompt, **kwargs)
