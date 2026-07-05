"""Trace replay + shallowest-failing-node localization (Phase 4, §3.2).

Wadler-Findler blame, generalized from whole-slot to node: given a failing
case (an input the current implementation gets wrong) and its tree, find the
node whose own, independently-replayed behavior on that input already
diverges from the expected output -- the frozen, well-typed surround is
never blamed, and a node this module cannot replay in isolation is blamed
whole rather than guessed into.

Scope, stated plainly (this is real, not decorative, but it is bounded): a
node is replayed by re-running its *own* leaf artifact(s) directly -- this
module does not run the tree through a general execution engine
(``anneal.py``, Phase 6). That makes replay exact for:

- MAP / FILTER whose iterable is a bare free variable (the common shape
  ``lower_source_to_tree`` actually produces for a root-level loop or
  comprehension) -- and on divergence, the specific element the leaf got
  wrong is reported (the germ for melt's rejuvenation / the search).
- BRANCH -- dispatch is exhaustive and mutually exclusive, so the branch's
  output *is* the matched arm's output; blame descends into that arm and
  keeps checking.

It conservatively refuses to localize past:
- FOLD (its accumulator's initial value lives in a sibling segment this
  module does not track), and
- OPAQUE / COMPOSE nodes whose iterable depends on a preceding segment's
  mutation.

Both fall back to "blame this node whole" -- correct and honest, not a
workaround, since a node with no discriminating case and no way to be
independently checked is not freeze/blame-eligible by construction (the
"no contract, no blame" floor from the thesis).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Optional

from semipy.kernel.tree import Node, NodeKind


@dataclass
class BlameResult:
    """The node blame could confirm is at fault (or gave up at), plus why."""

    node_id: str
    kind: str
    reason: str
    offending_input: Any = None
    replayed_output: Any = None
    expected_output: Any = None


def _compile_leaf(artifact: Optional[str]) -> Optional[Any]:
    if not artifact:
        return None
    ns: dict[str, Any] = {}
    try:
        exec(compile(artifact, "<leaf>", "exec"), ns)  # noqa: S102 -- our own stored source
    except Exception:
        return None
    return next((v for v in ns.values() if callable(v)), None)


def _resolve_iterable(node: Node, free_variables: dict[str, Any]) -> tuple[bool, Any]:
    """The node's recorded iterable expression, evaluated against the case's
    free-variable bindings. Only trusted when every name it loads is a free
    variable -- anything else may depend on a preceding segment's mutation,
    which this module does not track."""
    expr = node.meta.get("iterable")
    if not expr:
        return False, None
    try:
        parsed = ast.parse(expr, mode="eval")
    except Exception:
        return False, None
    names = {n.id for n in ast.walk(parsed) if isinstance(n, ast.Name)}
    if not names or not names.issubset(free_variables.keys()):
        return False, None
    try:
        return True, eval(compile(parsed, "<iterable>", "eval"), {}, dict(free_variables))  # noqa: S307
    except Exception:
        return False, None


def _matched_arm(node: Node, free_variables: dict[str, Any]) -> Optional[Node]:
    for guard, child in zip(node.guards, node.children):
        if guard.is_fallback:
            return child
        try:
            if eval(compile(guard.predicate_source, "<guard>", "eval"), {}, dict(free_variables)):  # noqa: S307
                return child
        except Exception:
            continue
    return None


def _return_target_name(artifact: Optional[str]) -> Optional[str]:
    """If *artifact* parses to a function whose sole statement is
    ``return <name>``, return that name; else None."""
    if not artifact:
        return None
    try:
        module = ast.parse(artifact)
    except SyntaxError:
        return None
    fn = next((n for n in module.body if isinstance(n, ast.FunctionDef)), None)
    if fn is None or len(fn.body) != 1:
        return None
    stmt = fn.body[0]
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
        return stmt.value.id
    return None


def _accumulator_passthrough_child(node: Node) -> Optional[Node]:
    """If *node* (a COMPOSE) is exactly ``[..., combinator, ..., return <acc>]``
    -- a single combinator child whose accumulator the final segment returns
    unchanged, and no other combinator among the siblings -- that combinator's
    own output *is* the compose's final output, so blame may safely descend
    into it. This is the common real shape (``out = []`` / a loop / ``return
    out``), not just the degenerate case where the combinator is the whole
    function body. Anything else (ambiguous data flow) returns None.
    """
    combinators = [
        c for c in node.children
        if c.kind in (NodeKind.MAP, NodeKind.FILTER, NodeKind.FOLD, NodeKind.BRANCH)
    ]
    if len(combinators) != 1:
        return None
    combinator = combinators[0]
    acc_name = combinator.meta.get("accumulator")
    if not acc_name:
        return None
    last = node.children[-1]
    if last.kind != NodeKind.OPAQUE:
        return None
    if _return_target_name(last.artifact) != acc_name:
        return None
    return combinator


def _first_divergent_element(kind: NodeKind, iterable: Any, replayed: list, expected: Any) -> Any:
    if not isinstance(expected, list):
        return None
    if kind == NodeKind.MAP:
        for el, actual, exp in zip(iterable, replayed, expected):
            if actual != exp:
                return el
        return None
    if kind == NodeKind.FILTER:
        for el in iterable:
            if (el in replayed) != (el in expected):
                return el
    return None


def blame(tree: Node, *, free_variables: dict[str, Any], expected_output: Any) -> BlameResult:
    """Shallowest-failing-node localization for one failing case.

    Descends from the root: a BRANCH recurses into whichever arm actually
    matched (its output *is* the branch's output, so this is exact, not a
    guess); a COMPOSE that is a single combinator with its accumulator
    returned unchanged (the common ``out = []`` / loop / ``return out``
    shape) descends into that combinator, since its output *is* the
    compose's; a MAP/FILTER is replayed and, on divergence, reports the
    specific element its leaf got wrong. Anything else (FOLD, OPAQUE, an
    ambiguous COMPOSE, or a MAP/FILTER whose iterable isn't a bare free
    variable) stops the descent and blames the current node whole.
    """
    node = tree
    while True:
        if node.kind == NodeKind.BRANCH:
            arm = _matched_arm(node, free_variables)
            if arm is None:
                return BlameResult(
                    node.node_id, node.kind.value,
                    "no guard matched (or a guard failed to evaluate); blaming the branch node whole",
                )
            node = arm
            continue

        if node.kind == NodeKind.COMPOSE:
            child = _accumulator_passthrough_child(node)
            if child is None:
                return BlameResult(
                    node.node_id, node.kind.value,
                    "compose has ambiguous or unverifiable data flow (not a single "
                    "accumulator passed through unchanged); blaming it whole",
                )
            node = child
            continue

        if node.kind in (NodeKind.MAP, NodeKind.FILTER):
            leaf = _compile_leaf(node.children[0].artifact) if node.children else None
            ok, iterable = _resolve_iterable(node, free_variables)
            if not (leaf and ok):
                return BlameResult(
                    node.node_id, node.kind.value,
                    "out of scope for isolated replay (iterable depends on prior state, "
                    "or the leaf did not compile); blaming it whole",
                )
            try:
                if node.kind == NodeKind.MAP:
                    value = [leaf(el) for el in iterable]
                else:
                    value = [el for el in iterable if leaf(el)]
            except Exception as e:
                return BlameResult(node.node_id, node.kind.value, f"leaf raised during replay: {e}")
            if value == expected_output:
                return BlameResult(
                    node.node_id, node.kind.value,
                    "node's independently-replayed output matches the expected output; "
                    "fault is not under this node",
                    replayed_output=value, expected_output=expected_output,
                )
            offending = _first_divergent_element(node.kind, iterable, value, expected_output)
            return BlameResult(
                node.node_id, node.kind.value,
                "leaf's per-element output diverges from the expected output at the reported input",
                offending_input=offending, replayed_output=value, expected_output=expected_output,
            )

        return BlameResult(
            node.node_id, node.kind.value,
            "node kind is not independently replayable without a full execution engine "
            "(FOLD/OPAQUE/COMPOSE); blaming it whole",
        )


def locality_metric(tree: Node, blamed_node_id: str) -> float:
    """regenerated-region size / whole-tree size, in node count.

    The point of per-node blame over whole-slot regeneration: a blame that
    localizes deep in the tree has a small numerator here. 1.0 (regenerate
    everything) when the blamed node is the root or wasn't found.
    """
    total = sum(1 for _ in tree.walk())
    if total == 0:
        return 1.0
    for n in tree.walk():
        if n.node_id == blamed_node_id:
            return sum(1 for _ in n.walk()) / total
    return 1.0
