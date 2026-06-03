"""
Pydantic models for agent tool I/O and dependencies.

Used by the pydantic_ai agent: tool arguments and return values are typed
for validation, streaming, and structured logging.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class DataFlowStep(BaseModel):
    """Single step in the data dependency flow (from AST + optional profile)."""

    step_id: int
    output_var: str
    input_vars: list[str]
    code_snippet: str
    line_no: Optional[int] = None
    shape_after: Optional[list[int]] = None
    dtypes_summary: Optional[dict[str, str]] = None
    change_summary: str = ""


class SkeletonNote(BaseModel):
    """One inline #< annotation placed relative to a code anchor in the user's source.

    anchor: substring of a code line to insert this note BEFORE.
           Empty string means the very start of the function body.
           The special value "RETURN" means just before the first return statement.
    tag:   one of Task, Given, Then, When, And, But, Verify.
           Task and Verify are always present; others are used as needed.
    text:  concise annotation, ideally ≤ 12 words.
    """

    tag: str
    text: str
    anchor: str = ""


class CommitmentRecord(BaseModel):
    """Structured artifact the agent commits to for each synthesis attempt.

    generated_source: the validated function source (pure Python, no #< lines).
    goal:             ≤ 15 words describing what this function produces (for trace).
    annotations:      deprecated — kept for backward compatibility with existing portals; leave empty.
    rejected_alternatives: brief notes on alternatives tried (for trace only).
    steering:         optional SteeringBlock synthesized post-validation; persists the keyword surface.
    """

    generated_source: str
    goal: str = ""
    annotations: list[SkeletonNote] = Field(default_factory=list)
    rejected_alternatives: list[str] = Field(default_factory=list)
    steering: Optional["SteeringBlock"] = None


class SteeringEntry(BaseModel):
    """One keyword entry in a SteeringBlock, with a stability signature."""

    value: str = ""
    input_sig: str = ""  # SHA256 hex of the causal inputs; "" means user-frozen
    user_frozen: bool = False  # True when the user edited the value on-disk


class SteeringBlock(BaseModel):
    """Structured keyword-value surface written as `#< key: value` around each slot anchor.

    Zone P (above anchor): intent, given, by, unless.
    Zone E (below anchor): yields, verified.

    The input_sig on each entry drives stability: unchanged signatures carry
    values forward verbatim across re-generation.
    """

    intent: SteeringEntry = Field(default_factory=SteeringEntry)
    given: list[SteeringEntry] = Field(default_factory=list)   # 0-3 entries
    by: SteeringEntry = Field(default_factory=SteeringEntry)
    unless: list[SteeringEntry] = Field(default_factory=list)  # 0-2 entries
    yields: SteeringEntry = Field(default_factory=SteeringEntry)
    verified: SteeringEntry = Field(default_factory=SteeringEntry)


CommitmentRecord.model_rebuild()


class SemiAgentDeps(BaseModel):
    """Dependencies for the pydantic_ai agent (mutable state during run)."""

    model_config = {"arbitrary_types_allowed": True}

    spec: Any = None
    executor: Any = None
    generated_source: Optional[str] = None
    reasoning_blocks: list[str] = Field(default_factory=list)
    tool_calls_log: list[str] = Field(default_factory=list)
