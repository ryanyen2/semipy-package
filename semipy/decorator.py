"""
@semiformal decorator: source analysis and context injection.

Wraps functions (or methods on classes) to set SemiformalContext for the duration
of the call. Extracts semi() and semi.name() call sites from source via AST.
"""
from __future__ import annotations

import functools
import inspect
from contextvars import ContextVar
from typing import Any, Callable, Optional, Union

from semipy.template import extract_named_call_templates, extract_semi_templates
from semipy.types import SemiformalContext, SemiCallSiteInfo

_semiformal_context_var: ContextVar[Optional[SemiformalContext]] = ContextVar(
    "semiformal_context", default=None
)


def get_semiformal_context() -> Optional[SemiformalContext]:
    """Return the current SemiformalContext if we are inside a @semiformal-decorated call."""
    return _semiformal_context_var.get()


def _wrap_function(
    fn: Callable[..., Any],
    description: Optional[str] = None,
    filename: Optional[str] = None,
) -> Callable[..., Any]:
    try:
        source = inspect.getsource(fn)
        try:
            _, first_lineno = inspect.getsourcelines(fn)
            first_lineno = first_lineno or 1
        except (OSError, TypeError):
            first_lineno = 1
    except OSError:
        source = ""
        first_lineno = 1
    if filename is None:
        try:
            raw = inspect.getsourcefile(fn) or "<unknown>"
            if raw and raw != "<unknown>":
                import os
                filename = os.path.abspath(raw)
            else:
                filename = raw
        except (OSError, TypeError):
            filename = "<unknown>"
    func_qualname = getattr(fn, "__qualname__", fn.__name__)
    semi_sites = extract_semi_templates(
        source, filename=filename, func_qualname=func_qualname, first_lineno=first_lineno
    )
    named_sites = extract_named_call_templates(
        source, filename=filename, func_qualname=func_qualname, first_lineno=first_lineno
    )
    type_hints = {}
    try:
        for name, ann in (inspect.getannotations(fn) or {}).items():
            if name != "return":
                type_hints[name] = ann
    except Exception:
        pass

    context = SemiformalContext(
        func_name=func_qualname,
        source_code=source,
        type_hints=type_hints,
        semi_call_sites=semi_sites,
        named_call_sites=named_sites,
    )

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _semiformal_context_var.set(context)
        try:
            return fn(*args, **kwargs)
        finally:
            _semiformal_context_var.reset(token)

    return wrapper


def _methods_with_semi(cls: type) -> list[str]:
    """Return names of methods that contain semi() or semi.<name>() in their source."""
    import ast as _ast
    result: list[str] = []
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return result
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return result
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef):
            for n in _ast.walk(node):
                if not isinstance(n, _ast.Call):
                    continue
                func = n.func
                if isinstance(func, _ast.Name) and func.id == "semi":
                    result.append(node.name)
                    break
                if isinstance(func, _ast.Attribute) and isinstance(func.value, _ast.Name) and func.value.id == "semi":
                    result.append(node.name)
                    break
    return result


def semiformal(
    fn_or_desc: Optional[Union[Callable[..., Any], str]] = None,
    *,
    description: Optional[str] = None,
) -> Any:
    """
    Decorate a function or class as semiformal.
    Usage:
      @semiformal
      def f(): ...
      @semiformal("description")
      def g(): ...
      @semiformal
      class C: ...
    """
    if fn_or_desc is None and description is None:
        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            return _wrap_function(f, filename=None)
        return decorator

    if isinstance(fn_or_desc, str):
        desc = fn_or_desc
        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            return _wrap_function(f, description=desc, filename=None)
        return decorator

    if callable(fn_or_desc):
        f = fn_or_desc
        if isinstance(f, type):
            method_names = _methods_with_semi(f)
            for name in method_names:
                if name in f.__dict__:
                    orig = f.__dict__[name]
                    if callable(orig):
                        setattr(f, name, _wrap_function(orig, filename=None))
            return f
        return _wrap_function(f, description=description, filename=None)

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        return _wrap_function(f, description=description, filename=None)
    return decorator
