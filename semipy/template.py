"""F-string template extraction from AST for semi() call sites."""
from __future__ import annotations

import ast
import textwrap
from typing import Any, Optional

from semipy.types import PromptTemplate, SemiCallSite, SemiCallSiteInfo, TemplatePart


def _names_in_expr(node: ast.AST) -> set[str]:
    """Collect all name ids referenced in an expression (including attributes and subscripts)."""
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name) -> None:
            names.add(n.id)
            self.generic_visit(n)

        def visit_Attribute(self, n: ast.Attribute) -> None:
            if isinstance(n.value, ast.Name):
                names.add(n.value.id)
            self.generic_visit(n)

    Visitor().visit(node)
    return names


def _loop_target_names(tree: ast.AST, semi_lineno: int) -> set[str]:
    """Find names that are loop targets in a for-loop enclosing the given line."""
    result: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_For(self, node: ast.For) -> None:
            if node.end_lineno is not None and node.lineno <= semi_lineno <= node.end_lineno:
                for t in ast.walk(node.target):
                    if isinstance(t, ast.Name):
                        result.add(t.id)
            self.generic_visit(node)

    Visitor().visit(tree)
    return result


def _decompose_joined_str(
    source: str,
    node: ast.JoinedStr,
    loop_names: set[str],
) -> tuple[list[TemplatePart], list[str], list[str]]:
    """Decompose an f-string into parts, variable names, and expression sources (for runtime eval)."""
    parts: list[TemplatePart] = []
    variable_names: list[str] = []
    variable_expressions: list[str] = []

    for i, value in enumerate(node.values):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(TemplatePart(is_literal=True, value=value.value))
        elif isinstance(value, ast.FormattedValue):
            expr_src = ast.get_source_segment(source, value.value) or ""
            names = _names_in_expr(value.value)
            is_loop = bool(names & loop_names)
            name = f"v{i}" if is_loop else f"c{i}"
            variable_names.append(name)
            variable_expressions.append(expr_src)
            parts.append(TemplatePart(is_literal=False, value=name))
        else:
            parts.append(TemplatePart(is_literal=True, value=""))

    return parts, variable_names, variable_expressions


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Build a mapping from each node to its parent."""
    parents: dict[ast.AST, ast.AST] = {}

    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    return parents


def _infer_expected_type_from_usage(node: ast.Call, tree: ast.AST) -> type:
    """Infer expected return type from how the semi() result is used (parent node)."""
    parents = _parent_map(tree)
    p = parents.get(node)
    if p is None:
        return type(None)
    if isinstance(p, ast.If) and p.test is node:
        return bool
    if isinstance(p, ast.Assign) and p.value is node:
        return type(None)
    if isinstance(p, ast.Return):
        return type(None)
    if isinstance(p, (ast.ListComp, ast.DictComp, ast.GeneratorExp)):
        if getattr(p, "elt", None) is node or (hasattr(p, "generators") and any(g.iter is node for g in p.generators)):
            return bool
        if p.elt is node:
            return bool
    if isinstance(p, ast.Compare):
        return bool
    return type(None)


def extract_semi_templates(
    source: str,
    filename: str = "<unknown>",
    func_qualname: str = "",
    first_lineno: int = 1,
) -> list[SemiCallSiteInfo]:
    """
    Parse source and extract template info for every semi() call.
    first_lineno: line number in the file where the function source starts (for correct call-site matching).
    Returns a list of SemiCallSiteInfo, one per semi() call site.
    """
    dedented = textwrap.dedent(source)
    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        return []

    results: list[SemiCallSiteInfo] = []
    loop_names_by_line: dict[int, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "semi":
            if not node.args:
                continue
            arg0 = node.args[0]
            line_in_tree = node.lineno or 0
            lineno = line_in_tree + first_lineno - 1
            if line_in_tree not in loop_names_by_line:
                loop_names_by_line[line_in_tree] = _loop_target_names(tree, line_in_tree)
            loop_names = loop_names_by_line[line_in_tree]

            if isinstance(arg0, ast.JoinedStr):
                parts, variable_names, variable_expressions = _decompose_joined_str(
                    dedented, arg0, loop_names
                )
            else:
                expr_src = ast.get_source_segment(dedented, arg0) if hasattr(arg0, "lineno") else ""
                parts = [TemplatePart(is_literal=False, value="c0")]
                variable_names = ["c0"]
                variable_expressions = [expr_src or "None"]

            template = PromptTemplate(
                template_parts=parts,
                variable_names=variable_names,
                variable_expressions=variable_expressions,
            )
            call_site = SemiCallSite(
                filename=filename,
                lineno=lineno,
                func_qualname=func_qualname,
            )
            expected = _infer_expected_type_from_usage(node, tree)
            loop_variant_names = [n for n in variable_names if n.startswith("v")]
            if not loop_variant_names and variable_names:
                loop_variant_names = [variable_names[0]]
            results.append(
                SemiCallSiteInfo(
                    call_site=call_site,
                    template=template,
                    expected_type=expected,
                    loop_variant_names=loop_variant_names,
                )
            )

    return results


