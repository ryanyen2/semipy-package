"""Tests for the US-001 sketch pattern-learning clarity gate."""
from __future__ import annotations

from semipy.library.binding import (
    SpecPhrase,
    build_semantic_binding,
    evaluate_binding_clarity,
)


def _param_phrase(text: str = "col", name: str = "col") -> SpecPhrase:
    return SpecPhrase(
        text=text,
        role="param",
        code_referent=f"df['{text}']",
        hole_name=name,
        safe_swap_set=None,
    )


def _structural_phrase(text: str = "filter rows where") -> SpecPhrase:
    return SpecPhrase(
        text=text,
        role="operation",
        code_referent="df[df[col] == value]",
        hole_name=None,
        safe_swap_set=None,
    )


def test_parametric_binding_is_accepted() -> None:
    phrases = (_structural_phrase(), _param_phrase())
    binding = build_semantic_binding("filter rows where col equals value", phrases, confidence=0.9)
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is True
    assert reason == ""


def test_no_holes_is_rejected_as_fixed_copy() -> None:
    phrases = (_structural_phrase(), _structural_phrase("return True"))
    binding = build_semantic_binding("filter rows where return True", phrases, confidence=0.9)
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is False
    assert "fixed copy" in reason


def test_no_structural_anchor_is_rejected() -> None:
    # All phrases are holes -- that means the pattern would match any spec.
    phrases = (_param_phrase("colA", "a"), _param_phrase("colB", "b"))
    binding = build_semantic_binding("{a} {b}", phrases, confidence=0.9)
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is False
    assert "structural anchor" in reason


def test_low_confidence_is_rejected() -> None:
    phrases = (_structural_phrase(), _param_phrase())
    binding = build_semantic_binding(
        "filter rows where col equals value", phrases, confidence=0.3
    )
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is False
    assert "0.30" in reason


def test_hole_without_code_referent_is_rejected() -> None:
    bad_hole = SpecPhrase(
        text="col",
        role="param",
        code_referent="",
        hole_name="col",
        safe_swap_set=None,
    )
    phrases = (_structural_phrase(), bad_hole)
    binding = build_semantic_binding("filter", phrases, confidence=0.9)
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is False
    assert "code_referent" in reason


def test_empty_phrases_is_rejected() -> None:
    binding = build_semantic_binding("opaque", tuple(), confidence=0.1)
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is False
    assert "no phrases" in reason


def test_confidence_zero_bypasses_threshold_when_unset() -> None:
    # When model returns confidence=0 (i.e. not provided), we do not penalize
    # purely on confidence; the structural gate still applies.
    phrases = (_structural_phrase(), _param_phrase())
    binding = build_semantic_binding(
        "filter rows where col equals value", phrases, confidence=0.0
    )
    is_clear, reason = evaluate_binding_clarity(binding, min_confidence=0.6)
    assert is_clear is True, f"expected clear, got {reason!r}"
