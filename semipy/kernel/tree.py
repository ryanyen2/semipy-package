"""The typed hardness tree: the node-level generalization of a semiformal slot.

A slot is no longer atomic. It is a tree of ``Node``s over the combinator core
(``map`` / ``filter`` / ``fold`` / ``branch`` / ``compose``) with opaque Python
blocks as the fallback leaf -- the recognition boundary from the 07-03 thesis.
Every existing slot loads as a single-node ``OPAQUE`` tree (zero-migration
back-compat: a legacy portal is a degenerate tree), so nothing about how a slot
executes changes in this phase. What changes is that *some* slots -- wherever
``lower_source_to_tree`` recognizes a combinator shape in the generated
implementation -- decompose into a tree whose parts can later be hardened,
blamed, and branched independently (Phases 3-5).

The combinator recognizer is deliberately general: it matches AST *shapes*
(a for-loop that appends a transformed element, an if/elif/else dispatch, ...),
never a domain, a variable name, or a specific example. The same recognizer that
finds a numeric-formatting branch also finds a merge-conflict-resolution branch
or a log-severity branch -- "which algorithm to run" is read off the shape of
the code the LLM already wrote, not hardcoded per use case.
"""
from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class NodeKind(Enum):
    """The combinator core, plus the two non-combinator leaf kinds."""

    OPAQUE = "opaque"      # fallback: a whole code region treated as one unit
    LEAF = "leaf"          # irreducibly semantic fuzzy leaf (no usable output equivalence)
    MAP = "map"
    FILTER = "filter"
    FOLD = "fold"
    BRANCH = "branch"
    COMPOSE = "compose"


class Hardness(Enum):
    """molten = per-call LLM; plastic = committed code, replaceable by one commit;
    frozen = fixed artifact + deopt guard, changeable only by a ledgered move."""

    MOLTEN = "molten"
    PLASTIC = "plastic"
    FROZEN = "frozen"


@dataclass
class Guard:
    """A typed predicate over a BRANCH node's input, selecting one child regime.

    ``predicate_source`` is the verbatim guard expression as written (e.g.
    ``isinstance(x, int)`` or ``msg.kind == "conflict"``) -- the recognizer never
    interprets *what* the predicate tests, only that the code branches on it. The
    closed typed-predicate DSL and its compiler (frontier-kernel Phase 5) turn
    this into a validated, runtime-dispatchable guard; until then it is
    descriptive provenance, not an executable object.
    """

    predicate_source: str
    is_fallback: bool = False   # True for a bare ``else`` arm (always matches, tried last)
    description: str = ""


@dataclass
class Node:
    """One node of the hardness tree.

    ``artifact`` holds this node's code (set for every node in Phase 1: an
    opaque leaf's own source, or a small glue snippet for a recognized
    combinator). ``population`` is the Phase 2 candidate population; it is
    always ``None`` until Phase 2 wires ``kernel/population.py`` in.
    """

    node_id: str
    kind: NodeKind
    hardness: Hardness
    input_type: str = "Any"
    output_type: str = "Any"
    artifact: str | None = None
    population: Any | None = None
    children: list["Node"] = field(default_factory=list)
    guards: list[Guard] = field(default_factory=list)   # BRANCH only: guards[i] gates children[i]
    meta: dict[str, Any] = field(default_factory=dict)

    def is_leaf(self) -> bool:
        return not self.children

    def walk(self) -> Iterator["Node"]:
        """Preorder traversal of this node and all descendants."""
        yield self
        for child in self.children:
            yield from child.walk()


def degenerate_tree(node_id: str, artifact: str, *, hardness: Hardness = Hardness.PLASTIC) -> Node:
    """A single OPAQUE node wrapping an entire implementation.

    This is the zero-migration shape every legacy slot loads as: no lowering
    was attempted (or none recognized a combinator shape), so the whole
    implementation is one unit. ``hardness`` should reflect the slot's real
    current state (PLASTIC for cached/committed code -- today's default;
    MOLTEN for a slot running in per-call interpreted mode).
    """
    return Node(node_id=node_id, kind=NodeKind.OPAQUE, hardness=hardness, artifact=artifact)


# ---------------------------------------------------------------------------
# Serialization (JSON-safe dict <-> Node), for Slot.kernel_tree round-trip.
# ---------------------------------------------------------------------------


def guard_to_dict(guard: Guard) -> dict[str, Any]:
    return {
        "predicate_source": guard.predicate_source,
        "is_fallback": guard.is_fallback,
        "description": guard.description,
    }


def guard_from_dict(d: dict[str, Any]) -> Guard:
    return Guard(
        predicate_source=d.get("predicate_source", ""),
        is_fallback=bool(d.get("is_fallback", False)),
        description=d.get("description", ""),
    )


def tree_to_dict(node: Node) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "kind": node.kind.value,
        "hardness": node.hardness.value,
        "input_type": node.input_type,
        "output_type": node.output_type,
        "artifact": node.artifact,
        # population is never serialized in Phase 1 (always None); Phase 2 adds
        # its own (de)serialization once kernel/population.py exists.
        "children": [tree_to_dict(c) for c in node.children],
        "guards": [guard_to_dict(g) for g in node.guards],
        "meta": dict(node.meta),
    }


def tree_from_dict(d: dict[str, Any]) -> Node:
    return Node(
        node_id=d.get("node_id", ""),
        kind=NodeKind(d.get("kind", NodeKind.OPAQUE.value)),
        hardness=Hardness(d.get("hardness", Hardness.PLASTIC.value)),
        input_type=d.get("input_type", "Any"),
        output_type=d.get("output_type", "Any"),
        artifact=d.get("artifact"),
        children=[tree_from_dict(c) for c in d.get("children", [])],
        guards=[guard_from_dict(g) for g in d.get("guards", [])],
        meta=dict(d.get("meta", {})),
    )


def get_tree(slot: Any) -> Node | None:
    """Return the slot's persisted hardness tree, or ``None`` if none was
    computed yet. By convention (zero-migration back-compat), no persisted tree
    means "degenerate": callers should treat the slot's current head commit as
    a single opaque node (``degenerate_tree``), not as an error.
    """
    raw = getattr(slot, "kernel_tree", None)
    if not raw:
        return None
    return tree_from_dict(raw)


def save_tree(slot: Any, node: Node) -> None:
    """Persist a hardness tree back onto the slot (caller saves the portal)."""
    slot.kernel_tree = tree_to_dict(node)


# ---------------------------------------------------------------------------
# Combinator recognition -- general AST-shape matching over generated source.
#
# Every matcher here keys on *shape* (loop/if/comprehension structure), never
# on a variable name, spec text, or domain. The same matchers fire whether the
# accumulator is called ``out`` or ``resolved_conflicts``, and whether the
# branch discriminant is a type check, a dict key, or a dataclass field.
# ---------------------------------------------------------------------------


def _first_function_def(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return stmt
    return None


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _stmts_to_source(stmts: list[ast.stmt], fn_def: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Wrap a statement list back into a standalone, unparsed function.

    Used to give every node (leaf or glue) real, storable Python source, even
    though nothing executes trees yet (Phase 2+). Reuses the parent function's
    signature so the snippet stays readable and self-contained.
    """
    body = list(stmts) or [ast.Pass()]
    new_fn = ast.FunctionDef(
        name=fn_def.name,
        args=fn_def.args,
        body=body,
        decorator_list=[],
        returns=None,
    )
    module = ast.Module(body=[new_fn], type_ignores=[])
    ast.fix_missing_locations(module)
    try:
        return ast.unparse(module)
    except Exception:
        return ""


def _loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _is_append_call(expr: ast.expr) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "append"
        and isinstance(expr.func.value, ast.Name)
        and len(expr.args) == 1
        and not expr.keywords
    )


def _append_call_parts(expr: ast.Call) -> tuple[str, ast.expr]:
    func = expr.func
    assert isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
    return func.value.id, expr.args[0]


def _is_bare_name(expr: ast.expr, name: str) -> bool:
    return isinstance(expr, ast.Name) and expr.id == name


def _match_for_loop(for_stmt: ast.For) -> tuple[str, str, ast.expr] | None:
    """Return (combinator_kind, acc_name, key_expr) for a recognized loop body shape."""
    if not isinstance(for_stmt.target, ast.Name):
        return None  # tuple-unpacking loop targets: out of scope for Phase 1
    item_name = for_stmt.target.id
    body = for_stmt.body

    if len(body) == 1 and isinstance(body[0], ast.If) and not body[0].orelse:
        cond = body[0].test
        inner = body[0].body
        if len(inner) == 1 and isinstance(inner[0], ast.Expr) and _is_append_call(inner[0].value):
            acc_name, arg = _append_call_parts(inner[0].value)
            if _is_bare_name(arg, item_name):
                return ("filter", acc_name, cond)
            return ("map_filter", acc_name, cond)
        return None

    if len(body) == 1 and isinstance(body[0], ast.Expr) and _is_append_call(body[0].value):
        acc_name, arg = _append_call_parts(body[0].value)
        return ("map", acc_name, arg)

    if len(body) == 1 and isinstance(body[0], ast.Assign):
        stmt = body[0]
        if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            acc_name = stmt.targets[0].id
            if acc_name in _loaded_names(stmt.value):
                return ("fold", acc_name, stmt.value)
        return None

    if len(body) == 1 and isinstance(body[0], ast.AugAssign) and isinstance(body[0].target, ast.Name):
        return ("fold", body[0].target.id, body[0].value)

    return None


def _try_loop_run(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node | None:
    """Recognize ``[for_stmt]`` (the whole run is exactly one for-loop)."""
    if len(stmts) != 1 or not isinstance(stmts[0], ast.For):
        return None
    matched = _match_for_loop(stmts[0])
    if matched is None:
        return None
    kind, acc_name, key_expr = matched
    leaf_source = _stmts_to_source([ast.Return(value=key_expr)], fn_def)
    meta = {"accumulator": acc_name, "iterable": ast.unparse(stmts[0].iter)}

    if kind == "map":
        leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=leaf_source)
        return Node(node_id=node_id, kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[leaf], meta=meta)
    if kind == "filter":
        leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=leaf_source, output_type="bool")
        return Node(node_id=node_id, kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[leaf], meta=meta)
    if kind == "fold":
        leaf = Node(node_id=f"{node_id}.fold.step", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=leaf_source)
        return Node(node_id=node_id, kind=NodeKind.FOLD, hardness=Hardness.PLASTIC, children=[leaf], meta=meta)
    if kind == "map_filter":
        pred_source = _stmts_to_source([ast.Return(value=stmts[0].body[0].test)], fn_def)  # type: ignore[union-attr]
        pred_leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        filter_node = Node(node_id=f"{node_id}.filter", kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[pred_leaf], meta=meta)
        map_leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=leaf_source)
        map_node = Node(node_id=f"{node_id}.map", kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[map_leaf], meta=meta)
        return Node(node_id=node_id, kind=NodeKind.COMPOSE, hardness=Hardness.PLASTIC, children=[filter_node, map_node])
    return None


def _try_comprehension_run(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node | None:
    """Recognize ``return [<elt> for <target> in <iter> (if <cond>)?]`` (single generator)."""
    if len(stmts) != 1 or not isinstance(stmts[0], ast.Return) or not isinstance(stmts[0].value, ast.ListComp):
        return None
    comp = stmts[0].value
    if len(comp.generators) != 1 or comp.generators[0].ifs and len(comp.generators[0].ifs) > 1:
        return None
    gen = comp.generators[0]
    if not isinstance(gen.target, ast.Name):
        return None
    item_name = gen.target.id
    is_map = not (isinstance(comp.elt, ast.Name) and comp.elt.id == item_name)
    has_filter = bool(gen.ifs)
    meta = {"iterable": ast.unparse(gen.iter)}

    if has_filter and is_map:
        pred_source = _stmts_to_source([ast.Return(value=gen.ifs[0])], fn_def)
        pred_leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        filter_node = Node(node_id=f"{node_id}.filter", kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[pred_leaf], meta=meta)
        map_source = _stmts_to_source([ast.Return(value=comp.elt)], fn_def)
        map_leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=map_source)
        map_node = Node(node_id=f"{node_id}.map", kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[map_leaf], meta=meta)
        return Node(node_id=node_id, kind=NodeKind.COMPOSE, hardness=Hardness.PLASTIC, children=[filter_node, map_node])
    if has_filter:
        pred_source = _stmts_to_source([ast.Return(value=gen.ifs[0])], fn_def)
        pred_leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        return Node(node_id=node_id, kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[pred_leaf], meta=meta)
    if is_map:
        map_source = _stmts_to_source([ast.Return(value=comp.elt)], fn_def)
        map_leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=map_source)
        return Node(node_id=node_id, kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[map_leaf], meta=meta)
    return None


def _collect_if_chain(if_stmt: ast.If) -> tuple[list[tuple[ast.expr, list[ast.stmt]]], list[ast.stmt] | None]:
    """Flatten an if/elif/.../else chain into (test, body) arms + an optional else body."""
    arms: list[tuple[ast.expr, list[ast.stmt]]] = [(if_stmt.test, if_stmt.body)]
    orelse = if_stmt.orelse
    while len(orelse) == 1 and isinstance(orelse[0], ast.If):
        arms.append((orelse[0].test, orelse[0].body))
        orelse = orelse[0].orelse
    return arms, (orelse or None)


def _try_branch_run(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node | None:
    """Recognize ``[if_stmt]`` with at least one elif/else arm as a regime dispatch."""
    if len(stmts) != 1 or not isinstance(stmts[0], ast.If):
        return None
    arms, else_body = _collect_if_chain(stmts[0])
    if len(arms) < 1 or (len(arms) == 1 and else_body is None):
        return None  # a single if with no else/elif is not (yet) a multi-regime dispatch

    guards: list[Guard] = []
    children: list[Node] = []
    for i, (test, body) in enumerate(arms):
        guards.append(Guard(predicate_source=ast.unparse(test)))
        children.append(lower_stmts_to_tree(body, f"{node_id}.branch.{i}", fn_def))
    if else_body is not None:
        guards.append(Guard(predicate_source="True", is_fallback=True, description="else"))
        children.append(lower_stmts_to_tree(else_body, f"{node_id}.branch.{len(arms)}", fn_def))

    return Node(node_id=node_id, kind=NodeKind.BRANCH, hardness=Hardness.PLASTIC, children=children, guards=guards)


def _segment_top_level(body: list[ast.stmt]) -> list[list[ast.stmt]]:
    """Split a statement list into runs, each a single ``For``/``If`` or a run of
    simple statements -- the unit each combinator matcher operates on."""
    runs: list[list[ast.stmt]] = []
    current: list[ast.stmt] = []
    for stmt in body:
        if isinstance(stmt, (ast.For, ast.If)):
            if current:
                runs.append(current)
                current = []
            runs.append([stmt])
        else:
            current.append(stmt)
    if current:
        runs.append(current)
    return runs


def lower_stmts_to_tree(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node:
    """Lower one statement list (a function body or a branch arm) to a tree."""
    stmts = _strip_docstring(stmts)
    if not stmts:
        return Node(node_id=node_id, kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=_stmts_to_source(stmts, fn_def))

    runs = _segment_top_level(stmts)
    if len(runs) == 1:
        run = runs[0]
        for matcher in (_try_comprehension_run, _try_loop_run, _try_branch_run):
            matched = matcher(run, node_id, fn_def)
            if matched is not None:
                return matched
        return Node(node_id=node_id, kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=_stmts_to_source(run, fn_def))

    children: list[Node] = []
    for i, run in enumerate(runs):
        run_id = f"{node_id}.compose.{i}"
        matched = None
        for matcher in (_try_comprehension_run, _try_loop_run, _try_branch_run):
            matched = matcher(run, run_id, fn_def)
            if matched is not None:
                break
        children.append(matched if matched is not None else Node(node_id=run_id, kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=_stmts_to_source(run, fn_def)))

    if all(c.kind == NodeKind.OPAQUE for c in children):
        # Splitting into runs recognized nothing real (every run stayed opaque):
        # a COMPOSE of plain opaque slices buys nothing over one opaque node --
        # collapse back rather than report a hollow multi-node tree.
        return Node(node_id=node_id, kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=_stmts_to_source(stmts, fn_def))
    return Node(node_id=node_id, kind=NodeKind.COMPOSE, hardness=Hardness.PLASTIC, children=children)


def lower_source_to_tree(source: str, node_id: str, *, hardness: Hardness = Hardness.PLASTIC) -> Node:
    """Decompose a generated implementation's source into a hardness tree.

    Falls back to a single OPAQUE node (``degenerate_tree``) whenever the
    source does not parse, has no top-level function, or matches none of the
    recognized combinator shapes -- lowering never blocks execution and never
    raises: an unrecognized shape is exactly today's whole-slot behavior.
    """
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return degenerate_tree(node_id, source, hardness=hardness)
    fn_def = _first_function_def(tree)
    if fn_def is None:
        return degenerate_tree(node_id, source, hardness=hardness)
    node = lower_stmts_to_tree(fn_def.body, node_id, fn_def)
    if node.kind == NodeKind.OPAQUE:
        # Preserve the caller's real source verbatim for the degenerate case
        # (the synthetic re-unparse in lower_stmts_to_tree is equivalent but
        # needlessly reformats it).
        return degenerate_tree(node_id, source, hardness=hardness)
    _set_hardness(node, hardness)
    return node


def _set_hardness(node: Node, hardness: Hardness) -> None:
    node.hardness = hardness
    for child in node.children:
        _set_hardness(child, hardness)


# ---------------------------------------------------------------------------
# Phase 1 go/no-go measurement: what fraction of a corpus lowers multi-node.
# ---------------------------------------------------------------------------


_RECOGNIZED_COMBINATORS = (NodeKind.MAP, NodeKind.FILTER, NodeKind.FOLD, NodeKind.BRANCH)


def is_multi_node(node: Node) -> bool:
    """True iff the tree contains at least one *recognized* combinator node.

    A COMPOSE whose children are all still OPAQUE (segmentation found stage
    boundaries but understood none of them) conveys no benefit -- there is
    nothing to freeze, blame, or branch independently -- so it does not count
    as multi-node for the go/no-go measurement below.
    """
    return any(n.kind in _RECOGNIZED_COMBINATORS for n in node.walk())


def multi_node_fraction(sources: list[str]) -> float:
    """Fraction of ``sources`` (generated implementations) that lower to a
    multi-node tree rather than falling back to a single opaque node.

    This is the frontier-kernel plan's Phase 1 go/no-go gate (Part III §6):
    per-node freezing, blame, and locality only fire on multi-node trees, so
    this number is reported, not assumed.
    """
    if not sources:
        return 0.0
    hits = 0
    for i, src in enumerate(sources):
        node = lower_source_to_tree(src, f"corpus.{i}")
        if is_multi_node(node):
            hits += 1
    return hits / len(sources)
