"""
Pydantic TypeAdapter helpers with correct annotation namespaces.

TypeAdapter (pydantic 2.12) builds dataclass schemas using a NamespacesTuple. The public
``rebuild(_types_namespace=...)`` still sets ``globals`` from the caller frame (often
``semipy.agents.validator``), while ``locals`` get the defining module. Some resolution
paths then fail with ``class-not-fully-defined``. Using the defining module dict for *both*
globals and locals matches BaseModel behavior (see pydantic ``get_module_ns_of``) and works
for types in ``__main__`` and regular modules.
"""
from __future__ import annotations

import inspect
import sys
from typing import Any, get_args

_type_adapter_cache: dict[tuple[Any, ...], Any] = {}


def _namespace_for_type_evaluation(expected_type: Any) -> dict[str, Any] | None:
    """
    Namespace where *expected_type*'s annotations (and nested dataclass refs) are defined.

    Prefer ``sys.modules[typ.__module__].__dict__`` when it actually contains *typ*.
    Otherwise walk the stack (exec / embedded contexts where __module__ is __main__ but
    the live globals live on a frame).
    """
    def _module_dict_if_contains(typ: type) -> dict[str, Any] | None:
        mod_name = getattr(typ, "__module__", None)
        if not isinstance(mod_name, str):
            return None
        mod = sys.modules.get(mod_name)
        if mod is None:
            return None
        d = vars(mod)
        name = getattr(typ, "__name__", None)
        if isinstance(name, str) and d.get(name) is typ:
            return d
        return None

    if isinstance(expected_type, type):
        hit = _module_dict_if_contains(expected_type)
        if hit is not None:
            return hit
    else:
        for arg in get_args(expected_type) or ():
            if isinstance(arg, type):
                hit = _module_dict_if_contains(arg)
                if hit is not None:
                    return hit

    for fr in inspect.stack():
        g = fr.frame.f_globals
        if isinstance(expected_type, type):
            n = getattr(expected_type, "__name__", None)
            if isinstance(n, str) and g.get(n) is expected_type:
                return g
        for arg in get_args(expected_type):
            if isinstance(arg, type):
                an = getattr(arg, "__name__", None)
                if isinstance(an, str) and g.get(an) is arg:
                    return g

    return None


def type_adapter_for(
    expected_type: Any,
    *,
    globals_namespace: dict[str, Any] | None = None,
) -> Any:
    """
    Return a ``pydantic.TypeAdapter`` for *expected_type* with schema built using the
    defining module's namespace (robust for ``__main__`` and cross-module validation).

    Pass *globals_namespace* when types were defined in an ``exec`` dict or other namespace
    that is not discoverable via ``sys.modules`` or the current stack (semipy does not need
    this for normal ``python script.py`` or imported modules).
    """
    from pydantic import TypeAdapter  # noqa: PLC0415
    from pydantic._internal import _namespace_utils  # noqa: PLC0415

    ns = globals_namespace if globals_namespace is not None else _namespace_for_type_evaluation(expected_type)
    cache_key: tuple[Any, ...]
    if globals_namespace is not None:
        cache_key = (id(expected_type), id(globals_namespace))
    else:
        cache_key = (id(expected_type),)

    cached = _type_adapter_cache.get(cache_key)
    if cached is not None:
        return cached

    ta = TypeAdapter(expected_type)
    if ns is not None:
        ns_resolver = _namespace_utils.NsResolver(
            namespaces_tuple=_namespace_utils.NamespacesTuple(globals=ns, locals=ns),
            parent_namespace=ns,
        )
        try:
            ta._init_core_attrs(ns_resolver=ns_resolver, force=True, raise_errors=True)
        except Exception:
            ta.rebuild(force=True, _types_namespace=ns, _parent_namespace_depth=0)
    _type_adapter_cache[cache_key] = ta
    return ta


def clear_type_adapter_cache() -> None:
    """Clear cached adapters (e.g. after module reload in tests)."""
    _type_adapter_cache.clear()
