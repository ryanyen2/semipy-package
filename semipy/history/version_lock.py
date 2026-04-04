"""Lock slot to a commit, rollback branch heads, unlock — used by CLI and editor."""
from __future__ import annotations

from semipy.history import Branch, Portal, Slot


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
