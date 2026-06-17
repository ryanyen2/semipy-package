"""Surface the model's silent decisions as navigable forks.

When a slot is genuinely underspecified, several candidate implementations are
drawn, executed, and clustered by *observed behavioral divergence*. Each
divergence is a decision the model made silently (e.g. "null reading: skip vs
count as zero"); this subsystem captures it, labels it in user language, and
surfaces it as a navigable fork the user can resolve while writing the code.

The grounding is execution, not static analysis: clustering (deterministic)
finds and weights the forks, and a classifier only *names* forks that execution
demonstrated -- so the surface can never contain an invented decision.

Vocabulary note: this subsystem owns "decision" / "fork" / "branch" / "germ".
It deliberately does NOT reuse "effect", which ``semipy/effects/`` reserves for
real-world mutations (DB/file/API writes).
"""
from __future__ import annotations

from semipy.decisions.germs import (
    GERMS,
    GermHit,
    detect_germ_ids,
    detect_germs,
)
from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.persistence import attach_decision_set, decision_set_for
from semipy.decisions.surface import format_decision_line, render_open_decisions

__all__ = [
    "GERMS",
    "GermHit",
    "detect_germs",
    "detect_germ_ids",
    "Branch",
    "Decision",
    "DecisionSet",
    "attach_decision_set",
    "decision_set_for",
    "render_open_decisions",
    "format_decision_line",
]
