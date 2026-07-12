"""The contract surface: one queryable, serializable view per slot (R1, R2).

Assembles what the slot already stores -- behavioral cases (``Slot.contract``),
regime guards (``Slot.kernel_tree``), and the freeze certificate
(``Slot.freeze_events``) -- into a single object, plus the fields the contract
surface adds: a scope-predicate reference (minted by U2; a loosely-typed seam
here), per-case ship flags (already on ``ContractCase``), and an explicit
certified/uncertified boundary (D4 ships as a *partial* contract).

It is a *view*, not a store: ``ContractSurface.from_slot`` reads the slot and
builds the object; there is no new persistence. Serialized to versioned,
pretty-printed JSON (KTD-6) so a surface diffs cleanly and a later loader can
reject an unknown schema version.

``diff`` classifies the delta between two surfaces by behavioral semver (R2):
evidence added = patch; scope widened or a regime added = minor; pinned behavior
changed (a case superseded or a certificate invalidated) = major.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.contract.access import get_contract
from semipy.contract.serialize import case_to_dict, dumps_pretty
from semipy.kernel.operators import get_freeze_events
from semipy.kernel.tree import get_tree, guard_to_dict

# Bump when the serialized shape changes incompatibly. A loader rejects any
# version it does not recognize (see ``surface_from_dict``).
SCHEMA_VERSION = 1


class ContractSchemaError(ValueError):
    """A serialized surface carries an unknown/unsupported schema version."""


@dataclass
class ContractSurface:
    """One slot's contract, assembled for query, display, and diff."""

    slot_id: str
    spec_text: str = ""
    expected_type: str = ""
    # Reference to the slot's compiled scope predicate (guard-DSL), minted by U2
    # at commit/freeze from the evidence ledger's input profiles. U1 only carries
    # the reference -- a loosely-typed seam a later unit populates; None until then.
    scope_predicate_ref: Optional[str] = None
    # case_id -> serialized ContractCase (all statuses, so a diff can see a
    # supersede). Active cases are the enforced floor; superseded/quarantined
    # ones stay for the audit trail.
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Regime guards (serialized ``kernel.tree.Guard`` dicts): the predicates that
    # select a child implementation on a BRANCH node. Empty for a single-regime slot.
    regimes: list[dict[str, Any]] = field(default_factory=list)
    # Distinct metamorphic relation names asserted by the active cases (R1's
    # "relations"; a summary -- the cases themselves carry the detail).
    relations: list[str] = field(default_factory=list)
    # The slot's most recent freeze certificate (licensed or refused), or None if
    # the slot never attempted a freeze. ``certified`` is True only when that
    # certificate licensed the freeze; when False the slot ships as a *partial*
    # contract -- its active cases/relations are still checkable (D4).
    certificate: Optional[dict[str, Any]] = None
    certified: bool = False

    @property
    def uncertified(self) -> bool:
        """True when no licensed freeze certificate covers the whole slot -- the
        explicit half of the certified/uncertified boundary (D4)."""
        return not self.certified

    def active_cases(self) -> list[dict[str, Any]]:
        return [c for c in self.cases.values() if c.get("status") == "active"]

    @classmethod
    def from_slot(cls, slot: Any, *, scope_predicate_ref: Optional[str] = None) -> "ContractSurface":
        """Assemble the surface from a slot's stored state (no persistence).

        ``scope_predicate_ref`` may be passed by a caller that already minted the
        scope (U2); otherwise it is read best-effort from the slot, so wherever U2
        chooses to store it (a ``slot.scope_predicate_ref`` attribute) flows in
        with no further change here.
        """
        contract = get_contract(slot)
        spec = getattr(slot, "slot_spec", None)
        spec = spec if isinstance(spec, dict) else {}

        tree = get_tree(slot)
        regimes: list[dict[str, Any]] = []
        if tree is not None:
            for node in tree.walk():
                for g in node.guards:
                    regimes.append(guard_to_dict(g))

        events = get_freeze_events(slot)
        certificate = events[-1].certificate.to_dict() if events else None
        certified = bool(events and events[-1].certificate.licensed)

        relations = sorted(
            {c.relation for c in contract.active() if c.kind == "metamorphic" and c.relation}
        )

        if scope_predicate_ref is None:
            scope_predicate_ref = getattr(slot, "scope_predicate_ref", None)

        return cls(
            slot_id=getattr(slot, "slot_id", "") or "",
            spec_text=str(spec.get("spec_text", "") or ""),
            expected_type=str(spec.get("expected_type", "") or ""),
            scope_predicate_ref=scope_predicate_ref,
            cases={cid: case_to_dict(c) for cid, c in contract.cases.items()},
            regimes=regimes,
            relations=relations,
            certificate=certificate,
            certified=certified,
        )


# ---------------------------------------------------------------------------
# (De)serialization -- versioned, pretty-printed JSON (KTD-6).
# ---------------------------------------------------------------------------


def surface_to_dict(surface: ContractSurface) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "slot_id": surface.slot_id,
        "spec_text": surface.spec_text,
        "expected_type": surface.expected_type,
        "scope_predicate_ref": surface.scope_predicate_ref,
        "cases": {cid: dict(c) for cid, c in surface.cases.items()},
        "regimes": [dict(g) for g in surface.regimes],
        "relations": list(surface.relations),
        "certificate": dict(surface.certificate) if surface.certificate is not None else None,
        "certified": bool(surface.certified),
    }


def surface_from_dict(d: dict[str, Any]) -> ContractSurface:
    """Rebuild a surface from its serialized dict, rejecting unknown versions."""
    if not isinstance(d, dict):
        raise ContractSchemaError("contract surface must be a JSON object")
    version = d.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ContractSchemaError(
            f"unsupported contract surface schema version {version!r} "
            f"(this semipy understands version {SCHEMA_VERSION})"
        )
    cases_raw = d.get("cases") or {}
    cases = {str(cid): dict(c) for cid, c in cases_raw.items() if isinstance(c, dict)}
    return ContractSurface(
        slot_id=str(d.get("slot_id", "") or ""),
        spec_text=str(d.get("spec_text", "") or ""),
        expected_type=str(d.get("expected_type", "") or ""),
        scope_predicate_ref=d.get("scope_predicate_ref"),
        cases=cases,
        regimes=[dict(g) for g in (d.get("regimes") or []) if isinstance(g, dict)],
        relations=[str(r) for r in (d.get("relations") or [])],
        certificate=(dict(d["certificate"]) if isinstance(d.get("certificate"), dict) else None),
        certified=bool(d.get("certified", False)),
    )


def surface_to_json(surface: ContractSurface) -> str:
    return dumps_pretty(surface_to_dict(surface))


def surface_from_json(text: str) -> ContractSurface:
    return surface_from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# Behavioral-semver diff (R2).
# ---------------------------------------------------------------------------


@dataclass
class ContractDiff:
    """The classified delta between two contract surfaces (R2), with the
    responsible entries so a caller can name what changed."""

    classification: str                                   # major | minor | patch | none
    added_cases: list[str] = field(default_factory=list)
    superseded_cases: list[str] = field(default_factory=list)
    added_regimes: list[str] = field(default_factory=list)
    scope_changed: bool = False
    certificate_invalidated: bool = False
    reasons: list[str] = field(default_factory=list)


def _regime_keys(regimes: list[dict[str, Any]]) -> set[str]:
    return {str(g.get("predicate_source", "")) for g in regimes if g.get("predicate_source")}


def diff(old: ContractSurface, new: ContractSurface) -> ContractDiff:
    """Classify ``old -> new`` by behavioral semver.

    - **major**: a case active in ``old`` is no longer active in ``new`` (a
      supersede, quarantine, or removal -- pinned behavior changed), or a
      certificate that licensed ``old`` no longer licenses ``new``.
    - **minor**: a regime guard present in ``new`` but not ``old``, or the scope
      predicate reference changed. (Scope refs are opaque here, so any change is
      reported as minor -- U2 owns membership-level widen/narrow reasoning.)
    - **patch**: a new evidence case with none of the above.
    - **none**: identical surfaces.
    """
    added_cases = sorted(cid for cid in new.cases if cid not in old.cases)
    superseded_cases = sorted(
        cid
        for cid, oc in old.cases.items()
        if oc.get("status") == "active" and new.cases.get(cid, {}).get("status") != "active"
    )
    added_regimes = sorted(_regime_keys(new.regimes) - _regime_keys(old.regimes))
    scope_changed = (old.scope_predicate_ref or None) != (new.scope_predicate_ref or None)
    certificate_invalidated = bool(old.certified and not new.certified)

    reasons: list[str] = []
    for cid in superseded_cases:
        reasons.append(f"major: pinned case {cid} is no longer active (behavior changed)")
    if certificate_invalidated:
        reasons.append("major: freeze certificate invalidated (was licensed, now not)")
    for pred in added_regimes:
        reasons.append(f"minor: regime guard added ({pred})")
    if scope_changed:
        reasons.append(
            f"minor: scope predicate changed ({old.scope_predicate_ref!r} -> {new.scope_predicate_ref!r})"
        )
    for cid in added_cases:
        reasons.append(f"patch: evidence case {cid} added")

    if superseded_cases or certificate_invalidated:
        classification = "major"
    elif added_regimes or scope_changed:
        classification = "minor"
    elif added_cases:
        classification = "patch"
    else:
        classification = "none"

    return ContractDiff(
        classification=classification,
        added_cases=added_cases,
        superseded_cases=superseded_cases,
        added_regimes=added_regimes,
        scope_changed=scope_changed,
        certificate_invalidated=certificate_invalidated,
        reasons=reasons,
    )
