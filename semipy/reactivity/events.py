"""Event types and payloads for the reactive system."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class EventType(Enum):
    COMMIT_CREATED = "commit_created"
    SLOT_STALE = "slot_stale"
    SLOT_REGENERATED = "slot_regenerated"
    LIBRARY_UPDATED = "library_updated"
    DEPENDENCY_ADDED = "dependency_added"


@dataclass(frozen=True)
class ReactiveEvent:
    """Immutable event emitted by the reactive system."""

    event_type: EventType
    source_ref: Any
    timestamp: float
    payload: dict[str, Any]

