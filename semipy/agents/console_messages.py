"""
Human-readable summaries for agent tool calls and results (no raw JSON dumps).

Used by console_io / agent streaming; pure functions for testing.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

from semipy.models import (
    DocumentContextResult,
    FileContextResult,
    GistRunResult,
    OutputValidationResult,
    ProfileDataResult,
    RuntimeDataContextResult,
    UpstreamContextResult,
)


def _count_non_empty_lines(s: str) -> int:
    return sum(1 for line in s.splitlines() if line.strip())


def _first_def_name(source: str) -> Optional[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node.name
        if isinstance(node, ast.AsyncFunctionDef):
            return node.name
    return None


def _relative_file(path_str: str) -> str:
    if not path_str:
        return ""
    try:
        p = Path(path_str).resolve()
        return str(p.relative_to(Path.cwd()))
    except Exception:
        return path_str


def format_tool_call_line(tool_name: str, args: Any, *, debug: bool = False) -> str:
    """
    Single-line intent for a tool invocation (no generated source body).
    `args` is a dict from ToolCallPart.args_as_dict() or empty.
    """
    if not isinstance(args, dict):
        args = {}

    if tool_name == "build_and_run_gist":
        src = args.get("generated_function_source") or ""
        n_lines = len(src.splitlines()) if src else 0
        fn = _first_def_name(src) if src else None
        base = "Test generated function in isolated run"
        if fn:
            base += f" (function {fn})"
        elif n_lines:
            base += f" ({n_lines} lines)"
        if debug and src:
            tail = src.strip().splitlines()[-1][:80] if src.strip() else ""
            if tail:
                base += f" | last line: {tail}"
        return base

    if tool_name == "validate_output":
        exp = args.get("expected_type_name") or "?"
        return f"Check return value against type {exp}"

    if tool_name == "read_file_context":
        fp = args.get("file_path") or ""
        rel = _relative_file(str(fp))
        sl = args.get("start_line")
        el = args.get("end_line")
        if sl is not None and el is not None:
            return f"Read file {rel} (lines {sl}-{el})"
        return f"Read file {rel}"

    if tool_name == "read_document_context":
        fp = args.get("file_path") or ""
        rel = _relative_file(str(fp))
        ci = args.get("chunk_index", 0)
        lh = args.get("layout_heavy")
        bk = args.get("backend") or "auto"
        extra = f", {bk}"
        if lh:
            extra += ", layout_heavy"
        return f"Read document {rel} (chunk {ci}{extra})"

    if tool_name == "profile_data_and_flow":
        code = args.get("code") or ""
        n = _count_non_empty_lines(code)
        wd = args.get("working_dir")
        base = f"Profile data flow from analysis code ({n} non-empty lines)"
        if wd and debug:
            base += f" | cwd {wd}"
        return base

    if tool_name == "get_runtime_data_context":
        return "Summarize variables in scope for this call"

    if tool_name == "read_upstream_context":
        return "Load parent implementation sources (adapt)"

    if tool_name == "list_library_primitives":
        return "List available library primitives"

    if debug:
        try:
            return f"{tool_name} {json.dumps(args, default=str)[:200]}"
        except Exception:
            return f"{tool_name} (args present)"
    return f"Run tool {tool_name}"


def _short_repr_for_result_repr(s: Optional[str], max_len: int = 72) -> str:
    if not s:
        return "no value"
    t = s.strip()
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def _gist_outcome(gr: GistRunResult) -> tuple[str, bool]:
    if gr.error:
        return (f"failed: {gr.error[:120]}", False)
    if not gr.success:
        err = (gr.stderr or "").strip() or "run failed"
        return (f"failed: {err[:120]}", False)
    rr = gr.result_repr
    ok = True
    if rr is None or rr.strip() == "":
        msg = "finished (no return value printed)"
    else:
        msg = f"finished; result {_short_repr_for_result_repr(rr)}"
    extra = []
    if (gr.stdout or "").strip():
        extra.append("stdout non-empty")
    if (gr.stderr or "").strip():
        extra.append("stderr non-empty")
    if extra and len(msg) < 60:
        msg += "; " + ", ".join(extra)
    return (msg, ok)


def _validation_outcome(ov: OutputValidationResult) -> tuple[str, bool]:
    ok = ov.valid
    if ok:
        body = (ov.message or "").strip()
        if len(body) > 100:
            body = body[:97] + "…"
        if body:
            return (f"ok — {body}", True)
        return ("ok", True)
    msg = (ov.message or "invalid").strip()
    if len(msg) > 140:
        msg = msg[:137] + "…"
    return (msg, False)


def _profile_outcome(pr: ProfileDataResult) -> tuple[str, bool]:
    if not pr.success:
        e = (pr.error or "error").strip()
        return (f"failed: {e[:120]}", False)
    summary = (pr.summary or "").strip()
    if summary:
        s = summary.replace("\n", " ")
        if len(s) > 100:
            s = s[:97] + "…"
        return (f"ok — {s}", True)
    return ("ok", True)


def _file_context_outcome(fr: FileContextResult) -> tuple[str, bool]:
    if not fr.success:
        return ((fr.error or "failed")[:120], False)
    n = len((fr.content or "").splitlines())
    return (f"read {n} lines", True)


def _document_context_outcome(dr: DocumentContextResult) -> tuple[str, bool]:
    if not dr.success:
        return ((dr.error or "failed")[:120], False)
    n = len((dr.content or "").splitlines())
    parts = [f"chunk {dr.chunk_index + 1}/{dr.total_chunks}", f"{n} lines"]
    if dr.page_count is not None:
        parts.append(f"{dr.page_count} pages")
    if (dr.source_kind or "").strip():
        parts.append(dr.source_kind.strip())
    return ("; ".join(parts), True)


def _upstream_outcome(ur: UpstreamContextResult) -> tuple[str, bool]:
    if not ur.success:
        return ((ur.error or "failed")[:120], False)
    n = len(ur.sources or [])
    return (f"{n} source(s); {ur.summary}".strip()[:140], True)


def _runtime_outcome(rr: RuntimeDataContextResult) -> tuple[str, bool]:
    if not rr.success:
        return ((rr.error or "failed")[:120], False)
    s = (rr.summary or "").strip()
    if len(s) > 80:
        s = s[:77] + "…"
    if s:
        return (f"ok — {s}", True)
    return ("ok", True)


def _coerce_to_models(tool_name: str, content: Any) -> Any:
    """Return a typed model instance if content matches; else None."""
    if content is None:
        return None
    for model, names in (
        (GistRunResult, ("build_and_run_gist",)),
        (OutputValidationResult, ("validate_output",)),
        (ProfileDataResult, ("profile_data_and_flow",)),
        (FileContextResult, ("read_file_context",)),
        (DocumentContextResult, ("read_document_context",)),
        (UpstreamContextResult, ("read_upstream_context",)),
        (RuntimeDataContextResult, ("get_runtime_data_context",)),
    ):
        if tool_name in names:
            try:
                if isinstance(content, model):
                    return content
                if isinstance(content, dict):
                    return model.model_validate(content)
            except Exception:
                pass
    return None


def format_tool_result_line(
    tool_name: str,
    content: Any,
    *,
    debug: bool = False,
) -> tuple[str, bool]:
    """
    One-line outcome for a tool return. Returns (summary, ok).
    `content` is ToolReturnPart.content (model instance or dict).
    """
    coerced = _coerce_to_models(tool_name, content)
    if isinstance(coerced, GistRunResult):
        return _gist_outcome(coerced)
    if isinstance(coerced, OutputValidationResult):
        return _validation_outcome(coerced)
    if isinstance(coerced, ProfileDataResult):
        return _profile_outcome(coerced)
    if isinstance(coerced, FileContextResult):
        return _file_context_outcome(coerced)
    if isinstance(coerced, DocumentContextResult):
        return _document_context_outcome(coerced)
    if isinstance(coerced, UpstreamContextResult):
        return _upstream_outcome(coerced)
    if isinstance(coerced, RuntimeDataContextResult):
        return _runtime_outcome(coerced)

    if tool_name == "list_library_primitives":
        s = content if isinstance(content, str) else str(content)
        s = s.strip()
        if len(s) > 120:
            s = s[:117] + "…"
        return (s or "(empty)", True)

    if debug:
        raw = repr(content)
        if len(raw) > 200:
            raw = raw[:197] + "…"
        return (raw, True)

    raw = str(content) if content is not None else ""
    if len(raw) > 100:
        raw = raw[:97] + "…"
    return (raw or "(empty)", True)


def tail_lines(text: str, max_lines: int) -> str:
    """Return the last `max_lines` lines of text (for peek window)."""
    if max_lines <= 0:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])
