"""Decision classifier role (U6).

Turns the deterministic divergence clusters into labeled ``Decision`` nodes. The
trust model (KTD2): the classifier may only describe forks that execution
demonstrated -- it labels clusters, it never invents them. A single cluster
yields no decision (noise already collapsed in clustering, R4).

Two layers, mirroring the verifier:

1. **Deterministic** -- always runs. Consequence rank is computed from the
   branches' observed outputs (structural change > categorical > numeric). Fate
   labels fall back to the observed output signature, so with no API key the user
   still gets a usable, unlabeled output-cluster view (R6).
2. **LLM labeling** -- best-effort. Names the axis and each fate in the user's
   language and proposes a guard. Abstains (keeps deterministic labels) with no
   API key or on error, so the offline suite is unaffected.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from semipy.agents.config import get_config
from semipy.decisions.divergence import DivergenceResult
from semipy.decisions.model import Branch, Decision
from semipy.orchestration.runtime import embed_run, make_responses_model

# Consequence scores by spread kind: a fork that changes output structure is
# higher-stakes than one that only shifts a number. Used to rank decisions (R8).
_KIND_SCORE = {"structural": 3.0, "categorical": 2.0, "numeric": 1.0, "opaque": 1.5}


def _short(value: Any, n: int = 60) -> str:
    s = repr(value)
    return s if len(s) <= n else s[: n - 3] + "..."


def _representative_output(divergence: DivergenceResult, cluster) -> Any:
    run = divergence.runs[cluster.representative_id]
    if not run.records:
        return None
    rec = run.records[0]
    if rec.get("error"):
        return f"raises {str(rec['error']).split(':', 1)[0]}"
    return rec.get("repr", rec.get("json"))


def _deterministic_fate_label(divergence: DivergenceResult, cluster) -> str:
    """A fate label derived purely from the observed output (the no-key view)."""
    out = _representative_output(divergence, cluster)
    if isinstance(out, str) and out.startswith("raises "):
        return out
    return f"-> {_short(out)}"


def _compute_consequence(divergence: DivergenceResult) -> tuple[float, str]:
    """Classify the spread across branches into a consequence kind + score."""
    outs: list[Any] = []
    any_error = False
    for c in divergence.clusters:
        run = divergence.runs[c.representative_id]
        rec = run.records[0] if run.records else {"error": "unrunnable"}
        if rec.get("error"):
            any_error = True
        outs_json = rec.get("json")
        outs_shape = rec.get("shape")
        outs_type = rec.get("type")
        outs.append((rec.get("error"), outs_type, outs_shape, outs_json))

    types = {o[1] for o in outs if o[0] is None}
    shapes = {o[2] for o in outs if o[0] is None}
    if len(types) > 1 or len(shapes) > 1:
        return _KIND_SCORE["structural"], "structural"
    if any_error:
        return _KIND_SCORE["categorical"], "categorical"
    # Same shape, no errors: numeric/value difference.
    numericish = all(o[1] in ("int", "float") for o in outs if o[0] is None)
    if numericish:
        return _KIND_SCORE["numeric"], "numeric"
    return _KIND_SCORE["categorical"], "categorical"


def _build_branches(divergence: DivergenceResult, example_in: Any) -> list[Branch]:
    branches: list[Branch] = []
    for c in divergence.clusters:
        branches.append(
            Branch(
                fate_label=_deterministic_fate_label(divergence, c),
                candidate_ids=list(c.candidate_ids),
                weight=round(c.weight, 4),
                signature=list(c.signature),
                example_in=example_in,
                example_out=_representative_output(divergence, c),
            )
        )
    return branches


# ---------------------------------------------------------------------------
# LLM labeling (best-effort)
# ---------------------------------------------------------------------------


class _DecisionLabels(BaseModel):
    """User-language names for the fork, grounded in the provided evidence."""

    axis_label: str
    guard: str = ""
    fate_labels: list[str] = Field(default_factory=list)


_LABEL_SYSTEM = """\
You name a decision a code-generating model made silently, so a user can \
understand and steer it. You are given an ambiguity GERM (the kind of input \
ambiguity), and 2+ BRANCHES -- each a candidate implementation excerpt plus a \
concrete {input -> output} pair showing how that branch behaved.

Return:
- axis_label: a short user-language name for WHAT is being decided (e.g. \
"null reading", "never-surveyed site"). 2-5 words. Describe the input feature, \
not the code.
- fate_labels: one short label per branch, in the SAME ORDER, naming the choice \
that branch made (e.g. "skip", "count as 0", "emit NaN"). 1-4 words each.
- guard: optional one-line predicate on the input under which the fork triggers \
(e.g. "reading is None"); empty string if not clear.

Ground every label in the provided code and I/O only. Never invent a branch or \
a behavior not shown. Keep labels plain and concrete."""


def _build_label_prompt(divergence: DivergenceResult, germ: str, branches: list[Branch]) -> str:
    import json

    parts = [f"## Germ\n{germ}", "## Branches"]
    for i, (c, b) in enumerate(zip(divergence.clusters, branches)):
        src = divergence.runs[c.representative_id].source.strip()
        parts.append(
            f"### Branch {i} (weight {b.weight})\n"
            f"```python\n{src}\n```\n"
            f"input -> output: {json.dumps(b.example_in, default=str)} -> {json.dumps(b.example_out, default=str)}"
        )
    parts.append("Name the axis and each branch's fate, in branch order.")
    return "\n\n".join(parts)


async def _label_async(prompt: str) -> Optional[_DecisionLabels]:
    model, settings = make_responses_model("decision_classifier")
    if model is None:
        return None
    try:
        from pydantic_ai import Agent

        agent: Agent[None, _DecisionLabels] = Agent(
            model,
            model_settings=settings,
            output_type=_DecisionLabels,
            instructions=_LABEL_SYSTEM,
        )
        import asyncio

        result = await asyncio.wait_for(agent.run(prompt), timeout=get_config().judge_timeout)
        return result.output
    except Exception:
        return None


def _apply_labels(decision: Decision, divergence: DivergenceResult) -> None:
    labels = embed_run(_label_async(_build_label_prompt(divergence, decision.germ, decision.branches)))
    if labels is None:
        return
    if labels.axis_label:
        decision.axis_label = labels.axis_label.strip()
    if labels.guard:
        decision.guard = labels.guard.strip()
    for branch, fate in zip(decision.branches, labels.fate_labels):
        if fate:
            branch.fate_label = fate.strip()
    decision.labeled = True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_divergence(
    divergence: DivergenceResult,
    *,
    germ: str = "output",
    example_in: Any = None,
    use_llm: bool = True,
) -> list[Decision]:
    """Turn divergence clusters into labeled ``Decision`` nodes.

    Returns an empty list when candidates agree (one cluster) -- there is no fork
    to surface. With ``use_llm=False`` (or no API key) the decision carries
    deterministic, output-derived labels (the R6 fallback view).
    """
    if not divergence.diverged():
        return []
    branches = _build_branches(divergence, example_in)
    score, kind = _compute_consequence(divergence)
    decision = Decision(
        germ=germ,
        axis_label=germ,  # deterministic default; LLM may replace
        branches=branches,
        consequence=score,
        consequence_kind=kind,
    )
    if use_llm:
        _apply_labels(decision, divergence)
    return [decision]


def rank_decisions(decisions: list[Decision]) -> list[Decision]:
    """Order decisions by consequence (highest-stakes first); stable on ties."""
    return sorted(decisions, key=lambda d: -d.consequence)
