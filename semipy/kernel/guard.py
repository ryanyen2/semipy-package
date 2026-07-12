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
import hashlib
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


# ---------------------------------------------------------------------------
# Scope predicates (U2, R3): the profile-predicate extension of the guard DSL.
#
# Where a regime guard (above) dispatches on a node's *typed inputs*, a scope
# predicate tests an input's *structural profile* (kernel.runtime_fingerprint.
# compute_input_profile): column set/type tests, per-column null-rate and numeric
# range bands, and length bands. It is minted at commit/freeze from the evidence
# ledger's profiles (synthesize_scope) and replaces fingerprint equality as the
# reuse fast path's membership test. A ScopePredicate is serializable and carries
# a stable ``predicate_id`` so other modules (e.g. the contract surface) can hold
# a reference to it, rather than an in-memory closure.
# ---------------------------------------------------------------------------


def _profile_length(profile: dict[str, Any]) -> Optional[int]:
    if "n_rows" in profile:
        return profile.get("n_rows")
    return profile.get("len")


def _profile_range(profile: dict[str, Any], column: Optional[str]) -> Optional[list]:
    if column is None:
        return profile.get("range")
    return (profile.get("column_ranges") or {}).get(column)


def _profile_null_rate(profile: dict[str, Any], column: Optional[str]) -> Optional[float]:
    if column is None:
        return profile.get("null_rate")
    return (profile.get("column_null_rates") or {}).get(column)


@dataclass(frozen=True)
class ScopeConjunct:
    """One atom of a scope predicate over a single free variable's profile.

    ``kind`` selects the test; ``var`` names the free variable it applies to;
    ``params`` carries the minted bounds. Every conjunct renders to a
    human-readable ``source`` (used to name the violated conjunct in a deopt
    event) and evaluates against a value profile.
    """

    var: str
    kind: str  # "columns" | "column_kinds" | "range" | "null_rate" | "length"
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def source(self) -> str:
        v = self.var
        if self.kind == "columns":
            required = sorted(self.params.get("required", []))
            allowed = sorted(self.params.get("allowed", []))
            if required == allowed:
                return f"{v}.columns == {required}"
            return f"{required} <= {v}.columns <= {allowed}"
        if self.kind == "column_kinds":
            kinds = dict(sorted((self.params.get("kinds") or {}).items()))
            return f"{v}.column_kinds == {kinds}"
        if self.kind == "range":
            column = self.params.get("column")
            target = f"{v}.{column}" if column else v
            return f"{self.params.get('lo')} <= {target} <= {self.params.get('hi')}"
        if self.kind == "null_rate":
            column = self.params.get("column")
            target = f"{v}.{column}" if column else v
            return f"null_rate({target}) <= {self.params.get('max')}"
        if self.kind == "length":
            return f"{self.params.get('lo')} <= len({v}) <= {self.params.get('hi')}"
        return "True"

    def evaluate(self, profile: Optional[dict[str, Any]]) -> bool:
        if profile is None:
            return False
        if self.kind == "columns":
            cols = set(profile.get("columns", []) or [])
            required = set(self.params.get("required", []))
            allowed = set(self.params.get("allowed", []))
            return required <= cols <= allowed
        if self.kind == "column_kinds":
            observed = profile.get("column_kinds") or {}
            for col, kind in (self.params.get("kinds") or {}).items():
                if observed.get(col) != kind:
                    return False
            return True
        if self.kind == "range":
            rng = _profile_range(profile, self.params.get("column"))
            if not rng:
                # No range evidence for this input -> a sibling columns/kinds
                # conjunct owns that violation; a range conjunct does not double-fault.
                return True
            return self.params.get("lo") <= rng[0] and rng[1] <= self.params.get("hi")
        if self.kind == "null_rate":
            rate = _profile_null_rate(profile, self.params.get("column"))
            if rate is None:
                return True
            return rate <= self.params.get("max", 1.0) + 1e-9
        if self.kind == "length":
            n = _profile_length(profile)
            if n is None:
                return True
            return self.params.get("lo", 0) <= n <= self.params.get("hi", n)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"var": self.var, "kind": self.kind, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScopeConjunct":
        return cls(var=d.get("var", ""), kind=d.get("kind", ""), params=dict(d.get("params") or {}))


@dataclass(frozen=True)
class ScopeCheck:
    """The verdict of testing one input profile against a scope predicate."""

    in_scope: bool
    violated: Optional[str] = None
    violated_var: Optional[str] = None


@dataclass(frozen=True)
class ScopePredicate:
    """A compiled, serializable conjunction of profile predicates -- the reuse
    fast path's membership test (R3). Identified by ``predicate_id`` (a stable
    hash of its source) so other modules can reference it."""

    conjuncts: tuple[ScopeConjunct, ...] = ()

    def is_empty(self) -> bool:
        return not self.conjuncts

    @property
    def source(self) -> str:
        if not self.conjuncts:
            return "True"
        return " and ".join(c.source for c in self.conjuncts)

    @property
    def predicate_id(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()[:16]

    def check(self, profiles: dict[str, dict[str, Any]]) -> ScopeCheck:
        """Membership test: every conjunct must hold against its variable's
        profile. Returns the first violated conjunct's source (for the deopt
        event), or an in-scope verdict when all hold."""
        for conjunct in self.conjuncts:
            if not conjunct.evaluate(profiles.get(conjunct.var)):
                return ScopeCheck(in_scope=False, violated=conjunct.source, violated_var=conjunct.var)
        return ScopeCheck(in_scope=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conjuncts": [c.to_dict() for c in self.conjuncts],
            "source": self.source,
            "predicate_id": self.predicate_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScopePredicate":
        raw = (d or {}).get("conjuncts", [])
        return cls(tuple(ScopeConjunct.from_dict(x) for x in raw))


def _length_conjunct(var: str, lengths: Sequence[Any]) -> list[ScopeConjunct]:
    lens = [n for n in lengths if isinstance(n, int)]
    if not lens:
        return []
    return [ScopeConjunct(var, "length", {"lo": min(lens), "hi": max(lens)})]


def _frame_conjuncts(var: str, var_profiles: Sequence[dict[str, Any]]) -> list[ScopeConjunct]:
    out: list[ScopeConjunct] = []
    colsets = [set(vp.get("columns", []) or []) for vp in var_profiles]
    required = set.intersection(*colsets) if colsets else set()
    allowed = set.union(*colsets) if colsets else set()
    out.append(ScopeConjunct(var, "columns", {"required": sorted(required), "allowed": sorted(allowed)}))

    # One column_kinds conjunct covering every common column with a stable kind
    # (MDL-flavored: a single type-test conjunct, not one per column).
    stable_kinds: dict[str, str] = {}
    for col in sorted(required):
        kinds = {(vp.get("column_kinds") or {}).get(col) for vp in var_profiles}
        if len(kinds) == 1 and None not in kinds:
            stable_kinds[col] = kinds.pop()
    if stable_kinds:
        out.append(ScopeConjunct(var, "column_kinds", {"kinds": stable_kinds}))

    # Numeric range band per common numeric column -- the coarsening lever: the
    # band spans [min over profiles, max over profiles], so two profiles with a
    # column in [0,10] and [20,30] admit a third at 15 (not a point-set union).
    for col in sorted(required):
        rngs = [(vp.get("column_ranges") or {}).get(col) for vp in var_profiles]
        if any(r is None for r in rngs):
            continue
        out.append(ScopeConjunct(var, "range", {"column": col, "lo": min(r[0] for r in rngs), "hi": max(r[1] for r in rngs)}))

    # Null-rate band per common column, only when nulls were actually observed
    # (an unobserved null constraint would be needlessly tight).
    for col in sorted(required):
        rates = [(vp.get("column_null_rates") or {}).get(col) for vp in var_profiles]
        rates = [r for r in rates if r is not None]
        if rates and max(rates) > 0.0:
            out.append(ScopeConjunct(var, "null_rate", {"column": col, "max": max(rates)}))
    return out


def _series_conjuncts(var: str, var_profiles: Sequence[dict[str, Any]]) -> list[ScopeConjunct]:
    out = _length_conjunct(var, [vp.get("len") for vp in var_profiles])
    kinds = {vp.get("dtype_kind") for vp in var_profiles}
    if kinds == {"numeric"}:
        rngs = [vp.get("range") for vp in var_profiles]
        if all(r is not None for r in rngs):
            out.append(ScopeConjunct(var, "range", {"column": None, "lo": min(r[0] for r in rngs), "hi": max(r[1] for r in rngs)}))
    rates = [vp.get("null_rate") for vp in var_profiles]
    rates = [r for r in rates if r is not None]
    if rates and max(rates) > 0.0:
        out.append(ScopeConjunct(var, "null_rate", {"column": None, "max": max(rates)}))
    return out


def synthesize_scope(profiles: Sequence[dict[str, dict[str, Any]]]) -> ScopePredicate:
    """Mint the coarsest scope predicate consistent with every input profile in
    the evidence ledger. Only free variables present as the *same kind* across
    all profiles get conjuncts; scalars (and mixed-kind variables) contribute
    none. MDL-flavored: numeric range/length *bands* (min..max over the profiles)
    rather than point-set unions, so an over-tight scope is the exception rather
    than the rule."""
    profiles = [p for p in profiles if p]
    if not profiles:
        return ScopePredicate(())
    common_vars = set(profiles[0])
    for p in profiles[1:]:
        common_vars &= set(p)
    conjuncts: list[ScopeConjunct] = []
    for var in sorted(common_vars):
        var_profiles = [p[var] for p in profiles]
        kinds = {vp.get("kind") for vp in var_profiles}
        if len(kinds) != 1:
            continue
        kind = kinds.pop()
        if kind == "frame":
            conjuncts.extend(_frame_conjuncts(var, var_profiles))
        elif kind == "series":
            conjuncts.extend(_series_conjuncts(var, var_profiles))
        elif kind == "collection":
            conjuncts.extend(_length_conjunct(var, [vp.get("len") for vp in var_profiles]))
        # scalar / mapping / none / other -> no conjuncts (keeps scalar slots as-is)
    return ScopePredicate(tuple(conjuncts))
