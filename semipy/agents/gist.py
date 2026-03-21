"""
AST-based gist builder: assemble a minimal standalone executable from user code
and a generated function for sandboxed validation.

Reuses patterns from agents/refs/example_glm.py and validator._extract_enclosing_statement.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from semipy.types import GenerationSpec


def _expr_for_gist_invocation(value: Any) -> str:
    """
    Build a Python expression string for embedding in generated gist source.
    repr() of arbitrary objects (e.g. class instances) is not valid Python when pasted
    as a call argument; use literals for primitives and None for everything else.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, bytes):
        return repr(value)
    if isinstance(value, tuple):
        inner = ", ".join(_expr_for_gist_invocation(x) for x in value)
        if len(value) == 1:
            return f"({inner},)"
        return f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(_expr_for_gist_invocation(x) for x in value) + "]"
    if isinstance(value, dict):
        parts = [f"{_expr_for_gist_invocation(k)}: {_expr_for_gist_invocation(v)}" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    return "None"


def _get_names_used(node: ast.AST) -> set[str]:
    """Collect all name ids read from an AST node (for dependency tracking)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Load):
            names.add(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            names.add(n.value.id)
    return names


def _future_imports_first(imports: list[str]) -> list[str]:
    """Put any __future__ import lines first so they appear at the top of the gist."""
    future = [s for s in imports if "__future__" in s]
    rest = [s for s in imports if "__future__" not in s]
    return future + rest


def _get_code_snippet(source: str, node: ast.AST) -> str:
    """Return the source slice for a node if we have full source."""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def _extract_enclosing_statement(
    source_code: str,
    semi_call_lineno: int,
    first_lineno: int,
) -> Optional[str]:
    """Return the source of the top-level statement that contains the semi() call line, or None."""
    if not source_code.strip() or semi_call_lineno < first_lineno:
        return None
    rel = semi_call_lineno - first_lineno + 1
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None
    if not tree.body or not isinstance(tree.body[0], ast.FunctionDef):
        return None
    func = tree.body[0]
    for stmt in func.body:
        start = stmt.lineno
        end = getattr(stmt, "end_lineno", stmt.lineno)
        if start <= rel <= end:
            seg = ast.get_source_segment(source_code, stmt)
            return seg.strip() if seg else None
    return None


@dataclass
class Gist:
    """Assembled minimal executable: source code, function name, test invocation snippet."""

    source: str
    fn_name: str
    test_invocation: str
    upstream_deps: list[str] = field(default_factory=list)
    mocked_externals: list[str] = field(default_factory=list)


class GistBuilder:
    """
    Builds a runnable gist for sandbox validation: only the generated function,
    any imports present in the generated snippet, and a test invocation with
    sample data. Does not include user-file imports (e.g. semipy) or upstream
    context, so the gist runs in a minimal environment (e.g. E2B) without
    requiring the semiformal package.
    """

    def __init__(self, spec: GenerationSpec) -> None:
        self.spec = spec
        self.last_build_error: Optional[str] = None

    def build(self, generated_function_source: str) -> Optional[Gist]:
        """
        Assemble a minimal standalone script to test the generated function with
        real data flow: only imports from the generated snippet, the generated
        function, and test invocation (sample_input / variable_values from spec).
        Returns None if the generated source cannot be parsed or has no function.
        """
        self.last_build_error = None
        raw = _extract_function_source(generated_function_source)
        if not raw.strip():
            self.last_build_error = "empty generated source"
            return None

        import_lines, fn_source = _extract_imports_and_function_from_generated(raw)
        if not fn_source.strip():
            self.last_build_error = "no function definition in generated source"
            return None

        try:
            fn_tree = ast.parse(fn_source)
        except SyntaxError:
            self.last_build_error = "syntax error in generated function"
            return None
        funcs = [n for n in ast.walk(fn_tree) if isinstance(n, ast.FunctionDef)]
        if not funcs:
            self.last_build_error = "no function definition after parse"
            return None
        gen_fn_name = funcs[0].name

        test_invocation = _build_test_invocation(self.spec, gen_fn_name)
        try:
            ns: dict[str, Any] = {}
            exec(compile(fn_source, "<gist_sig>", "exec"), ns)
            compiled = [v for v in ns.values() if callable(v) and not isinstance(v, type)]
            if compiled:
                fn = compiled[0]
                sample = self.spec.sample_input or {}
                args = tuple(sample.get("args", ()) or ())
                kwargs = dict(sample.get("kwargs", {}) or {})
                inspect.signature(fn).bind(*args, **kwargs)
        except TypeError as e:
            self.last_build_error = (
                "Generated function signature does not accept the slot's sample call "
                f"(positional arity must match slot inputs). Detail: {e}"
            )
            return None
        except Exception as e:
            self.last_build_error = f"preflight compile/bind failed: {e}"
            return None

        lines: list[str] = []
        if import_lines:
            lines.extend(import_lines)
            lines.append("")
        lines.append(fn_source)
        lines.append("")
        lines.append(test_invocation)

        return Gist(
            source="\n".join(lines),
            fn_name=gen_fn_name,
            test_invocation=test_invocation,
            upstream_deps=[],
            mocked_externals=[],
        )


def _extract_function_source(raw: str) -> str:
    """Extract Python code from markdown code block if present."""
    raw = raw.strip()
    if "```python" in raw:
        start = raw.index("```python") + len("```python")
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()
    if "```" in raw:
        start = raw.index("```") + 3
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()
    return raw


def _strip_future_imports(source: str) -> str:
    """Remove any line that is a __future__ import (so it is not duplicated in gist)."""
    out: list[str] = []
    for line in source.splitlines():
        s = line.strip()
        if s.startswith("from __future__"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _normalize_generated_function_source(fn_source: str) -> str:
    """
    Use only the first top-level function definition from the generated source.
    The full function is preserved (signature, return type annotation, body). We only
    drop leading module-level imports and __future__ lines so the gist never has
    __future__ or duplicate imports in the middle (which cause SyntaxError).
    """
    fn_source = _strip_future_imports(fn_source)
    if not fn_source.strip():
        return fn_source
    try:
        tree = ast.parse(fn_source)
    except SyntaxError:
        return fn_source
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            seg = _get_code_snippet(fn_source, node)
            if seg and seg.strip():
                return seg.strip()
            break
    return fn_source


def _extract_imports_and_function_from_generated(raw_source: str) -> tuple[list[str], str]:
    """
    From raw generated snippet, return (leading_import_lines, function_source).
    Leading imports are Import/ImportFrom before the first FunctionDef; the rest
    is the first function definition. Used so the gist only includes imports
    that the generated code needs (no user-file imports like semipy).
    """
    if not raw_source.strip():
        return [], ""
    try:
        tree = ast.parse(raw_source)
    except SyntaxError:
        return [], ""
    import_lines: list[str] = []
    function_source = ""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            seg = _get_code_snippet(raw_source, node)
            if seg:
                import_lines.append(seg)
        elif isinstance(node, ast.FunctionDef):
            seg = _get_code_snippet(raw_source, node)
            if seg and seg.strip():
                function_source = seg.strip()
            break
    return _future_imports_first(import_lines), function_source


def _collect_upstream_snippets(
    file_source: str,
    func_source: str,
    semi_lineno: int,
    first_lineno: int,
    names_used: set[str],
) -> list[str]:
    """
    Collect source snippets that define the names used in the enclosing statement.
    Only module-level assignments are included (imports are handled separately in build()).
    Function-body statements are not pasted into the gist (they are invalid at module level).
    """
    snippets: list[str] = []
    try:
        file_tree = ast.parse(file_source)
    except SyntaxError:
        return []

    defined_in_module: set[str] = set()
    for node in ast.iter_child_nodes(file_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in (node.names if hasattr(node, "names") else []):
                name = getattr(alias, "asname", None) or getattr(alias, "name", None)
                if name:
                    defined_in_module.add(name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined_in_module.add(t.id)
            seg = _get_code_snippet(file_source, node)
            if seg and any(
                isinstance(t, ast.Name) and t.id in names_used
                for t in node.targets
            ):
                snippets.append(seg)

    return snippets


def _build_test_invocation(spec: GenerationSpec, fn_name: str) -> str:
    """Build the test invocation line(s) from spec.sample_input or spec.variable_values."""
    marker = "__GIST_RESULT__"
    sample = spec.sample_input
    if sample and isinstance(sample, dict):
        args = sample.get("args", ())
        kwargs = sample.get("kwargs", {})
        if args or kwargs:
            args_str = ", ".join(_expr_for_gist_invocation(a) for a in args)
            if kwargs:
                args_str += ", " + ", ".join(f"{k}={_expr_for_gist_invocation(v)}" for k, v in kwargs.items())
            return f"{marker} = {fn_name}({args_str})\nprint({repr(marker)}, repr({marker}), flush=True)"
    variable_values = getattr(spec, "variable_values", None) or {}
    if variable_values:
        ordered = getattr(spec.template, "variable_names", []) if spec.template else []
        if ordered:
            vals = [variable_values.get(n, None) for n in ordered]
            args_str = ", ".join(_expr_for_gist_invocation(v) for v in vals)
            return f"{marker} = {fn_name}({args_str})\nprint({repr(marker)}, repr({marker}), flush=True)"
    return f"{marker} = {fn_name}()\nprint({repr(marker)}, repr({marker}), flush=True)"
