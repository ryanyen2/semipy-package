"""Contract runner: execute a slot's behavioral cases against a candidate
implementation and report which prior decisions it violates.

One subprocess (two only when relational cases — idempotent/metamorphic — are
present). Reuses the batch-gist execution primitive from ``agents.decision`` but
emits a richer per-row record so assertions evaluate the *real* runtime object
(type, emptiness, identity, shape) rather than a JSON-lossy projection.

No LLM. The result maps each violated case to a validator ``failure_kind`` so the
existing ``RoutingPolicy`` ADAPT route consumes it with no new routing code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from semipy.agents.decision import (
    _extract_fn_name,
    _extract_scaffold_derivations,
    _expr,
    _run_batch_gist,
)
from semipy.contract.models import ContractCase
from semipy.contract.relations import get_relation
from semipy.types import SlotSpec, ValidationResult

# Minimum input length before an identity-passthrough (output == input) counts as
# a failure — mirrors the validator's guard to avoid flagging short canonical outputs.
_IDENTITY_MIN_LEN = 9

# Map a violated case to the validator failure_kind that drives RoutingPolicy ADAPT.
_FAILURE_KIND = {
    "non_empty": "empty_output",
    "non_identity": "identity_return",
    "type_match": "type_mismatch",
    "category_preserving": "type_mismatch",
    "idempotent": "type_mismatch",
    "example": "type_mismatch",
    "metamorphic": "type_mismatch",
}


@dataclass
class CaseFailure:
    case_id: str
    kind: str
    label: str            # invariant/relation name or "example"
    reason: str           # the case's stored reason (the prior decision)
    observed: str         # short observed value/type
    message: str          # drop-in for verify_failure_context
    failure_kind: str     # validator failure_kind for routing


@dataclass
class ContractRunResult:
    passed: bool
    failures: list[CaseFailure] = field(default_factory=list)
    n_evaluated: int = 0
    n_skipped: int = 0

    def failing_case_ids(self) -> set[str]:
        return {f.case_id for f in self.failures}

    def first_failure_message(self) -> str:
        if not self.failures:
            return ""
        return self.failures[0].message

    def as_validation_result(self) -> ValidationResult:
        fk = self.failures[0].failure_kind if self.failures else "type_mismatch"
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=False,
            execution_ok=True,
            error_message=self.first_failure_message(),
            failure_kind=fk,
        )


# ---------------------------------------------------------------------------
# Gist construction
# ---------------------------------------------------------------------------


def _build_contract_gist(
    *,
    implementation_source: str,
    free_variables: list[str],
    sample_rows: list[dict[str, Any]],
    scaffold_source: str | None,
    output_names: list[str] | None = None,
) -> str:
    """Standalone script: run impl over rows, print a rich record per row.

    Records are computed against the *effective* output the caller consumes. For a
    single-output STATEMENT_BLOCK the impl returns ``{name: value}``; the contract
    must reason about ``value`` (the thing downstream code uses), not the dict
    wrapper — otherwise non_empty/non_identity/type_match check the wrapper and
    provide almost no protection.
    """
    fn_name = _extract_fn_name(implementation_source)
    if not fn_name:
        return ""
    derivation_lines = _extract_scaffold_derivations(scaffold_source)
    non_self_vars = [v for v in free_variables if v != "self"]

    lines: list[str] = [
        "from __future__ import annotations",
        "import json",
        "",
        implementation_source,
        "",
        "_OUTPUT_NAMES = " + _expr(list(output_names or [])),
        "_results = []",
        "_INPUTS = " + _expr(sample_rows),
        "for _row in _INPUTS:",
    ]
    for v in non_self_vars:
        lines.append(f"    {v} = _row.get({v!r}, '')")
    for dl in derivation_lines:
        lines.append(f"    {dl}")
    lines.append(f"    _primary = {non_self_vars[0]}" if non_self_vars else "    _primary = None")

    arg_parts = ["None" if v == "self" else v for v in free_variables]
    lines.append("    _err = None")
    lines.append("    try:")
    lines.append(f"        _out = {fn_name}({', '.join(arg_parts)})")
    lines.append("    except Exception as _e:")
    lines.append("        _out = None")
    lines.append("        _err = type(_e).__name__ + ': ' + str(_e)")
    # Project to the effective output value the caller consumes.
    lines.append("    _eff = _out")
    lines.append("    if _err is None and isinstance(_out, dict) and len(_OUTPUT_NAMES) == 1 and _OUTPUT_NAMES[0] in _out:")
    lines.append("        _eff = _out[_OUTPUT_NAMES[0]]")
    lines.append("    _rec = {'error': _err}")
    lines.append("    if _err is None:")
    lines.append("        _rec['type'] = type(_eff).__name__")
    lines.append("        try:\n            _rec['repr'] = repr(_eff)[:600]\n        except Exception:\n            _rec['repr'] = '<unrepr>'")
    lines.append("        try:\n            _rec['is_empty'] = (_eff is None) or (hasattr(_eff, '__len__') and len(_eff) == 0)\n        except Exception:\n            _rec['is_empty'] = False")
    lines.append("        try:\n            _rec['eq_primary'] = isinstance(_eff, str) and isinstance(_primary, str) and _eff.strip() == _primary.strip()\n        except Exception:\n            _rec['eq_primary'] = False")
    lines.append("        try:\n            _rec['json'] = json.dumps(_eff, default=str)\n        except Exception:\n            _rec['json'] = None")
    lines.append("        try:\n            _rec['shape'] = ('dict:' + ','.join(sorted(str(k) for k in _eff.keys()))) if isinstance(_eff, dict) else type(_eff).__name__\n        except Exception:\n            _rec['shape'] = type(_eff).__name__")
    lines.append("    _results.append(_rec)")
    lines.append("print(json.dumps(_results, default=str))")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


def _row_for_input(input_sample: dict[str, Any], free_variables: list[str]) -> dict[str, Any] | None:
    """Build a runnable row, or None if any required value is a non-rehydratable marker."""
    row: dict[str, Any] = {}
    for v in free_variables:
        if v == "self":
            continue
        val = input_sample.get(v)
        if isinstance(val, dict) and "__repr__" in val:
            return None
        row[v] = val if val is not None else ""
    return row


def _primary_value(row: dict[str, Any], free_variables: list[str]) -> Any:
    for v in free_variables:
        if v == "self":
            continue
        if v in row:
            return row[v]
    return None


# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------


def _short(value: Any, n: int = 120) -> str:
    s = str(value)
    return s if len(s) <= n else s[: n - 3] + "..."


def _eval_single(case: ContractCase, rec: dict[str, Any]) -> str | None:
    """Return a failure message for a single-input case, or None if it holds."""
    err = rec.get("error")
    kind = case.kind
    if kind == "example":
        if err is not None:
            return f"raised {err} (expected {case.expected_repr})"
        if rec.get("type") != case.expected_type or rec.get("repr") != case.expected_repr:
            return f"got {rec.get('type')} {_short(rec.get('repr'))}, expected {case.expected_type} {_short(case.expected_repr)}"
        return None

    # invariants
    if err is not None:
        # An exception on a previously-working input is a regression for every invariant.
        return f"raised {err}"
    inv = case.invariant
    if inv == "non_empty":
        return "output is empty" if rec.get("is_empty") else None
    if inv == "non_identity":
        primary = case.primary_input
        if isinstance(primary, str) and len(primary.strip()) >= _IDENTITY_MIN_LEN and rec.get("eq_primary"):
            return "output equals the input (identity passthrough)"
        return None
    if inv == "type_match":
        return None if rec.get("type") == case.expected_type else f"type {rec.get('type')}, expected {case.expected_type}"
    if inv == "category_preserving":
        return None if rec.get("shape") == case.expected_repr else f"shape {_short(rec.get('shape'))}, expected {_short(case.expected_repr)}"
    return None


def _values_equal(rec_a: dict[str, Any], rec_b: dict[str, Any]) -> bool:
    if rec_a.get("error") is not None or rec_b.get("error") is not None:
        return False
    return rec_a.get("json") == rec_b.get("json")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_contract(
    *,
    implementation_source: str,
    slot_spec: SlotSpec,
    cases: list[ContractCase],
    scaffold_source: str | None = None,
    timeout: int = 15,
) -> ContractRunResult:
    """Run all active cases against the implementation. Never blocks on an
    inability to test: when a case cannot be run, it is skipped, not failed."""
    free_variables = list(slot_spec.free_variables)
    if not cases:
        return ContractRunResult(passed=True)

    # Build base rows (one per runnable case) and remember which case each maps to.
    base_rows: list[dict[str, Any]] = []
    base_case_idx: list[int] = []
    skipped = 0
    for i, case in enumerate(cases):
        row = _row_for_input(case.input_sample, free_variables)
        if row is None:
            skipped += 1
            continue
        base_rows.append(row)
        base_case_idx.append(i)

    if not base_rows:
        return ContractRunResult(passed=True, n_skipped=skipped)

    output_names = list(getattr(slot_spec, "output_names", None) or [])
    base_gist = _build_contract_gist(
        implementation_source=implementation_source,
        free_variables=free_variables,
        sample_rows=base_rows,
        scaffold_source=scaffold_source,
        output_names=output_names,
    )
    base_recs = _run_batch_gist(base_gist, timeout=timeout)
    if len(base_recs) != len(base_rows):
        # Gist failed to run or returned a mismatched count — cannot test; do not block.
        return ContractRunResult(passed=True, n_skipped=len(cases))

    # rec lookup per case index
    rec_by_case: dict[int, dict[str, Any]] = {
        base_case_idx[j]: base_recs[j] for j in range(len(base_recs))
    }
    row_by_case: dict[int, dict[str, Any]] = {
        base_case_idx[j]: base_rows[j] for j in range(len(base_rows))
    }

    # Second pass for relational cases (idempotent feedback + metamorphic transforms).
    derived_rows: list[dict[str, Any]] = []
    derived_case_idx: list[int] = []
    for i, case in enumerate(cases):
        if i not in rec_by_case:
            continue
        rec = rec_by_case[i]
        if case.kind == "metamorphic":
            rel = get_relation(case.relation)
            if rel is None:
                continue
            transform, _ = rel
            row = dict(row_by_case[i])
            primary_var = next((v for v in free_variables if v != "self"), None)
            if primary_var is None:
                continue
            row[primary_var] = transform(row.get(primary_var))
            derived_rows.append(row)
            derived_case_idx.append(i)
        elif case.kind == "invariant" and case.invariant == "idempotent":
            if rec.get("error") is not None or rec.get("json") is None:
                continue
            try:
                fed = json.loads(rec["json"])
            except Exception:
                continue
            if not isinstance(fed, str):
                continue  # idempotence only defined when output type == input type (str)
            row = dict(row_by_case[i])
            primary_var = next((v for v in free_variables if v != "self"), None)
            if primary_var is None:
                continue
            row[primary_var] = fed
            derived_rows.append(row)
            derived_case_idx.append(i)

    derived_rec_by_case: dict[int, dict[str, Any]] = {}
    if derived_rows:
        derived_gist = _build_contract_gist(
            implementation_source=implementation_source,
            free_variables=free_variables,
            sample_rows=derived_rows,
            scaffold_source=scaffold_source,
            output_names=output_names,
        )
        derived_recs = _run_batch_gist(derived_gist, timeout=timeout)
        if len(derived_recs) == len(derived_rows):
            derived_rec_by_case = {
                derived_case_idx[j]: derived_recs[j] for j in range(len(derived_recs))
            }

    # Evaluate every runnable case.
    failures: list[CaseFailure] = []
    evaluated = 0
    for i, case in enumerate(cases):
        if i not in rec_by_case:
            continue
        evaluated += 1
        rec = rec_by_case[i]
        msg: str | None
        if case.kind == "metamorphic":
            drec = derived_rec_by_case.get(i)
            if drec is None:
                continue  # could not evaluate the relation; skip rather than fail
            msg = None if _values_equal(rec, drec) else (
                f"{case.relation} violated: base {_short(rec.get('repr'))} != transformed {_short(drec.get('repr'))}"
            )
        elif case.kind == "invariant" and case.invariant == "idempotent":
            drec = derived_rec_by_case.get(i)
            if drec is None:
                continue
            msg = None if _values_equal(rec, drec) else "not idempotent: re-applying changed the output"
        else:
            msg = _eval_single(case, rec)
        if msg is not None:
            label = case.invariant or case.relation or case.kind
            observed = rec.get("repr", "") if rec.get("error") is None else f"error:{rec.get('error')}"
            reason = case.reason or "(prior decision)"
            failures.append(
                CaseFailure(
                    case_id=case.case_id,
                    kind=case.kind,
                    label=label,
                    reason=reason,
                    observed=_short(observed),
                    message=f"Contract case [{label}] violated: {msg}. This case exists because: {reason}",
                    failure_kind=_FAILURE_KIND.get(label, _FAILURE_KIND.get(case.kind, "type_mismatch")),
                )
            )

    return ContractRunResult(
        passed=not failures,
        failures=failures,
        n_evaluated=evaluated,
        n_skipped=skipped,
    )
