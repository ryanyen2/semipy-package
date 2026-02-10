"""Core runtime primitive: semi() function and call-site resolution."""
from __future__ import annotations

import hashlib
import inspect
from typing import Any, Optional

from semipy.agent import SemiAgent
from semipy.cache import SemiCache, build_template_hash
from semipy.config import get_config
from semipy.decorator import get_semiformal_context
from semipy.types import GenerationSpec, SemiCallSite


def _normalize_filename(path: str) -> str:
    """Normalize to absolute path for consistent matching."""
    if not path or path == "<unknown>":
        return path
    try:
        return str(__import__("os").path.abspath(path))
    except Exception:
        return path


def _identify_call_site() -> SemiCallSite:
    """Determine filename, line, and function qualname of the semi() caller."""
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
    """Find SemiCallSiteInfo for this call site from context."""
    if context is None or not getattr(context, "semi_call_sites", None):
        return None
    call_filename = _normalize_filename(call_site.filename)
    for info in context.semi_call_sites:
        info_filename = _normalize_filename(info.call_site.filename)
        if info.call_site.lineno == call_site.lineno and info_filename == call_filename:
            return info
    return None


def _eval_expressions(expressions: list[str], frame: Any) -> list[Any]:
    """Evaluate expression sources in the given frame."""
    values = []
    g = frame.f_globals
    l = frame.f_locals
    for expr in expressions:
        try:
            values.append(eval(expr, g, l))
        except Exception:
            values.append(None)
    return values


def semi(prompt: str, **kwargs: Any) -> Any:
    """
    Semiformal expression: at runtime, either runs a cached generated function
    or triggers LLM generation, then returns the result. Type and control-flow
    context constrain what the generated function may return.
    """
    call_site = _identify_call_site()
    context = get_semiformal_context()
    site_info = _find_site_info(call_site, context)

    config = get_config()
    cache = SemiCache(config.cache_dir)
    agent = SemiAgent(cache=cache)

    if site_info is not None and site_info.template.variable_expressions:
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None:
            return _semi_fallback(prompt, call_site, agent, cache, **kwargs)
        caller_frame = frame.f_back
        try:
            values = _eval_expressions(site_info.template.variable_expressions, caller_frame)
        except Exception:
            return _semi_fallback(prompt, call_site, agent, cache, **kwargs)
        name_to_value = dict(zip(site_info.template.variable_names, values))
        loop_names = set(site_info.loop_variant_names)
        constant_values = {n: name_to_value[n] for n in site_info.template.variable_names if n not in loop_names}
        all_values_ordered = [name_to_value[n] for n in site_info.template.variable_names]
        template_hash = build_template_hash(site_info.template.template_parts, constant_values)
        entry = cache.get(call_site.site_id, template_hash)
        if entry and entry.compiled_fn:
            return entry.compiled_fn(*all_values_ordered, **kwargs)
        sample_input = _sample_from_values(site_info.template.variable_names, name_to_value, site_info.loop_variant_names)
        spec = GenerationSpec(
            prompt=prompt,
            call_site=call_site,
            template=site_info.template,
            context=context,
            expected_type=site_info.expected_type,
            sample_input=sample_input,
            constant_values=constant_values,
            variable_values={n: name_to_value[n] for n in site_info.template.variable_names},
        )
        entry = agent.generate(spec)
        return entry.compiled_fn(*all_values_ordered, **kwargs)

    return _semi_fallback(prompt, call_site, agent, cache, **kwargs)


def _sample_from_values(
    variable_names: list[str],
    name_to_value: dict[str, Any],
    loop_variant_names: list[str],
) -> dict[str, Any]:
    """Build sample_input for validator: args = all variable values in order (same as runtime call)."""
    args = tuple(name_to_value.get(n) for n in variable_names)
    return {"args": args, "kwargs": {}}


def _semi_fallback(
    prompt: str,
    call_site: SemiCallSite,
    agent: SemiAgent,
    cache: SemiCache,
    **kwargs: Any,
) -> Any:
    """Standalone semi() without @semiformal context: use prompt hash as cache key."""
    template_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    entry = cache.get(call_site.site_id, template_hash)
    if entry and entry.compiled_fn:
        return entry.compiled_fn(prompt, **kwargs)
    spec = GenerationSpec(
        prompt=prompt,
        call_site=call_site,
        template=None,
        context=None,
        expected_type=type(None),
        sample_input=None,
        constant_values=None,
        variable_values=None,
    )
    entry = agent.generate(spec)
    return entry.compiled_fn(prompt, **kwargs)
