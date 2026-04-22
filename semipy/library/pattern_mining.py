"""AST-based pattern mining: subtree extraction, normalization, anti-unification, mining."""
from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from dataclasses import dataclass

from semipy.library.abstractions import ASTPattern


def _node_count(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def all_subtrees(
    tree: ast.AST,
    min_nodes: int = 5,
    max_nodes: int = 200,
) -> list[ast.AST]:
    """Extract all subtrees with node count in [min_nodes, max_nodes]. Excludes trivial stubs."""
    result: list[ast.AST] = []

    def visit(n: ast.AST) -> None:
        cnt = _node_count(n)
        if min_nodes <= cnt <= max_nodes:
            result.append(n)
        for c in ast.iter_child_nodes(n):
            visit(c)

    visit(tree)
    return result


def _normalize_name(name: str, local_map: dict[str, str], counter: list[int]) -> str:
    if name in ("True", "False", "None") or (name.startswith("__") and name.endswith("__")):
        return name
    if name in local_map:
        return local_map[name]
    idx = len(local_map)
    var = f"x{idx}"
    local_map[name] = var
    return var


def _normalize_node(
    node: ast.AST,
    source: str,
    local_map: dict[str, str],
    counter: list[int],
) -> ast.AST:
    """Return a copy of node with local names alpha-renamed to x0, x1, ..."""
    if isinstance(node, ast.Name):
        return ast.Name(
            id=_normalize_name(node.id, local_map, counter),
            ctx=node.ctx,
        )
    if isinstance(node, ast.FunctionDef):
        new_locals = dict(local_map)
        new_args: list[ast.arg] = []
        for a in node.args.args:
            nid = _normalize_name(a.arg, new_locals, counter)
            new_args.append(ast.arg(arg=nid, annotation=a.annotation))
        new_body = [
            _normalize_node(stmt, source, dict(new_locals), counter)
            for stmt in node.body
        ]
        return ast.FunctionDef(
            name=node.name,
            args=ast.arguments(
                posonlyargs=[],
                args=new_args,
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=new_body,
            decorator_list=[],
        )
    if isinstance(node, ast.Call):
        new_func = _normalize_node(node.func, source, local_map, counter)
        new_args = [_normalize_node(a, source, local_map, counter) for a in node.args]
        new_kwargs = [
            ast.keyword(arg=k.arg, value=_normalize_node(k.value, source, local_map, counter))
            for k in node.keywords
        ]
        return ast.Call(func=new_func, args=new_args, keywords=new_kwargs)
    if isinstance(node, ast.Attribute):
        return ast.Attribute(
            value=_normalize_node(node.value, source, local_map, counter),
            attr=node.attr,
            ctx=node.ctx,
        )
    if isinstance(node, ast.Subscript):
        return ast.Subscript(
            value=_normalize_node(node.value, source, local_map, counter),
            slice=_normalize_node(node.slice, source, local_map, counter) if isinstance(node.slice, ast.AST) else node.slice,
            ctx=node.ctx,
        )
    if isinstance(node, ast.Compare):
        return ast.Compare(
            left=_normalize_node(node.left, source, local_map, counter),
            ops=node.ops,
            comparators=[_normalize_node(c, source, local_map, counter) for c in node.comparators],
        )
    if isinstance(node, ast.BinOp):
        return ast.BinOp(
            left=_normalize_node(node.left, source, local_map, counter),
            op=node.op,
            right=_normalize_node(node.right, source, local_map, counter),
        )
    if isinstance(node, ast.UnaryOp):
        return ast.UnaryOp(
            op=node.op,
            operand=_normalize_node(node.operand, source, local_map, counter),
        )
    if isinstance(node, ast.If):
        return ast.If(
            test=_normalize_node(node.test, source, local_map, counter),
            body=[_normalize_node(s, source, local_map, counter) for s in node.body],
            orelse=[_normalize_node(s, source, local_map, counter) for s in node.orelse],
        )
    if isinstance(node, ast.For):
        return ast.For(
            target=_normalize_node(node.target, source, local_map, counter),
            iter=_normalize_node(node.iter, source, local_map, counter),
            body=[_normalize_node(s, source, local_map, counter) for s in node.body],
            orelse=[_normalize_node(s, source, local_map, counter) for s in node.orelse],
        )
    if isinstance(node, ast.ListComp):
        return ast.ListComp(
            elt=_normalize_node(node.elt, source, local_map, counter),
            generators=[
                ast.comprehension(
                    target=_normalize_node(g.target, source, local_map, counter),
                    iter=_normalize_node(g.iter, source, local_map, counter),
                    ifs=[_normalize_node(i, source, local_map, counter) for i in g.ifs],
                    is_async=g.is_async,
                )
                for g in node.generators
            ],
        )
    if isinstance(node, ast.Return):
        return ast.Return(value=_normalize_node(node.value, source, local_map, counter) if node.value else None)
    if isinstance(node, ast.Assign):
        return ast.Assign(
            targets=[_normalize_node(t, source, local_map, counter) for t in node.targets],
            value=_normalize_node(node.value, source, local_map, counter),
        )
    if isinstance(node, ast.Constant):
        return ast.Constant(value=node.value)
    if isinstance(node, ast.List):
        return ast.List(elts=[_normalize_node(e, source, local_map, counter) for e in node.elts], ctx=node.ctx)
    if isinstance(node, ast.Dict):
        return ast.Dict(
            keys=[_normalize_node(k, source, local_map, counter) if k else None for k in node.keys],
            values=[_normalize_node(v, source, local_map, counter) for v in node.values],
        )
    if isinstance(node, ast.Lambda):
        new_locals = dict(local_map)
        new_args = [ast.arg(arg=_normalize_name(a.arg, new_locals, counter), annotation=a.annotation) for a in node.args.args]
        new_body = _normalize_node(node.body, source, new_locals, counter)
        return ast.Lambda(
            args=ast.arguments(posonlyargs=[], args=new_args, vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
            body=new_body,
        )
    if isinstance(node, ast.BoolOp):
        return ast.BoolOp(
            op=node.op,
            values=[_normalize_node(v, source, local_map, counter) for v in node.values],
        )
    if isinstance(node, ast.keyword):
        return ast.keyword(arg=node.arg, value=_normalize_node(node.value, source, local_map, counter))
    return node


def normalize_subtree(node: ast.AST, source: str) -> str:
    """Normalize a subtree (alpha-rename locals to x0, x1, ...) and return canonical source."""
    local_map: dict[str, str] = {}
    counter: list[int] = [0]
    normalized = _normalize_node(node, source, local_map, counter)
    try:
        return ast.unparse(normalized)
    except Exception:
        return ast.dump(normalized)


def ast_hash(node: ast.AST) -> str:
    """Structural hash of the AST (dump and hash)."""
    return hashlib.sha256(ast.dump(node).encode()).hexdigest()[:32]


def anti_unify(sources: list[str]) -> tuple[str, list[str]]:
    """
    Find a most specific generalization of the given normalized source strings.
    Returns (generalized_source, parameter_names). Parameter names are placeholders where sources differed.
    Simple approach: use the first source as template; where others differ in corresponding positions, introduce a hole.
    For robustness we use a line-based diff: same line -> keep, differing -> hole named p0, p1, ...
    """
    if not sources:
        return "", []
    if len(sources) == 1:
        return sources[0], []
    param_names: list[str] = []
    seen_params: dict[str, str] = {}
    lines_by_pos: list[list[str]] = []
    max_lines = max(len(s.splitlines()) for s in sources)
    all_lines = [s.splitlines() for s in sources]
    for i in range(max_lines):
        col: list[str] = []
        for L in all_lines:
            col.append(L[i] if i < len(L) else "")
        lines_by_pos.append(col)
    result_lines: list[str] = []
    for i, col in enumerate(lines_by_pos):
        uniq = list(dict.fromkeys(c for c in col if c.strip()))
        if len(uniq) <= 1:
            result_lines.append(col[0] if col else "")
            continue
        key = "|".join(sorted(uniq))
        if key not in seen_params:
            idx = len(seen_params)
            name = f"p{idx}"
            seen_params[key] = name
            param_names.append(name)
        result_lines.append(seen_params[key])
    return "\n".join(result_lines), param_names


@dataclass
class MinedPatternGroup:
    ast_hash: str
    normalized_source: str
    parameter_names: list[str]
    frequency: int
    node_count: int
    commit_sources: list[str]


def mine_patterns(
    commit_sources: list[tuple[str, str]],
    min_pattern_frequency: int = 3,
    min_nodes: int = 5,
    max_nodes: int = 200,
) -> list[tuple[ASTPattern, list[tuple[str, str]]]]:
    """
    Mine AST patterns from a list of (commit_id, source) pairs.
    Returns list of (ASTPattern, [(commit_id, source), ...]) for each pattern that appears at least min_pattern_frequency times.
    No hardcoded patterns; structure is derived entirely from the provided sources.
    """
    by_hash: dict[str, list[tuple[str, str, ast.AST, str]]] = defaultdict(list)
    for commit_id, source in commit_sources:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for subtree in all_subtrees(tree, min_nodes=min_nodes, max_nodes=max_nodes):
            try:
                norm = normalize_subtree(subtree, source)
                h = hashlib.sha256(norm.encode()).hexdigest()[:32]
                by_hash[h].append((commit_id, source, subtree, norm))
            except Exception:
                continue
    result: list[tuple[ASTPattern, list[tuple[str, str]]]] = []
    for h, group in by_hash.items():
        if len(group) < min_pattern_frequency:
            continue
        norms = [g[3] for g in group]
        gen_source, param_names = anti_unify(norms)
        if not gen_source.strip():
            continue
        pattern_id = hashlib.sha256(f"{h}:{gen_source}".encode()).hexdigest()[:24]
        pattern = ASTPattern(
            pattern_id=pattern_id,
            normalized_source=gen_source,
            parameter_names=param_names,
            ast_hash=h,
            embedding_id="",
        )
        commits_sources = [(g[0], g[1]) for g in group]
        result.append((pattern, commits_sources))
    return result
