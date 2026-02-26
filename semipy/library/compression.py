"""Pattern-to-primitive compression: LLM classification, naming, gist validation, deduplication."""
from __future__ import annotations

import ast
import asyncio
import hashlib
from pathlib import Path
from typing import Any, Optional

from semipy.agents.config import get_config
from semipy.agents.llm_utils import classify_with_llm
from semipy.library.abstractions import (
    ASTPattern,
    AbstractionLibrary,
    LibraryPrimitive,
)


def _pattern_to_function_source(pattern: ASTPattern, sample_impl: str) -> str:
    """
    Turn an anti-unified pattern into a function source. Uses sample_impl to fill holes with concrete names where possible.
    If pattern.normalized_source is already function-like (e.g. contains 'def '), return it with parameter_names as params.
    Otherwise wrap in a function. No hardcoded patterns; structure comes from the pattern and sample.
    """
    norm = pattern.normalized_source.strip()
    params = pattern.parameter_names or []
    if not norm:
        return ""
    try:
        ast.parse(norm)
    except SyntaxError:
        return ""
    if "def " in norm and norm.strip().startswith("def "):
        return norm
    param_str = ", ".join(params) if params else "x0"
    return f"def _primitive({param_str}):\n    return " + norm.replace("\n", "\n    ")


def _is_reusable_prompt(normalized_source: str) -> str:
    return f"""You are a code analyst. Consider this normalized Python code fragment (variables renamed to x0, x1, ...):

```python
{normalized_source}
```

Answer with exactly one line: YES or NO - is this fragment genuinely reusable as a standalone helper in other code? Consider: does it express a clear, self-contained operation? Would it be useful across different call sites? If it is too trivial (e.g. a single comparison) or too specific, answer NO. Otherwise YES."""


def _naming_prompt(normalized_source: str) -> str:
    return f"""You are a code analyst. Consider this normalized Python code fragment:

```python
{normalized_source}
```

Respond with exactly two lines:
Line 1: A short function name in snake_case (e.g. filter_by_condition, safe_get_first).
Line 2: A one-sentence description of what the function does (no code)."""


def _parse_reusable(response: str) -> bool:
    r = response.strip().upper()
    return r.startswith("YES") and "NO" not in r.split()[0]


def _parse_naming(response: str) -> tuple[str, str]:
    lines = [l.strip() for l in response.strip().splitlines() if l.strip()]
    name = "primitive"
    desc = ""
    if lines:
        name = lines[0].split()[0] if lines[0] else "primitive"
        for c in name:
            if not (c.isalnum() or c == "_"):
                name = "primitive"
                break
    if len(lines) > 1:
        desc = lines[1]
    return (name[:64], desc[:256])


async def _classify_reusable_async(normalized_source: str) -> bool:
    return await classify_with_llm(
        _is_reusable_prompt(normalized_source),
        parse_fn=_parse_reusable,
        default=False,
        timeout=15.0,
    )


async def _name_and_describe_async(normalized_source: str) -> tuple[str, str]:
    return await classify_with_llm(
        _naming_prompt(normalized_source),
        parse_fn=_parse_naming,
        default=("primitive", ""),
        timeout=15.0,
    )


def _validate_syntax(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


async def _validate_with_gist_async(source: str) -> bool:
    """Return True if the source runs without error in a minimal gist (optional; best-effort)."""
    try:
        from semipy.agents.gist import GistBuilder
        from semipy.agents.executor import GistExecutor
        from semipy.types import GenerationSpec, SemiCallSite
        config = get_config()
        spec = GenerationSpec(
            prompt="",
            call_site=SemiCallSite(filename="", lineno=0, func_qualname=""),
            template=None,
            context=None,
            expected_type=type(None),
        )
        builder = GistBuilder(spec)
        gist = builder.build(source)
        executor = GistExecutor(use_e2b=config.use_e2b, timeout=min(15, config.gist_timeout), e2b_api_key=config.e2b_api_key)
        result = executor.execute_sync(gist)
        return result.success if result else False
    except Exception:
        return False


def _deduplicate_source(library: AbstractionLibrary, source: str) -> bool:
    """Return True if source is already represented in library (by source hash)."""
    h = hashlib.sha256(source.encode()).hexdigest()[:20]
    for p in library.primitives.values():
        if hashlib.sha256(p.source.encode()).hexdigest()[:20] == h:
            return True
    return False


async def compress_pattern_async(
    pattern: ASTPattern,
    commit_sources: list[tuple[str, str]],
    library: AbstractionLibrary,
    skip_llm: bool = False,
    skip_gist: bool = True,
) -> Optional[LibraryPrimitive]:
    """
    Compress a single pattern into a LibraryPrimitive: LLM reusable check, LLM naming, optional gist validation.
    skip_llm: if True, skip LLM classification/naming (for tests). skip_gist: if True, skip gist execution check.
    """
    sample_impl = commit_sources[0][1] if commit_sources else ""
    source = _pattern_to_function_source(pattern, sample_impl)
    if not source or not _validate_syntax(source):
        return None
    if not skip_llm:
        reusable = await _classify_reusable_async(pattern.normalized_source)
        if not reusable:
            return None
        name, description = await _name_and_describe_async(pattern.normalized_source)
    else:
        name = "primitive"
        description = ""
    if _deduplicate_source(library, source):
        return None
    if not skip_gist:
        ok = await _validate_with_gist_async(source)
        if not ok:
            return None
    primitive_id = hashlib.sha256(source.encode()).hexdigest()[:24]
    commit_ids = [c[0] for c in commit_sources]
    sig = f"{name}({', '.join(pattern.parameter_names)})" if pattern.parameter_names else name
    return LibraryPrimitive(
        primitive_id=primitive_id,
        name=name,
        source=source,
        signature=sig,
        pattern_id=pattern.pattern_id,
        occurrence_count=len(commit_sources),
        commit_ids=commit_ids,
        validated=not skip_gist,
        description=description,
        tags=[],
        embedding_id="",
    )


def compress_patterns_sync(
    pattern_groups: list[tuple[ASTPattern, list[tuple[str, str]]]],
    library: AbstractionLibrary,
    skip_llm: bool = False,
    skip_gist: bool = True,
) -> list[LibraryPrimitive]:
    """Run compress_pattern_async for each group; return list of new primitives (sync wrapper)."""
    async def run() -> list[LibraryPrimitive]:
        out: list[LibraryPrimitive] = []
        for pattern, commits_sources in pattern_groups:
            prim = await compress_pattern_async(pattern, commits_sources, library, skip_llm=skip_llm, skip_gist=skip_gist)
            if prim is not None:
                out.append(prim)
        return out
    return asyncio.run(run())
