"""Core data structures for the runtime semiformal system."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class SemiCallSite:
    """Identifies where semi() is called for cache and context lookup."""

    filename: str
    lineno: int
    func_qualname: str

    @property
    def site_id(self) -> str:
        import hashlib
        key = f"{self.filename}:{self.lineno}:{self.func_qualname}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class TemplatePart:
    """One segment of a decomposed f-string: either literal or variable."""

    is_literal: bool
    value: str  # literal text or the source expression for variable


@dataclass
class PromptTemplate:
    """Decomposed f-string: literal parts and variable expressions for cache keying."""

    template_parts: list[TemplatePart]
    variable_names: list[str]  # names for generated function params (v0, c1, ...)
    variable_expressions: list[str] = field(default_factory=list)  # source code to eval at runtime


@dataclass
class CacheEntry:
    """Cached generated function and metadata."""

    site_id: str
    template_hash: str
    generated_source: str
    compiled_fn: Optional[Callable[..., Any]] = None
    expected_type: type = type(None)


@dataclass
class SemiCallSiteInfo:
    """Per-call-site template and type info from decorator analysis."""

    call_site: SemiCallSite
    template: PromptTemplate
    expected_type: type
    loop_variant_names: list[str]  # which variable_names are loop-variant (function params)


@dataclass
class SemiformalContext:
    """Context set by @semiformal decorator for the duration of the call."""

    func_name: str
    source_code: str
    type_hints: dict[str, Any]
    semi_call_sites: list[SemiCallSiteInfo] = field(default_factory=list)


@dataclass
class GenerationSpec:
    """Input to the agent for one generation request."""

    prompt: str
    call_site: SemiCallSite
    template: Optional[PromptTemplate]
    context: Optional[SemiformalContext]
    expected_type: type
    sample_input: Optional[dict[str, Any]] = None
    constant_values: Optional[dict[str, Any]] = None
    variable_values: Optional[dict[str, Any]] = None
    require_external_tools: bool = False


@dataclass
class ValidationResult:
    """Result of validating a generated function."""

    passed: bool
    ast_valid: bool
    type_correct: bool
    execution_ok: bool
    error_message: str = ""


class GenerationStrategy(Enum):
    FRESH = "fresh"
    REUSE = "reuse"
    INCREMENTAL = "incremental"


class SemiGenerationError(Exception):
    """Raised when the agent cannot produce a valid function after retries."""

    pass


# Protocol for deterministic tools run by the agent (e.g. code analyzer, checker).
# Called after each validation failure with (spec, source, result); returns structured info for logging.
SemiToolResult = dict[str, Any]
SemiTool = Callable[[GenerationSpec, str, ValidationResult], SemiToolResult]
