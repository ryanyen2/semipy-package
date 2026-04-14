"""CommitmentRegistry — authoritative interface to accepted slot implementations.

Wraps portal load/save and version_control operations behind a clean API. The portal
remains the underlying storage format; this class enforces the semantic contract that
all commitment reads and writes go through one place.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from semipy.history.version_control import (
    Commit,
    Portal,
    most_recent_branch_head,
    walk_history,
)
from semipy.store import load_portal, save_portal


class CommitmentRegistry:
    """Provides read/write access to the slot commitment DAG.

    All portal mutations go through this class. ``portal`` is the backing store;
    call ``save()`` after mutations to persist.
    """

    def __init__(self, portal: Portal, cache_dir: Path) -> None:
        self._portal = portal
        self._cache_dir = cache_dir

    @classmethod
    def load(cls, cache_dir: Path, session_id: str, portal_anchor: str, module_name: str) -> "CommitmentRegistry":
        portal = load_portal(cache_dir, session_id, portal_anchor, module_name)
        return cls(portal, cache_dir)

    def save(self) -> None:
        save_portal(self._cache_dir, self._portal)

    @property
    def portal(self) -> Portal:
        return self._portal

    def get_active_commit(self, slot_id: str) -> Optional[Commit]:
        """Return the most recent branch head commit for this slot, or None."""
        slot = self._portal.slots.get(slot_id)
        if slot is None or not slot.commits:
            return None
        head_id = most_recent_branch_head(slot)
        if head_id is None:
            return None
        return slot.commits.get(head_id)

    def record_commit(self, slot_id: str, commit: Commit, branch_name: str = "main") -> None:
        """Add a commit to the slot's DAG and update the branch head."""
        from semipy.history.version_control import add_commit_to_slot, Branch
        slot = self._portal.slots.get(slot_id)
        if slot is None:
            return
        add_commit_to_slot(slot, commit, branch_name=branch_name)

    def get_lineage(self, slot_id: str, depth: int = 5) -> list[Commit]:
        """Return the ancestor chain of the active commit, newest first, up to depth."""
        commit = self.get_active_commit(slot_id)
        if commit is None:
            return []
        slot = self._portal.slots[slot_id]
        ancestors = walk_history(slot, commit.commit_id, max_depth=depth)
        return [slot.commits[cid] for cid in ancestors if cid in slot.commits]

    def get_donor(self, equivalence_key: str, exclude_slot_id: str) -> Optional[Commit]:
        """Find the most recent commit from another slot with the same equivalence key."""
        best: Optional[Commit] = None
        for slot_id, slot in self._portal.slots.items():
            if slot_id == exclude_slot_id:
                continue
            stored_spec = slot.slot_spec
            if not isinstance(stored_spec, dict):
                continue
            from semipy.types import equivalence_key_from_stored_snapshot
            stored_key = equivalence_key_from_stored_snapshot(stored_spec)
            if stored_key != equivalence_key:
                continue
            head_id = most_recent_branch_head(slot)
            if head_id is None:
                continue
            commit = slot.commits.get(head_id)
            if commit is None:
                continue
            if best is None or commit.timestamp > best.timestamp:
                best = commit
        return best
