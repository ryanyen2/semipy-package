"""Compilation helper for generated semi() source (session cache handles persistence)."""
from __future__ import annotations

from typing import Any, Callable


def _compile_source(source: str) -> Callable[..., Any]:
    """Compile generated source into a callable. Expects a single function def."""
    ns: dict[str, Any] = {}
    exec(compile(source, "<generated>", "exec"), ns)
    fns = [v for v in ns.values() if callable(v) and not isinstance(v, type)]
    if not fns:
        raise ValueError("Generated source did not define a callable")
    return fns[0]
