"""Resolve SlotSpec to REUSE / INSTANTIATE / ADAPT / GENERATE using spec hash and cross-slot equivalence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from semipy.history import Commit, Slot, most_recent_branch_head, walk_history
from semipy.types import Decision, SlotSpec, equivalence_key_from_stored_snapshot


@dataclass
class ResolutionResult:
    decision: Decision
    slot: Optional[Slot]
    branch_name: Optional[str]
    parent_commit_ids: list[str]
    parent_sources: list[str]
    lineage_summary: Optional[str]
    commit_id: Optional[str]
    """When reusing another slot's dispatch entry, load the function via this slot_id."""
    reuse_dispatch_slot_id: Optional[str] = None
    sketch_id: Optional[str] = None
    sketch_hole_values: Optional[dict[str, str]] = None


def _head_commit(slot: Slot) -> Optional[Commit]:
    c = most_recent_branch_head(slot)
    if c is not None:
        return c
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


def _stored_equivalence(slot: Slot) -> Optional[str]:
    snap = slot.slot_spec
    if isinstance(snap, dict):
        return equivalence_key_from_stored_snapshot(snap)
    return None


def _equivalence_matches(slot: Slot, slot_spec: SlotSpec) -> bool:
    sk = _stored_equivalence(slot)
    if sk is not None:
        return sk == slot_spec.spec_equivalence_key
    return (slot.spec_hash or "") == slot_spec.spec_hash


def _donor_commits_for_equivalence(
    portal: Any,
    slot_spec: SlotSpec,
    current_slot_id: str,
) -> list[tuple[Slot, Commit]]:
    target = slot_spec.spec_equivalence_key
    out: list[tuple[Slot, Commit]] = []
    for sid, s in portal.slots.items():
        if sid == current_slot_id:
            continue
        key = _stored_equivalence(s)
        if key != target:
            continue
        head = _head_commit(s)
        if head is None:
            continue
        out.append((s, head))
    out.sort(key=lambda t: -t[1].timestamp)
    return out


def _best_donor(
    portal: Any,
    slot_spec: SlotSpec,
    current_slot_id: str,
) -> Optional[tuple[Slot, Commit]]:
    donors = _donor_commits_for_equivalence(portal, slot_spec, current_slot_id)
    if not donors:
        return None
    return donors[0]


def list_equivalence_donors(
    portal: Any,
    slot_spec: SlotSpec,
    current_slot_id: str,
) -> list[tuple[Slot, Commit]]:
    """All slots (newest commit first) that share this spec_equivalence_key, excluding current_slot_id."""
    return _donor_commits_for_equivalence(portal, slot_spec, current_slot_id)


def _try_sketch_instantiation(
    slot_spec: SlotSpec,
    sketch_library: Any,
) -> ResolutionResult | None:
    if sketch_library is None:
        return None
    try:
        from semipy.library.sketch import find_sketch_match
    except Exception:
        return None
    m = find_sketch_match(slot_spec, sketch_library)
    if m is None:
        return None
    sk, hv = m
    return ResolutionResult(
        decision=Decision.INSTANTIATE,
        slot=None,
        branch_name=None,
        parent_commit_ids=[],
        parent_sources=[],
        lineage_summary=None,
        commit_id=None,
        sketch_id=sk.sketch_id,
        sketch_hole_values=dict(hv),
    )


def resolve(
    portal: Any,
    slot_spec: SlotSpec,
    *,
    force_regenerate: bool = False,
    sketch_library: Any | None = None,
) -> ResolutionResult:
    """
    Resolution precedence:
    1. No slot in portal -> GENERATE
    2. force_regenerate: ADAPT from local head if commits exist; else ADAPT from best donor if any;
       else GENERATE
    3. Local commits exist, stored equivalence matches current, not forced -> REUSE local head
    4. Local commits exist, equivalence mismatch -> try sketch INSTANTIATE; else ADAPT
    5. Local slot empty (no commits): cross-slot donor REUSE; else sketch INSTANTIATE; else GENERATE
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

    from semipy.history.version_lock import locked_commit_id

    lc = locked_commit_id(slot)
    if lc is not None:
        c = slot.commits.get(lc)
        if c is not None:
            return ResolutionResult(
                decision=Decision.REUSE,
                slot=slot,
                branch_name=None,
                parent_commit_ids=[],
                parent_sources=[],
                lineage_summary=None,
                commit_id=lc,
            )

    has_commits = bool(slot.commits)
    head = _head_commit(slot) if has_commits else None
    equiv_ok = _equivalence_matches(slot, slot_spec)

    if force_regenerate:
        if head is not None:
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
        donor = _best_donor(portal, slot_spec, slot_spec.slot_id)
        if donor is not None:
            ds, dc = donor
            lineage = _lineage_summary(ds, dc.commit_id)
            return ResolutionResult(
                decision=Decision.ADAPT,
                slot=slot,
                branch_name=f"b_{slot_spec.spec_hash[:8]}",
                parent_commit_ids=[dc.commit_id],
                parent_sources=[dc.generated_source],
                lineage_summary=lineage,
                commit_id=None,
            )
        return ResolutionResult(
            decision=Decision.GENERATE,
            slot=slot,
            branch_name="main",
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=None,
        )

    if has_commits and equiv_ok:
        return ResolutionResult(
            decision=Decision.REUSE,
            slot=slot,
            branch_name=None,
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=head.commit_id if head is not None else None,
        )

    if not has_commits:
        donor = _best_donor(portal, slot_spec, slot_spec.slot_id)
        if donor is not None:
            ds, dc = donor
            return ResolutionResult(
                decision=Decision.REUSE,
                slot=slot,
                branch_name=None,
                parent_commit_ids=[],
                parent_sources=[],
                lineage_summary=None,
                commit_id=dc.commit_id,
                reuse_dispatch_slot_id=ds.slot_id,
            )
        sk_r = _try_sketch_instantiation(slot_spec, sketch_library)
        if sk_r is not None:
            sk_r.slot = slot
            return sk_r
        return ResolutionResult(
            decision=Decision.GENERATE,
            slot=slot,
            branch_name="main",
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=None,
        )

    sk_r = _try_sketch_instantiation(slot_spec, sketch_library)
    if sk_r is not None:
        sk_r.slot = slot
        return sk_r

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
