"""
Write `#<` reasoning skeleton lines into the user's source file after GENERATE/ADAPT.

`#>` lines are user spec (never modified). `#<` lines are system-managed: stripped on each
new generation and replaced with a fresh structured skeleton derived from the model's
reasoning summary and generated implementation.

**When a file is updated**

- Only ``@semiformal`` (and similar) slots carry ``enclosing_function_source``; the writer
  replaces the enclosing function in that ``.py`` file. **Standalone** ``semi(...)`` outside
  any decorated function has no enclosing function body to attach to, so nothing is written.
- The skeleton pass runs only when resolution is **GENERATE** or **ADAPT**. On **REUSE** the
  pipeline does not call the writer, so existing ``#<`` lines are left as-is (no refresh).
"""
from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import os
import threading
import traceback
from pathlib import Path

from pydantic_ai import Agent

from semipy.agents.config import get_config
from semipy.agents.console_io import print_pipeline_log
from semipy.agents.generator import _create_openai_model, _create_openrouter_model
from semipy.lowering import strip_skeleton_lines
from semipy.types import CacheEntry, SlotSpec, SemiCallSite
import re

_file_write_locks: dict[str, threading.Lock] = {}
_file_write_locks_mutex = threading.Lock()


def _pipeline_trace_skeleton() -> bool:
    """Log raw skeleton LLM output when ``SEMIPY_PIPELINE_TRACE`` is set (same as agent pipeline trace)."""
    return os.getenv("SEMIPY_PIPELINE_TRACE", "").strip().lower() in ("1", "true", "yes")


def _log_surface(slot_spec: SlotSpec, message: str) -> None:
    call_site = SemiCallSite(
        filename=slot_spec.source_span[0],
        lineno=slot_spec.source_span[1],
        func_qualname=slot_spec.enclosing_function_qualname,
    )
    print_pipeline_log(call_site, "surface", message)


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _file_write_locks_mutex:
        if key not in _file_write_locks:
            _file_write_locks[key] = threading.Lock()
        return _file_write_locks[key]


def _spec_marker_lines(source: str) -> list[str]:
    """
    Normalized text of each user spec fragment from `#>` / `# >`, in source order.

    Matches lowering: full-line `#>` blocks and inline `#>` on the same line as code
    (e.g. `x = ... #> infer ...`) each contribute one entry.
    """
    out: list[str] = []
    for line in source.splitlines():
        s = line.lstrip()
        if s.startswith("#>") or s.startswith("# >"):
            if s.startswith("#>"):
                out.append(s[2:].strip())
            else:
                out.append(s[3:].strip())
        elif "#>" in line:
            idx = line.find("#>")
            out.append(line[idx + 2 :].strip())
        elif "# >" in line:
            idx = line.find("# >")
            out.append(line[idx + 3 :].strip())
    return out


def _call_skeleton_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    timeout: float = 120.0,
) -> str | None:
    """
    Single-turn text generation via pydantic_ai, matching ``generator.py`` backend selection:
    OpenAI when ``OPENAI_API_KEY`` / ``configure(openai_api_key=...)`` is set, else OpenRouter.
    Runs in a worker thread with its own event loop (safe from daemon threads and nested async).
    """
    async def _async_skeleton() -> str | None:
        config = get_config()
        use_openai = bool(config.openai_api_key) or bool(os.getenv("OPENAI_API_KEY"))
        try:
            if use_openai:
                model, settings = _create_openai_model(config)
            else:
                model, settings = _create_openrouter_model(config)
        except ValueError:
            return None
        agent = Agent(
            model,
            model_settings=settings,
            output_type=str,
            system_prompt=system_prompt,
        )
        result = await agent.run(user_prompt)
        out = getattr(result, "output", None)
        if isinstance(out, str) and out.strip():
            return out
        return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _async_skeleton()).result(timeout=timeout)
    except Exception:
        if get_config().verbose or _pipeline_trace_skeleton():
            traceback.print_exc()
        return None


def _build_skeleton_prompts(
    fn_source_clean: str,
    generated_source: str,
    reasoning_summary: str,
) -> tuple[str, str]:
    rs = reasoning_summary.strip() if reasoning_summary.strip() else "(none)"
    system_prompt = """You are generating brief program-tracing lines as Python comments—called "reasoning surfaces" (`#<`)—so the user can read and edit intent separately from executable code.

**Source of truth (non-negotiable)**

- The block labeled "Enclosing function" in the user message is the exact text that must appear in your output, except that you **insert new whole lines** whose first non-whitespace characters are `#<`.
- **Character-for-character preservation:** Every existing line of code, every string literal, every `...` ellipsis placeholder, every import, and every user `#>` / `# >` fragment must stay exactly as given (same order, same line breaks; inline `#>` stays on the same code line). You are **not** allowed to "fix", refactor, or complete placeholders using the separate "Generated implementation" section.
- **Generated implementation is reference only:** It shows how the slot was implemented for testing. Use it to understand behavior when writing `#<` text. **Do not paste** statements, assignments, return shapes, or literals from that section into the enclosing function. If the enclosing function still has `...` where the generator used a concrete value, **keep `...`** in your output.
- **No duplicate semantics:** `#<` lines should add rationale, control flow, or risk notes that are **not** already stated in a `#>` line on the same stretch of code. Prefer "why / what to watch" over repeating the user's spec wording.

Your output should:
- Insert `#<` annotated reasoning traces within the function (never outside), matching code block indentation.
- For each explanation, use a [Tag] (first token after `#<`), including [Task] (first), and choose from: [Given], [Then], [When], [But], [Verify], both alone and mixed as needed.
- Each `#<` line should be true reasoning (non-obvious step, intent, constraint, or implication), not a line-by-line recap of the code.
- Insert between 2 and 6 `#<` lines: each after-tag phrase must use **no more than 8 words** (prefer 4-6), e.g. "parse then format", "watch ambiguous month order".
- Do NOT emit filler or blank comment lines. Do NOT wrap the function in markdown fences or prose before or after it.

# Steps

- Read the enclosing function and each `#>` fragment so you understand constraints.
- Use the generated implementation only to inform what `#<` should emphasize (edge cases, ordering), without copying its code into the output.
- Add `#<` lines that a user could edit later to steer the next generation.

# Output Format

Return **only** the full, syntactically valid Python function: from `def` or `async def` through the last line of the function body. No markdown code fences, no preamble, no trailing commentary. The only new lines you add are `#<` lines.

# Examples

Here are several diverse, high-quality examples, demonstrating a spectrum of domains, mixed tone and formality, and strictly reasoning-based traces, not program summaries:

---
**Example 1 (natural language clean-up and transformation)**

def clean_title(title: str) -> str:
    #< [Task] enforce title tidy heuristic
    title = "untitled" if not title else title.strip()
    #< [When] missing, fallback to generic title
    #< [But] trim whitespace, skip in test
    #> Normalize and sanitize the given title string. If missing or blank, use "untitled".
    #> In tests skip fields with mock titles.
    title = title.lower()
    #< [Then] conversion to lowercase standardizes
    return title

---
**Example 2 (event log enrichment, mixing formality and informality)**

def tag_log(log: dict) -> dict:
    #< [Task] enrich log with family and iso time
    ts = log.get("timestamp")
    #< [Given] timestamp should be ISO8601
    fam = semi("family name?", context=log)
    #< [When] family not present parse from body
    #< [Verify] result aligns with context sample
    log["family"] = fam
    log["iso_time"] = ts
    return log

---
**Example 3 (decision point, complex branching, informal phrase)**

def process_invoice(doc: dict) -> str:
    #< [Task] route by category, then extract id
    if not doc.get("supplier"):
        #< [When] supplier missing, abort fast
        return "failed"
    cat = get_category(doc)
    #< [Then] use vendor rules for categorizing
    #> Apply supplier rules to process and classify the invoice
    inv_id = doc.get("invoice_id")
    #< [Verify] invoice id really conforms (loose check)
    return f"{cat}:{inv_id or 'unknown'}"

---
**Example 4 (pure ML inference, formal-informal blend)**

def predict_sentiment(inputs: list[str]) -> float:
    #< [Task] aggregate ML prediction for sentiment
    preds = ml_model.predict(inputs)
    #< [Given] input list cleaned above, else warn
    val = float(sum(preds) / len(preds))
    #< [But] weighted mean unnecessary here
    #> Compute aggregate sentiment using pretrained model.
    return val

---
(Real tasks should draw from varied code and domains. These are shortened to fit; typical traces and surfaces may involve longer or more technical tags/logic.)

# Notes

- All `#<` lines must be concise, informative reasoning surfaces. Do not turn program actions into simple summaries; your annotations should help users see non-trivial logic and iterate/refine accordingly.
- Reasoning should be discoverable—highlight why, not merely what. Emphasize points of design choice, decision, or error-prone areas.
- Strive for a mix of tags, tones, and application scenarios.
- Never start an annotation with a conclusion; always supply reasoning context first.
- If `#>` already states a rule, `#<` should add rationale/context around it, not duplicate it.
- Your goal is for the user to look at the annotated reasoning traces and directly edit them to revise program logic in future iterations.

Reminder: Insert annotations inside function body only; always reason before conclusions or actions; be succinct but illuminating; enable iterative user editing."""
    user_prompt = (
        "Enclosing function (copy this exactly into your reply, inserting only new `#<` lines; "
        "keep every `#>` line and inline `#>` fragment verbatim):\n"
        "```python\n"
        + fn_source_clean.strip()
        + "\n```\n\n"
        + "Generated implementation for this slot (reference only; do not paste into the enclosing function):\n"
        + "```python\n"
        + generated_source.strip()
        + "\n```\n\n"
        + "Model reasoning summary (may be empty):\n"
        + rs
        + "\n\n"
        + "Reply with the complete function as plain Python only (no markdown around the whole answer). "
        + "Insert `#<` annotations as specified in the system message."
    )
    return system_prompt, user_prompt


def _strip_markdown_fences(raw: str) -> str:
    """Remove common markdown wrappers; prefer a fenced block that contains a ``def``."""
    s = raw.strip()
    if not s:
        return s
    m = re.search(r"```(?:python|py)?\s*\r?\n([\s\S]*?)```", s, re.IGNORECASE)
    if m:
        inner = m.group(1).strip()
        if inner:
            s = inner
    elif s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _trim_prose_before_first_def(s: str) -> str:
    """Drop leading explanation when the model prints prose before ``def``."""
    m = re.search(r"(?ms)^(?:async\s+)?def\s+\w+\s*\(", s)
    if m and m.start() > 0:
        return s[m.start() :]
    return s


def _first_function_slice_from_text(s: str) -> str | None:
    """If the whole buffer does not parse, take the longest prefix from the first ``def`` that parses."""
    lines = s.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*(?:async\s+)?def\s+\w+\s*\(", line):
            start = i
            break
    if start is None:
        return None
    for end in range(len(lines), start, -1):
        block = "\n".join(lines[start:end])
        try:
            ast.parse(block)
            return block
        except SyntaxError:
            continue
    return None


def _strip_leading_decorators_before_def(src: str) -> str:
    """Drop ``@...`` lines above ``def`` when the model echoes the ``@semiformal`` decorator."""
    lines = src.splitlines()
    for i, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith("def ") or s.startswith("async def "):
            return "\n".join(lines[i:])
    return src


def _function_simple_name(func_qualname: str) -> str:
    return func_qualname.split(".")[-1]


def _extract_function_source_for_name(parsed_source: str, simple_name: str) -> str | None:
    try:
        tree = ast.parse(parsed_source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == simple_name:
            seg = ast.get_source_segment(parsed_source, node)
            if isinstance(seg, str) and seg.strip():
                return seg
    return None


def _extract_function_from_llm_output(
    raw: str | None,
    func_qualname: str,
    original_fn_source: str,
) -> tuple[str | None, str]:
    """
    Validate skeleton LLM output: only ``#<`` lines may differ from the user's enclosing function.

    Returns ``(result, "")`` on success, or ``(None, short reason)`` for logging.
    """
    if not raw or not raw.strip():
        return None, "empty model output"
    cleaned = _strip_leading_decorators_before_def(_strip_markdown_fences(raw))
    cleaned = _trim_prose_before_first_def(cleaned)
    if not cleaned.strip():
        return None, "no text after stripping markdown or leading prose"
    simple = _function_simple_name(func_qualname)
    candidate = _extract_function_source_for_name(cleaned, simple)
    if candidate is None:
        candidate = _first_function_slice_from_text(cleaned)
    if candidate is None:
        stripped = cleaned.lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            candidate = _first_function_slice_from_text(stripped)
    if candidate is None:
        return None, "could not parse a Python function (check for markdown or extra text around the function)"
    try:
        ast.parse(candidate)
    except SyntaxError as e:
        return None, f"syntax error in extracted function: {e}"
    has_skeleton = any(ln.lstrip().startswith("#<") for ln in candidate.splitlines())
    if not has_skeleton:
        return None, "model output had no #< reasoning lines"
    original_simple = _function_simple_name(func_qualname)
    original_fn = _extract_function_source_for_name(original_fn_source, original_simple)
    if original_fn is None:
        original_fn = _strip_leading_decorators_before_def(original_fn_source)
    if _normalized_source_for_compare(candidate) != _normalized_source_for_compare(original_fn):
        return None, (
            "executable text differs from enclosing function (only insert #< lines; "
            "do not change code, literals, or ... placeholders; do not paste generated implementation)"
        )
    before = _spec_marker_lines(original_fn_source)
    after = _spec_marker_lines(candidate)
    if before != after:
        return None, "user #> spec text changed or reordered relative to enclosing source"
    out = _drop_empty_hash_only_lines(_cap_skeleton_line_words(candidate))
    return out, ""


def _drop_empty_hash_only_lines(source: str) -> str:
    """Remove lines that are only ``#`` (placeholders from prior strips); keep ``#<`` / ``#>``."""
    out: list[str] = []
    for line in source.splitlines(keepends=True):
        core = line.rstrip("\r\n")
        stripped = core.lstrip()
        if stripped.startswith("#<") or stripped.startswith("#>") or stripped.startswith("# >"):
            out.append(line)
            continue
        if stripped.startswith("#"):
            remainder = stripped[1:].lstrip()
            if remainder == "":
                continue
        out.append(line)
    return "".join(out)


def _normalized_source_for_compare(source: str) -> str:
    """Normalize source by stripping skeleton markers and placeholder hash lines."""
    return _drop_empty_hash_only_lines(strip_skeleton_lines(source)).strip()


def _cap_skeleton_line_words(source: str, *, max_words: int = 10) -> str:
    """Keep text after each `#< [Tag]` to at most ``max_words`` words (model may be verbose)."""
    out: list[str] = []
    for line in source.splitlines(keepends=True):
        ending = ""
        core = line
        if line.endswith("\r\n"):
            ending = "\r\n"
            core = line[:-2]
        elif line.endswith("\n"):
            ending = "\n"
            core = line[:-1]
        elif line.endswith("\r"):
            ending = "\r"
            core = line[:-1]
        stripped = core.lstrip()
        if not stripped.startswith("#<"):
            out.append(line)
            continue
        indent = core[: len(core) - len(stripped)]
        rb = stripped.find("]")
        if rb == -1:
            out.append(line)
            continue
        prefix = stripped[: rb + 1]
        rest = stripped[rb + 1 :].lstrip()
        words = rest.split()
        rest_short = " ".join(words[:max_words])
        new_core = indent + prefix + (" " + rest_short if rest_short else "")
        out.append(new_core + ending)
    return "".join(out)


def _find_function_span_in_file(
    file_text: str,
    func_qualname: str,
    hint_start: int,
) -> tuple[int, int]:
    if hint_start <= 0:
        return (0, 0)
    try:
        tree = ast.parse(file_text)
    except SyntaxError:
        return (0, 0)
    simple = _function_simple_name(func_qualname)
    candidates: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == simple:
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            dist = abs(start - hint_start)
            if dist < 30:
                candidates.append((start, end, dist))
    if candidates:
        candidates.sort(key=lambda t: t[2])
        return (candidates[0][0], candidates[0][1])
    fallback: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == simple:
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            fallback.append((start, end, abs(start - hint_start)))
    if not fallback:
        return (0, 0)
    fallback.sort(key=lambda t: t[2])
    return (fallback[0][0], fallback[0][1])


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".semipy_skeleton.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _split_line_core_ending(line: str) -> tuple[str, str]:
    """Split a line into text without newline and the newline suffix (``\\n``, ``\\r\\n``, ``\\r``)."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _reindent_to_match(skeleton_fn: str, file_lines: list[str], func_start_line: int) -> str:
    """Canonicalize skeleton indentation, then align with the on-disk ``def`` line.

    1. Move the first ``def`` / ``async def`` to column 0 by subtracting its leading indent
       from every line (preserves relative structure).
    2. If the **minimum** indent among non-empty lines after ``def`` is greater than 4 spaces,
       treat that as an over-indented flat body and subtract ``(min_body - 4)`` from every line
       whose indent is at least ``min_body`` (fixes LLM body drift when ``def`` was already aligned
       so a uniform delta alone did nothing).
    3. Prepend the file's ``def``-line indent to every non-empty line (class / module alignment).

    Steps 1--2 are idempotent for well-formed output (``def`` at 0, body at 4 spaces).
    """
    if not skeleton_fn.strip():
        return skeleton_fn
    original_line = file_lines[func_start_line - 1] if func_start_line >= 1 else ""
    target_cols = len(original_line) - len(original_line.lstrip())

    lines = skeleton_fn.splitlines(keepends=True)
    def_idx: int | None = None
    d_def = 0
    for i, line in enumerate(lines):
        core, _ = _split_line_core_ending(line)
        st = core.lstrip()
        if st.startswith("def ") or st.startswith("async def "):
            def_idx = i
            d_def = len(core) - len(core.lstrip())
            break
    if def_idx is None:
        return skeleton_fn

    # Step 1: subtract d_def so def sits at column 0
    worked: list[str] = []
    for line in lines:
        core, ending = _split_line_core_ending(line)
        if not core.strip():
            worked.append(line)
            continue
        cur = len(core) - len(core.lstrip())
        if cur >= d_def:
            worked.append(" " * (cur - d_def) + core.lstrip() + ending)
        else:
            worked.append(line)

    # Step 2: normalize minimum body indent to 4 (Python logical indent under def at 0)
    indents: list[int] = []
    for i in range(def_idx + 1, len(worked)):
        core, _ = _split_line_core_ending(worked[i])
        if not core.strip():
            continue
        indents.append(len(core) - len(core.lstrip()))
    if indents:
        min_body = min(indents)
        if min_body > 4:
            reduction = min_body - 4
            adjusted: list[str] = []
            for i, line in enumerate(worked):
                core, ending = _split_line_core_ending(line)
                if i <= def_idx or not core.strip():
                    adjusted.append(line)
                    continue
                cur = len(core) - len(core.lstrip())
                if cur >= min_body:
                    adjusted.append(" " * (cur - reduction) + core.lstrip() + ending)
                else:
                    adjusted.append(line)
            worked = adjusted

    # Step 3: prepend file indent
    out: list[str] = []
    for line in worked:
        core, ending = _split_line_core_ending(line)
        if not core.strip():
            out.append(line)
            continue
        out.append(" " * target_cols + core + ending)
    return "".join(out)


def surface_skeleton(
    slot_spec: SlotSpec,
    cache_entry: CacheEntry,
) -> None:
    """
    Replace the enclosing function in the on-disk file with a version that includes `#<` lines.

    The portal may use a directory ``session_source`` for stable session identity; writes always
    target ``slot_spec.source_span[0]`` (the real ``.py`` path). Skips Jupyter ephemeral kernel
    paths and standalone ``semi()`` slots with no enclosing function source.

    Not invoked on **REUSE**; standalone ``semi()`` slots have no enclosing function source.
    """
    try:
        _surface_skeleton_impl(slot_spec, cache_entry)
    except Exception:
        traceback.print_exc()


def _surface_skeleton_impl(slot_spec: SlotSpec, cache_entry: CacheEntry) -> None:
    if not slot_spec.enclosing_function_source.strip():
        return

    target = Path(slot_spec.source_span[0])
    try:
        resolved = str(target.resolve())
    except OSError:
        return
    if "ipykernel" in resolved.replace("\\", "/").lower():
        return
    if not target.is_file() or target.suffix.lower() != ".py":
        return

    fn_source_clean_all = strip_skeleton_lines(slot_spec.enclosing_function_source)
    simple_qual = slot_spec.enclosing_function_qualname
    simple_name = _function_simple_name(simple_qual)
    fn_source_clean = _extract_function_source_for_name(fn_source_clean_all, simple_name)
    if fn_source_clean is None:
        fn_source_clean = _strip_leading_decorators_before_def(fn_source_clean_all)
    generated = cache_entry.generated_source or ""
    reasoning = cache_entry.reasoning_summary or ""

    hint_start = slot_spec.enclosing_function_span[1] or slot_spec.source_span[1]
    system_prompt, user_prompt = _build_skeleton_prompts(
        fn_source_clean, generated, reasoning
    )
    raw = _call_skeleton_llm(system_prompt, user_prompt)
    skeleton_fn, sk_reason = _extract_function_from_llm_output(raw, simple_qual, fn_source_clean)
    if skeleton_fn is None:
        msg = f"Skeleton skipped: LLM output failed validation. ({sk_reason})"
        if raw is None or not str(raw).strip():
            msg = "Skeleton skipped: skeleton model returned no text (check API keys and network)."
        elif _pipeline_trace_skeleton() and raw:
            head = raw.strip()
            if len(head) > 1200:
                head = head[:1200] + "..."
            msg = f"{msg} Raw head: {head!r}"
        _log_surface(slot_spec, msg)
        return

    lock = _lock_for_path(target)
    with lock:
        file_text = target.read_text(encoding="utf-8")
        start, end = _find_function_span_in_file(file_text, simple_qual, hint_start)
        if start == 0 or end == 0:
            return

        lines = file_text.splitlines(keepends=True)
        block = "".join(lines[start - 1 : end])
        disk_clean = strip_skeleton_lines(block)
        if disk_clean.strip() != fn_source_clean.strip():
            system_prompt2, user_prompt2 = _build_skeleton_prompts(
                disk_clean, generated, reasoning
            )
            raw2 = _call_skeleton_llm(system_prompt2, user_prompt2)
            sk2, sk2_reason = _extract_function_from_llm_output(raw2, simple_qual, disk_clean)
            if sk2 is None:
                msg = f"Skeleton skipped: stale on-disk block did not match lowered source; rewrite failed. ({sk2_reason})"
                if _pipeline_trace_skeleton() and raw2:
                    h = raw2.strip()
                    if len(h) > 1200:
                        h = h[:1200] + "..."
                    msg = f"{msg} Raw head: {h!r}"
                _log_surface(slot_spec, msg)
                return
            skeleton_fn = sk2

        skeleton_fn = _drop_empty_hash_only_lines(_cap_skeleton_line_words(skeleton_fn))
        skeleton_fn = _reindent_to_match(skeleton_fn, lines, start)

        sk_lines = skeleton_fn.splitlines(keepends=True)
        if sk_lines and not sk_lines[-1].endswith("\n"):
            sk_lines[-1] += "\n"

        new_text = "".join(lines[: start - 1] + sk_lines + lines[end:])
        _atomic_write_text(target, new_text)
        _log_surface(slot_spec, "Skeleton surfaced to source file.")
