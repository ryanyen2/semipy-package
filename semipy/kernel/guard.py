"""The regime-guard DSL: a closed typed-predicate fragment + compiler (§3.3).

Today's guards (``kernel.tree.Guard.predicate_source``) are just captured
source text -- descriptive, never executed. This module is the DSL §3.3
says does not exist yet: comparisons and null/empty/shape tests over a
node's typed inputs, closed (no arbitrary calls, no attribute side effects,
no imports), with a compiler that *rejects* anything outside the grammar --
a guard that does not compile keeps the node molten rather than dispatching
on an unverified predicate.

Grammar (all expressions, recursively):
- comparisons: ``a <op> b`` for op in ==, !=, <, <=, >, >=, is, is not,
  in, not in (chained comparisons like ``0 <= x < 10`` included)
- boolean combinations: ``and`` / ``or`` / ``not``
- values: a name, a literal, a dotted attribute chain on a name
  (``msg.kind``), a constant-keyed subscript (``row["status"]``), a signed
  numeric literal, a tuple of such (for ``isinstance``'s second argument),
  or a call to exactly one of ``isinstance``, ``len``, ``type``
- a bare value on its own (``if labels:``) is a valid predicate (truthiness)

Evaluation additionally locks ``__builtins__`` down to just those three
names -- defense in depth: the grammar already can't reach anything else,
but a guard's source is LLM-proposed (the same trust tier as generated
implementations, not a new boundary, but worth not relying on the parser
alone).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

_ALLOWED_COMPARE_OPS = (
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
)
_ALLOWED_CALL_NAMES = ("isinstance", "len", "type")
_SAFE_BUILTINS = {
    "isinstance": isinstance, "len": len, "type": type,
    # isinstance/type's second argument is commonly a builtin type name
    # (`isinstance(x, int)`); these are values, not callables the grammar
    # would otherwise let through, so exposing them is not an escalation.
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "tuple": tuple, "set": set,
    "frozenset": frozenset, "bytes": bytes,
}


def _is_allowed_value(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        # Reject dunder attributes: `x.__class__.__mro__`, `x.__globals__` etc.
        # are the standard route from a bare value to object internals, which the
        # grammar's "no attribute side effects, no internals" promise forbids.
        if node.attr.startswith("__"):
            return False
        return _is_allowed_value(node.value)
    if isinstance(node, ast.Subscript):
        return _is_allowed_value(node.value) and isinstance(node.slice, ast.Constant)
    if isinstance(node, ast.Tuple):
        return all(_is_allowed_value(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_allowed_value(node.operand)
    if isinstance(node, ast.Call):
        return _is_allowed_call(node)
    return False


def _is_allowed_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALL_NAMES:
        return False
    if node.keywords:
        return False
    return all(_is_allowed_value(a) for a in node.args)


def _is_allowed_predicate(node: ast.expr) -> bool:
    if isinstance(node, ast.Compare):
        if not all(isinstance(op, _ALLOWED_COMPARE_OPS) for op in node.ops):
            return False
        return _is_allowed_value(node.left) and all(_is_allowed_value(c) for c in node.comparators)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        return all(_is_allowed_predicate(v) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_allowed_predicate(node.operand)
    return _is_allowed_value(node)


@dataclass(frozen=True)
class CompiledGuard:
    """A validated, directly-evaluable guard. Never constructed directly --
    always via ``compile_guard``, which is where the grammar is enforced."""

    predicate_source: str
    _code: Any = field(repr=False, compare=False)

    def evaluate(self, free_variables: dict[str, Any]) -> bool:
        try:
            return bool(eval(self._code, {"__builtins__": _SAFE_BUILTINS}, dict(free_variables)))  # noqa: S307
        except Exception:
            return False


def compile_guard(predicate_source: str) -> Optional[CompiledGuard]:
    """Compile *predicate_source* into the closed DSL, or ``None`` to reject.

    Rejection covers anything outside the grammar: a syntax error, a call to
    something other than isinstance/len/type, attribute access with a
    non-constant subscript, or any other unrestricted expression.
    """
    try:
        parsed = ast.parse(predicate_source, mode="eval")
    except SyntaxError:
        return None
    if not _is_allowed_predicate(parsed.body):
        return None
    try:
        code = compile(parsed, "<guard>", "eval")
    except Exception:
        return None
    return CompiledGuard(predicate_source=predicate_source, _code=code)


def dispatch(guards: Sequence[CompiledGuard], free_variables: dict[str, Any]) -> Optional[int]:
    """Runtime guard dispatch ahead of tree execution (§3.3): the index of
    the first guard that matches this input, or ``None`` if none do."""
    for i, guard in enumerate(guards):
        if guard.evaluate(free_variables):
            return i
    return None
