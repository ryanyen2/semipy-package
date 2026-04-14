"""RoutingPolicy — explicit, testable routing policy for slot resolution decisions.

All routing logic previously split across resolver.py and slot_resolver.py is
consolidated here. The policy evaluates signals in strict priority order and
returns a ResolutionResult with the full context needed by the caller.

Priority order (first match wins):
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
  10. Default → REUSE
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.history.version_control import Commit, Slot, most_recent_branch_head, walk_history
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
        """Evaluate routing signals in priority order and return a ResolutionResult."""

        # Case 0: No slot in portal.
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

        # Version lock: bypass all other routing when a commit is pinned.
        try:
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
        except Exception:
            pass

        has_commits = bool(slot.commits)
        head = _head_commit(slot) if has_commits else None
        equiv_ok = _equivalence_matches(slot, slot_spec)

        # Case 1: force_regenerate — ADAPT from best available parent or GENERATE.
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
            donor = _best_donor(self._portal, slot_spec, slot.slot_id)
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

        # Case 8: post-verify failure with adapt-forcing failure_kind.
        # Execution errors (execution_error, syntax_error) retry via force_regenerate=True.
        # Shape mismatches (type_mismatch, empty_output, identity_return) trigger ADAPT.
        if prior_validation is not None and not prior_validation.passed:
            failure_kind = getattr(prior_validation, "failure_kind", None)
            if failure_kind in ("type_mismatch", "empty_output", "identity_return"):
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
                return ResolutionResult(
                    decision=Decision.GENERATE,
                    slot=slot,
                    branch_name="main",
                    parent_commit_ids=[],
                    parent_sources=[],
                    lineage_summary=None,
                    commit_id=None,
                )

        # Case 9: semantic check concluded the implementation needs updating.
        if semantic_result is not None and getattr(semantic_result, "decision", None) == "adapt":
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
            return ResolutionResult(
                decision=Decision.GENERATE,
                slot=slot,
                branch_name="main",
                parent_commit_ids=[],
                parent_sources=[],
                lineage_summary=None,
                commit_id=None,
            )

        # Case 5: has commits but equivalence mismatch — INSTANTIATE (sketch) or ADAPT.
        if has_commits and not equiv_ok:
            sk_r = _try_sketch_instantiation(slot_spec, sketch_library, slot)
            if sk_r is not None:
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

        # Cases 6/7: has commits and equivalence ok → REUSE (caller runs verify).
        # If verify fails the caller re-invokes with prior_validation set (case 8).
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

        # No local commits.
        # Case 4: cross-slot donor REUSE.
        donor = _best_donor(self._portal, slot_spec, slot.slot_id)
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

        # Case 3: sketch INSTANTIATE.
        sk_r = _try_sketch_instantiation(slot_spec, sketch_library, slot)
        if sk_r is not None:
            return sk_r

        # Case 2: no commits, no donor, no sketch → GENERATE.
        return ResolutionResult(
            decision=Decision.GENERATE,
            slot=slot,
            branch_name="main",
            parent_commit_ids=[],
            parent_sources=[],
            lineage_summary=None,
            commit_id=None,
        )
