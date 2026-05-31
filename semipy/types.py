"""Core data structures for the runtime semiformal system."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def session_id_from_filename(filename: str) -> str:
    """Derive a stable session id from source file path (one session = one source file)."""
    if not filename or filename == "<unknown>":
        return hashlib.sha256(b"<unknown>").hexdigest()[:16]
    normalized = filename.replace("\\", "/").strip().lower()
    base = normalized.split("/")[-1] if "/" in normalized else normalized
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
    base = normalized.split("/")[-1] if "/" in normalized else normalized
    if base.endswith(".py"):
        base = base[:-3]
    return base or "unknown"


@dataclass(frozen=True)
class SemiCallSite:
    """Identifies where semi() is called for diagnostics and standalone slot ids."""

    filename: str
    lineno: int
    func_qualname: str

    @property
    def site_id(self) -> str:
        """Stable 16-char hash identifying this call site."""
        key = f"{self.filename}:{self.lineno}:{self.func_qualname}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class SlotCategory(Enum):
    """Durable slot category derived from where an open region lives."""

    EXPRESSION = "expression"  # inline semi(...) inside @semiformal
    EXPRESSION_STANDALONE = "standalone"  # semi(...) outside @semiformal
    STATEMENT_BLOCK = "statement"  # a #> block producing named locals
    FUNCTION_BODY = "function_body"  # whole @semiformal body (no #> found)


def _stable_slot_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def compute_spec_equivalence_key(
    spec_text: str,
    free_variables: list[str],
    expected_type: Any,
    *,
    expected_category: SlotCategory,
    output_names: list[str],
) -> str:
    """
    Stable fingerprint of the semiformal *meaning* for reuse across call sites.

    Excludes file path and line number so two semi() calls with the same template,
    arity, return contract, and slot category can share one implementation.
    """
    fv = ",".join(free_variables)
    outs = ",".join(output_names)
    raw = (
        f"{spec_text}\0{fv}\0{repr(expected_type)}\0{expected_category.value}\0{outs}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def equivalence_key_from_stored_snapshot(snapshot: dict[str, Any] | None) -> Optional[str]:
    """
    Read spec_equivalence_key from a persisted slot_spec dict, or derive the same
    fingerprint legacy portals used implicitly (spec + free vars + type repr + category + outputs).
    """
    if not snapshot:
        return None
    k = snapshot.get("spec_equivalence_key")
    if isinstance(k, str) and len(k) >= 8:
        return k
    spec_text = snapshot.get("spec_text") or ""
    fv = snapshot.get("free_variables") or []
    et = snapshot.get("expected_type")
    if et is None:
        et = repr(type(None))
    elif not isinstance(et, str):
        et = repr(et)
    cat_raw = snapshot.get("expected_category") or SlotCategory.EXPRESSION_STANDALONE.value
    outs = snapshot.get("output_names") or []
    raw = f"{spec_text}\0{','.join(fv)}\0{et}\0{cat_raw}\0{','.join(outs)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SlotSpec:
    """Durable specification for one open region (one LLM generation unit)."""

    slot_id: str
    source_span: tuple[str, int, int]
    spec_text: str
    spec_hash: str
    spec_equivalence_key: str
    free_variables: list[str]
    control_context: str
    expected_category: SlotCategory
    expected_type: Any
    output_names: list[str]
    formal_constraints: list[str]
    usage_hints: list[str]
    enclosing_function_source: str
    enclosing_function_qualname: str
    enclosing_function_span: tuple[str, int, int] = field(default=("", 0, 0))


@dataclass
class CacheEntry:
    """Cached generated function and metadata."""

    generated_source: str
    compiled_fn: Optional[Callable[..., Any]] = None
    expected_type: type = type(None)
    cache_display_path: Optional[str] = None
    reasoning_summary: Optional[str] = None
    tool_calls_made: Optional[list[str]] = None
    commitment_record: Optional[Any] = None
    # Post-validation SteeringBlock; Any to avoid circular import from semipy.models.
    steering: Optional[Any] = None


class Decision(Enum):
    """Resolution decision: how this slot implementation was satisfied."""

    REUSE = "reuse"
    ADAPT = "adapt"
    COMPOSE = "compose"
    FORK = "fork"
    GENERATE = "generate"
    MERGE = "merge"
    INSTANTIATE = "instantiate"


@dataclass
class SemiformalContext:
    """Context set by @semiformal for the duration of a call."""

    func_name: str
    source_code: str
    type_hints: dict[str, Any]
    first_lineno: int
    slot_specs: list[SlotSpec]
    scaffold_source: str
    defining_globals: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationSpec:
    """Input to the agent for one slot generation request."""

    prompt: str
    call_site: SemiCallSite
    expected_type: Any
    decision: Optional[Decision] = None

    parent_sources: Optional[list[str]] = None
    parent_commit_ids: Optional[list[str]] = None
    lineage_summary: Optional[str] = None

    slot_spec: SlotSpec | None = None
    scaffold_source: str | None = None
    sibling_slot_ids: list[str] | None = None
    sample_input: dict[str, Any] | None = None

    source_file_imports: list[str] | None = None
    upstream_lineage: Optional[list[tuple[str, str]]] = None
    downstream_requirements: Optional[dict[str, Any]] = None
    enclosing_function_source: str | None = None

    # kept for agent tooling / gist validation
    user_source_code: Optional[str] = None

    # Call-site module globals (decorated function / stack frame) for exec validation and gist preambles.
    execution_namespace: dict[str, Any] | None = None

    # Distinct values seen for each slot parameter across invocations (from portal); profiling only.
    session_input_observations: dict[str, list[str]] | None = None
    # True when current runtime_values contain only non-collection inputs (no DataFrame/Series in scope).
    runtime_profile_scalar_only: bool = False

    # When ADAPT is triggered by verify_runtime_execution failure, this carries the error so the
    # generation prompt can explain *why* the previous implementation was rejected.
    verify_failure_context: str | None = None
    # Optional: prior sketch / pattern context when ADAPT follows a failed INSTANTIATE or operator mismatch.
    sketch_context: str | None = None

    # Keys that the user has edited on-disk mapped to the user-edited string values.
    # Forwarded to the generator prompt so the next implementation aligns with the user's steering.
    steering_overrides: dict[str, str] = field(default_factory=dict)

    # One-line summary of the change's traced effect (from the commit's ChangeRecord),
    # set after generation so steering synthesis grounds `by`/`unless` in the real reason.
    change_summary: str | None = None

    # Curated examples rendered into the generation prompt (input -> expected output;
    # for effectful slots, input -> intended effects). Loaded from the slot's active
    # contract / effect cases + recent ledger so the model anchors on pinned behavior.
    # Each item: {"input": {...}, "output_repr": str, "effect_summary": str, "reason": str}.
    contract_examples: list[dict[str, Any]] | None = None


@dataclass
class ValidationResult:
    """Result of validating a generated function."""

    passed: bool
    ast_valid: bool
    type_correct: bool
    execution_ok: bool
    error_message: str = ""
    gist_executed: bool = False
    gist_stdout: str = ""
    gist_stderr: str = ""
    # Typed failure category — used by slot_resolver for deterministic routing decisions.
    # Stage 1 (boundary): "syntax_error" | "no_function" | "signature_mismatch" | "shape_mismatch"
    # Stage 2 (sandbox):  "execution_error" | "type_mismatch" | "empty_output" | "identity_return"
    failure_kind: Optional[str] = None


@dataclass
class AmbiguousFlag:
    """One ambiguous input identified by the intent judge."""

    input: str
    picked_output: str
    alternative_outputs: list[str] = field(default_factory=list)
    why: str = ""


@dataclass
class CallOutcome:
    """Record of one slot call's actual result, stored in slot.advisor_state['call_outcomes']."""

    ts: float
    runtime_input_fingerprint: str
    input_repr_short: str
    returned_type: str
    returned_repr_short: str
    raised: bool = False
    exception_type: str = ""
    ambiguity_signal: bool = False


@dataclass
class BatchOutcome:
    """Aggregate stats from a recent batch of calls (e.g. Series.apply) for one slot."""

    ts: float
    n_in: int
    n_returned: int
    n_raised: int
    n_unique_outputs: int
    n_ambiguity_signals: int = 0


class SemiGenerationError(Exception):
    """Raised when the agent cannot produce a valid function after retries."""

    def __init__(
        self,
        message: str,
        last_source: Optional[str] = None,
        last_result: Optional[ValidationResult] = None,
    ):
        super().__init__(message)
        self.last_source = last_source
        self.last_result = last_result


def _interpret_semi_call_cause(cause: BaseException) -> tuple[str, str]:
    """From the underlying exception, return (what_went_wrong, fix_hint)."""
    msg = str(cause).strip()
    what = msg
    fix = "Adjust your semi() prompt or the generated implementation so the return value matches what the callee expects."
    if "must be an instance of" in msg or ("must be" in msg and "not" in msg):
        what = msg
        fix = "Make your semi() return the type the callee expects (see the error above)."
    if isinstance(cause, TypeError):
        if "unhashable type" in msg.lower() and "dict" in msg:
            what = (
                f"{msg} — Often the callee expected a string or other hashable (e.g. a Matplotlib scale name), "
                "but received a dict or wrong type from semi()."
            )
            fix = (
                "Pass the right expected_type to semi() (e.g. expected_type=str), or use structured values from "
                "another function (e.g. axis config) instead of a second semi() for the same field."
            )
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
    """Raised when a generated semi() function raises at runtime."""

    def __init__(
        self,
        message: str,
        call_site: Optional[SemiCallSite] = None,
        generated_path: str = "",
        line_range: tuple[int, int] = (0, 0),
        prompt_preview: str = "",
        cause: Optional[BaseException] = None,
    ):
        super().__init__(message)
        self.call_site = call_site
        self.generated_path = generated_path
        self.line_range = line_range
        self.prompt_preview = prompt_preview
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
# Called after each validation failure with (spec, source, result).
SemiToolResult = dict[str, Any]
SemiTool = Callable[[GenerationSpec, str, ValidationResult], SemiToolResult]


