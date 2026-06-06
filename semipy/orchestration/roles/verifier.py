"""Verifier role: deterministic rules first, then an LLM alignment judge.

Correctness-first (the user's stated priority). Two layers, in order:

1. **Deterministic** -- AST / type / execution guards already live in
   ``agents.validator``; the orchestrator runs those first and only reaches the
   alignment layer for candidates that already pass them.
2. **LLM alignment** -- does the *observed behavior* (executed ``{input, output}``
   pairs) satisfy the natural-language spec? This is the irreducibly-semantic
   check. It is **binary** (aligned / misaligned), **evidence-grounded** (the real
   I/O is the reference -- the judge never imagines outputs), reasons before
   verdict, and draws ``verifier_vote_samples`` independent samples that are
   combined by **majority vote** (GenRM / self-consistency: more reliable than a
   single draw). The judge uses the ``verifier`` role model, distinct from the
   coder, to blunt self-enhancement bias.

Best-effort: with no API key (or on judge failure) the alignment layer abstains
(``passed=True``, ``alignment_verdict=None``) so the deterministic guards remain
the sole gate and the offline suite is unaffected.

``aggregate_votes`` is a pure function -- the voting policy is unit-tested offline
without any LLM. Ties fail (bias toward verification / ADAPT).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, Field

from semipy.agents.config import get_config
from semipy.orchestration.artifacts import VerificationVerdict
from semipy.orchestration.runtime import embed_run, make_responses_model


class AlignmentVerdict(BaseModel):
    """One judge's binary verdict on behavior-vs-intent, with cited failures."""

    aligned: bool
    reasoning: str = ""
    failing_samples: list[dict] = Field(default_factory=list)


_ALIGNMENT_SYSTEM = """\
You judge whether a Python function's OBSERVED BEHAVIOR satisfies the INTENT of \
its natural-language spec.

You receive: the spec (intent), the surrounding scaffold, the implementation \
source, and CONCRETE executed {input -> output} pairs. The I/O pairs are your \
only evidence -- judge against them, never against outputs you imagine.

Reason briefly first, then give a BINARY verdict:
- aligned = true: every observed output satisfies the spec's intent for its \
input (minor cosmetic differences are fine).
- aligned = false: one or more outputs clearly fail the intent (wrong result, \
generic fallback where a real answer was expected, unhandled format silently \
passed through). When false, populate failing_samples with the specific \
offending {input, output} rows (at most 5).

Be conservative about cosmetic/style differences, but do not approve clear \
intent failures. Keep reasoning under 3 sentences."""


def _build_alignment_prompt(
    *,
    spec_text: str,
    scaffold_source: Optional[str],
    implementation_source: str,
    io_pairs: list[dict],
) -> str:
    import json

    parts = [f"## Spec (intent)\n{spec_text}"]
    if scaffold_source:
        parts.append(f"## Scaffold\n```python\n{scaffold_source}\n```")
    parts.append(f"## Implementation\n```python\n{implementation_source}\n```")
    parts.append(
        "## Observed results (executed input -> output)\n"
        + json.dumps(io_pairs[:20], default=str, indent=2)
    )
    parts.append("Does each observed output satisfy the spec's intent for its input?")
    return "\n\n".join(parts)


async def _alignment_judge_async(prompt: str) -> Optional[AlignmentVerdict]:
    """One LLM alignment judgment. Returns None when unavailable / on error."""
    model, settings = make_responses_model("verifier")
    if model is None:
        return None
    try:
        from pydantic_ai import Agent

        agent: Agent[None, AlignmentVerdict] = Agent(
            model,
            model_settings=settings,
            output_type=AlignmentVerdict,
            instructions=_ALIGNMENT_SYSTEM,
        )
        result = await agent.run(prompt)
        return result.output
    except Exception:
        return None


def aggregate_votes(verdicts: list[Optional[AlignmentVerdict]]) -> VerificationVerdict:
    """Combine independent alignment verdicts by majority vote (pure; ties fail).

    No verdicts (all abstained / no key) -> abstain: ``passed=True``,
    ``alignment_verdict=None`` so the alignment layer does not block. With votes,
    a STRICT majority of ``aligned`` is required to pass; a tie fails (bias toward
    verification / ADAPT). Failing samples and reasons are collected from the
    dissenting (misaligned) verdicts for feedback to the coder.
    """
    votes = [v for v in verdicts if v is not None]
    if not votes:
        return VerificationVerdict(passed=True, alignment_verdict=None, vote_count=0)

    aligned_count = sum(1 for v in votes if v.aligned)
    passed = aligned_count * 2 > len(votes)  # strict majority; tie -> fail
    failing_samples: list[dict] = []
    reasons: list[str] = []
    for v in votes:
        if not v.aligned:
            failing_samples.extend(v.failing_samples)
            if v.reasoning:
                reasons.append(v.reasoning)
    return VerificationVerdict(
        passed=passed,
        deterministic_passed=True,
        alignment_verdict="aligned" if passed else "misaligned",
        failing_samples=failing_samples,
        reasons=reasons,
        vote_count=len(votes),
    )


async def _gather_votes_async(prompt: str, samples: int) -> list[Optional[AlignmentVerdict]]:
    tasks = [_alignment_judge_async(prompt) for _ in range(samples)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[Optional[AlignmentVerdict]] = []
    for r in results:
        out.append(r if isinstance(r, AlignmentVerdict) else None)
    return out


def verify_alignment(
    *,
    spec_text: str,
    implementation_source: str,
    io_pairs: list[dict],
    scaffold_source: Optional[str] = None,
    samples: Optional[int] = None,
) -> VerificationVerdict:
    """Run the LLM alignment layer with multi-sample majority voting.

    Returns an abstaining (``passed=True``, ``alignment_verdict=None``) verdict
    when no API key is configured or no I/O evidence is available, so callers can
    treat the deterministic guards as the gate. ``samples`` defaults to
    ``config.verifier_vote_samples``.
    """
    if not io_pairs:
        # No executed evidence to ground the judgment -> abstain (never hallucinate).
        return VerificationVerdict(passed=True, alignment_verdict=None, vote_count=0)
    n = samples if samples is not None else get_config().verifier_vote_samples
    n = max(1, int(n))
    prompt = _build_alignment_prompt(
        spec_text=spec_text,
        scaffold_source=scaffold_source,
        implementation_source=implementation_source,
        io_pairs=io_pairs,
    )
    verdicts = embed_run(_gather_votes_async(prompt, n))
    return aggregate_votes(verdicts)
