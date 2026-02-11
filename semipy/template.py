"""F-string template extraction from AST for semi() call sites."""
from __future__ import annotations

import ast
import hashlib
import json
import textwrap
from typing import Any, Optional

from semipy.types import (
    NamedCallSiteInfo,
    PromptTemplate,
    SemiCallSite,
    SemiCallSiteInfo,
    TemplatePart,
)

# Template tree: list of ("literal", str) | ("var", str) for structural comparison.
TemplateTree = list[tuple[str, str]]


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


def extract_named_call_templates(
    source: str,
    filename: str = "<unknown>",
    func_qualname: str = "",
    first_lineno: int = 1,
) -> list[NamedCallSiteInfo]:
    """
    Parse source and extract template info for every semi.<name>(...) call.
    Returns a list of NamedCallSiteInfo, one per semi.name() call site.
    """
    dedented = textwrap.dedent(source)
    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        return []

    results: list[NamedCallSiteInfo] = []
    loop_names_by_line: dict[int, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "semi":
            continue
        method_name = func.attr
        if method_name.startswith("_"):
            continue

        line_in_tree = node.lineno or 0
        lineno = line_in_tree + first_lineno - 1
        if line_in_tree not in loop_names_by_line:
            loop_names_by_line[line_in_tree] = _loop_target_names(tree, line_in_tree)
        loop_names = loop_names_by_line[line_in_tree]

        parts: list[TemplatePart] = [TemplatePart(is_literal=True, value=f"@named:{method_name}")]
        variable_names: list[str] = []
        variable_expressions: list[str] = []
        kwarg_names: list[str] = []

        for i, arg in enumerate(node.args):
            expr_src = ast.get_source_segment(dedented, arg) or ""
            names = _names_in_expr(arg)
            is_loop = bool(names & loop_names)
            name = f"v{i}" if is_loop else f"c{i}"
            variable_names.append(name)
            variable_expressions.append(expr_src)
            parts.append(TemplatePart(is_literal=False, value=name))

        for kw in sorted(node.keywords, key=lambda k: k.arg or ""):
            if kw.arg is None:
                continue
            name = f"c_kw_{kw.arg}"
            variable_names.append(name)
            variable_expressions.append("")
            kwarg_names.append(kw.arg)
            parts.append(TemplatePart(is_literal=False, value=name))

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
        if not loop_variant_names and variable_names and not variable_names[0].startswith("c_kw_"):
            loop_variant_names = [variable_names[0]]

        results.append(
            NamedCallSiteInfo(
                call_site=call_site,
                method_name=method_name,
                template=template,
                expected_type=expected,
                loop_variant_names=loop_variant_names,
                kwarg_names=kwarg_names,
            )
        )

    return results


def template_tree_from_prompt(template: PromptTemplate) -> TemplateTree:
    """Build a small tree (list of literal/var nodes) from a PromptTemplate."""
    tree: TemplateTree = []
    for p in template.template_parts:
        if p.is_literal:
            tree.append(("literal", p.value))
        else:
            tree.append(("var", p.value))
    return tree


def structural_fingerprint(tree: TemplateTree) -> str:
    """
    Stable hash of template structure only: literal vs var and var slot names.
    Same fingerprint => same shape (structural match); constants can differ.
    """
    shape = [(kind, name if kind == "var" else "") for kind, name in tree]
    raw = json.dumps(shape, sort_keys=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def trees_structurally_equal(a: TemplateTree, b: TemplateTree) -> bool:
    """True if both trees have the same shape (same sequence of literal vs var and var names)."""
    if len(a) != len(b):
        return False
    for (ka, va), (kb, vb) in zip(a, b):
        if ka != kb:
            return False
        if ka == "var" and va != vb:
            return False
    return True


def template_tree_diff_description(old_tree: TemplateTree, new_tree: TemplateTree) -> str:
    """Human-readable description of the diff between two template trees."""
    if trees_structurally_equal(old_tree, new_tree):
        return "same structure, only constant values differ"
    if len(old_tree) != len(new_tree):
        return f"different number of segments: {len(old_tree)} vs {len(new_tree)}"
    diffs: list[str] = []
    for i, ((ko, vo), (kn, vn)) in enumerate(zip(old_tree, new_tree)):
        if ko != kn:
            diffs.append(f"segment {i}: was {ko} became {kn}")
        elif ko == "var" and vo != vn:
            diffs.append(f"segment {i}: var {vo} -> {vn}")
        elif ko == "literal" and vo != vn:
            diffs.append(f"segment {i}: literal text changed")
    return "; ".join(diffs) if diffs else "no structural change"


