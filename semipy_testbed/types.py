"""
Core types for testbed inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class SimpleInferenceResult:
    """
    Result of one-shot semiformal inference (parse → generate → validate).
    
    Attributes:
        success: Whether inference and validation succeeded.
        compiled_function: The executable function (when success=True).
        source_code: Full generated Python source code.
        gist_source: Minimal standalone script (for isolated execution).
        execution_stdout: Stdout from gist execution.
        execution_stderr: Stderr from gist execution.
        execution_result: Result extracted from gist (parsed from stdout marker).
        error: Error message (when success=False).
        reasoning: LLM reasoning trace (if verbose=True).
    """

    success: bool
    compiled_function: Optional[Callable] = None
    source_code: str = ""
    gist_source: str = ""
    execution_stdout: str = ""
    execution_stderr: str = ""
    execution_result: str = ""
    error: Optional[str] = None
    reasoning: Optional[str] = None


@dataclass
class GistExecutorResult:
    """Result of gist execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    result_repr: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ValidationReport:
    """Validation result details."""

    syntax_ok: bool = False
    syntax_error: Optional[str] = None
    execution_ok: bool = False
    execution_error: Optional[str] = None
    type_ok: bool = False
    type_error: Optional[str] = None

    @property
    def passed(self) -> bool:
        """All checks passed."""
        return self.syntax_ok and self.execution_ok and self.type_ok

    @property
    def error_message(self) -> str:
        """First error encountered."""
        if not self.syntax_ok:
            return f"Syntax error: {self.syntax_error}"
        if not self.execution_ok:
            return f"Execution error: {self.execution_error}"
        if not self.type_ok:
            return f"Type error: {self.type_error}"
        return ""
