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


def _call_skeleton_llm(prompt: str, *, timeout: float = 120.0) -> str | None:
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
        agent = Agent(model, model_settings=settings, output_type=str)
        result = await agent.run(prompt)
        out = getattr(result, "output", None)
        if isinstance(out, str) and out.strip():
            return out
        return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _async_skeleton()).result(timeout=timeout)
    except Exception:
        return None


def _build_skeleton_prompt(
    fn_source_clean: str,
    generated_source: str,
    reasoning_summary: str,
) -> str:
    rs = reasoning_summary.strip() if reasoning_summary.strip() else "(none)"
    static = """You are formatting a short reasoning skeleton as Python comments for a semipy slot.

The user file uses `#>` for their natural-language specification (immutable). You add `#<` lines
that summarize how the generated implementation relates to that spec. Tags (first token after `#<`):
[Task] (required first annotation inside the function body), [Given], [Then], [When], [But], [Verify].

Rules:
- Output ONLY the full enclosing function source: from `def ` or `async def ` through the last line
  of that function. No markdown fences, no prose before or after.
- Do NOT change the function name, parameters, decorators, or any `#>` line text.
- Add 4 to 8 `#<` lines total. The first `#<` line in the function body must use [Task].
- Brevity (strict): on each `#<` line, after the `[Tag]` token, use at most **5 words** (prefer **3 to 4**).
  No full sentences, no commas for multiple clauses; labels only (e.g. "parse then format").
- Match indentation of neighboring code. Each `#<` line must be at most 90 characters including
  leading spaces and `#< `.
- Do not duplicate or remove user spec lines; do not add executable code.

Concrete shape examples (illustrative only; your output must follow the actual sources below):

Example A -- expression slot with `semi(...)` and `#>` on the same line as code:
```python
def infer_datetime_formatter(date_str: str) -> str:
    #< [Task] infer pattern then format
    input_pattern = ... #> infer the input date regex/strptime pattern from the observed string format in this session.
    output_pattern = "%b %Y"
    if ...: #> year is over 2026
        return ... #> return the formatted date string
    else:
        return datetime.strptime(str(date_str), input_pattern).strftime(output_pattern)
```

Example B -- `@semiformal` method with a multi-line `#>` statement block (classification):
```python
def classify_body(self, body: str) -> str:
    #< [Task] label body snake_case family
    text = "" if body is None else str(body).strip()
    lower = text.lower()
    #> Classify this Apache error log body into a short snake_case event family name.
    if not lower:
        return "unknown"
    #> other conditions for other families like worker_init, jk_error, scoreboard_found, etc.
    return family  # type: ignore[name-defined]
```

Example C -- method building a prompt then calling `semi` with `expected_type=list`:
```python
def infer_templates(self, families_and_bodies: dict[str, list[str]]) -> list[dict]:
    #< [Task] one regex per family
    families_text = ""
    for fam, fam_bodies in sorted(families_and_bodies.items()):
        samples = "\\n".join("    - " + repr(b) for b in sorted(set(fam_bodies))[:15])
        families_text += "\\n  " + fam + ":\\n" + samples
    return semi(
        "For each event family, create a Python regex ... Families and example bodies:" + families_text,
        expected_type=list,
    )
```

---

Enclosing function (no `#<` yet; `#>` lines must appear verbatim in your output):
```python
"""
    tail = """

Generated implementation for this slot (reference only):
```python
"""
    tail2 = """

Model reasoning summary (may be empty):
"""
    tail3 = """

Produce the complete function source with `#<` annotations inserted."""
    return (
        static
        + fn_source_clean.strip()
        + "\n```\n"
        + tail
        + generated_source.strip()
        + "\n```\n"
        + tail2
        + rs
        + tail3
    )


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
    before = _spec_marker_lines(original_fn_source)
    after = _spec_marker_lines(candidate)
    if before != after:
        return None
    return _cap_skeleton_line_words(candidate)


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

    fn_source_clean = strip_skeleton_lines(slot_spec.enclosing_function_source)
    generated = cache_entry.generated_source or ""
    reasoning = cache_entry.reasoning_summary or ""

    hint_start = slot_spec.enclosing_function_span[1] or slot_spec.source_span[1]
    prompt = _build_skeleton_prompt(fn_source_clean, generated, reasoning)
    raw = _call_skeleton_llm(prompt)
    simple_qual = slot_spec.enclosing_function_qualname
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
            prompt2 = _build_skeleton_prompt(disk_clean, generated, reasoning)
            raw2 = _call_skeleton_llm(prompt2)
            sk2 = _extract_function_from_llm_output(raw2, simple_qual, disk_clean)
            if sk2 is None:
                return
            skeleton_fn = sk2

        sk_lines = skeleton_fn.splitlines(keepends=True)
        if sk_lines and not sk_lines[-1].endswith("\n"):
            sk_lines[-1] += "\n"

        new_text = "".join(lines[: start - 1] + sk_lines + lines[end:])
        _atomic_write_text(target, new_text)
