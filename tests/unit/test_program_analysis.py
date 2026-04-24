"""Tests for US-003 program-analysis guardrail context builder."""
from __future__ import annotations

import textwrap

from semipy.agents.program_analysis import (
    analyze_slot,
    build_program_analysis_context,
)
from semipy.types import SlotCategory, SlotSpec


def _slot(
    *,
    free_variables: list[str],
    output_names: list[str],
    enclosing_qualname: str,
    source_span=("user.py", 3, 3),
    expected_category: SlotCategory = SlotCategory.STATEMENT_BLOCK,
    expected_type=type(None),
    spec_text: str = "placeholder spec",
) -> SlotSpec:
    return SlotSpec(
        slot_id="slot_test",
        source_span=source_span,
        spec_text=spec_text,
        spec_hash="h",
        spec_equivalence_key="k",
        free_variables=free_variables,
        control_context="function",
        expected_category=expected_category,
        expected_type=expected_type,
        output_names=output_names,
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname=enclosing_qualname,
    )


def test_parameter_types_are_recovered() -> None:
    source = textwrap.dedent(
        """
        def process(row: dict, threshold: float) -> dict:
            # slot at line 3
            result = ...
            return result
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row", "threshold"],
        output_names=["result"],
        enclosing_qualname="process",
        source_span=("user.py", 3, 3),
    )
    analysis = analyze_slot(slot, source)
    assert analysis.parameter_types.get("row") == "dict"
    assert analysis.parameter_types.get("threshold") == "float"
    assert analysis.return_annotation == "dict"


def test_downstream_key_access_is_surfaced() -> None:
    source = textwrap.dedent(
        """
        def compute(row):
            out = ...
            label = out['category']
            score = out['score']
            return label, score
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row"],
        output_names=["out"],
        enclosing_qualname="compute",
        source_span=("user.py", 2, 2),
    )
    analysis = analyze_slot(slot, source)
    keys = analysis.output_key_accesses.get("out") or []
    assert "'category'" in keys
    assert "'score'" in keys


def test_downstream_attribute_access_is_surfaced() -> None:
    source = textwrap.dedent(
        """
        def compute(row):
            out = ...
            if out.is_valid:
                print(out.payload)
            return out
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row"],
        output_names=["out"],
        enclosing_qualname="compute",
        source_span=("user.py", 2, 2),
    )
    analysis = analyze_slot(slot, source)
    attrs = analysis.output_attribute_accesses.get("out") or []
    assert "is_valid" in attrs
    assert "payload" in attrs


def test_downstream_call_target_is_surfaced_with_signature() -> None:
    source = textwrap.dedent(
        """
        def serialize(parsed: dict) -> str:
            return str(parsed)

        def compute(row):
            parsed = ...
            return serialize(parsed)
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row"],
        output_names=["parsed"],
        enclosing_qualname="compute",
        source_span=("user.py", 5, 5),
    )
    analysis = analyze_slot(slot, source)
    calls = analysis.output_passed_to
    assert any(out == "parsed" and "serialize" in target for out, target, _ in calls)
    # Enriched form should contain the callee parameter annotation.
    enriched_target = next(target for out, target, _ in calls if out == "parsed")
    assert "dict" in enriched_target, f"expected dict annotation, got {enriched_target!r}"


def test_precondition_on_output_is_captured() -> None:
    source = textwrap.dedent(
        """
        def compute(row):
            parsed = ...
            if parsed['status'] == 'ok':
                return True
            return False
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row"],
        output_names=["parsed"],
        enclosing_qualname="compute",
        source_span=("user.py", 2, 2),
    )
    analysis = analyze_slot(slot, source)
    assert any("'status'" in p or "status" in p for p in analysis.preconditions)


def test_render_respects_budget() -> None:
    source = textwrap.dedent(
        """
        def process(row: dict) -> dict:
            out = ...
            a = out['k1']
            b = out['k2']
            c = out['k3']
            return a, b, c
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row"],
        output_names=["out"],
        enclosing_qualname="process",
        source_span=("user.py", 2, 2),
    )
    tight = build_program_analysis_context(slot, source, budget_chars=120)
    loose = build_program_analysis_context(slot, source, budget_chars=4000)
    assert len(tight) <= 120
    assert len(loose) > len(tight)


def test_empty_source_returns_empty_block() -> None:
    slot = _slot(
        free_variables=["row"],
        output_names=["out"],
        enclosing_qualname="process",
    )
    assert build_program_analysis_context(slot, None) == ""
    assert build_program_analysis_context(slot, "") == ""


def test_syntax_error_source_returns_empty_block() -> None:
    slot = _slot(
        free_variables=["row"],
        output_names=["out"],
        enclosing_qualname="process",
    )
    assert build_program_analysis_context(slot, "def broken(:\n  return") == ""


def test_render_block_contains_expected_sections() -> None:
    source = textwrap.dedent(
        """
        def process(row: dict) -> dict:
            out = ...
            key = out['k1']
            return key
        """
    ).lstrip("\n")
    slot = _slot(
        free_variables=["row"],
        output_names=["out"],
        enclosing_qualname="process",
        source_span=("user.py", 2, 2),
    )
    block = build_program_analysis_context(slot, source)
    assert "Formal code constraints" in block
    assert "Input parameter types" in block
    assert "Required output keys" in block
    assert "row" in block
    assert "out" in block
