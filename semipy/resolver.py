"""Resolve usage to REUSE, ADAPT, FORK, or GENERATE using the DAG."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from semipy.dag import (
    Commit,
    Slot,
    find_branch_by_fingerprint,
    find_commit_by_fingerprint,
    find_commit_by_operation_signature,
    freeze_constants,
    compute_operation_signature,
    walk_history,
)
from semipy.types import Decision, Usage


@dataclass
class ResolutionResult:
    decision: Decision
    slot: Optional[Slot]
    branch_name: Optional[str]
    parent_commit_ids: list[str]
    parent_sources: list[str]
    lineage_summary: Optional[str]
    commit_id: Optional[str]


def resolve(
    portal: Any,
    usage: Usage,
    template_fingerprint: str,
    constants: dict[str, Any],
) -> ResolutionResult:
    """
    Resolve a usage against the portal DAG.

    Returns REUSE with commit_id when a matching implementation exists;
    returns ADAPT or GENERATE with parent_commit_ids and lineage when a new
    or adapted implementation is needed.
    """
    slot_id = usage.call_site.site_id
    usage_id = usage.usage_id()
    constants_snapshot = freeze_constants(constants)
    operation_signature = compute_operation_signature(template_fingerprint, constants_snapshot)

    slot = portal.slots.get(slot_id)
    if slot is None:
        return ResolutionResult(
            decision=Decision.GENERATE,
            slot=None,
            branch_name=None,
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=None,
        )

    ref_commit_id = slot.refs.get(usage_id)
    if ref_commit_id is not None:
        return ResolutionResult(
            decision=Decision.REUSE,
            slot=slot,
            branch_name=None,
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=ref_commit_id,
        )

    existing = find_commit_by_operation_signature(slot, operation_signature, usage_id)
    if existing is not None:
        return ResolutionResult(
            decision=Decision.REUSE,
            slot=slot,
            branch_name=None,
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=existing.commit_id,
        )

    by_fingerprint = find_commit_by_fingerprint(slot, template_fingerprint, usage_id)
    if by_fingerprint is not None:
        return ResolutionResult(
            decision=Decision.REUSE,
            slot=slot,
            branch_name=None,
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=by_fingerprint.commit_id,
        )

    branch_match = find_branch_by_fingerprint(slot, template_fingerprint)
    if branch_match is not None:
        branch_name, head_commit = branch_match
        parent_sources = [head_commit.generated_source]
        lineage = _lineage_summary(slot, head_commit.commit_id)
        return ResolutionResult(
            decision=Decision.ADAPT,
            slot=slot,
            branch_name=branch_name,
            parent_commit_ids=[head_commit.commit_id],
            parent_sources=parent_sources,
            lineage_summary=lineage,
            commit_id=None,
        )

    return ResolutionResult(
        decision=Decision.GENERATE,
        slot=slot,
        branch_name=None,
        parent_commit_ids=[],
        parent_sources=[],
        lineage_summary=None,
        commit_id=None,
    )


def _lineage_summary(slot: Slot, commit_id: str) -> str:
    commits = walk_history(slot, commit_id)
    if not commits:
        return ""
    lines = [f"  {c.commit_id[:8]} {c.message} ({c.decision})" for c in commits[:5]]
    if len(commits) > 5:
        lines.append(f"  ... {len(commits) - 5} more")
    return "\n".join(lines)
