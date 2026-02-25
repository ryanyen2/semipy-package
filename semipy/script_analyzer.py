"""
AST-based analysis of user scripts for variable-level dependency hints.

Builds a graph of which variables flow into which assignments. Detects
semi() and semi.name() calls and records which variable they produce.
No hardcoded method or API names; driven by AST structure only.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ScriptDepGraph:
    """
    Variable-level dependency graph from static analysis.
    variable_deps: for each variable, list of variable names it depends on (RHS names).
    var_to_slot_hint: for variables produced by a semi() call, hint string (file:line:func).
    """

    variable_deps: dict[str, list[str]] = field(default_factory=dict)
    var_to_slot_hint: dict[str, str] = field(default_factory=dict)


def _is_semi_call(node: ast.AST) -> bool:
    """True if node is semi(...) or semi.name(...)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "semi"
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "semi":
            return True
    return False


def _names_used_in(node: ast.AST) -> list[str]:
    """Collect all Name ids that appear in the expression (variable reads)."""
    out: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name) -> None:
            if isinstance(n.ctx, ast.Load):
                out.append(n.id)
            self.generic_visit(n)

    Visitor().visit(node)
    return out


def _analyze_node(
    node: ast.AST,
    graph: ScriptDepGraph,
    filename: str,
    func_name: str,
) -> None:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                lhs = target.id
                names_in_rhs = _names_used_in(node.value)
                graph.variable_deps[lhs] = names_in_rhs
                if _is_semi_call(node.value):
                    line = getattr(node, "lineno", 0) or 0
                    graph.var_to_slot_hint[lhs] = f"{filename}:{line}:{func_name}"
        return
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        if isinstance(node.target, ast.Name):
            lhs = node.target.id
            names_in_rhs = _names_used_in(node.value)
            graph.variable_deps[lhs] = names_in_rhs
            if _is_semi_call(node.value):
                line = getattr(node, "lineno", 0) or 0
                graph.var_to_slot_hint[lhs] = f"{filename}:{line}:{func_name}"


def _visit_with_scope(
    node: ast.AST,
    graph: ScriptDepGraph,
    filename: str,
    func_name: str,
) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name or ""
        for stmt in node.body:
            _visit_with_scope(stmt, graph, filename, name)
        return
    if isinstance(node, ast.ClassDef):
        for stmt in node.body:
            _visit_with_scope(stmt, graph, filename, func_name)
        return
    _analyze_node(node, graph, filename, func_name)
    for child in ast.iter_child_nodes(node):
        _visit_with_scope(child, graph, filename, func_name)


def analyze_script(source_path: str) -> ScriptDepGraph:
    """
    Parse script at source_path and build variable dependency graph.
    Tracks assignments and which variables feed into them; records which
    variables are produced by semi() calls (slot hints).
    """
    graph = ScriptDepGraph()
    path = Path(source_path)
    if not path.exists():
        return graph
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return graph
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return graph

    filename = str(path.resolve())
    for node in ast.iter_child_nodes(tree):
        _visit_with_scope(node, graph, filename, "")
    return graph
