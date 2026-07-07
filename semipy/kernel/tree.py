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


def _leaf_function_source(name: str, params: list[str], expr: ast.expr) -> str:
    """A genuinely self-contained, callable ``def name(params...): return expr``.

    Used for a MAP/FILTER/FOLD leaf, whose expression references the *loop or
    comprehension-local* variable(s) (the element, the accumulator) rather
    than the enclosing function's own parameters. Reusing the parent's
    signature there (as ``_stmts_to_source`` correctly does for whole-scope
    nodes like a BRANCH arm) would unparse to source that raises ``NameError``
    the moment anything tries to call it -- the loop variable was never in
    scope. This is what makes blame's per-node trace replay (Phase 4) able to
    actually execute a leaf, not just read it as a comment.
    """
    args = ast.arguments(
        posonlyargs=[], args=[ast.arg(arg=p) for p in params],
        vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
    )
    fn = ast.FunctionDef(name=name, args=args, body=[ast.Return(value=expr)], decorator_list=[], returns=None)
    module = ast.Module(body=[fn], type_ignores=[])
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


@dataclass
class _LoopMatch:
    """One recognized for-loop body shape.

    ``expr`` is the map transform / filter predicate / fold step; for
    ``map_filter`` specifically, ``expr`` is the map transform and
    ``filter_expr`` is the separate guarding predicate -- kept apart so a
    caller can build correct, independently-executable leaves for *both*
    (a prior version conflated them, silently dropping the map transform).
    """

    kind: str  # "map" | "filter" | "fold" | "map_filter"
    item_name: str
    acc_name: str
    expr: ast.expr
    filter_expr: ast.expr | None = None


def _match_for_loop(for_stmt: ast.For) -> _LoopMatch | None:
    """Recognize a for-loop body shape."""
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
                return _LoopMatch("filter", item_name, acc_name, cond)
            return _LoopMatch("map_filter", item_name, acc_name, arg, filter_expr=cond)
        return None

    if len(body) == 1 and isinstance(body[0], ast.Expr) and _is_append_call(body[0].value):
        acc_name, arg = _append_call_parts(body[0].value)
        return _LoopMatch("map", item_name, acc_name, arg)

    if len(body) == 1 and isinstance(body[0], ast.Assign):
        stmt = body[0]
        if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            acc_name = stmt.targets[0].id
            if acc_name in _loaded_names(stmt.value):
                return _LoopMatch("fold", item_name, acc_name, stmt.value)
        return None

    if len(body) == 1 and isinstance(body[0], ast.AugAssign) and isinstance(body[0].target, ast.Name):
        acc_name = body[0].target.id
        # ``total += x`` has no standalone expression for "the new value" --
        # synthesize the equivalent ``total <op> x`` so the fold leaf's step
        # actually depends on the accumulator, not just the loop item.
        step_expr = ast.BinOp(left=ast.Name(id=acc_name, ctx=ast.Load()), op=body[0].op, right=body[0].value)
        return _LoopMatch("fold", item_name, acc_name, step_expr)

    return None


def _try_loop_run(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node | None:
    """Recognize ``[for_stmt]`` (the whole run is exactly one for-loop)."""
    if len(stmts) != 1 or not isinstance(stmts[0], ast.For):
        return None
    matched = _match_for_loop(stmts[0])
    if matched is None:
        return None
    meta = {"accumulator": matched.acc_name, "iterable": ast.unparse(stmts[0].iter)}

    if matched.kind == "map":
        leaf_source = _leaf_function_source("map_body", [matched.item_name], matched.expr)
        leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=leaf_source)
        return Node(node_id=node_id, kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[leaf], meta=meta)
    if matched.kind == "filter":
        pred_source = _leaf_function_source("filter_pred", [matched.item_name], matched.expr)
        leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        return Node(node_id=node_id, kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[leaf], meta=meta)
    if matched.kind == "fold":
        step_source = _leaf_function_source("fold_step", [matched.acc_name, matched.item_name], matched.expr)
        leaf = Node(node_id=f"{node_id}.fold.step", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=step_source)
        return Node(node_id=node_id, kind=NodeKind.FOLD, hardness=Hardness.PLASTIC, children=[leaf], meta=meta)
    if matched.kind == "map_filter":
        pred_source = _leaf_function_source("filter_pred", [matched.item_name], matched.filter_expr)
        pred_leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        filter_node = Node(node_id=f"{node_id}.filter", kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[pred_leaf], meta=meta)
        map_source = _leaf_function_source("map_body", [matched.item_name], matched.expr)
        map_leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=map_source)
        map_node = Node(node_id=f"{node_id}.map", kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[map_leaf], meta=meta)
        return Node(node_id=node_id, kind=NodeKind.COMPOSE, hardness=Hardness.PLASTIC, children=[filter_node, map_node])
    return None


def _comprehension_node(comp: ast.expr, node_id: str) -> Node | None:
    """Build the MAP/FILTER/COMPOSE(FILTER,MAP) shape for one list comprehension
    (single generator, <=1 filter clause) -- shared by both the bare-``return``
    and the assign-to-a-name recognizers below."""
    if not isinstance(comp, ast.ListComp):
        return None
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
        pred_source = _leaf_function_source("filter_pred", [item_name], gen.ifs[0])
        pred_leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        filter_node = Node(node_id=f"{node_id}.filter", kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[pred_leaf], meta=meta)
        map_source = _leaf_function_source("map_body", [item_name], comp.elt)
        map_leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=map_source)
        map_node = Node(node_id=f"{node_id}.map", kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[map_leaf], meta=meta)
        return Node(node_id=node_id, kind=NodeKind.COMPOSE, hardness=Hardness.PLASTIC, children=[filter_node, map_node])
    if has_filter:
        pred_source = _leaf_function_source("filter_pred", [item_name], gen.ifs[0])
        pred_leaf = Node(node_id=f"{node_id}.filter.pred", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=pred_source, output_type="bool")
        return Node(node_id=node_id, kind=NodeKind.FILTER, hardness=Hardness.PLASTIC, children=[pred_leaf], meta=meta)
    if is_map:
        map_source = _leaf_function_source("map_body", [item_name], comp.elt)
        map_leaf = Node(node_id=f"{node_id}.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=map_source)
        return Node(node_id=node_id, kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[map_leaf], meta=meta)
    return None


def _try_comprehension_run(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node | None:
    """Recognize ``return [<elt> for <target> in <iter> (if <cond>)?]`` (single generator)."""
    if len(stmts) != 1 or not isinstance(stmts[0], ast.Return) or not isinstance(stmts[0].value, ast.ListComp):
        return None
    return _comprehension_node(stmts[0].value, node_id)


def _is_listcomp_assign(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.ListComp)
    )


def _try_assigned_comprehension_run(stmts: list[ast.stmt], node_id: str, fn_def: ast.FunctionDef) -> Node | None:
    """Recognize ``<name> = [<elt> for <target> in <iter> (if <cond>)?]`` -- a
    comprehension assigned to a plain name rather than returned directly.

    This is not a niche shape: it is what semipy's own generation convention
    actually produces (a placeholder ``result = ...`` line, then the real
    ``result = [...]`` assignment, then a later, separate ``return {"result":
    result}``) -- ``_try_comprehension_run`` alone never matches semipy's own
    generated code because it requires the comprehension to sit directly inside
    a bare ``return``. ``_segment_top_level`` isolates this assignment into its
    own run exactly like a ``For``/``If``, so the surrounding placeholder
    assignment and the wrapping return end up as separate sibling nodes in the
    enclosing COMPOSE -- the same way ``out = []`` / ``return out`` already
    surround a recognized loop.
    """
    if len(stmts) != 1 or not _is_listcomp_assign(stmts[0]):
        return None
    return _comprehension_node(stmts[0].value, node_id)  # type: ignore[union-attr]


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
    """Split a statement list into runs, each a single ``For``/``If``/comprehension-
    assignment or a run of simple statements -- the unit each combinator matcher
    operates on. A comprehension-assignment (``name = [... for ... in ...]``) is
    isolated the same way a ``For``/``If`` is: it *is* the combinator, not
    incidental to one, so leaving it grouped with a neighboring placeholder
    assignment or wrapping return would hide it from
    ``_try_assigned_comprehension_run``."""
    runs: list[list[ast.stmt]] = []
    current: list[ast.stmt] = []
    for stmt in body:
        if isinstance(stmt, (ast.For, ast.If)) or _is_listcomp_assign(stmt):
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
        for matcher in (_try_comprehension_run, _try_assigned_comprehension_run, _try_loop_run, _try_branch_run):
            matched = matcher(run, node_id, fn_def)
            if matched is not None:
                return matched
        return Node(node_id=node_id, kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact=_stmts_to_source(run, fn_def))

    children: list[Node] = []
    for i, run in enumerate(runs):
        run_id = f"{node_id}.compose.{i}"
        matched = None
        for matcher in (_try_comprehension_run, _try_assigned_comprehension_run, _try_loop_run, _try_branch_run):
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
# patch_source: the inverse of lowering, for melt's local rejuvenation
# (Phase 4, §3.2). Splices a regenerated node's artifact back into the whole
# function's original source, leaving everything else byte-identical.
#
# Scoped to exactly the node shapes blame.py can localize to: the root
# itself, a node reached by descending through zero or more BRANCH arms, and
# a MAP/FILTER leaf reached directly from a plain for-loop or comprehension
# (not the map_filter combo, which lowers to a COMPOSE -- blame never
# descends into one, so melt is never asked to patch inside it). Anything
# else returns None rather than guessing; the caller falls back to
# whole-function regeneration, today's behavior.
# ---------------------------------------------------------------------------


def _split_docstring(stmts: list[ast.stmt]) -> tuple[list[ast.stmt], list[ast.stmt]]:
    body = _strip_docstring(stmts)
    prefix_len = len(stmts) - len(body)
    return stmts[:prefix_len], stmts[prefix_len:]


def _extract_new_function(new_artifact: str) -> ast.FunctionDef | None:
    try:
        module = ast.parse(textwrap.dedent(new_artifact))
    except SyntaxError:
        return None
    fn = _first_function_def(module)
    return fn if isinstance(fn, ast.FunctionDef) else None


def _extract_body_stmts(new_artifact: str) -> list[ast.stmt] | None:
    fn = _extract_new_function(new_artifact)
    if fn is None:
        return None
    return _strip_docstring(fn.body)


def _extract_return_expr(new_artifact: str) -> ast.expr | None:
    """The sole return value of a leaf artifact shaped like
    ``_leaf_function_source`` builds: ``def name(params): return expr``."""
    body = _extract_body_stmts(new_artifact)
    if body is None or len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return None
    return body[0].value


def _patch_loop_leaf(for_stmt: ast.For, node_id: str, target_id: str, new_artifact: str) -> bool:
    matched = _match_for_loop(for_stmt)
    if matched is None or matched.kind == "map_filter":
        return False  # combo lowers to a COMPOSE; blame never targets inside it
    new_expr = _extract_return_expr(new_artifact)
    if new_expr is None:
        return False
    if matched.kind == "map" and target_id == f"{node_id}.map.body":
        append_call = for_stmt.body[0].value  # type: ignore[union-attr]
        append_call.args[0] = new_expr
        return True
    if matched.kind == "filter" and target_id == f"{node_id}.filter.pred":
        for_stmt.body[0].test = new_expr  # type: ignore[union-attr]
        return True
    if matched.kind == "fold" and target_id == f"{node_id}.fold.step":
        stmt = for_stmt.body[0]
        if isinstance(stmt, ast.Assign):
            stmt.value = new_expr
        elif isinstance(stmt, ast.AugAssign):
            # An AugAssign has no standalone "new value" slot -- promote it to
            # a plain assignment so the melted (possibly non-augmented) step
            # expression can be stored directly.
            for_stmt.body[0] = ast.Assign(targets=[ast.Name(id=matched.acc_name, ctx=ast.Store())], value=new_expr)
        else:
            return False
        return True
    return False


def _patch_comprehension_leaf(return_stmt: ast.Return, node_id: str, target_id: str, new_artifact: str) -> bool:
    comp = return_stmt.value
    if not isinstance(comp, ast.ListComp) or len(comp.generators) != 1:
        return False
    gen = comp.generators[0]
    if not isinstance(gen.target, ast.Name):
        return False
    is_map = not (isinstance(comp.elt, ast.Name) and comp.elt.id == gen.target.id)
    new_expr = _extract_return_expr(new_artifact)
    if new_expr is None:
        return False
    if is_map and target_id == f"{node_id}.map.body":
        comp.elt = new_expr
        return True
    if gen.ifs and target_id == f"{node_id}.filter.pred":
        gen.ifs[0] = new_expr
        return True
    return False


def _patch_run(run: list[ast.stmt], node_id: str, target_id: str, new_artifact: str) -> bool:
    if node_id == target_id:
        new_stmts = _extract_body_stmts(new_artifact)
        if new_stmts is None:
            return False
        run[:] = new_stmts
        return True

    if len(run) == 1 and isinstance(run[0], ast.For):
        return _patch_loop_leaf(run[0], node_id, target_id, new_artifact)

    if len(run) == 1 and isinstance(run[0], ast.Return) and isinstance(run[0].value, ast.ListComp):
        return _patch_comprehension_leaf(run[0], node_id, target_id, new_artifact)

    if len(run) == 1 and isinstance(run[0], ast.If):
        arms, else_body = _collect_if_chain(run[0])
        for i, (_test, arm_body) in enumerate(arms):
            arm_id = f"{node_id}.branch.{i}"
            if target_id == arm_id or target_id.startswith(arm_id + "."):
                return _patch_stmts(arm_body, arm_id, target_id, new_artifact)
        if else_body is not None:
            arm_id = f"{node_id}.branch.{len(arms)}"
            if target_id == arm_id or target_id.startswith(arm_id + "."):
                return _patch_stmts(else_body, arm_id, target_id, new_artifact)

    return False


def _patch_stmts(stmts: list[ast.stmt], node_id: str, target_id: str, new_artifact: str) -> bool:
    """Mutate *stmts* (a live AST list -- a function body or a branch arm's
    body/orelse) in place so the sub-structure lower_stmts_to_tree would label
    *target_id* is replaced. Mirrors lower_stmts_to_tree's own segmentation
    exactly, so the ids line up without extra bookkeeping on Node."""
    prefix, body = _split_docstring(stmts)
    if not body:
        return False
    runs = _segment_top_level(body)

    if len(runs) == 1:
        if not _patch_run(runs[0], node_id, target_id, new_artifact):
            return False
        stmts[:] = prefix + runs[0]
        return True

    for i, run in enumerate(runs):
        run_id = f"{node_id}.compose.{i}"
        if target_id == run_id or target_id.startswith(run_id + "."):
            if not _patch_run(run, run_id, target_id, new_artifact):
                return False
            stmts[:] = prefix + [s for r in runs for s in r]
            return True
    return False


def patch_source(source: str, root_id: str, target_id: str, new_artifact: str) -> str | None:
    """Splice *new_artifact* in as the replacement for node *target_id* in the
    tree ``lower_source_to_tree(source, root_id)`` would produce. Returns the
    patched whole-function source, or ``None`` when the shape is out of scope
    (never guesses; the caller falls back to whole-function regeneration).
    """
    if target_id == root_id:
        try:
            ast.parse(textwrap.dedent(new_artifact))
        except SyntaxError:
            return None
        return new_artifact

    try:
        module = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    fn_def = _first_function_def(module)
    if fn_def is None:
        return None
    if not _patch_stmts(fn_def.body, root_id, target_id, new_artifact):
        return None
    ast.fix_missing_locations(module)
    try:
        return ast.unparse(module)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# build_branch_wrapper: whole-function regime dispatch, for branch (Phase 5,
# §3.3). Unlike patch_source, this never needs the hardness tree at all -- it
# combines two *whole* candidate implementations (both generated for the same
# slot signature) behind a guard, since today every live slot is still a
# single opaque node (Phase 1's go/no-go fraction). If/when a slot's tree
# actually decomposes further, the same technique applies at a sub-node level;
# nothing here depends on that having happened yet.
# ---------------------------------------------------------------------------


def build_branch_wrapper(guard_source: str, *, old_source: str, new_source: str) -> str | None:
    """Combine two whole-function implementations of the same slot into one
    guard-dispatching wrapper: ``if <guard>: return _regime_old(...) else:
    return _regime_new(...)``. Uses *new_source*'s own signature for the public
    function (both sources are expected to share one -- they implement the same
    slot); renames the two bodies into private helpers called positionally.
    Returns ``None`` if either source fails to parse, has no top-level
    function, the two signatures have a different arity, or the guard itself
    fails to parse as an expression -- the caller falls back to whatever it
    would have done without the split (e.g. quarantining a case).
    """
    import copy

    try:
        old_module = ast.parse(textwrap.dedent(old_source))
        new_module = ast.parse(textwrap.dedent(new_source))
        guard_expr = ast.parse(guard_source, mode="eval").body
    except SyntaxError:
        return None
    old_fn = _first_function_def(old_module)
    new_fn = _first_function_def(new_module)
    if not isinstance(old_fn, ast.FunctionDef) or not isinstance(new_fn, ast.FunctionDef):
        return None
    if len(old_fn.args.args) != len(new_fn.args.args):
        return None

    fn_name = new_fn.name
    old_name, new_name = f"_{fn_name}__regime_old", f"_{fn_name}__regime_new"
    param_names = [a.arg for a in new_fn.args.args]

    def _call(target: str) -> ast.Call:
        args = [ast.Name(id=p, ctx=ast.Load()) for p in param_names]
        return ast.Call(func=ast.Name(id=target, ctx=ast.Load()), args=args, keywords=[])

    old_helper = ast.FunctionDef(
        name=old_name, args=copy.deepcopy(new_fn.args), body=old_fn.body,
        decorator_list=[], returns=None,
    )
    new_helper = ast.FunctionDef(
        name=new_name, args=copy.deepcopy(new_fn.args), body=new_fn.body,
        decorator_list=[], returns=None,
    )
    dispatcher = ast.FunctionDef(
        name=fn_name, args=copy.deepcopy(new_fn.args),
        body=[
            ast.If(test=guard_expr, body=[ast.Return(value=_call(old_name))], orelse=[]),
            ast.Return(value=_call(new_name)),
        ],
        decorator_list=[], returns=None,
    )
    module = ast.Module(body=[old_helper, new_helper, dispatcher], type_ignores=[])
    ast.fix_missing_locations(module)
    try:
        return ast.unparse(module)
    except Exception:
        return None


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
