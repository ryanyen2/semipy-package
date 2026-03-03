"""
AST-based extraction of related source segments for standalone semi() (no decorator).

Finds the statement containing a given line and all module-level definitions
of names used in that statement, so the agent receives generous code context
without relying on @semiformal.
"""
from __future__ import annotations

import ast
from typing import Optional


def _get_names_used(node: ast.AST) -> set[str]:
    """Collect all name ids read from an AST node (Load context)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Load):
            names.add(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            names.add(n.value.id)
    return names


def _get_code_segment(source: str, node: ast.AST) -> str:
    try:
        return (ast.get_source_segment(source, node) or "").strip()
    except Exception:
        return ""


def _target_names(node: ast.AST) -> set[str]:
    """Names assigned by an Assign/For/With node."""
    out: set[str] = set()
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                out.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in ast.walk(t):
                    if isinstance(e, ast.Name):
                        out.add(e.id)
    elif isinstance(node, ast.For):
        if isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node.target, (ast.Tuple, ast.List)):
            for e in ast.walk(node.target):
                if isinstance(e, ast.Name):
                    out.add(e.id)
    elif isinstance(node, ast.With):
        for item in node.items:
            if isinstance(item.optional_vars, ast.Name):
                out.add(item.optional_vars.id)
    return out


def _is_statement_node(node: ast.AST) -> bool:
    """True if node is a statement (can stand alone in module/function body)."""
    return isinstance(
        node,
        (
            ast.Assign,
            ast.AnnAssign,
            ast.AugAssign,
            ast.Expr,
            ast.For,
            ast.While,
            ast.With,
            ast.If,
            ast.Try,
            ast.FunctionDef,
            ast.ClassDef,
            ast.Return,
            ast.Raise,
        ),
    )


def _find_statement_containing_line(
    source: str, lineno: int, tree: ast.AST
) -> Optional[ast.AST]:
    """Return the statement node (Assign, For, Expr, etc.) that contains the given line."""

    def visit(node: ast.AST) -> Optional[ast.AST]:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        if not (start <= lineno <= end):
            return None
        if _is_statement_node(node):
            return node
        for child in ast.iter_child_nodes(node):
            found = visit(child)
            if found is not None:
                return found
        return node

    for node in ast.iter_child_nodes(tree):
        found = visit(node)
        if found is not None:
            return found
    return None


def get_names_used_at_line(source: str, lineno: int) -> set[str]:
    """Return the set of names read (Load) in the statement that contains the given line."""
    if not source.strip() or lineno < 1:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    node = _find_statement_containing_line(source, lineno, tree)
    if node is None:
        return set()
    return _get_names_used(node)


def extract_enclosing_statement_at_line(source: str, lineno: int) -> Optional[str]:
    """
    Return the source of the top-level statement that contains the given line.
    Works at module level (Assign, Expr, For, With, If, etc.).
    """
    if not source.strip() or lineno < 1:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    node = _find_statement_containing_line(source, lineno, tree)
    if node is None:
        return None
    return _get_code_segment(source, node)


def extract_related_source_segments(
    file_source: str, call_lineno: int, max_snippets: int = 30
) -> list[str]:
    """
    Collect the enclosing statement at call_lineno and all module-level
    statements that define names used in it (assignments, for-loops, with).
    Returns a list of code snippets (related definitions first, enclosing last).
    Data-agnostic and generic: no hardcoded names or domains.
    """
    if not file_source.strip() or call_lineno < 1:
        return []
    try:
        tree = ast.parse(file_source)
    except SyntaxError:
        return []

    enclosing = _find_statement_containing_line(file_source, call_lineno, tree)
    if enclosing is None:
        return []

    names_used = _get_names_used(enclosing)
    if not names_used:
        seg = _get_code_segment(file_source, enclosing)
        return [seg] if seg else []

    defined_here: set[str] = set()
    snippets: list[str] = []
    seen_seg: set[str] = set()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in getattr(node, "names", []):
                name = getattr(alias, "asname", None) or getattr(alias, "name", None)
                if name and name in names_used:
                    defined_here.add(name)
                    seg = _get_code_segment(file_source, node)
                    if seg and seg not in seen_seg:
                        seen_seg.add(seg)
                        snippets.append(seg)
            continue
        if isinstance(node, (ast.Assign, ast.For, ast.With)):
            targets = _target_names(node)
            if targets & names_used:
                defined_here.update(targets)
                seg = _get_code_segment(file_source, node)
                if seg and seg not in seen_seg:
                    seen_seg.add(seg)
                    snippets.append(seg)
        if isinstance(node, ast.FunctionDef):
            if node.name in names_used:
                seg = _get_code_segment(file_source, node)
                if seg and seg not in seen_seg:
                    seen_seg.add(seg)
                    snippets.append(seg)
        if len(snippets) >= max_snippets:
            break

    enclosing_seg = _get_code_segment(file_source, enclosing)
    if enclosing_seg and enclosing_seg not in seen_seg:
        snippets.append(enclosing_seg)

    return snippets
