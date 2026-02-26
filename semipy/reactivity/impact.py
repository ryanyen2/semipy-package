"""LLM-assisted impact analysis: does an upstream change require downstream regeneration?"""
from __future__ import annotations

from typing import Optional

from semipy.agents.llm_utils import classify_with_llm


def _impact_prompt(upstream_diff_summary: str, downstream_source: str) -> str:
    return f"""You are a code analyst. An upstream generated function was changed.

Summary of the upstream change (old vs new):
{upstream_diff_summary}

The downstream slot currently has this implementation:
```python
{downstream_source[:2000]}
```

Does this upstream change require the downstream implementation to be regenerated? Consider: return type changes, semantic contract changes, column/field additions or removals that the downstream relies on, or any change that would make the current downstream implementation incorrect or inconsistent.

Answer with exactly one line: YES or NO. If unsure, answer YES (conservative)."""


def _parse_impact(response: str) -> bool:
    r = response.strip().upper()
    return r.startswith("YES")


async def assess_impact_async(
    upstream_old_source: str,
    upstream_new_source: str,
    downstream_source: str,
    diff_summary: Optional[str] = None,
) -> bool:
    """
    Ask the validator model whether the upstream change requires downstream regeneration.
    Returns True if regeneration is needed, False if not. On error/timeout returns True (conservative).
    """
    if diff_summary is None:
        diff_summary = f"Old (excerpt):\n{upstream_old_source[:800]}\n\nNew (excerpt):\n{upstream_new_source[:800]}"
    prompt = _impact_prompt(diff_summary, downstream_source)
    return await classify_with_llm(prompt, parse_fn=_parse_impact, default=True, timeout=15.0)
