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
    """Stable hash for template + constants (mirrors cache.build_template_hash)."""
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
    change_summary: Optional[ChangeSummary] = None
    existing_implementation_source: Optional[str] = None


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


class ChangeDecision(Enum):
    """LLM or rule-based decision for how to handle a change."""

    REUSE = "reuse"       # no regeneration
    REFACTOR = "refactor" # small edit to existing implementation
    REGENERATE = "regenerate"  # regenerate implementation for this semicode
    FULL_REWRITE = "full_rewrite"  # new semicode or full session regen


@dataclass
class ChangeSummary:
    """Structured summary of what changed (for LLM or rule-based decision)."""

    template_tree_changed: bool = False
    template_diff_description: str = ""
    constants_changed: bool = False
    constants_diff_description: str = ""
    source_changed: bool = False
    source_diff_description: str = ""


class SemiGenerationError(Exception):
    """Raised when the agent cannot produce a valid function after retries."""

    pass


# Protocol for deterministic tools run by the agent (e.g. code analyzer, checker).
# Called after each validation failure with (spec, source, result); returns structured info for logging.
SemiToolResult = dict[str, Any]
SemiTool = Callable[[GenerationSpec, str, ValidationResult], SemiToolResult]


# --- Session / semicode model (one session = one source file, one implementation per semicode) ---

@dataclass
class SemicodeEntry:
    """One semicode in a session: one implementation shared by multiple usages."""

    semicode_id: str
    implementation_id: str
    usage_ids: list[str] = field(default_factory=list)
    function_name: str = ""  # readable name in entry module, e.g. frame_filter_condition
    param_names: list[str] = field(default_factory=list)
    expected_type: type = type(None)
    template_fingerprint: str = ""  # structural fingerprint for tree-based match
    usage_count: int = 0
    last_validated_at: Optional[float] = None  # optional timestamp
    # For loading from legacy cache (site_id/template_hash) before module-style storage.
    primary_site_id: str = ""
    primary_template_hash: str = ""


@dataclass
class SessionIndex:
    """In-memory session index: which semicodes exist and which usage(s) map to each."""

    session_id: str
    source_file: str
    module_name: str  # human-readable, e.g. data_wrangling
    semicodes: list[SemicodeEntry] = field(default_factory=list)
    last_source_fingerprint: Optional[str] = None

    def semicode_by_id(self, semicode_id: str) -> Optional[SemicodeEntry]:
        for se in self.semicodes:
            if se.semicode_id == semicode_id:
                return se
        return None

    def semicode_by_usage_id(self, usage_id: str) -> Optional[SemicodeEntry]:
        for se in self.semicodes:
            if usage_id in se.usage_ids:
                return se
        return None

    def semicode_by_structural_fingerprint(self, fingerprint: str) -> Optional[SemicodeEntry]:
        for se in self.semicodes:
            if se.template_fingerprint == fingerprint:
                return se
        return None
