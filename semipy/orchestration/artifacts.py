"""Typed handoff artifacts exchanged between orchestration roles.

These are the contract between roles: plain, JSON-safe Pydantic models carrying
*data* (ids, sources, samples), never live objects (a ``Slot``, a compiled
function, a portal). Live objects stay in the orchestrator; roles receive and
return artifacts. Keeping them JSON-safe means they can be logged, persisted, and
unit-tested in isolation, and it keeps the orchestrator's routing deterministic.

One model per role boundary in the pipeline:

    ExplorationResult   <- code-explorer (deps, signatures, upstream, profile)
    VersionContext      <- version-checker routing (REUSE/ADAPT/GENERATE/INSTANTIATE)
    ReuseVerdict        <- version-checker reuse judge (LLM, evidence-grounded)
    GenerationResult    <- coder
    ExecutionEvidence   <- executor (deterministic; real I/O of a candidate)
    VerificationVerdict <- verifier (deterministic rules + LLM alignment)
    SurfacePlan         <- surfacer (what to surface; verified is rule-derived)
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ExplorationResult(BaseModel):
    """Read-only facts the code-explorer gathered for a slot."""

    dependency_signatures: list[str] = Field(default_factory=list)
    upstream_requirements: list[str] = Field(default_factory=list)
    data_profile: str = ""
    notes: str = ""


class VersionContext(BaseModel):
    """JSON-safe projection of the router's ``ResolutionResult``.

    Carries the routing decision and lineage without the live ``Slot`` object.
    """

    decision: str
    commit_id: Optional[str] = None
    parent_commit_ids: list[str] = Field(default_factory=list)
    parent_sources: list[str] = Field(default_factory=list)
    lineage_summary: Optional[str] = None
    reuse_dispatch_slot_id: Optional[str] = None
    sketch_id: Optional[str] = None
    sketch_hole_values: Optional[dict[str, str]] = None


class ReuseVerdict(BaseModel):
    """Evidence-grounded reuse/adapt judgment (filled by the LLM judge in U7)."""

    verdict: str  # "reuse" | "adapt"
    reasoning: str = ""
    problematic_inputs: list[str] = Field(default_factory=list)
    #: The executed I/O sample(s) the verdict cites as its grounding.
    evidence_samples: list[dict] = Field(default_factory=list)
    context_changed: list[str] = Field(default_factory=list)


class GenerationResult(BaseModel):
    """JSON-safe projection of a coder run's output."""

    generated_source: str
    function_name: Optional[str] = None
    decision: Optional[str] = None
    commit_id: Optional[str] = None


class ExecutionEvidence(BaseModel):
    """Result of running a candidate / cached implementation deterministically."""

    success: bool
    #: Observed ``{"input": ..., "output": ...}`` rows when the gist emits them.
    io_pairs: list[dict] = Field(default_factory=list)
    result_repr: Optional[str] = None
    stdout: str = ""
    error: Optional[str] = None


class VerificationVerdict(BaseModel):
    """Layered verification outcome: deterministic rules + optional LLM alignment."""

    passed: bool
    #: Set when a deterministic guard rejected (e.g. ``empty_output``, ``identity_return``).
    failure_kind: Optional[str] = None
    deterministic_passed: bool = True
    #: "aligned" | "misaligned" | None (no LLM alignment check ran).
    alignment_verdict: Optional[str] = None
    failing_samples: list[dict] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    #: Number of alignment-judge samples that voted, for trace surfacing.
    vote_count: int = 0


class SurfacePlan(BaseModel):
    """What the surfacer decided to surface; the deterministic writer applies it.

    ``verified`` is carried here for completeness but is always rule-derived
    upstream, never synthesized by an LLM.
    """

    steering_values: dict[str, Any] = Field(default_factory=dict)
    zones: list[str] = Field(default_factory=list)
    verified: Optional[str] = None


__all__ = [
    "ExplorationResult",
    "VersionContext",
    "ReuseVerdict",
    "GenerationResult",
    "ExecutionEvidence",
    "VerificationVerdict",
    "SurfacePlan",
]
