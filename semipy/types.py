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
        """Stable 16-char hash identifying this call site."""
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
    expected_type: type = type(None)  # when set, included in usage_id so value vs function style get distinct cache

    def usage_id(self) -> str:
        """Stable id for this usage (site_id + template_hash with constants + expected_type when set)."""
        th = _template_hash_for_usage(
            list(self.template.template_parts),
            self.constant_values,
        )
        type_suffix = ""
        if self.expected_type is not None and self.expected_type is not type(None):
            type_suffix = f":{getattr(self.expected_type, '__name__', repr(self.expected_type))}"
        return f"{self.call_site.site_id}:{th}{type_suffix}"


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
    """Per-call-site template and type info from decorator analysis (inline semi(f\"...\"))."""

    call_site: SemiCallSite
    template: PromptTemplate
    expected_type: type
    loop_variant_names: list[str]  # which variable_names are loop-variant (function params)
    usage_hint: str = ""


@dataclass
class NamedCallSiteInfo:
    """Per-call-site info for semi.name(...) from decorator analysis."""

    call_site: SemiCallSite
    method_name: str
    template: PromptTemplate
    expected_type: type
    loop_variant_names: list[str]
    kwarg_names: list[str]
    usage_hint: str = ""


@dataclass
class SemiformalContext:
    """Context set by @semiformal decorator for the duration of the call."""

    func_name: str
    source_code: str
    type_hints: dict[str, Any]
    semi_call_sites: list[SemiCallSiteInfo] = field(default_factory=list)
    named_call_sites: list[NamedCallSiteInfo] = field(default_factory=list)


class Decision(Enum):
    """Resolution decision: how this invocation was satisfied (reuse vs new commit)."""

    REUSE = "reuse"
    ADAPT = "adapt"  # same structure, adapt from parent commit (e.g. new prompt/constants)
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
    method_name: Optional[str] = None  # for semi.name(...) named calls
    usage_hint: str = ""


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


def _interpret_semi_call_cause(cause: BaseException) -> tuple[str, str]:
    """From the underlying exception, return (what_went_wrong, fix_hint) for the debugger summary."""
    msg = str(cause).strip()
    what = msg
    fix = "Adjust your semi() prompt or the generated implementation so the return value matches what the callee expects."
    if "is not a valid value for" in msg and "supported values are" in msg:
        try:
            import re
            m = re.search(r"supported values are ([^.]+)", msg, re.IGNORECASE)
            supported = (m.group(1).strip() if m else "").strip("'\" ")
            m2 = re.search(r"^(.+?) is not a valid value", msg)
            raw = m2.group(1).strip() if m2 else ""
            if raw.startswith("{") and "}" in raw:
                received = "a dict (e.g. kwargs). The callee expects a single value, not a dict."
            else:
                received = raw if len(raw) <= 60 else raw[:57] + "..."
            what = f"The callee expects one of {supported}, but received: {received}"
            fix = f"Make your semi() return one of {supported} (e.g. a string), not a dict."
        except Exception:
            pass
    elif "must be an instance of" in msg or ("must be" in msg and "not" in msg):
        what = msg
        fix = "Make your semi() return the type the callee expects (see the error above)."
    return (what, fix)


def _read_source_line(filename: str, lineno: int) -> Optional[str]:
    """Return the source line at filename:lineno, or None."""
    if not filename or lineno <= 0 or filename == "<unknown>":
        return None
    try:
        from pathlib import Path
        path = Path(filename).resolve()
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if 0 <= lineno - 1 < len(lines):
            return lines[lineno - 1].strip()
    except Exception:
        pass
    return None


def _read_source_lines(path: str, start_line: int, end_line: int, max_lines: int = 25) -> list[str]:
    """Return source lines from path [start_line, end_line], with line numbers. Caps at max_lines."""
    if not path or start_line <= 0:
        return []
    try:
        from pathlib import Path
        p = Path(path).resolve()
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        if start >= end:
            return []
        snippet = lines[start:end]
        if len(snippet) > max_lines:
            snippet = snippet[: max_lines - 1] + ["  ..."]
        return [f"  {start + i + 1:4d} | {s}" for i, s in enumerate(snippet)]
    except Exception:
        return []


def _relative_path_display(path: str, max_len: int = 64) -> str:
    """Return path relative to cwd for display."""
    try:
        from pathlib import Path
        p = Path(path).resolve()
        try:
            s = str(p.relative_to(Path.cwd()))
        except ValueError:
            s = p.name
        return s if len(s) <= max_len else "..." + s[-(max_len - 3) :]
    except Exception:
        return path[:max_len] if len(path) > max_len else path


class SemiCallError(Exception):
    """Raised when a generated semi() function raises at runtime. Includes a debugger-style summary."""

    def __init__(
        self,
        message: str,
        call_site: Optional[SemiCallSite] = None,
        generated_path: str = "",
        line_range: tuple[int, int] = (0, 0),
        prompt_preview: str = "",
        usage_hint: str = "",
        cause: Optional[BaseException] = None,
    ):
        super().__init__(message)
        self.call_site = call_site
        self.generated_path = generated_path
        self.line_range = line_range
        self.prompt_preview = prompt_preview
        self.usage_hint = usage_hint
        self.__cause__ = cause

    def __str__(self) -> str:
        lines = []
        cause = self.__cause__
        what, fix = _interpret_semi_call_cause(cause) if cause else (str(self), "Fix the generated code or your prompt.")

        lines.append("SEMIPY: semi() failed at runtime. Fix your code or prompt in the file below.")
        lines.append("")

        if self.call_site is not None:
            user_path = _relative_path_display(self.call_site.filename)
            lines.append(f"  Where: {user_path}:{self.call_site.lineno}")
            if self.call_site.func_qualname:
                lines.append(f"  In:    {self.call_site.func_qualname}")
            source_line = _read_source_line(self.call_site.filename, self.call_site.lineno)
            if source_line:
                lines.append(f"  Your code: {source_line}")
            elif self.prompt_preview:
                lines.append(f"  Prompt:   {self.prompt_preview[:100]}{'...' if len(self.prompt_preview) > 100 else ''}")
            lines.append("")

        if self.usage_hint:
            lines.append(f"  Result is used as: {self.usage_hint}")
            lines.append("")

        lines.append(f"  What went wrong: {what}")
        lines.append(f"  Fix: {fix}")
        lines.append("")

        if self.generated_path and self.line_range != (0, 0):
            gen_path = _relative_path_display(self.generated_path)
            s, e = self.line_range
            lines.append(f"  Generated code ({gen_path}:{s}-{e}) — this return value caused the error:")
            snippet = _read_source_lines(self.generated_path, s, e)
            lines.extend(snippet)
            lines.append("")
        elif self.generated_path:
            gen_path = _relative_path_display(self.generated_path)
            lines.append(f"  Generated implementation: {gen_path}")
            lines.append("")

        lines.append("Original error:")
        if cause is not None:
            lines.append(f"  {type(cause).__name__}: {cause}")
        else:
            lines.append(f"  {super().__str__()}")

        return "\n".join(lines)


# Protocol for deterministic tools run by the agent (e.g. code analyzer, checker).
# Called after each validation failure with (spec, source, result); returns structured info for logging.
SemiToolResult = dict[str, Any]
SemiTool = Callable[[GenerationSpec, str, ValidationResult], SemiToolResult]


