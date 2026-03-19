"""Resolve one durable SlotSpec to REUSE / ADAPT / GENERATE using spec_hash."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from semipy.history import Commit, Slot, walk_history
from semipy.types import Decision, SlotSpec


@dataclass
class ResolutionResult:
    decision: Decision
    slot: Optional[Slot]
    branch_name: Optional[str]
    parent_commit_ids: list[str]
    parent_sources: list[str]
    lineage_summary: Optional[str]
    commit_id: Optional[str]


def _head_commit(slot: Slot) -> Optional[Commit]:
    # Prefer head of default branch.
    branch = slot.branches.get(slot.default_branch)
    if branch is not None:
        c = slot.commits.get(branch.head)
        if c is not None:
            return c
    # Otherwise choose most recent commit across all branches/refs.
    if not slot.commits:
        return None
    return max(slot.commits.values(), key=lambda c: c.timestamp)


def _lineage_summary(slot: Slot, commit_id: str) -> str:
    commits = walk_history(slot, commit_id)
    if not commits:
        return ""
    lines = [f"  {c.commit_id[:8]} {c.message} ({c.decision})" for c in commits[:5]]
    if len(commits) > 5:
        lines.append(f"  ... {len(commits) - 5} more")
    return "\n".join(lines)


def resolve(
    portal: Any,
    slot_spec: SlotSpec,
    *,
    force_regenerate: bool = False,
) -> ResolutionResult:
    """
    Resolution precedence:
    1. No slot in portal -> GENERATE
    2. force_regenerate=True and slot has commits -> ADAPT (head as parent)
    3. force_regenerate=True and no commits -> GENERATE
    4. slot.spec_hash == slot_spec.spec_hash and commits exist -> REUSE (head)
    5. spec_hash mismatch and commits exist -> ADAPT (head as parent)
    6. No commits -> GENERATE
    """
    slot = portal.slots.get(slot_spec.slot_id)
    if slot is None:
        return ResolutionResult(
            decision=Decision.GENERATE,
            slot=None,
            branch_name="main",
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=None,
        )

    has_commits = bool(slot.commits)
    head = _head_commit(slot) if has_commits else None

    if not has_commits:
        return ResolutionResult(
            decision=Decision.GENERATE,
            slot=slot,
            branch_name="main",
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=None,
        )

    if force_regenerate:
        if head is None:
            return ResolutionResult(
                decision=Decision.GENERATE,
                slot=slot,
                branch_name="main",
                parent_commit_ids=[],
                parent_sources=[],
                lineage_summary=None,
                commit_id=None,
            )
        lineage = _lineage_summary(slot, head.commit_id)
        return ResolutionResult(
            decision=Decision.ADAPT,
            slot=slot,
            branch_name=f"b_{slot_spec.spec_hash[:8]}",
            parent_commit_ids=[head.commit_id],
            parent_sources=[head.generated_source],
            lineage_summary=lineage,
            commit_id=None,
        )

    # Not forced: decide REUSE vs ADAPT on stored spec_hash.
    if slot.spec_hash == slot_spec.spec_hash:
        return ResolutionResult(
            decision=Decision.REUSE,
            slot=slot,
            branch_name=None,
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=head.commit_id if head is not None else None,
        )

    # spec mismatch -> ADAPT from head
    lineage = _lineage_summary(slot, head.commit_id) if head is not None else None
    return ResolutionResult(
        decision=Decision.ADAPT,
        slot=slot,
        branch_name=f"b_{slot_spec.spec_hash[:8]}",
        parent_commit_ids=[head.commit_id] if head is not None else [],
        parent_sources=[head.generated_source] if head is not None else [],
        lineage_summary=lineage,
        commit_id=None,
    )
