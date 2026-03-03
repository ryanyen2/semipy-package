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


class SemiAgentDeps(BaseModel):
    """Dependencies for the pydantic_ai agent (mutable state during run)."""

    model_config = {"arbitrary_types_allowed": True}

    spec: Any = None
    gist_builder: Any = None
    executor: Any = None
    generated_source: Optional[str] = None
    reasoning_blocks: list[str] = Field(default_factory=list)
    tool_calls_log: list[str] = Field(default_factory=list)
