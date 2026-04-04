"""Semantic binding: map NL spec phrases to code roles and extract structural signatures."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from semipy.agents.llm_utils import classify_with_llm


def _norm_token(s: str) -> str:
    return s.strip().casefold()


def _binding_canonical(
    spec_text: str,
    phrases: tuple[SpecPhrase, ...],
) -> str:
    parts = [spec_text.strip().casefold()]
    for p in phrases:
        parts.append(f"{p.role}:{p.text}:{p.code_referent}:{p.hole_name or ''}")
    return "\0".join(parts)


def compute_structural_signature(phrases: tuple[SpecPhrase, ...]) -> str:
    """Hash of non-hole phrases (role + normalized text); defines structural pattern identity."""
    parts: list[str] = []
    for p in phrases:
        if p.hole_name is None:
            parts.append(f"{p.role}:{_norm_token(p.text)}")
    raw = "\0".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_binding_id(spec_text: str, phrases: tuple[SpecPhrase, ...]) -> str:
    return hashlib.sha256(_binding_canonical(spec_text, phrases).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SpecPhrase:
    text: str
    role: str
    code_referent: str
    hole_name: str | None
    safe_swap_set: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SemanticBinding:
    binding_id: str
    spec_text: str
    phrases: tuple[SpecPhrase, ...]
    structural_signature: str
    hole_names: tuple[str, ...]
    hole_values: dict[str, str]
    hole_code_referents: dict[str, str]


def _parse_binding_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _phrases_from_payload(data: dict[str, Any]) -> tuple[SpecPhrase, ...] | None:
    raw_list = data.get("phrases")
    if not isinstance(raw_list, list):
        return None
    out: list[SpecPhrase] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        role = str(item.get("role", "param")).strip() or "param"
        code_ref = str(item.get("code_referent", "")).strip()
        is_hole = bool(item.get("is_hole", False))
        hole_name = item.get("hole_name")
        hn: str | None = None
        if is_hole:
            hn = str(hole_name).strip() if hole_name is not None and str(hole_name).strip() else None
            if not hn:
                hn = f"h{len(out)}"
        else:
            hn = None
        swaps = item.get("safe_swap_set")
        safe: tuple[str, ...] | None = None
        if isinstance(swaps, list):
            safe = tuple(str(x).strip() for x in swaps if str(x).strip())
        elif swaps is None and role == "operator" and is_hole:
            safe = None
        out.append(
            SpecPhrase(
                text=text,
                role=role,
                code_referent=code_ref,
                hole_name=hn if is_hole else None,
                safe_swap_set=safe,
            )
        )
    if not out:
        return None
    return tuple(out)


def _hole_values_from_phrases(phrases: tuple[SpecPhrase, ...]) -> dict[str, str]:
    hv: dict[str, str] = {}
    for p in phrases:
        if p.hole_name:
            hv[p.hole_name] = p.text.strip()
    return hv


def _hole_code_referents(phrases: tuple[SpecPhrase, ...]) -> dict[str, str]:
    cr: dict[str, str] = {}
    for p in phrases:
        if p.hole_name:
            cr[p.hole_name] = p.code_referent
    return cr


def build_semantic_binding(spec_text: str, phrases: tuple[SpecPhrase, ...]) -> SemanticBinding:
    hv = _hole_values_from_phrases(phrases)
    names = tuple(sorted(hv.keys()))
    sig = compute_structural_signature(phrases)
    bid = compute_binding_id(spec_text, phrases)
    return SemanticBinding(
        binding_id=bid,
        spec_text=spec_text.strip(),
        phrases=phrases,
        structural_signature=sig,
        hole_names=names,
        hole_values=hv,
        hole_code_referents=_hole_code_referents(phrases),
    )


def _extraction_prompt(spec_text: str, generated_source: str) -> str:
    return f"""You align a natural-language slot specification with the Python implementation.

Spec:
{spec_text}

Code:
```python
{generated_source.strip()}
```

Identify which spec phrases are structural (define the operation pattern) versus parametric (values that could change without changing code shape).

For each operator-role hole, list safe_swap_set: other NL wordings that would map to the same Python operator or pattern (same control flow). If a different wording would require different code structure, do NOT include it.

Respond with JSON only (no markdown), shape:
{{
  "phrases": [
    {{"text": "...", "role": "operation|param|operator|connective", "code_referent": "...", "is_hole": false, "hole_name": null, "safe_swap_set": null}},
    {{"text": "...", "role": "param", "code_referent": "df[\\\"col\\\"]", "is_hole": true, "hole_name": "col", "safe_swap_set": null}},
    {{"text": "equals", "role": "operator", "code_referent": "==", "is_hole": true, "hole_name": "cmp", "safe_swap_set": ["equals", "is equal to", "=="]}}
  ]
}}

Rules:
- Every substring of the spec that maps to a distinct code fragment should appear as one phrase.
- hole_name must be unique among holes; use short snake_case names.
- code_referent must be the exact Python fragment in the code when possible.
"""


async def extract_binding_async(spec_text: str, generated_source: str) -> SemanticBinding | None:
    """LLM extraction of phrase roles; returns None on failure."""
    prompt = _extraction_prompt(spec_text, generated_source)
    data = await classify_with_llm(
        prompt,
        parse_fn=_parse_binding_json,
        default=None,
        timeout=90.0,
        max_tokens=2048,
    )
    if not isinstance(data, dict):
        return None
    phrases = _phrases_from_payload(data)
    if phrases is None:
        return None
    try:
        return build_semantic_binding(spec_text, phrases)
    except Exception:
        return None


def build_spec_template(spec_text: str, binding: SemanticBinding) -> str:
    """Replace hole phrase texts in spec_text with {{hole_name}} placeholders."""
    pairs: list[tuple[str, str]] = []
    for p in binding.phrases:
        if p.hole_name and p.text:
            pairs.append((p.text, "{" + p.hole_name + "}"))
    pairs.sort(key=lambda t: -len(t[0]))
    out = spec_text
    for literal, placeholder in pairs:
        if literal in out:
            out = out.replace(literal, placeholder, 1)
    return out
