"""
AST-based gist builder: assemble a minimal standalone executable from user code
and a generated function for sandboxed validation.

Reuses patterns from agents/refs/example_glm.py and validator._extract_enclosing_statement.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from semipy.types import GenerationSpec


def _get_names_used(node: ast.AST) -> set[str]:
    """Collect all name ids read from an AST node (for dependency tracking)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Load):
            names.add(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            names.add(n.value.id)
    return names


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
    Builds a runnable gist from a GenerationSpec: user source + generated function
    + test invocation. Falls back to None when user source is unavailable or AST trace fails.
    """

    def __init__(self, spec: GenerationSpec) -> None:
        self.spec = spec

    def build(self, generated_function_source: str) -> Optional[Gist]:
        """
        Assemble a minimal standalone script: imports, upstream definitions (from
        use-def trace), generated function, and test invocation. Returns None if
        user source or context is missing or AST analysis fails.
        """
        user_source = getattr(self.spec, "user_source_code", None) or ""
        enclosing_source = getattr(self.spec, "enclosing_function_source", None)
        context = self.spec.context
        call_site = self.spec.call_site
        first_lineno = getattr(context, "first_lineno", 1) if context else 1

        if not user_source.strip():
            return None
        if not enclosing_source and not context:
            return None

        try:
            file_tree = ast.parse(user_source)
        except SyntaxError:
            return None

        imports: list[str] = []
        for node in ast.iter_child_nodes(file_tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                seg = _get_code_snippet(user_source, node)
                if seg:
                    imports.append(seg)

        fn_source = _extract_function_source(generated_function_source)
        if not fn_source.strip():
            return None
        try:
            fn_tree = ast.parse(fn_source)
        except SyntaxError:
            return None
        funcs = [n for n in ast.walk(fn_tree) if isinstance(n, ast.FunctionDef)]
        if not funcs:
            return None
        gen_fn_name = funcs[0].name

        statement_source = _extract_enclosing_statement(
            enclosing_source or "",
            call_site.lineno,
            first_lineno,
        )
        if not statement_source:
            upstream_deps: list[str] = []
        else:
            try:
                stmt_node = ast.parse(statement_source)
                names_used = set()
                for n in ast.walk(stmt_node):
                    if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Load):
                        names_used.add(n.id)
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                        names_used.add(n.value.id)
            except SyntaxError:
                names_used = set()

            upstream_deps = _collect_upstream_snippets(
                user_source,
                enclosing_source or "",
                call_site.lineno,
                first_lineno,
                names_used,
            )

        test_invocation = _build_test_invocation(self.spec, gen_fn_name)
        lines = []
        if imports:
            lines.extend(imports)
            lines.append("")
        for snip in upstream_deps:
            if snip.strip():
                lines.append(snip)
                lines.append("")
        lines.append(fn_source)
        lines.append("")
        lines.append(test_invocation)

        return Gist(
            source="\n".join(lines),
            fn_name=gen_fn_name,
            test_invocation=test_invocation,
            upstream_deps=upstream_deps,
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


def _collect_upstream_snippets(
    file_source: str,
    func_source: str,
    semi_lineno: int,
    first_lineno: int,
    names_used: set[str],
) -> list[str]:
    """
    Collect source snippets that define the names used in the enclosing statement.
    Returns a list of code snippets in dependency order (module-level imports/assignments
    first, then function-level statements before the semi() line).
    """
    snippets: list[str] = []
    try:
        file_tree = ast.parse(file_source)
        func_tree = ast.parse(func_source) if func_source.strip() else None
    except SyntaxError:
        return []

    defined_in_module: set[str] = set()
    for node in ast.iter_child_nodes(file_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            seg = _get_code_snippet(file_source, node)
            if seg:
                snippets.append(seg)
            for alias in (node.names if hasattr(node, "names") else []):
                name = getattr(alias, "asname", None) or getattr(alias, "name", None)
                if name:
                    defined_in_module.add(name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined_in_module.add(t.id)
            seg = _get_code_snippet(file_source, node)
            if seg and any(
                isinstance(t, ast.Name) and t.id in names_used
                for t in node.targets
            ):
                snippets.append(seg)

    if not func_tree or not func_tree.body or not isinstance(func_tree.body[0], ast.FunctionDef):
        return snippets

    func = func_tree.body[0]
    rel_line = semi_lineno - first_lineno + 1
    for stmt in func.body:
        end = getattr(stmt, "end_lineno", stmt.lineno)
        if end and end >= rel_line:
            break
        seg = _get_code_snippet(func_source, stmt)
        if seg and seg.strip():
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
            args_str = ", ".join(repr(a) for a in args)
            if kwargs:
                args_str += ", " + ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
            return f"{marker} = {fn_name}({args_str})\nprint({repr(marker)}, repr({marker}), flush=True)"
    variable_values = getattr(spec, "variable_values", None) or {}
    if variable_values:
        ordered = getattr(spec.template, "variable_names", []) if spec.template else []
        if ordered:
            vals = [variable_values.get(n, None) for n in ordered]
            args_str = ", ".join(repr(v) for v in vals)
            return f"{marker} = {fn_name}({args_str})\nprint({repr(marker)}, repr({marker}), flush=True)"
    return f"{marker} = {fn_name}()\nprint({repr(marker)}, repr({marker}), flush=True)"
