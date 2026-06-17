"""The decision data model (U6/U7): Branch, Decision, DecisionSet.

A ``Decision`` is the feature-fate node the user navigates: an ambiguity germ in
the input, the set of fates candidates gave it, an optional guard, and the
distribution over candidates. A ``DecisionSet`` is the content-addressed artifact
persisted per slot resolution; it references *all* candidate sources (including
the losers) so a later pick can swap the committed head without regenerating.

All three types are JSON round-trippable for portal persistence and as the
contract the VS Code extension renders.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Branch:
    """One behavioral fate a set of candidates gave the ambiguity germ."""

    fate_label: str
    candidate_ids: list[str] = field(default_factory=list)
    weight: float = 0.0
    signature: list[str] = field(default_factory=list)
    example_in: Any = None
    example_out: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fate_label": self.fate_label,
            "candidate_ids": list(self.candidate_ids),
            "weight": self.weight,
            "signature": list(self.signature),
            "example_in": self.example_in,
            "example_out": self.example_out,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Branch":
        return cls(
            fate_label=d.get("fate_label", ""),
            candidate_ids=list(d.get("candidate_ids", [])),
            weight=d.get("weight", 0.0),
            signature=list(d.get("signature", [])),
            example_in=d.get("example_in"),
            example_out=d.get("example_out"),
        )


@dataclass
class Decision:
    """One surfaced fork: a germ, its fates, a guard, a distribution, a status."""

    germ: str
    axis_label: str
    branches: list[Branch] = field(default_factory=list)
    guard: Optional[str] = None
    consequence: float = 0.0
    consequence_kind: str = ""
    status: str = "open"  # "open" | "resolved"
    resolution: Optional[dict[str, Any]] = None
    labeled: bool = False  # True when an LLM named axis/fates (vs deterministic)
    decision_id: str = ""

    def __post_init__(self) -> None:
        if not self.decision_id:
            sig = "|".join(",".join(b.signature) for b in self.branches)
            self.decision_id = _hash(f"{self.germ}\0{sig}")

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "germ": self.germ,
            "axis_label": self.axis_label,
            "branches": [b.to_dict() for b in self.branches],
            "guard": self.guard,
            "consequence": self.consequence,
            "consequence_kind": self.consequence_kind,
            "status": self.status,
            "resolution": self.resolution,
            "labeled": self.labeled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        return cls(
            germ=d.get("germ", ""),
            axis_label=d.get("axis_label", ""),
            branches=[Branch.from_dict(b) for b in d.get("branches", [])],
            guard=d.get("guard"),
            consequence=d.get("consequence", 0.0),
            consequence_kind=d.get("consequence_kind", ""),
            status=d.get("status", "open"),
            resolution=d.get("resolution"),
            labeled=d.get("labeled", False),
            decision_id=d.get("decision_id", ""),
        )


@dataclass
class DecisionSet:
    """All decisions for one slot resolution, plus every candidate's source."""

    slot_id: str = ""
    decisions: list[Decision] = field(default_factory=list)
    candidates: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.decisions

    def open_decisions(self) -> list[Decision]:
        return [d for d in self.decisions if d.is_open]

    def decision_by_id(self, decision_id: str) -> Optional[Decision]:
        return next((d for d in self.decisions if d.decision_id == decision_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "decisions": [d.to_dict() for d in self.decisions],
            "candidates": dict(self.candidates),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionSet":
        return cls(
            slot_id=d.get("slot_id", ""),
            decisions=[Decision.from_dict(x) for x in d.get("decisions", [])],
            candidates=dict(d.get("candidates", {})),
        )
