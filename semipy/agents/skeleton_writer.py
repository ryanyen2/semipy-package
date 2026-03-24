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
from semipy.agents.generator import _create_openai_model, _create_openrouter_model
from semipy.lowering import strip_skeleton_lines
from semipy.types import CacheEntry, SlotSpec

_file_write_locks: dict[str, threading.Lock] = {}
_file_write_locks_mutex = threading.Lock()


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
        return None


def _build_skeleton_prompts(
    fn_source_clean: str,
    generated_source: str,
    reasoning_summary: str,
) -> tuple[str, str]:
    rs = reasoning_summary.strip() if reasoning_summary.strip() else "(none)"
    system_prompt = """You are generating brief but substantial program-tracing lines as Python comments—called "reasoning surfaces"—to capture the core logic, decision points, or constraints, not just recapping each statement, in order to help users both understand and iteratively refine the code by editing these comments.

Your output should:
- Insert `#<` annotated reasoning traces within the function (never outside), matching code block indentation. The user provides `#>` specification lines, which must remain unchanged.
- For each explanation, use a [Tag] (first token after `#<`), including [Task] (first), and choose from: [Given], [Then], [When], [But], [Verify], both alone and mixed as needed. Encourage creative mixing of formality and informality across tags and domains.
- Always ensure that each `#<` is true reasoning (explain a non-obvious step, intent, constraint, condition, or implication), not just a summary or a direct echo.
- Insert between 4 and 8 `#<` lines, tight but not verbose: Each after-tag phrase must use **no more than 10 words** (prefer 5-8 words), e.g. "parse then format", "skip if missing pattern", not sentences, not multi-clause lists.
- Do NOT modify code, specifications (`#>` lines), or structure; do NOT emit filler or blank comment lines.
- Place all `#<` lines in the code body, never before function definition or after the last line.
- Every annotation is crafted for editability: the user should be able to review, modify, or replace these reasoning traces to direct iterative code changes.
- Reasoning always precedes or is interleaved with the associated code; never summarize afterward.
- Produce only the complete enclosing function source (from `def ` or `async def` to the last line), with `#<` annotations; NO markdown or prose intro/outro.

# Steps

- Read the target code and `#>` lines so you can understand the program's operational semantics.
- Choose reasoning tags and short surface texts that clarify choices, decisions, or constraints—not just "what" but "why/how"—showing a range of domains and both formal and informal phrasing.
- Examples should span multiple fields (e.g. data parsing, user interaction, business logic, ML inferences, process coordination, error handling) and show several permutations: formal-informal tone mixing, varied tag sets, and complex reasoning made concise.
- For each line or block in need of explanation, add the best fitting reasoning tag and concise phrase, designed for high editability.
- Ensure every annotation is actionable: users should be able to adjust these to directly revisit and iterate the code.

# Output Format

Return only the full, syntactically correct Python function with your `#<` reasoning traces inserted as comments, in-place, matching code indentation. No markdown/code delimiters, no explanation, no filler, no blank comments, and no removal or alteration of existing `#>` lines, code, or function signature.

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
- Your goal is for the user to look at the annotated reasoning traces and directly edit them to revise program logic in future iterations.

Reminder: Insert annotations inside function body only; always reason before conclusions or actions; be succinct but illuminating; enable iterative user editing."""
    user_prompt = (
        "Enclosing function (no `#<` yet; `#>` lines must appear verbatim in your output):\n"
        "```python\n"
        + fn_source_clean.strip()
        + "\n```\n\n"
        + "Generated implementation for this slot (reference only):\n"
        + "```python\n"
        + generated_source.strip()
        + "\n```\n\n"
        + "Model reasoning summary (may be empty):\n"
        + rs
        + "\n\n"
        + "Produce the complete function source with `#<` annotations inserted."
    )
    return system_prompt, user_prompt


def _strip_markdown_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


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
) -> str | None:
    if not raw or not raw.strip():
        return None
    cleaned = _strip_leading_decorators_before_def(_strip_markdown_fences(raw))
    simple = _function_simple_name(func_qualname)
    candidate = _extract_function_source_for_name(cleaned, simple)
    if candidate is None:
        stripped = cleaned.lstrip()
        if not (stripped.startswith("def ") or stripped.startswith("async def ")):
            return None
        candidate = cleaned
    try:
        ast.parse(candidate)
    except SyntaxError:
        return None
    has_skeleton = any(
        ln.lstrip().startswith("#<") for ln in candidate.splitlines()
    )
    if not has_skeleton:
        return None
    # Guardrail: surfaced skeleton must not rewrite executable code.
    # Compare function bodies only (ignore decorators around the original source).
    # Both sides are normalized by stripping `#<` annotations to placeholder lines.
    original_simple = _function_simple_name(func_qualname)
    original_fn = _extract_function_source_for_name(original_fn_source, original_simple)
    if original_fn is None:
        original_fn = _strip_leading_decorators_before_def(original_fn_source)
    if _normalized_source_for_compare(candidate) != _normalized_source_for_compare(original_fn):
        return None
    before = _spec_marker_lines(original_fn_source)
    after = _spec_marker_lines(candidate)
    if before != after:
        return None
    return _drop_empty_hash_only_lines(_cap_skeleton_line_words(candidate))


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


def _cap_skeleton_line_words(source: str, *, max_words: int = 5) -> str:
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


def surface_skeleton(
    slot_spec: SlotSpec,
    cache_entry: CacheEntry,
    portal_source_file: str,
) -> None:
    """
    Replace the enclosing function in the on-disk file with a version that includes `#<` lines.

    The portal may use a directory ``session_source`` for stable session identity; writes always
    target ``slot_spec.source_span[0]`` (the real ``.py`` path). Skips Jupyter ephemeral kernel
    paths and standalone ``semi()`` slots with no enclosing function source.

    ``portal_source_file`` is kept for call-site compatibility; it does not gate writes.

    Not invoked on **REUSE**; standalone ``semi()`` slots have no enclosing function source.
    """
    _ = portal_source_file  # portal anchor may be a directory; write target is source_span[0]
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
    skeleton_fn = _extract_function_from_llm_output(raw, simple_qual, fn_source_clean)
    if skeleton_fn is None:
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
            print(raw2)
            sk2 = _extract_function_from_llm_output(raw2, simple_qual, disk_clean)
            print(sk2)
            if sk2 is None:
                return
            skeleton_fn = sk2

        skeleton_fn = _drop_empty_hash_only_lines(_cap_skeleton_line_words(skeleton_fn))

        sk_lines = skeleton_fn.splitlines(keepends=True)
        if sk_lines and not sk_lines[-1].endswith("\n"):
            sk_lines[-1] += "\n"

        new_text = "".join(lines[: start - 1] + sk_lines + lines[end:])
        _atomic_write_text(target, new_text)
