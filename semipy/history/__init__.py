"""Version control (Merkle DAG) for generated implementations."""
from __future__ import annotations

from semipy.history.version_control import (
    Branch,
    Commit,
    Portal,
    Slot,
    add_commit_to_slot,
    create_commit,
    find_branch_by_fingerprint,
    find_commit_by_fingerprint,
    find_commit_by_operation_signature,
    freeze_constants,
    compute_operation_signature,
    walk_history,
)

__all__ = [
    "Branch",
    "Commit",
    "Portal",
    "Slot",
    "add_commit_to_slot",
    "create_commit",
    "find_branch_by_fingerprint",
    "find_commit_by_fingerprint",
    "find_commit_by_operation_signature",
    "freeze_constants",
    "compute_operation_signature",
    "walk_history",
]
