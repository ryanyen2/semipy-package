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


class ProfileDataResult(BaseModel):
    """Result of profile_data_and_flow tool."""

    success: bool
    error: Optional[str] = None
    data_profile: dict[str, Any] = Field(default_factory=dict)
    data_flow: list[DataFlowStep] = Field(default_factory=list)
    summary: str = ""
    insights_placeholder: Optional[str] = None


class FileContextResult(BaseModel):
    """Result of read_file_context tool."""

    success: bool
    content: str = ""
    error: Optional[str] = None


class DocumentContextResult(BaseModel):
    """Result of read_document_context tool (text or PDF via liteparse when available)."""

    success: bool
    content: str = ""
    error: Optional[str] = None
    page_count: Optional[int] = None
    chunk_index: int = 0
    total_chunks: int = 1
    source_kind: str = ""


class UpstreamContextResult(BaseModel):
    """Result of read_upstream_context tool (parent implementations)."""

    success: bool
    sources: list[str] = Field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


class RuntimeDataContextResult(BaseModel):
    """Result of get_runtime_data_context tool (variables in scope, structure and value distributions)."""

    success: bool
    summary: str = ""
    error: Optional[str] = None


class GistRunResult(BaseModel):
    """Result of build_and_run_gist tool."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    result_repr: Optional[str] = None
    error: Optional[str] = None


class OutputValidationResult(BaseModel):
    """Result of validate_output tool."""

    valid: bool
    message: str = ""
    expected_type: str = ""
    actual_type: Optional[str] = None


class ObservationBundle(BaseModel):
    """Structured evidence returned by execute_action_program."""

    data_profile: dict[str, str] = Field(default_factory=dict)
    upstream_summary: str = ""
    file_excerpts: list[str] = Field(default_factory=list)
    document_excerpts: list[str] = Field(default_factory=list)
    search_results: list[str] = Field(default_factory=list)
    gist_result: Optional[GistRunResult] = None
    action_errors: list[str] = Field(default_factory=list)


class CommitmentRecord(BaseModel):
    """Structured artifact the agent commits to for each synthesis attempt.

    Filled by the model as a structured output. Replaces freeform reasoning_summary
    as the source of truth for #< skeleton lines and durable commit metadata.
    """

    generated_source: str
    goal: str = ""
    givens: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    decision_points: list[str] = Field(default_factory=list)
    checks_performed: list[str] = Field(default_factory=list)
    downstream_expectations: list[str] = Field(default_factory=list)
    rejected_alternatives: list[str] = Field(default_factory=list)


class SemiAgentDeps(BaseModel):
    """Dependencies for the pydantic_ai agent (mutable state during run)."""

    model_config = {"arbitrary_types_allowed": True}

    spec: Any = None
    gist_builder: Any = None
    executor: Any = None
    generated_source: Optional[str] = None
    reasoning_blocks: list[str] = Field(default_factory=list)
    tool_calls_log: list[str] = Field(default_factory=list)
