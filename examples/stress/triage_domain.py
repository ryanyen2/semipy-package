"""Domain models for the support-triage app (imported by other modules)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Ticket:
    id: str
    customer_tier: str          # "free" | "pro" | "enterprise"
    subject: str
    body: str
    attachments: int = 0


@dataclass
class Triage:
    priority: Priority
    category: str               # short slug, e.g. "billing", "outage", "how-to"
    suggested_team: str
    needs_human: bool
    summary: str
    tags: list[str] = field(default_factory=list)
