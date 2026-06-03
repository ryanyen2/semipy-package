"""Lock slot to a commit, rollback branch heads, unlock, reset — used by CLI and editor."""
from __future__ import annotations

from semipy.history import Branch, Portal, Slot, most_recent_branch_head


LOCK_REF_KEY = "__locked__"


def locked_commit_id(slot: Slot) -> str | None:
    cid = (slot.refs or {}).get(LOCK_REF_KEY)
    if isinstance(cid, str) and cid and cid in slot.commits:
        return cid
    return None


def is_slot_locked(slot: Slot) -> bool:
    return locked_commit_id(slot) is not None


def lock_slot_to_commit(portal: Portal, slot_id: str, commit_id: str) -> None:
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise KeyError(f"unknown slot_id {slot_id!r}")
    if commit_id not in slot.commits:
        raise KeyError(f"unknown commit_id {commit_id!r}")
    slot.refs[LOCK_REF_KEY] = commit_id


def unlock_slot(portal: Portal, slot_id: str) -> None:
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise KeyError(f"unknown slot_id {slot_id!r}")
    slot.refs.pop(LOCK_REF_KEY, None)


def rollback_slot(portal: Portal, slot_id: str, commit_id: str) -> None:
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise KeyError(f"unknown slot_id {slot_id!r}")
    if commit_id not in slot.commits:
        raise KeyError(f"unknown commit_id {commit_id!r}")
    name = slot.default_branch or "main"
    slot.branches[name] = Branch(name=name, head=commit_id)


def reset_slot(portal: Portal, slot_id: str) -> None:
    """Remove a slot entirely so the next call regenerates it from scratch.

    Drops all of the slot's versions (commits/branches/refs) plus its contract and
    effect ledger by removing the slot from the portal. ``_ensure_slot`` re-creates
    a fresh empty slot on the next ``execute_slot`` for that call site.
    """
    if slot_id not in portal.slots:
        raise KeyError(f"unknown slot_id {slot_id!r}")
    del portal.slots[slot_id]
    portal.spec_map.pop(slot_id, None)
    for fn, sids in list(portal.enclosing_function_slots.items()):
        portal.enclosing_function_slots[fn] = [s for s in sids if s != slot_id]
        if not portal.enclosing_function_slots[fn]:
            del portal.enclosing_function_slots[fn]


def reset_version(portal: Portal, slot_id: str, commit_id: str) -> None:
    """Delete a single version (commit) from a slot and recompute its active head.

    Removes the commit, any branch head pointing at it, and any ref (including the
    version lock) pointing at it, then re-points the default branch at the most
    recent remaining branch head. If it was the slot's only commit the slot is left
    empty (the dispatch module then skips it and the next call regenerates).
    """
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise KeyError(f"unknown slot_id {slot_id!r}")
    if commit_id not in slot.commits:
        raise KeyError(f"unknown commit_id {commit_id!r}")

    del slot.commits[commit_id]
    slot.branches = {n: b for n, b in slot.branches.items() if b.head != commit_id}
    slot.refs = {k: v for k, v in slot.refs.items() if v != commit_id}

    name = slot.default_branch or "main"
    head = most_recent_branch_head(slot)
    if head is None and slot.commits:
        # Removing the head pruned its branch; fall back to the newest remaining
        # commit (e.g. the parent) so the slot keeps a usable active version.
        head = max(slot.commits.values(), key=lambda c: c.timestamp)
    if head is not None:
        slot.branches[name] = Branch(name=name, head=head.commit_id)
    else:
        slot.branches.pop(name, None)
