"""Fixed registry of metamorphic relations.

Each relation names a data-agnostic input transformation and the output relation
that must hold between the original and transformed runs. Relations are the
no-oracle way to assert behavior: we do not know the *correct* output, but we
know how the output must (not) change when the input is transformed in a
meaning-preserving way. The registry is intentionally small and closed — no
per-dataset logic, nothing case-sensitive (CLAUDE.md case-independence rule).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, get_args, get_origin

# Output relation kinds evaluated by the runner.
#   "equal"     : transformed output must equal the original output
#   "unchanged" : alias of "equal" (kept for readability of intent)
OutputRelation = str


def _t_whitespace(value: Any) -> Any:
    """Pad with surrounding whitespace; a robust parser should ignore it."""
    if isinstance(value, str):
        return "  " + value + "  "
    return value


def _t_trailing_newline(value: Any) -> Any:
    """Append a trailing newline; should not change a line-oriented result."""
    if isinstance(value, str):
        return value + "\n"
    return value


def _t_dict_key_order_reversed(value: Any) -> Any:
    """Reverse a dict's key insertion order (same keys and values, same set --
    only iteration order differs). A function that reads fields by name, not
    by iteration position, must produce the same output either way."""
    if isinstance(value, dict) and len(value) > 1:
        return dict(reversed(list(value.items())))
    return value


def _t_list_reversed(value: Any) -> Any:
    """Reverse a list's element order. Only meaningful for a slot the proposer
    judges order-INSENSITIVE (an aggregate, a set-like lookup) -- proposing it
    for an order-sensitive slot (sort, "first match", concatenation) is a
    proposer error, not something this transform can detect on its own."""
    if isinstance(value, list) and len(value) > 1:
        return list(reversed(value))
    return value


_REGISTRY: dict[str, dict[str, Any]] = {
    "whitespace_invariance": {"transform": _t_whitespace, "relation": "equal"},
    "trailing_newline_invariance": {"transform": _t_trailing_newline, "relation": "equal"},
    "dict_key_order_invariance": {"transform": _t_dict_key_order_reversed, "relation": "equal"},
    "list_permutation_invariance": {"transform": _t_list_reversed, "relation": "equal"},
}


def relation_names() -> tuple[str, ...]:
    return tuple(_REGISTRY.keys())


def get_relation(name: str) -> tuple[Callable[[Any], Any], OutputRelation] | None:
    entry = _REGISTRY.get(name)
    if entry is None:
        return None
    return entry["transform"], entry["relation"]


def is_relation_nonvacuous(name: str, value: Any) -> bool:
    """True iff transforming *value* under relation *name* actually perturbs
    it (a differently-shaped or differently-ordered input), as opposed to
    returning it unchanged because the value's shape doesn't match what this
    relation transforms (e.g. a dict-shape relation applied to a string, or a
    1-element collection with nothing to reorder). A vacuous relation still
    "passes" every time -- trivially, since the input never actually changed --
    so it carries no evidence and must not count toward a freeze-eligibility
    floor that requires a genuine metamorphic check.
    """
    rel = get_relation(name)
    if rel is None:
        return False
    transform, _ = rel
    try:
        # repr(), not ==: a dict-key-order reversal is == to the original (dict
        # equality ignores insertion order) but is a genuinely different input
        # to anything order-sensitive (json.dumps without sort_keys, next(iter(d)),
        # list.pop(0)) -- exactly the kind of divergence this is meant to catch.
        return repr(transform(value)) != repr(value)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Containment relation for extractor-category slots (R20, U11).
#
# A different *kind* of typed relation from the metamorphic transforms above.
# Those perturb the input and compare two runs; this one is a checkable predicate
# over a single (input, output) pair: every value the slot extracts must occur in
# the input text, modulo a small declared set of normalizers. It carries no
# oracle and no labels -- only the text at hand -- so it ships as an extractor
# floor and evaluates at the consumer site with no page snapshot present (D3).
# ---------------------------------------------------------------------------

# The closed, declared normalizer vocabulary. Bare containment is too strict for
# real extractors (a parsed price ``1299.0`` never occurs verbatim in ``"$1,299"``);
# each normalizer relaxes the match in one auditable, label-free way. ``numeric``
# and ``date_reformat`` are opt-in -- omitted by default so a slot that declares
# them says so explicitly.
CONTAINMENT_NORMALIZERS: tuple[str, ...] = (
    "case_fold",           # match case-insensitively
    "whitespace_collapse", # collapse whitespace runs before matching
    "numeric",             # a parsed number traces to any numeric token ("$1,299" -> 1299.0)
    "date_reformat",       # a reformatted date traces to its parts in the text
)
DEFAULT_CONTAINMENT_NORMALIZERS: tuple[str, ...] = ("case_fold", "whitespace_collapse")

_MONTHS: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%d %B %Y", "%d %b %Y",
)

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


class ContainmentRegistrationError(ValueError):
    """The containment relation was registered for a non-extractor-category slot."""


def _short(value: Any, n: int = 120) -> str:
    s = str(value)
    return s if len(s) <= n else s[: n - 3] + "..."


def _collapse_ws(s: str) -> str:
    return " ".join(s.split())


def _numeric_value(v: Any) -> Optional[float]:
    """Parse *v* as a number, or None. Booleans are not numbers here (they are
    derived flags, not extracted quantities)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[^0-9.\-]", "", v)
        if not re.search(r"\d", cleaned):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _numeric_tokens(text: str) -> list[float]:
    """Every numeric token in *text*, thousands separators removed ("1,299" -> 1299.0)."""
    out: list[float] = []
    for m in _NUM_RE.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def _numeric_contained(value: Any, text: str) -> bool:
    nv = _numeric_value(value)
    if nv is None:
        return False
    return any(math.isclose(nv, tok) for tok in _numeric_tokens(text))


def _parse_date(s: str) -> Optional[_dt.date]:
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _date_contained(sval: str, text: str) -> bool:
    """True if *sval* parses as a date whose year, day, and month all trace to
    *text* (the month as a number or as a name/abbreviation)."""
    d = _parse_date(sval)
    if d is None:
        return False
    ints = {int(x) for x in _numeric_tokens(text) if x.is_integer()}
    low = text.casefold()
    month_ok = d.month in ints or any(name in low for name, mo in _MONTHS.items() if mo == d.month)
    return d.year in ints and d.day in ints and month_ok


def _text_contains_value(text: str, value: Any, normalizers: Sequence[str]) -> bool:
    """True if *value* occurs in *text* under the declared *normalizers*."""
    sval = value if isinstance(value, str) else str(value)
    if sval == "":
        return True
    ntext, nval = text, sval
    if "case_fold" in normalizers:
        ntext, nval = ntext.casefold(), nval.casefold()
    if "whitespace_collapse" in normalizers:
        ntext, nval = _collapse_ws(ntext), _collapse_ws(nval)
    if nval and nval in ntext:
        return True
    if "numeric" in normalizers and _numeric_contained(value, text):
        return True
    if "date_reformat" in normalizers and _date_contained(sval, text):
        return True
    return False


def _child_path(prefix: str, key: Any) -> str:
    return str(key) if prefix == "" else f"{prefix}.{key}"


def _iter_field_values(output: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten an extractor output into ``(field_path, leaf_value)`` pairs.

    Records (dataclass / pydantic model / dict / TypedDict) and lists are walked
    to their leaves so a hallucinated nested field can be named; a bare ``str``
    output is a single leaf named ``<output>``.
    """
    if output is None:
        return []
    if dataclasses.is_dataclass(output) and not isinstance(output, type):
        output = dataclasses.asdict(output)
    else:
        dump = getattr(output, "model_dump", None)
        if callable(dump):
            try:
                output = dump()
            except Exception:
                pass
    if isinstance(output, Mapping):
        leaves: list[tuple[str, Any]] = []
        for k, v in output.items():
            leaves.extend(_iter_field_values(v, _child_path(prefix, k)))
        return leaves
    if isinstance(output, (list, tuple)):
        leaves = []
        for i, v in enumerate(output):
            leaves.extend(_iter_field_values(v, f"{prefix}[{i}]"))
        return leaves
    return [(prefix or "<output>", output)]


def _is_text_type(t: Any) -> bool:
    return t is str


def _is_pydantic_model(t: Any) -> bool:
    try:
        return isinstance(t, type) and any(getattr(b, "__name__", "") == "BaseModel" for b in t.__mro__)
    except Exception:
        return False


def _is_record_type(t: Any) -> bool:
    if t is None or t is type(None):
        return False
    if t is dict:
        return True
    origin = get_origin(t)
    if origin is dict or origin is Mapping:
        return True
    # TypedDict classes expose annotations + a totality flag but are not plain dict subclasses.
    if hasattr(t, "__annotations__") and hasattr(t, "__total__"):
        return True
    if isinstance(t, type):
        try:
            if dataclasses.is_dataclass(t) or issubclass(t, dict):
                return True
        except TypeError:
            pass
        if _is_pydantic_model(t):
            return True
    if origin in (list, tuple):
        args = [a for a in get_args(t) if a is not Ellipsis]
        return any(_is_str_or_record_type(a) for a in args)
    return False


def _is_str_or_record_type(t: Any) -> bool:
    return _is_text_type(t) or _is_record_type(t)


@dataclass
class ContainmentFailure:
    field: str            # dotted path of the output field that failed containment
    value: str            # short repr of the offending value
    message: str          # human-readable, names the field


@dataclass
class ContainmentResult:
    holds: bool
    failures: list[ContainmentFailure] = field(default_factory=list)

    def message(self) -> str:
        return "; ".join(f.message for f in self.failures)

    def failing_fields(self) -> list[str]:
        return [f.field for f in self.failures]


@dataclass(frozen=True)
class ContainmentRelation:
    """"Every extracted value occurs in the input text modulo declared normalizers."

    Registered for extractor-category slots via ``for_slot`` (which rejects a
    non-extractor slot). Serializes to a plain dict (``to_dict``/``from_dict``) so
    it travels in a shipped floor, and ``evaluate`` checks it against a single
    (input, output) pair using only the current input text -- no snapshot (D3).
    """

    text_field: str
    normalizers: tuple[str, ...] = DEFAULT_CONTAINMENT_NORMALIZERS

    @classmethod
    def for_slot(
        cls,
        *,
        output_type: Any,
        input_types: Mapping[str, Any],
        text_field: Optional[str] = None,
        normalizers: Sequence[str] = DEFAULT_CONTAINMENT_NORMALIZERS,
    ) -> "ContainmentRelation":
        """Build the relation after checking the slot is extractor-category.

        The check is structural and deterministic (no LLM): it reads the slot's
        type layer -- ``output_type`` and ``input_types`` (free-variable name ->
        type) -- and requires a str/record output plus a text (str) input field.
        A slot that is scalar-only or has no text input is rejected here rather
        than silently accepted.
        """
        norm = tuple(normalizers)
        unknown = [n for n in norm if n not in CONTAINMENT_NORMALIZERS]
        if unknown:
            raise ContainmentRegistrationError(
                f"unknown normalizer(s) {unknown}; the declared set is {list(CONTAINMENT_NORMALIZERS)}"
            )

        types = dict(input_types or {})
        text_candidates = [name for name, t in types.items() if _is_text_type(t)]
        if text_field is not None:
            if text_field not in types:
                raise ContainmentRegistrationError(
                    f"text_field {text_field!r} is not an input of this slot (inputs: {sorted(types)})"
                )
            if not _is_text_type(types[text_field]):
                raise ContainmentRegistrationError(
                    f"text_field {text_field!r} is typed {types[text_field]!r}, not text (str); the "
                    "containment relation needs a text input to trace extracted values back to"
                )
            chosen = text_field
        elif not text_candidates:
            raise ContainmentRegistrationError(
                "containment relation requires a text (str) input field, but this slot has no str-typed "
                "input; it is not an extractor-category slot"
            )
        elif len(text_candidates) > 1:
            raise ContainmentRegistrationError(
                f"slot has multiple text inputs {text_candidates}; pass text_field=... to name the one "
                "containment should trace against"
            )
        else:
            chosen = text_candidates[0]

        if not _is_str_or_record_type(output_type):
            raise ContainmentRegistrationError(
                f"containment relation applies to str/record-output extractor slots, but this slot's "
                f"output type is {output_type!r} (scalar-only or unstructured); it is not an "
                "extractor-category slot"
            )
        return cls(text_field=chosen, normalizers=norm)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "containment", "text_field": self.text_field, "normalizers": list(self.normalizers)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContainmentRelation":
        if not isinstance(d, dict):
            raise ContainmentRegistrationError("containment relation must be a JSON object")
        norms = d.get("normalizers")
        return cls(
            text_field=str(d.get("text_field", "") or ""),
            normalizers=tuple(str(n) for n in norms) if norms else DEFAULT_CONTAINMENT_NORMALIZERS,
        )

    def evaluate(self, input_sample: Mapping[str, Any], output: Any) -> ContainmentResult:
        """Check that every value in *output* traces to the current input text.

        Uses only ``input_sample[self.text_field]`` -- no snapshot -- so a shipped
        floor evaluates this at the consumer site. None and boolean leaves (derived
        flags, not extracted spans) are skipped; str and numeric leaves are checked.
        Failures name the offending field.
        """
        raw = (input_sample or {}).get(self.text_field)
        text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        failures: list[ContainmentFailure] = []
        for path, value in _iter_field_values(output):
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            if not _text_contains_value(text, value, self.normalizers):
                failures.append(
                    ContainmentFailure(
                        field=path,
                        value=_short(repr(value)),
                        message=(
                            f"output field {path!r} = {_short(repr(value))} does not occur in the "
                            f"{self.text_field!r} text (normalizers: {list(self.normalizers)})"
                        ),
                    )
                )
        return ContainmentResult(holds=not failures, failures=failures)
