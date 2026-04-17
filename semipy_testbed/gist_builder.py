"""
Simplified gist builder: assemble minimal executable from generated function.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Optional, get_args, get_origin
from dataclasses import dataclass, field


@dataclass
class Gist:
    """Minimal standalone executable."""

    source: str
    fn_name: str
    test_invocation: str
    user_source_path: Optional[str] = None


def _extract_imports_and_function(source_code: str) -> tuple[list[str], str]:
    """
    Extract import statements and function definition from generated source.
    Returns (import_lines, function_source).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return [], source_code

    imports: list[str] = []
    function_lines: list[int] = []
    function_defs: list[str] = []
    in_function = False
    last_import_idx = -1

    for idx, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))
            last_import_idx = idx

    # Get all lines from the function definitions
    if imports:
        # Skip import nodes, get everything after last import
        for idx in range(last_import_idx + 1, len(tree.body)):
            node = tree.body[idx]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_defs.append(ast.unparse(node))

    if function_defs:
        fn_src = "\n".join(function_defs)
    else:
        # Fallback: just use everything after imports
        fn_src = "\n".join(source_code.split("\n")[len(imports) :]).strip()

    return imports, fn_src


def _expr_for_gist_invocation(value: Any) -> str:
    """Build safe Python expression for embedding in gist."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, tuple):
        inner = ", ".join(_expr_for_gist_invocation(x) for x in value)
        if len(value) == 1:
            return f"({inner},)"
        return f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(_expr_for_gist_invocation(x) for x in value) + "]"
    if isinstance(value, dict):
        items = [
            f"{_expr_for_gist_invocation(k)}: {_expr_for_gist_invocation(v)}"
            for k, v in value.items()
        ]
        return "{" + ", ".join(items) + "}"
    # For complex objects, return None
    return "None"


def _build_test_invocation(
    fn_name: str,
    sample_input: Optional[dict[str, Any]] = None,
    free_variables: Optional[list[str]] = None,
) -> str:
    """Build test invocation line(s) from sample input."""
    marker = "__GIST_RESULT__"

    if not sample_input:
        return f"{marker} = {fn_name}()\nprint('{marker}', repr({marker}), flush=True)"

    args = tuple(sample_input.get("args", ()) or ())
    kwargs = dict(sample_input.get("kwargs", {}) or {})

    # Build argument string
    if not kwargs:
        if free_variables and len(free_variables) == len(args):
            # Keyword binding
            parts = [
                f"{name}={_expr_for_gist_invocation(arg)}"
                for name, arg in zip(free_variables, args)
            ]
            args_str = ", ".join(parts)
        else:
            # Positional
            args_str = ", ".join(_expr_for_gist_invocation(arg) for arg in args)
    else:
        # Mix of args and kwargs
        args_str = ", ".join(_expr_for_gist_invocation(arg) for arg in args)
        if kwargs:
            kwarg_str = ", ".join(
                f"{k}={_expr_for_gist_invocation(v)}" for k, v in kwargs.items()
            )
            args_str = f"{args_str}, {kwarg_str}" if args_str else kwarg_str

    return f"{marker} = {fn_name}({args_str})\nprint('{marker}', repr({marker}), flush=True)"


class SimpleGistBuilder:
    """Build minimal gist from generated function source and sample input."""

    def __init__(
        self,
        sample_input: Optional[dict[str, Any]] = None,
        free_variables: Optional[list[str]] = None,
        user_source_path: Optional[str] = None,
    ):
        self.sample_input = sample_input
        self.free_variables = free_variables or []
        self.user_source_path = user_source_path
        self.last_build_error: Optional[str] = None

    def build(self, generated_source: str) -> Optional[Gist]:
        """Assemble gist; return None if fails."""
        self.last_build_error = None

        # Extract imports and function
        imports, fn_src = _extract_imports_and_function(generated_source)

        if not fn_src.strip():
            self.last_build_error = "No function definition in generated source"
            return None

        # Parse to find function name
        try:
            tree = ast.parse(fn_src)
        except SyntaxError as e:
            self.last_build_error = f"Syntax error in function: {e}"
            return None

        fn_name: Optional[str] = None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_name = node.name
                break

        if not fn_name:
            self.last_build_error = "Could not find function name"
            return None

        # Build test invocation
        test_invocation = _build_test_invocation(
            fn_name,
            self.sample_input,
            self.free_variables,
        )

        # Assemble gist: imports + function + test invocation
        lines: list[str] = []
        if imports:
            lines.extend(imports)
            lines.append("")
        lines.append(fn_src)
        lines.append("")
        lines.append(test_invocation)

        gist_source = "\n".join(lines)

        return Gist(
            source=gist_source,
            fn_name=fn_name,
            test_invocation=test_invocation,
            user_source_path=self.user_source_path,
        )
