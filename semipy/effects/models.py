"""Data model for reified real-world effects.

An *effectful* semiformal slot does not touch the world directly. Its generated
function receives an object-capability ``fx`` (see :mod:`semipy.effects.capability`)
and **emits** intended operations, producing an :class:`EffectScript` -- a list of
:class:`Effect` values that are pure data. A trusted, non-LLM handler then stages
that script in a shadow of each target artifact, verifies it, gates it, and only
then commits, appending the applied effects to a per-slot ledger.

The op vocabulary is deliberately small, fixed, and data-agnostic: the artifact
backend interprets ops; semipy never branches on payload *contents* to decide
behaviour. This mirrors the contract subsystem's fixed invariant vocabulary
(:data:`semipy.contract.models.INVARIANT_NAMES`).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# --- Fixed, data-agnostic operation vocabulary -----------------------------
EffectOp = Literal["create", "read", "update", "delete", "append", "call"]

#: Ops that observe state without mutating it.
READ_OPS: tuple[str, ...] = ("read",)
#: Ops that remove existing state (used by the ``append_only`` static check).
DESTRUCTIVE_OPS: tuple[str, ...] = ("delete",)

# --- Fixed effect-invariant vocabulary (mirrors INVARIANT_NAMES) -----------
EFFECT_INVARIANT_NAMES: tuple[str, ...] = (
    "append_only",            # no delete / destructive update ops
    "bounded_blast_radius",   # affected targets and records bounded
    "target_whitelist",       # every target in a declared allowlist
    "reversible",             # every effect carries a compensation
    "idempotent_effect",      # re-applying the script is a no-op
)

EventStatus = Literal["applied", "reverted", "shadow", "approval_pending"]


class EffectRefused(Exception):
    """Raised when the handler refuses to apply a reified effect.

    Distinct from :class:`semipy.types.SemiCallError` (which means the generated
    function itself raised): here the function ran fine, but its reified effect was
    rejected at apply time -- unsafe (irreversible / unbounded / not provably
    bounded) or not approved (an externalized target without consent). The refused
    plan is carried for inspection, and the message states the reason plainly rather
    than framing it as a code failure.
    """

    def __init__(self, reason: str, *, effect_script: Any = None, call_site: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.effect_script = effect_script
        self.call_site = call_site

    def __str__(self) -> str:
        msg = f"semipy refused to apply this effect: {self.reason}"
        script = self.effect_script
        if script is not None and not getattr(script, "is_empty", lambda: True)():
            msg += f"\n  planned effect (not applied): {script.summary()}"
        if self.call_site is not None:
            where = getattr(self.call_site, "filename", "")
            line = getattr(self.call_site, "lineno", 0)
            if where:
                msg += f"\n  at: {where}:{line}"
        return msg


def _stable_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _canonical_repr(value: Any) -> str:
    """Order-independent, stable repr of a JSON-ish value for content addressing."""
    if isinstance(value, dict):
        items = sorted((str(k), _canonical_repr(v)) for k, v in value.items())
        return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_repr(v) for v in value) + "]"
    return repr(value)


def compute_effect_id(
    *, op: str, target: str, payload: dict[str, Any] | None, selector: dict[str, Any] | None
) -> str:
    """Content-addressed effect id: hash of (op, target, payload, selector)."""
    raw = f"{op}\0{target}\0{_canonical_repr(payload or {})}\0{_canonical_repr(selector or {})}"
    return _stable_hash(raw)


def compute_event_id(
    *, slot_id: str, origin_commit_id: str, invocation_id: str, seq: int
) -> str:
    """Content-addressed ledger event id."""
    raw = f"{slot_id}\0{origin_commit_id}\0{invocation_id}\0{seq}"
    return _stable_hash(raw)


@dataclass
class Effect:
    """One reified, intended operation on an artifact target.

    ``target`` is a ``scheme://name`` id (e.g. ``db://customers``). ``payload`` is
    the data to write (data-agnostic; the backend interprets it). ``selector``
    chooses which records an ``update``/``delete``/``read`` applies to.
    ``compensation`` is the reified inverse, filled by the backend at staging time
    so a later revert never re-derives it from the (mutable) implementation.
    """

    op: EffectOp
    target: str
    payload: dict[str, Any] = field(default_factory=dict)
    selector: Optional[dict[str, Any]] = None
    compensation: Optional["Effect"] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    effect_id: str = ""

    def __post_init__(self) -> None:
        if not self.effect_id:
            self.effect_id = compute_effect_id(
                op=self.op, target=self.target, payload=self.payload, selector=self.selector
            )

    def is_mutating(self) -> bool:
        return self.op not in READ_OPS

    def short(self) -> str:
        """Human-readable one-line summary (for prompts / portal_inspect)."""
        sel = f" where {self.selector}" if self.selector else ""
        return f"{self.op} {self.target}{sel}".strip()


@dataclass
class EffectScript:
    """An ordered list of reified effects emitted by one slot invocation."""

    effects: list[Effect] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.effects

    def targets(self) -> set[str]:
        return {e.target for e in self.effects}

    def mutating(self) -> list[Effect]:
        return [e for e in self.effects if e.is_mutating()]

    def op_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.effects:
            counts[e.op] = counts.get(e.op, 0) + 1
        return counts

    def summary(self) -> str:
        if self.is_empty():
            return "(no effects)"
        return "; ".join(e.short() for e in self.effects)


@dataclass
class EffectResult:
    """What an effectful slot returns to the caller.

    Carries the reified :class:`EffectScript` (always), the generated function's
    own return value (often the script or ``None``), and -- once Stage 4 lands --
    whether/where it was applied.
    """

    effect_script: EffectScript
    value: Any = None
    applied: bool = False
    event_id: str = ""

    def __repr__(self) -> str:  # friendly for `print(result)` in a notebook
        state = "applied" if self.applied else "planned"
        return f"<EffectResult {state}: {self.effect_script.summary()}>"

    def revert(self) -> int:
        """Undo these effects in-hand by replaying their materialized compensations.

        Returns the number of compensations applied. Works whether or not the
        effects were auto-applied (a planned, never-applied result reverts to a
        no-op against the current state).
        """
        from semipy.effects.compensate import revert as _revert

        return _revert(self)


@dataclass
class EffectInvariant:
    """A declared effect invariant plus its parameter (bound / whitelist)."""

    name: str  # one of EFFECT_INVARIANT_NAMES
    param: dict[str, Any] = field(default_factory=dict)


# --- Effect-contract cases (parallel to contract.ContractCase) -------------
EffectCaseStatus = Literal["active", "superseded", "quarantined"]


def compute_effect_case_id(*, invariant: str, param: dict[str, Any], input_fingerprint: str) -> str:
    raw = f"{invariant}\0{_canonical_repr(param)}\0{input_fingerprint}"
    return _stable_hash(raw)


@dataclass
class EffectCase:
    """One carried-forward effect-invariant assertion over an input pattern.

    Reuses the *shape* of :class:`semipy.contract.models.ContractCase`
    (content-addressed, status lifecycle, provenance) but asserts over an
    EffectScript / artifact state rather than a return value.
    """

    case_id: str
    invariant: str  # one of EFFECT_INVARIANT_NAMES
    param: dict[str, Any] = field(default_factory=dict)
    input_sample: dict[str, Any] = field(default_factory=dict)
    input_fingerprint: str = ""

    reason: str = ""
    effect: str = ""
    decision: str = ""
    origin_commit_id: str = ""
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)

    status: EffectCaseStatus = "active"
    superseded_by: str = ""
    supersede_reason: str = ""

    def is_active(self) -> bool:
        return self.status == "active"


@dataclass
class SlotEffectContract:
    """All effect-invariant cases for one slot, plus a monotonic version."""

    version: int = 1
    cases: dict[str, EffectCase] = field(default_factory=dict)

    def active(self) -> list[EffectCase]:
        return [c for c in self.cases.values() if c.status == "active"]

    def superseded(self) -> list[EffectCase]:
        return [c for c in self.cases.values() if c.status == "superseded"]

    def quarantined(self) -> list[EffectCase]:
        return [c for c in self.cases.values() if c.status == "quarantined"]

    def add(self, case: EffectCase) -> EffectCase:
        existing = self.cases.get(case.case_id)
        if existing is not None and existing.status == "active":
            existing.reason = case.reason or existing.reason
            existing.effect = case.effect or existing.effect
            existing.updated_ts = case.updated_ts
            return existing
        self.cases[case.case_id] = case
        self.version += 1
        return case

    def quarantine(self, case_id: str, why: str) -> None:
        c = self.cases.get(case_id)
        if c is not None:
            c.status = "quarantined"
            c.supersede_reason = why
            self.version += 1


@dataclass
class LedgerEvent:
    """One append-only ledger entry: applied effects of a single invocation.

    Keyed by ``(slot_id, origin_commit_id, invocation_id)``. Stores the
    *materialized* applied effects and their compensations so a revert replays
    exact inverses and never re-derives them from a regenerated implementation
    (the "semantic rollback" hazard).
    """

    event_id: str
    slot_id: str
    origin_commit_id: str
    invocation_id: str
    applied_effects: list[Effect] = field(default_factory=list)
    compensations: list[Effect] = field(default_factory=list)
    artifact_snapshot_ref: str = ""
    contract_case_ids: list[str] = field(default_factory=list)
    status: EventStatus = "applied"
    timestamp: float = field(default_factory=time.time)
    parent_event_id: str = ""
