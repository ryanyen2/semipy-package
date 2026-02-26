"""Merkle DAG versioning: Commit, Branch, Slot, Portal and DAG operations."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


def compute_source_hash(generated_source: str) -> str:
    """Return a 20-char hash of the generated source for commit identity."""
    return hashlib.sha256(generated_source.encode()).hexdigest()[:20]


def freeze_constants(constant_values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Stable (key, repr(val)) pairs for constants."""
    items = sorted((k, repr(v)) for k, v in (constant_values or {}).items())
    return tuple(items)


def _constants_hash(constants_snapshot: tuple[tuple[str, str], ...]) -> str:
    raw = repr(constants_snapshot)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_operation_signature(template_fingerprint: str, constants_snapshot: tuple[tuple[str, str], ...]) -> str:
    raw = f"{template_fingerprint}:{_constants_hash(constants_snapshot)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def compute_commit_id(parent_ids: tuple[str, ...], source_hash: str) -> str:
    """Stable commit id from parent ids and source hash."""
    key = "".join(sorted(parent_ids)) + source_hash
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def message_for_decision(decision: str) -> str:
    if decision == "GENERATE":
        return "initial implementation"
    if decision == "ADAPT":
        return "adapt for new parameters"
    if decision == "COMPOSE":
        return "compose from library primitive"
    if decision == "FORK":
        return "new branch, structure changed"
    if decision == "MERGE":
        return "merge branches"
    return decision.lower()


@dataclass(frozen=True)
class Commit:
    commit_id: str
    parent_ids: tuple[str, ...]
    generated_source: str
    source_hash: str
    template_fingerprint: str
    constants_snapshot: tuple[tuple[str, str], ...]
    operation_signature: str
    prompt_snapshot: str
    timestamp: float
    message: str
    decision: str
    usage_id: str = ""


@dataclass
class Branch:
    name: str
    head: str


@dataclass
class Slot:
    slot_id: str
    call_site_info: dict[str, Any]
    function_name_base: str
    commits: dict[str, Commit] = field(default_factory=dict)
    branches: dict[str, Branch] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    default_branch: str = "main"
    upstream_slot_refs: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Portal:
    session_id: str
    source_file: str
    module_name: str
    slots: dict[str, Slot] = field(default_factory=dict)


def create_commit(
    parent_ids: tuple[str, ...],
    generated_source: str,
    template_fingerprint: str,
    constants_snapshot: tuple[tuple[str, str], ...],
    prompt_snapshot: str,
    decision: str,
    usage_id: str = "",
) -> Commit:
    """Build a new Commit and compute its id, source_hash, and operation_signature."""
    source_hash = compute_source_hash(generated_source)
    commit_id = compute_commit_id(parent_ids, source_hash)
    operation_signature = compute_operation_signature(template_fingerprint, constants_snapshot)
    message = message_for_decision(decision)
    return Commit(
        commit_id=commit_id,
        parent_ids=parent_ids,
        generated_source=generated_source,
        source_hash=source_hash,
        template_fingerprint=template_fingerprint,
        constants_snapshot=constants_snapshot,
        operation_signature=operation_signature,
        prompt_snapshot=prompt_snapshot,
        timestamp=time.time(),
        message=message,
        decision=decision,
        usage_id=usage_id,
    )


def add_commit_to_slot(
    slot: Slot,
    commit: Commit,
    branch_name: str,
    usage_id: str,
) -> None:
    """Add commit to slot, set branch head, and register ref from usage_id to commit."""
    slot.commits[commit.commit_id] = commit
    slot.branches[branch_name] = Branch(name=branch_name, head=commit.commit_id)
    slot.refs[usage_id] = commit.commit_id


def find_commit_by_operation_signature(
    slot: Slot, operation_signature: str, usage_id: str
) -> Commit | None:
    """Return a commit with this operation_signature only if it was generated for this usage_id."""
    for c in slot.commits.values():
        if c.operation_signature != operation_signature:
            continue
        if not c.usage_id or c.usage_id == usage_id:
            return c
    return None


def find_commit_by_fingerprint(
    slot: Slot, template_fingerprint: str, usage_id: str
) -> Commit | None:
    """Return a commit with this template_fingerprint only if it was generated for this usage_id (most recent by timestamp)."""
    candidates = [
        c
        for c in slot.commits.values()
        if c.template_fingerprint == template_fingerprint and (not c.usage_id or c.usage_id == usage_id)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.timestamp)


def find_branches_by_fingerprint(slot: Slot, template_fingerprint: str) -> list[tuple[str, Commit]]:
    """Return all (branch_name, head_commit) whose head has this fingerprint."""
    out: list[tuple[str, Commit]] = []
    for name, branch in slot.branches.items():
        head = slot.commits.get(branch.head)
        if head is not None and head.template_fingerprint == template_fingerprint:
            out.append((name, head))
    return out


def find_branch_by_fingerprint(slot: Slot, template_fingerprint: str) -> tuple[str, Commit] | None:
    """Best (branch_name, head_commit): prefer default_branch, then most-recent head."""
    candidates = find_branches_by_fingerprint(slot, template_fingerprint)
    if not candidates:
        return None
    default = slot.default_branch

    def key(item: tuple[str, Commit]) -> tuple[int, float]:
        name, head = item
        prefer_default = 0 if name == default else 1
        return (prefer_default, -head.timestamp)

    candidates.sort(key=key)
    return candidates[0]


def walk_history(slot: Slot, commit_id: str) -> list[Commit]:
    """Topological ancestor walk from commit_id (commit first, then parents)."""
    result: list[Commit] = []
    seen: set[str] = set()
    stack = [commit_id]
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        c = slot.commits.get(cid)
        if c is None:
            continue
        result.append(c)
        for pid in c.parent_ids:
            stack.append(pid)
    return result
