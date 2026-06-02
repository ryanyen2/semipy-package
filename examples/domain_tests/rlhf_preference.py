"""Domain: RLHF / preference modeling.

An alignment researcher building a preference dataset needs a judge that, given a
prompt and two candidate completions, picks the better one under a helpful/honest/
harmless rubric and emits a calibrated confidence plus a rationale. This is the
labeling function that feeds a reward model.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from semipy import configure, semiformal

configure(verbose=True, cache_dir=os.environ.get("DT_CACHE", "examples/.dt_cache"))


@dataclass
class PreferenceJudgment:
    winner: str        # "A" or "B"
    confidence: float  # in [0.0, 1.0]
    rationale: str


@semiformal
def judge_preference(prompt: str, response_a: str, response_b: str) -> PreferenceJudgment:
    verdict = None
    #< intent: Produce preference judgment verdict
    #< given: a response may be unsafe, evasive, or simply wrong
    #< by: scoring helpfulness, honesty, harmlessness, relevance, and specificity cues
    #< unless: PreferenceJudgment import fails, yields no valid judgment
    #> judge which of {response_a} or {response_b} is the better reply to {prompt} under a
    #> helpful, honest, and harmless rubric; set winner to "A" or "B", confidence in [0,1],
    #> and a one-sentence rationale grounded in the rubric
    #< yields: verdict key containing winner, confidence, and rationale
    return verdict


PAIRS = [
    (
        "How do I stop my sourdough starter from going moldy?",
        "Just throw it out and buy bread, baking is a waste of time.",
        "Discard mold immediately, keep the starter cooler, feed it on a regular schedule with equal flour/water by weight, and use a clean jar.",
    ),
    (
        "What's a safe dose of acetaminophen for an adult?",
        "Take as much as you want, it's basically harmless.",
        "Typical adult dosing is up to 1000mg per dose and no more than 3000-4000mg in 24h, but check the label and your doctor, especially with alcohol or liver issues.",
    ),
]


if __name__ == "__main__":
    for prompt, a, b in PAIRS:
        j = judge_preference(prompt, a, b)
        print(f"\nPROMPT: {prompt}")
        print(f"  winner={j.winner!r} confidence={j.confidence} ")
        print(f"  rationale: {j.rationale}")
