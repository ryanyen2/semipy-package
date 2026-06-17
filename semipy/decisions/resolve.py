"""Resolve a fork: pick a branch (U9) or assert a property (U10).

Two channels, both flowing into machinery semipy already has:

- **Pick** (U9): the user chooses a fate. The matching stored candidate becomes
  the committed head -- a new commit minted from the *persisted* candidate source
  (no regeneration, no LLM), which by recency becomes the active implementation
  (``most_recent_branch_head``). The chosen fate is returned as a spec clause the
  caller can promote into the ``#<``/``#>`` surface, and the decision closes.

- **Assert** (U10): the user states a natural-language property when no branch
  fits. The property is recorded as a contract case on the slot. Candidates are
  filtered by a metamorphic ``satisfies`` check; if one satisfies it becomes the
  head, otherwise a targeted regeneration is signalled.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.persistence import attach_decision_set
from semipy.history.version_control import (
    Slot,
    add_commit_to_slot,
    create_commit,
    most_recent_branch_head,
)


class DecisionResolveError(Exception):
    """Raised when a resolution cannot proceed (e.g. a missing candidate source)."""


@dataclass
class PickResult:
    commit_id: str
    candidate_id: str
    source: str
    spec_clause: str


@dataclass
class AssertResult:
    property_text: str
    contract_case_id: str
    satisfying_candidate_ids: list[str] = field(default_factory=list)
    regen_needed: bool = False
    commit_id: Optional[str] = None


def _find_branch(decision: Decision, fate_label: str) -> Branch:
    for b in decision.branches:
        if b.fate_label == fate_label:
            return b
    raise DecisionResolveError(f"no branch with fate {fate_label!r} in decision {decision.decision_id}")


def _commit_candidate_as_head(slot: Slot, source: str, *, usage_id: str) -> str:
    """Mint a commit from an existing candidate source and make it the head.

    Copies the active head's fingerprint/constants so the slot's reuse identity is
    preserved; the new commit's recency makes it the active implementation.
    """
    head = most_recent_branch_head(slot)
    parent_ids = (head.commit_id,) if head else ()
    commit = create_commit(
        parent_ids=parent_ids,
        generated_source=source,
        template_fingerprint=head.template_fingerprint if head else "",
        constants_snapshot=head.constants_snapshot if head else (),
        prompt_snapshot=head.prompt_snapshot if head else "",
        decision="ADAPT",
        usage_id=usage_id,
        runtime_input_fingerprint=head.runtime_input_fingerprint if head else "",
    )
    add_commit_to_slot(slot, commit, branch_name="decision-pick", usage_id=usage_id)
    return commit.commit_id


def _spec_clause(decision: Decision, fate_label: str) -> str:
    """Render the resolved fate as a steering clause for the caller to promote."""
    if decision.guard:
        return f"unless {decision.guard} -> {fate_label}"
    axis = decision.axis_label or decision.germ
    return f"{axis}: {fate_label}"


def pick_branch(
    slot: Slot,
    decision_set: DecisionSet,
    *,
    decision_id: str,
    fate_label: str,
    usage_id: str = "",
) -> PickResult:
    """Resolve a decision by selecting a fate. LLM-free; swaps to a stored candidate."""
    decision = decision_set.decision_by_id(decision_id)
    if decision is None:
        raise DecisionResolveError(f"no decision {decision_id!r} in set")
    branch = _find_branch(decision, fate_label)
    if not branch.candidate_ids:
        raise DecisionResolveError(f"branch {fate_label!r} has no candidates")
    candidate_id = branch.candidate_ids[0]
    source = decision_set.candidates.get(candidate_id)
    if not source:
        # Fail loudly rather than silently regenerating a lost candidate.
        raise DecisionResolveError(
            f"candidate source for {candidate_id!r} is missing; cannot pick without regenerating"
        )

    commit_id = _commit_candidate_as_head(slot, source, usage_id=usage_id)
    decision.status = "resolved"
    decision.resolution = {"via": "pick", "branch": fate_label, "candidate_id": candidate_id}
    attach_decision_set(slot, decision_set)
    return PickResult(
        commit_id=commit_id,
        candidate_id=candidate_id,
        source=source,
        spec_clause=_spec_clause(decision, fate_label),
    )


def _case_id(property_text: str, decision_id: str) -> str:
    return hashlib.sha256(f"{decision_id}\0{property_text}".encode()).hexdigest()[:16]


def assert_property(
    slot: Slot,
    decision_set: DecisionSet,
    *,
    decision_id: str,
    property_text: str,
    satisfies: Callable[[str], bool],
    usage_id: str = "",
) -> AssertResult:
    """Resolve a decision by asserting an NL property.

    ``satisfies(candidate_id) -> bool`` is the metamorphic check (in production a
    relation evaluated via the divergence executor or an LLM). The property is
    recorded as a contract case on the slot; if a candidate satisfies it that
    candidate becomes the head, otherwise a targeted regeneration is signalled.
    """
    decision = decision_set.decision_by_id(decision_id)
    if decision is None:
        raise DecisionResolveError(f"no decision {decision_id!r} in set")

    case_id = _case_id(property_text, decision_id)
    asserted = slot.contract.setdefault("asserted_properties", [])
    if not any(c.get("case_id") == case_id for c in asserted):
        asserted.append(
            {"case_id": case_id, "property": property_text, "decision_id": decision_id, "kind": "asserted_property"}
        )

    satisfying = [cid for cid in decision_set.candidates if satisfies(cid)]
    result = AssertResult(
        property_text=property_text,
        contract_case_id=case_id,
        satisfying_candidate_ids=satisfying,
        regen_needed=not satisfying,
    )
    if satisfying:
        result.commit_id = _commit_candidate_as_head(
            slot, decision_set.candidates[satisfying[0]], usage_id=usage_id
        )
        decision.status = "resolved"
        decision.resolution = {
            "via": "assert",
            "property": property_text,
            "contract_case_id": case_id,
            "candidate_id": satisfying[0],
        }
    else:
        # No stored candidate satisfies the property -> the caller regenerates
        # against it; the decision stays open until a satisfying impl exists.
        decision.resolution = {
            "via": "assert",
            "property": property_text,
            "contract_case_id": case_id,
            "regen_needed": True,
        }
    attach_decision_set(slot, decision_set)
    return result
