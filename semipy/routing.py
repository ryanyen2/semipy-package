"""RoutingPolicy — explicit, testable routing policy for slot resolution decisions.

Fetching the signals (locked-commit lookup, donor search, sketch matching --
all real I/O against the portal) stays here. Deciding what they mean -- the
priority cascade below -- is ``kernel.policy.decide_route`` (Phase 6, §5): one
canonical, pure, unit-testable function instead of logic embedded in this
I/O-touching class, so nothing outside ``kernel/`` can drift from it.

Priority order (first match wins; see ``kernel.policy.decide_route``):
  0. No slot in portal → GENERATE
  L. Version lock present → REUSE (locked commit)
  1. force_regenerate=True → ADAPT from head; ADAPT from donor if no head; GENERATE
  8. prior_validation with failure_kind in {type_mismatch, empty_output, identity_return} → ADAPT
  9. semantic_result.decision == "adapt" → ADAPT
  5. Has commits, equivalence mismatch → INSTANTIATE (sketch) or ADAPT
  6/7. Has commits, equivalence ok → REUSE (caller verifies; re-invoke with prior_validation on fail)
  4. No local commits, donor found → REUSE (from donor)
  3. No local commits, sketch found → INSTANTIATE
  2. No local commits, no donor, no sketch → GENERATE
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.history.version_control import Commit, Slot, most_recent_branch_head, walk_history
from semipy.kernel.policy import decide_route
from semipy.resolver import ResolutionResult
from semipy.types import Decision, SlotSpec, ValidationResult, equivalence_key_from_stored_snapshot


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


def _best_donor(
    portal: Any,
    slot_spec: SlotSpec,
    current_slot_id: str,
) -> Optional[tuple[Slot, Commit]]:
    target = slot_spec.spec_equivalence_key
    candidates: list[tuple[Slot, Commit]] = []
    for sid, s in portal.slots.items():
        if sid == current_slot_id:
            continue
        key = _stored_equivalence(s)
        if key != target:
            continue
        head = _head_commit(s)
        if head is None:
            continue
        candidates.append((s, head))
    if not candidates:
        return None
    candidates.sort(key=lambda t: -t[1].timestamp)
    return candidates[0]


def _try_sketch_instantiation(
    slot_spec: SlotSpec,
    sketch_library: Any,
    slot: Optional[Slot],
) -> Optional[ResolutionResult]:
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
        slot=slot,
        branch_name=None,
        parent_commit_ids=[],
        parent_sources=[],
        lineage_summary=None,
        commit_id=None,
        sketch_id=sk.sketch_id,
        sketch_hole_values=dict(hv),
    )


class RoutingPolicy:
    """Explicit routing policy for slot resolution decisions.

    All routing decisions are evaluated in one place. Pass ``prior_validation``
    or ``semantic_result`` on a second call after verify/semantic check fails to
    get the re-route decision without re-running the full resolution chain.
    """

    def __init__(self, portal: Any) -> None:
        self._portal = portal

    def decide(
        self,
        slot_spec: SlotSpec,
        slot: Optional[Slot],
        *,
        force_regenerate: bool = False,
        sketch_library: Any = None,
        prior_validation: Optional[ValidationResult] = None,
        semantic_result: Optional[Any] = None,
    ) -> ResolutionResult:
        """Fetch the routing signals, hand them to ``kernel.policy.decide_route``
        for the precedence call, then attach whichever object the winning
        source names to a ``ResolutionResult``."""
        if slot is None:
            return ResolutionResult(
                decision=Decision.GENERATE, slot=None, branch_name="main",
                parent_commit_ids=[], parent_sources=[], lineage_summary=None, commit_id=None,
            )

        locked_commit: Optional[Commit] = None
        try:
            from semipy.history.version_lock import locked_commit_id
            lc = locked_commit_id(slot)
            if lc is not None:
                locked_commit = slot.commits.get(lc)
        except Exception:
            pass

        has_commits = bool(slot.commits)
        head = _head_commit(slot) if has_commits else None
        equiv_ok = _equivalence_matches(slot, slot_spec)
        # These two signals are now computed eagerly (decide_route is a pure
        # function of resolved signals), but they do real work -- a donor search
        # across the portal's slots and a sketch-library lookup. A raise in
        # either must not propagate on a path that never consumes it (a version
        # lock, a force_regenerate-with-head), which is what the pre-Phase-6
        # lazy cascade guaranteed by only touching them inside their branches.
        # Degrade to "signal absent" instead, matching the locked-commit lookup
        # above.
        try:
            donor = _best_donor(self._portal, slot_spec, slot.slot_id)
        except Exception:
            donor = None
        try:
            sketch_result = _try_sketch_instantiation(slot_spec, sketch_library, slot)
        except Exception:
            sketch_result = None

        failure_kind = None
        if prior_validation is not None and not prior_validation.passed:
            failure_kind = getattr(prior_validation, "failure_kind", None)
        semantic_wants_adapt = (
            semantic_result is not None and getattr(semantic_result, "decision", None) == "adapt"
        )

        route = decide_route(
            has_slot=True,
            is_locked=locked_commit is not None,
            force_regenerate=force_regenerate,
            has_head=head is not None,
            has_commits=has_commits,
            equiv_ok=equiv_ok,
            prior_validation_failure_kind=failure_kind,
            semantic_wants_adapt=semantic_wants_adapt,
            donor_available=donor is not None,
            sketch_available=sketch_result is not None,
        )

        if route.source == "locked":
            assert locked_commit is not None
            return ResolutionResult(
                decision=Decision.REUSE, slot=slot, branch_name=None,
                parent_commit_ids=[], parent_sources=[], lineage_summary=None,
                commit_id=locked_commit.commit_id,
            )

        if route.source == "sketch":
            assert sketch_result is not None
            return sketch_result

        if route.source == "donor":
            assert donor is not None
            ds, dc = donor
            if route.decision == Decision.ADAPT:
                return ResolutionResult(
                    decision=Decision.ADAPT, slot=slot,
                    branch_name=f"b_{slot_spec.spec_hash[:8]}",
                    parent_commit_ids=[dc.commit_id], parent_sources=[dc.generated_source],
                    lineage_summary=_lineage_summary(ds, dc.commit_id), commit_id=None,
                )
            return ResolutionResult(
                decision=Decision.REUSE, slot=slot, branch_name=None,
                parent_commit_ids=[], parent_sources=[], lineage_summary=None,
                commit_id=dc.commit_id, reuse_dispatch_slot_id=ds.slot_id,
            )

        if route.source == "head":
            if route.decision == Decision.ADAPT:
                lineage = _lineage_summary(slot, head.commit_id) if head is not None else None
                return ResolutionResult(
                    decision=Decision.ADAPT, slot=slot,
                    branch_name=f"b_{slot_spec.spec_hash[:8]}",
                    parent_commit_ids=[head.commit_id] if head is not None else [],
                    parent_sources=[head.generated_source] if head is not None else [],
                    lineage_summary=lineage, commit_id=None,
                )
            return ResolutionResult(
                decision=Decision.REUSE, slot=slot, branch_name=None,
                parent_commit_ids=[], parent_sources=[], lineage_summary=None,
                commit_id=head.commit_id if head is not None else None,
            )

        # route.source == "none": GENERATE (or a headless/donorless ADAPT fallback).
        return ResolutionResult(
            decision=route.decision, slot=slot, branch_name="main",
            parent_commit_ids=[], parent_sources=[], lineage_summary=None, commit_id=None,
        )
