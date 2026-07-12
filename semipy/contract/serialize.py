"""(De)serialization for SlotContract <-> plain JSON-safe dicts.

Kept separate from ``semipy.store`` so the history/persistence layers stay
dependency-light; ``store.py`` lazy-imports these helpers. Portal JSON is dumped
without a ``default=`` encoder, so every value persisted here must be JSON-safe;
``to_json_safe`` enforces that (non-encodable values become ``{"__repr__": ...}``).
"""
from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from semipy.contract.models import ContractCase, SlotContract

_JSON_SCALARS = (str, int, float, bool, type(None))


def dumps_pretty(data: dict[str, Any]) -> str:
    """Pretty-print a JSON-safe dict per KTD-6: two-space indent and sorted keys
    so serialized contract/surface files are human-readable and diff cleanly
    (one entry per line). ``data`` must already be JSON-safe (see ``to_json_safe``)."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def to_json_safe(value: Any) -> Any:
    """Recursively coerce a value into a JSON-serialisable form.

    Scalars and JSON containers pass through; anything else is preserved as
    ``{"__repr__": repr(value)}`` so the contract can still display/compare it
    without re-hydrating arbitrary objects.
    """
    if isinstance(value, _JSON_SCALARS):
        return value
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    try:
        return {"__repr__": repr(value)}
    except Exception:
        return {"__repr__": "<unrepresentable>"}


def case_to_dict(case: ContractCase) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(case):
        out[f.name] = to_json_safe(getattr(case, f.name))
    return out


def case_from_dict(d: dict[str, Any]) -> ContractCase:
    valid = {f.name for f in fields(ContractCase)}
    kwargs = {k: v for k, v in d.items() if k in valid}
    return ContractCase(**kwargs)


def contract_to_dict(contract: SlotContract) -> dict[str, Any]:
    return {
        "version": int(contract.version),
        "cases": {cid: case_to_dict(c) for cid, c in contract.cases.items()},
    }


def contract_from_dict(d: dict[str, Any] | None) -> SlotContract:
    if not isinstance(d, dict):
        return SlotContract()
    cases_raw = d.get("cases", {})
    cases: dict[str, ContractCase] = {}
    if isinstance(cases_raw, dict):
        for cid, cd in cases_raw.items():
            if isinstance(cd, dict):
                try:
                    cases[cid] = case_from_dict(cd)
                except Exception:
                    continue
    return SlotContract(version=int(d.get("version", 1) or 1), cases=cases)
