"""
@semiformal decorator: scan + lower open regions at decoration time.
"""
from __future__ import annotations

import functools
import inspect
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Optional, Union, get_type_hints

from semipy.lowering import _make_slot_id, lower_to_scaffold, scan_informal_specs, strip_skeleton_lines
from semipy.slot_resolver import _make_slot_proxy
from semipy.types import SemiformalContext, SlotCategory, SlotSpec, compute_spec_equivalence_key
from semipy.agents.config import get_config


def _type_hints_for_lowering(fn: Callable[..., Any]) -> dict[str, Any]:
    """
    Resolve postponed annotations (``from __future__ import annotations``) so lowering
    sees real types for ``SlotSpec.expected_type``, not strings like ``'SponsorshipIR'``.
    Without this, validation uses ``TypeAdapter('SponsorshipIR')`` and pydantic cannot
    build a complete schema (class-not-fully-defined).
    """
    try:
        return get_type_hints(fn, globalns=fn.__globals__, localns={})
    except Exception:
        raw = getattr(fn, "__annotations__", None) or {}
        return dict(raw) if raw else {}

_semiformal_context_var: ContextVar[Optional[SemiformalContext]] = ContextVar(
    "semiformal_context", default=None
)


def get_semiformal_context() -> Optional[SemiformalContext]:
    """Return the current SemiformalContext if we are inside a @semiformal-decorated call."""
    return _semiformal_context_var.get()


def _compile_scaffold(
    scaffold_src: str,
    module_globals: dict[str, Any],
    proxy_ns: dict[str, Callable[..., Any]],
) -> Callable[..., Any]:
    local_ns: dict[str, Any] = {}
    exec(compile(scaffold_src, "<semiformal_scaffold>", "exec"), {**module_globals, **proxy_ns}, local_ns)
    # scaffold_src should define a single function; return the first callable in locals.
    for v in local_ns.values():
        if callable(v) and not isinstance(v, type):
            return v
    raise RuntimeError("Scaffold compilation did not produce a callable")


def _find_cache_dir(filename: str) -> Path:
    config = get_config()
    # Cache directory is global for the repo; session identity is carried by store.py.
    return Path(config.cache_dir)


def _decorated_fn_source(
    fn: Callable[..., Any],
) -> tuple[str, str, int, str, dict[str, Any]]:
    source = ""
    first_lineno = 1
    filename = "<unknown>"
    try:
        source = inspect.getsource(fn)
    except OSError:
        source = ""
    try:
        _lines, first_lineno = inspect.getsourcelines(fn)
        first_lineno = first_lineno or 1
    except Exception:
        first_lineno = 1
    try:
        raw = inspect.getsourcefile(fn) or "<unknown>"
        filename = str(Path(raw).resolve()) if raw != "<unknown>" else "<unknown>"
    except Exception:
        filename = "<unknown>"

    func_qualname = getattr(fn, "__qualname__", fn.__name__)

    type_hints: dict[str, Any] = {}
    try:
        for name, ann in (inspect.getannotations(fn) or {}).items():
            if name != "return":
                type_hints[name] = ann
    except Exception:
        pass
    return source, filename, first_lineno, func_qualname, type_hints


def _wrap_function(fn: Callable[..., Any], description: Optional[str] = None, filename: Optional[str] = None) -> Callable[..., Any]:
    source, resolved_filename, first_lineno, func_qualname, type_hints = _decorated_fn_source(fn)
    resolved_filename = filename or resolved_filename
    resolved_for_slots = _type_hints_for_lowering(fn)
    # Important: cache_dir may be configured after module import via `semipy.configure(...)`.
    # Slot proxies therefore read the effective cache_dir dynamically at call time.
    cache_dir: Optional[Path] = None

    source = strip_skeleton_lines(source)

    slot_specs: list[SlotSpec] = scan_informal_specs(
        source,
        filename=resolved_filename,
        func_qualname=func_qualname,
        first_lineno=first_lineno,
        type_hints=resolved_for_slots or type_hints,
        globals_ns=getattr(fn, "__globals__", None) or {},
    )
    scaffold_src = lower_to_scaffold(
        source, slot_specs, slot_index_offset=0, dedent_anchor_abs=first_lineno
    )

    ctx = SemiformalContext(
        func_name=func_qualname,
        source_code=source,
        type_hints=resolved_for_slots or type_hints,
        first_lineno=first_lineno,
        slot_specs=slot_specs,
        scaffold_source=scaffold_src,
        defining_globals=dict(fn.__globals__),
    )

    try:
        proxy_ns = {
            f"__slot_{i}__": _make_slot_proxy(spec, resolved_filename, cache_dir)
            for i, spec in enumerate(slot_specs)
        }
        scaffold_fn = _compile_scaffold(scaffold_src, fn.__globals__, proxy_ns)
    except Exception as _scaffold_exc:
        from semipy.agents.console_io import get_console
        get_console().print(f"[dim][semipy] warning: scaffolding failed for {func_qualname!r}, falling back to whole-function slot: {_scaffold_exc}[/]")
        # Structural error in scaffolding: fall back to executing the whole function as one slot.
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())
        expected_type = _type_hints_for_lowering(fn).get("return", type(None))
        spec_text = source
        spec_hash = __import__("hashlib").sha256(spec_text.encode()).hexdigest()[:16]
        slot_id = _make_slot_id(resolved_filename, func_qualname, 0, spec_text)
        spec_equivalence_key = compute_spec_equivalence_key(
            spec_text,
            param_names,
            expected_type,
            expected_category=SlotCategory.FUNCTION_BODY,
            output_names=[],
        )

        _end_line = first_lineno + len(source.splitlines()) - 1
        whole_slot = SlotSpec(
            slot_id=slot_id,
            source_span=(resolved_filename, first_lineno, _end_line),
            spec_text=spec_text,
            spec_hash=spec_hash,
            spec_equivalence_key=spec_equivalence_key,
            free_variables=param_names,
            control_context="method" if "." in func_qualname else "top_level",
            expected_category=SlotCategory.FUNCTION_BODY,
            expected_type=expected_type,
            output_names=[],
            formal_constraints=[],
            usage_hints=[],
            enclosing_function_source=source,
            enclosing_function_qualname=func_qualname,
            enclosing_function_span=(resolved_filename, first_lineno, _end_line),
        )

        proxy_fn = _make_slot_proxy(whole_slot, resolved_filename, cache_dir)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            token = _semiformal_context_var.set(ctx)
            try:
                bound = sig.bind_partial(*args, **kwargs)
                # Ensure defaults don't disappear from the runtime_values dict.
                bound.apply_defaults()
                runtime_values = {k: v for k, v in bound.arguments.items()}
                return proxy_fn(**runtime_values)
            finally:
                _semiformal_context_var.reset(token)

        wrapper._semipy_context = ctx  # type: ignore[attr-defined]
        return wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _semiformal_context_var.set(ctx)
        try:
            return scaffold_fn(*args, **kwargs)
        finally:
            _semiformal_context_var.reset(token)

    wrapper._semipy_context = ctx  # type: ignore[attr-defined]
    return wrapper


def _methods_with_open_regions(cls: type) -> list[str]:
    import ast

    method_names: list[str] = []
    try:
        for name, member in cls.__dict__.items():
            if not callable(member):
                continue
            try:
                src = inspect.getsource(member)
            except (OSError, TypeError):
                continue
            if "#>" in src or "semi(" in src:
                method_names.append(name)
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "semi":
                    method_names.append(name)
                    break
    except Exception:
        return []
    return method_names


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
        if isinstance(fn_or_desc, type):
            class_ = fn_or_desc
            for name in _methods_with_open_regions(class_):
                if name in class_.__dict__ and callable(class_.__dict__[name]):
                    orig = class_.__dict__[name]
                    setattr(class_, name, _wrap_function(orig, filename=None))
            return class_
        return _wrap_function(fn_or_desc, description=description, filename=None)

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        return _wrap_function(f, description=description, filename=None)

    return decorator

