"""Core data structures for the runtime semiformal system."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


def session_id_from_filename(filename: str) -> str:
    """Derive a stable session id from source file path (one session = one source file)."""
    if not filename or filename == "<unknown>":
        return hashlib.sha256(b"<unknown>").hexdigest()[:16]
    normalized = filename.replace("\\", "/").strip().lower()
    if "/" in normalized:
        base = normalized.split("/")[-1]
    else:
        base = normalized
    if base.endswith(".py"):
        base = base[:-3]
    if not base:
        base = normalized
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def session_module_name_from_filename(filename: str) -> str:
    """Human-readable module name for session (e.g. data_wrangling for data_wrangling.py)."""
    if not filename or filename == "<unknown>":
        return "unknown"
    normalized = filename.replace("\\", "/").strip()
    if "/" in normalized:
        base = normalized.split("/")[-1]
    else:
        base = normalized
    if base.endswith(".py"):
        base = base[:-3]
    return base or "unknown"


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


def _template_hash_for_usage(template_parts: list[TemplatePart], constant_values: dict[str, Any]) -> str:
    """Stable hash for template + constants."""
    import json
    try:
        const_ser = json.dumps(constant_values, sort_keys=True, default=repr)
    except Exception:
        const_ser = repr(sorted(constant_values.items()))
    parts_ser = json.dumps([(p.is_literal, p.value) for p in template_parts], sort_keys=True)
    raw = f"{parts_ser}|{const_ser}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Usage:
    """A single semi() call site with concrete prompt (literal parts + variable bindings)."""

    call_site: SemiCallSite
    template: PromptTemplate
    constant_values: dict[str, Any]  # param/precomputed values that are part of cache key

    def usage_id(self) -> str:
        """Stable id for this usage (site_id + template_hash with constants)."""
        th = _template_hash_for_usage(
            list(self.template.template_parts),
            self.constant_values,
        )
        return f"{self.call_site.site_id}:{th}"


@dataclass
class CacheEntry:
    """Cached generated function and metadata."""

    generated_source: str
    compiled_fn: Optional[Callable[..., Any]] = None
    expected_type: type = type(None)
    # Display path for console (e.g. session entry module path)
    cache_display_path: Optional[str] = None


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


class Decision(Enum):
    """Resolution decision: how this invocation was satisfied (reuse vs new commit)."""

    REUSE = "reuse"
    ADVANCE = "advance"
    FORK = "fork"
    GENERATE = "generate"
    MERGE = "merge"


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
    decision: Optional[Decision] = None
    parent_sources: Optional[list[str]] = None
    parent_commit_ids: Optional[list[str]] = None
    lineage_summary: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of validating a generated function."""

    passed: bool
    ast_valid: bool
    type_correct: bool
    execution_ok: bool
    error_message: str = ""


class SemiGenerationError(Exception):
    """Raised when the agent cannot produce a valid function after retries."""

    pass


# Protocol for deterministic tools run by the agent (e.g. code analyzer, checker).
# Called after each validation failure with (spec, source, result); returns structured info for logging.
SemiToolResult = dict[str, Any]
SemiTool = Callable[[GenerationSpec, str, ValidationResult], SemiToolResult]


