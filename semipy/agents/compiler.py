"""Compile generated Python source into a callable."""
from __future__ import annotations

import ast
from typing import Any, Callable


def _compile_source(source: str) -> Callable[..., Any]:
    """Compile generated source into a callable. Expects a single function def."""
    tree = ast.parse(source)
    primary_name: str | None = None
    if isinstance(tree, ast.Module):
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                primary_name = node.name
                break
    if primary_name is None:
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                primary_name = n.name
                break
    if primary_name is None:
        raise ValueError("Generated source did not define a function")
    ns: dict[str, Any] = {}
    exec(compile(source, "<generated>", "exec"), ns)
    fn = ns.get(primary_name)
    if fn is None or not callable(fn) or isinstance(fn, type):
        raise ValueError(f"Generated source did not define a callable named {primary_name!r}")
    return fn
