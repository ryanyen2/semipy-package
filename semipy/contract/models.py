"""Behavioral-contract data model.

A ``SlotContract`` is the durable, accumulating record of what a semiformal slot
must do and *why*. Each ``ContractCase`` carries the reason it exists, the effect
it pins, and provenance — closing the gap that specification-by-example leaves
open (examples are spec+test, but do not record rationale/evolution).

Cases are content-addressed so re-deriving the same case is idempotent. The case
vocabulary is deliberately small and data-agnostic:

- ``invariant``  : a structural property from a fixed vocabulary that holds for
  every input of a pattern (non_empty / non_identity / type_match /
  category_preserving / idempotent). These promote the validator's transient
  guards into persisted, carried-forward cases.
- ``metamorphic``: a named relation between an input and a transformed input
  (e.g. ``whitespace_invariance``) drawn from a fixed registry.
- ``example``    : a pinned input -> output (golden master / characterization).
  Used sparingly, only for canonical low-cardinality outputs.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Literal

CaseKind = Literal["example", "invariant", "metamorphic"]
CaseStatus = Literal["active", "superseded", "quarantined"]

# Fixed, data-agnostic invariant vocabulary. Mirrors the validator's guard kinds
# (empty_output / identity_return / type_mismatch) plus two structural extras.
INVARIANT_NAMES: tuple[str, ...] = (
    "non_empty",
    "non_identity",
    "type_match",
    "category_preserving",
    "idempotent",
)

# Fraction of cases reserved as held-out (not used to accept/select a candidate;
# reserved for the freeze certificate's reproducibility gate — see frontier-kernel
# plan Part I §3.1). Bounded so recent replay history does not grow without limit.
_HOLDOUT_FRACTION = 0.2
_MAX_CASE_OUTCOMES = 50


def assign_holdout_split(case_id: str, *, fraction: float = _HOLDOUT_FRACTION) -> bool:
    """Deterministic train/held-out split from a case's content-addressed id.

    A hash-bucket split is data-agnostic: it works uniformly whether the case's
    input is a string, a record, a number, or anything else, so no per-type split
    rule is needed. Callers assign this once at case-creation time and persist the
    result (``ContractCase.holdout``) rather than recomputing it, so a later change
    to ``fraction`` cannot retroactively reassign an existing case's split.
    """
    bucket = int(hashlib.sha256(f"holdout\0{case_id}".encode()).hexdigest(), 16) % 100
    return bucket < int(fraction * 100)


def _assertion_key(
    *,
    kind: str,
    expected_repr: str,
    expected_type: str,
    invariant: str,
    relation: str,
    relation_param: dict[str, Any] | None,
) -> str:
    """Stable string identifying the *assertion* of a case (independent of input)."""
    if kind == "example":
        return f"example:{expected_type}:{expected_repr}"
    if kind == "invariant":
        return f"invariant:{invariant}:{expected_type}"
    if kind == "metamorphic":
        param = repr(sorted((relation_param or {}).items()))
        return f"metamorphic:{relation}:{param}"
    return kind


def compute_case_id(
    *,
    kind: str,
    input_fingerprint: str,
    expected_repr: str = "",
    expected_type: str = "",
    invariant: str = "",
    relation: str = "",
    relation_param: dict[str, Any] | None = None,
) -> str:
    """Content-addressed case id: hash of (kind, input pattern, assertion)."""
    akey = _assertion_key(
        kind=kind,
        expected_repr=expected_repr,
        expected_type=expected_type,
        invariant=invariant,
        relation=relation,
        relation_param=relation_param,
    )
    raw = f"{kind}\0{input_fingerprint}\0{akey}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class ContractCase:
    """One behavioral case: an assertion over an input pattern, with provenance."""

    case_id: str
    kind: CaseKind
    # Input under test (durable, JSON-safe). Maps free-variable name -> value.
    input_sample: dict[str, Any] = field(default_factory=dict)
    # Structural (digit-normalised) fingerprint of input_sample; the pattern bucket.
    input_fingerprint: str = ""

    # Assertion payload (the meaningful field depends on ``kind``):
    expected_repr: str = ""            # kind == "example": pinned output repr
    expected_type: str = ""            # kind == "example"/"invariant": type name
    invariant: str = ""                # kind == "invariant": one of INVARIANT_NAMES
    relation: str = ""                 # kind == "metamorphic": registry name
    relation_param: dict[str, Any] = field(default_factory=dict)

    # Provenance (the WHY / WHAT-CHANGED):
    reason: str = ""                   # why this case exists (triggering failure/usage)
    effect: str = ""                   # what behavior it pins / what changed
    decision: str = ""                 # GENERATE | ADAPT | ... that created it
    origin_commit_id: str = ""
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)

    status: CaseStatus = "active"
    superseded_by: str = ""            # case_id that replaced this one
    supersede_reason: str = ""         # deliberate behavior-change rationale

    # Distribution provenance (R6 / R14). A case is eligible for the shipped
    # contract floor only when ``ship`` is True. ``provenance`` is the category
    # that decides the default ship flag (external-source-derived cases default
    # to ship=False; synthetic/relation/user cases to True) -- U3 computes both
    # at ledger-persistence time. The three source_* fields identify an external
    # input (file/URL/API payload/DB schema) so its staleness is decidable; they
    # stay empty for synthetic / in-memory cases.
    ship: bool = False
    provenance: str = ""               # "" | synthetic | external | user | relation | consumer-report
    source_locator: str = ""           # external input locator (path / URL / endpoint / schema id)
    snapshot_fingerprint: str = ""     # fingerprint of the external snapshot at capture time
    source_profile: dict[str, Any] = field(default_factory=dict)  # profile of the external input

    # Evidence-ledger fields (frontier-kernel Phase 0):
    # Which tree node this case pins. "" = whole-slot (today's only granularity);
    # populated with a real per-node id once trace replay materializes node-local
    # (input, output) pairs (frontier-kernel Phase 4).
    node_id: str = ""
    # Train/held-out split, assigned once at creation via assign_holdout_split().
    holdout: bool = False
    # Bounded replay history against this case: [{"ts", "passed", "commit_id"}, ...].
    outcomes: list[dict[str, Any]] = field(default_factory=list)

    def is_active(self) -> bool:
        return self.status == "active"

    def record_outcome(self, *, passed: bool, commit_id: str) -> None:
        """Append one replay outcome, keeping only the most recent ones."""
        self.outcomes.append({"ts": time.time(), "passed": passed, "commit_id": commit_id})
        if len(self.outcomes) > _MAX_CASE_OUTCOMES:
            self.outcomes = self.outcomes[-_MAX_CASE_OUTCOMES:]

    @property
    def primary_input(self) -> Any:
        """First non-self input value (the value most assertions compare against)."""
        for k, v in self.input_sample.items():
            if isinstance(k, str) and (k.startswith("_") or k == "self"):
                continue
            return v
        return None


@dataclass
class SlotContract:
    """All behavioral cases for one slot, plus a monotonic version counter."""

    version: int = 1
    cases: dict[str, ContractCase] = field(default_factory=dict)

    def active(self) -> list[ContractCase]:
        return [c for c in self.cases.values() if c.status == "active"]

    def superseded(self) -> list[ContractCase]:
        return [c for c in self.cases.values() if c.status == "superseded"]

    def quarantined(self) -> list[ContractCase]:
        return [c for c in self.cases.values() if c.status == "quarantined"]

    def add(self, case: ContractCase) -> ContractCase:
        """Add or refresh a case by content-addressed id. Returns the stored case."""
        existing = self.cases.get(case.case_id)
        if existing is not None and existing.status == "active":
            # Refresh provenance/effect on the existing active case rather than duplicate.
            existing.reason = case.reason or existing.reason
            existing.effect = case.effect or existing.effect
            existing.updated_ts = case.updated_ts
            return existing
        self.cases[case.case_id] = case
        self.version += 1
        return case

    def supersede(self, old_id: str, new_case: ContractCase, why: str) -> None:
        """Mark an existing case superseded by a new one (audit trail preserved)."""
        old = self.cases.get(old_id)
        if old is not None:
            old.status = "superseded"
            old.superseded_by = new_case.case_id
            old.supersede_reason = why
            old.updated_ts = new_case.updated_ts
        self.add(new_case)

    def quarantine(self, case_id: str, why: str) -> None:
        """Mark a case quarantined (kept, not enforced) with a reason."""
        c = self.cases.get(case_id)
        if c is not None:
            c.status = "quarantined"
            c.supersede_reason = why
            self.version += 1
