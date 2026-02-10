"""Structured 'what changed' and rule-based or LLM-guided change decision."""
from __future__ import annotations

from typing import Any, Optional

from semipy.template import structural_fingerprint, template_tree_from_prompt
from semipy.types import ChangeDecision, ChangeSummary, SemicodeEntry, Usage


def build_change_summary(
    usage: Usage,
    existing_semicode: Optional[SemicodeEntry] = None,
    old_constant_values: Optional[dict[str, Any]] = None,
) -> ChangeSummary:
    """
    Build a structured summary of what changed between the current usage and
    the existing semicode (if any). Used to guide reuse / refactor / regenerate.
    """
    summary = ChangeSummary()
    tree = template_tree_from_prompt(usage.template)
    current_fingerprint = structural_fingerprint(tree)

    if existing_semicode is None:
        summary.template_tree_changed = True
        summary.template_diff_description = "no existing semicode"
        return summary

    if existing_semicode.template_fingerprint != current_fingerprint:
        summary.template_tree_changed = True
        summary.template_diff_description = "template structure changed (fingerprint mismatch)"
    else:
        summary.template_diff_description = "same structure, only constant values may differ"

    if old_constant_values is not None and usage.constant_values != old_constant_values:
        summary.constants_changed = True
        diff_parts = []
        all_keys = set(old_constant_values) | set(usage.constant_values)
        for k in sorted(all_keys):
            ov = old_constant_values.get(k)
            nv = usage.constant_values.get(k)
            if ov != nv:
                diff_parts.append(f"{k}: {repr(ov)} -> {repr(nv)}")
        summary.constants_diff_description = "; ".join(diff_parts) if diff_parts else "constants changed"

    return summary


def decide_change(
    summary: ChangeSummary,
    existing_source: Optional[str] = None,
) -> ChangeDecision:
    """
    Rule-based decision for how to handle the change. Returns REUSE, REFACTOR,
    REGENERATE, or FULL_REWRITE. Can be extended later with an LLM call that
    uses summary + existing_source to decide.
    """
    if not summary.template_tree_changed and not summary.constants_changed:
        return ChangeDecision.REUSE
    if summary.template_tree_changed:
        return ChangeDecision.REGENERATE
    if summary.constants_changed and not summary.template_tree_changed:
        return ChangeDecision.REFACTOR
    return ChangeDecision.REGENERATE


def format_change_summary_for_prompt(summary: ChangeSummary) -> str:
    """Format the change summary as a short string for inclusion in an LLM prompt."""
    parts = []
    if summary.template_tree_changed:
        parts.append("Template structure changed: " + summary.template_diff_description)
    if summary.constants_changed:
        parts.append("Constants changed: " + summary.constants_diff_description)
    if summary.source_changed:
        parts.append("Source changed: " + summary.source_diff_description)
    if not parts:
        return "No significant change (reuse existing implementation)."
    return " ".join(parts)
