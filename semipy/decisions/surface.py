"""The inline ``#?`` decision surface (U8).

An open decision renders as a ``#?`` line in the source skeleton, parallel to
``#>`` (the user's spec) and ``#<`` (the system's reasoning). The guesses are
therefore visible in any editor, in ``git diff``, and in PR review -- not only
inside the VS Code extension, which upgrades the same lines into a pick UI.

``#?`` lines are stripped before lowering (see
``lowering_ast.strip_skeleton_lines``), so adding, editing, or resolving a fork
never perturbs ``slot_id``, slot ordinals, or line numbers (KTD8).
"""
from __future__ import annotations

from semipy.decisions.model import Decision, DecisionSet

_PREFIX = "#?"


def _short(value: object, n: int = 48) -> str:
    s = repr(value)
    return s if len(s) <= n else s[: n - 3] + "..."


def format_decision_line(decision: Decision, indent: str = "") -> str:
    """Render one open decision as a single ``#?`` line.

    Example: ``#? null cover: skip (60%) | count as 0 (40%)``. Falls back to the
    germ when no axis label was assigned (the no-key view).

    When the observer recorded the evidence, the line also shows *why* the
    branches differ: each branch's example output (``->out``), a ``≈N`` marker on
    a branch that only pooled its candidates *over N tested inputs* (not proven
    identical -- the caveat that licenses re-open), and the shared discriminating
    input once as a trailing ``[in=...]``. All evidence is optional; a branch
    with none renders exactly as before.
    """
    axis = decision.axis_label or decision.germ
    parts = []
    for b in decision.branches:
        pct = round(b.weight * 100)
        seg = f"{b.fate_label} ({pct}%)"
        if b.example_out is not None:
            seg += f" ->{_short(b.example_out)}"
        if b.equivalent_over_n is not None:
            seg += f" ≈{b.equivalent_over_n}"
        parts.append(seg)
    line = f"{indent}{_PREFIX} {axis}: " + " | ".join(parts)
    example_in = next(
        (b.example_in for b in decision.branches if b.example_in is not None), None
    )
    if example_in is not None:
        line += f"  [in={_short(example_in)}]"
    return line


def render_open_decisions(decision_set: DecisionSet, indent: str = "") -> list[str]:
    """The ``#?`` lines for every still-open decision, highest-consequence first.

    Resolved decisions render nothing; an empty/agreeing slot renders nothing.
    """
    return [format_decision_line(d, indent) for d in decision_set.open_decisions()]


def is_decision_line(line: str) -> bool:
    """True for a ``#?`` open-decision line (after optional indentation)."""
    return line.lstrip().startswith(_PREFIX)


def strip_decision_lines(source: str) -> str:
    """Remove all ``#?`` lines from ``source`` (used when re-rendering the zone)."""
    kept = [ln for ln in source.splitlines(keepends=True) if not is_decision_line(ln)]
    return "".join(kept)
