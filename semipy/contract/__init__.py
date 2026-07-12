"""Behavioral-contract subsystem: records WHY each regeneration happened, traces
the EFFECT of every change, and enforces prior decisions as an executable gate."""
from __future__ import annotations

from semipy.contract.models import (
    INVARIANT_NAMES,
    ContractCase,
    SlotContract,
    compute_case_id,
)
from semipy.contract.fingerprint import (
    normalize_token,
    structural_input_fingerprint,
)
from semipy.contract.serialize import (
    contract_from_dict,
    contract_to_dict,
    to_json_safe,
)
from semipy.contract.access import (
    get_contract,
    load_active_cases,
    quarantine_cases,
    retire_active_cases,
    save_contract,
)
from semipy.contract.runner import ContractRunResult, CaseFailure, run_contract
from semipy.contract.change import (
    ChangeRecord,
    EffectDiffEntry,
    change_record_from_dict,
    change_record_to_dict,
    compute_effect_diff,
    regression_summary,
)
from semipy.contract.maintainer import (
    ContractUpdate,
    ProposedCase,
    SupersedeProposal,
    maintain_contract,
)
from semipy.contract.redaction import (
    EXTERNAL_PROVENANCE,
    RedactionResult,
    apply_capture_time_policy,
    default_ship_flag,
    redact_case,
    redact_value,
)

__all__ = [
    "INVARIANT_NAMES",
    "ContractCase",
    "SlotContract",
    "compute_case_id",
    "normalize_token",
    "structural_input_fingerprint",
    "contract_from_dict",
    "contract_to_dict",
    "to_json_safe",
    "get_contract",
    "load_active_cases",
    "quarantine_cases",
    "retire_active_cases",
    "save_contract",
    "ContractRunResult",
    "CaseFailure",
    "run_contract",
    "ChangeRecord",
    "EffectDiffEntry",
    "change_record_from_dict",
    "change_record_to_dict",
    "compute_effect_diff",
    "regression_summary",
    "ContractUpdate",
    "ProposedCase",
    "SupersedeProposal",
    "maintain_contract",
    "EXTERNAL_PROVENANCE",
    "RedactionResult",
    "apply_capture_time_policy",
    "default_ship_flag",
    "redact_case",
    "redact_value",
]
