"""Library context injection: select relevant primitives for a spec, build prompt block."""
from __future__ import annotations


from semipy.library.abstractions import AbstractionLibrary, LibraryPrimitive
from semipy.types import GenerationSpec


def select_relevant_primitives(
    library: AbstractionLibrary,
    spec: GenerationSpec,
    max_count: int = 5,
    structural_only: bool = True,
) -> list[LibraryPrimitive]:
    """
    Select primitives relevant to the current spec. structural_only: match by template/spec context;
    when False and CocoIndex is available, semantic similarity can be used (not implemented here to avoid hard dependency).
    No hardcoded patterns; selection is driven by spec prompt and library content.
    """
    if not library.primitives:
        return []
    validated = [p for p in library.primitives.values() if p.validated]
    if not validated:
        validated = list(library.primitives.values())
    if len(validated) <= max_count:
        return validated[:]
    prompt_lower = (spec.prompt or "").strip().lower()
    scored: list[tuple[float, LibraryPrimitive]] = []
    for p in validated:
        score = 0.0
        desc = (p.description or "").lower()
        name = (p.name or "").lower()
        sig = (p.signature or "").lower()
        if prompt_lower and (name in prompt_lower or any(w in prompt_lower for w in name.split("_") if len(w) > 2)):
            score += 2.0
        if desc and any(w in prompt_lower for w in desc.split() if len(w) > 3):
            score += 1.0
        if sig and any(w in prompt_lower for w in sig.replace("(", " ").replace(")", " ").split() if len(w) > 2):
            score += 0.5
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _s, p in scored[:max_count]]


def build_library_context(
    library: AbstractionLibrary,
    spec: GenerationSpec,
    max_count: int = 5,
) -> str:
    """Build a text block for the agent prompt listing available primitives with source."""
    prims = select_relevant_primitives(library, spec, max_count=max_count)
    if not prims:
        return ""
    lines = [
        "Available library primitives (you may reuse or adapt these):",
        "",
    ]
    for p in prims:
        lines.append(f"# {p.name}: {p.signature}")
        if p.description:
            lines.append(f"# {p.description}")
        lines.append(p.source.strip())
        lines.append("")
    return "\n".join(lines)
