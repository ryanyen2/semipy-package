"""Frontier-kernel Phase 6: sketch-library re-gating.

Replaces the single-shot LLM-confidence-only promotion gate with recurrence +
generalization (does the template reproduce a later, *independent*
occurrence) + MDL. Also covers the ``merge_sketch_into_library`` fix that
makes recurrence trackable at all: matching on structural pattern (signature
+ spec_template), not exact ``sketch_id``, since two independently generated
occurrences of the same conceptual pattern rarely produce byte-identical code.
"""
from __future__ import annotations

from semipy.kernel.operators import SketchLicense, license_sketch
from semipy.library.binding import build_semantic_binding, SpecPhrase
from semipy.library.sketch import (
    CodeSketch,
    SketchLibrary,
    SketchParam,
    build_code_sketch_from_commit,
    find_sketch_match,
    merge_sketch_into_library,
)
from semipy.types import SlotCategory, SlotSpec


def _structural_phrase(text: str = "filter rows where status equals") -> SpecPhrase:
    return SpecPhrase(text=text, role="operation", code_referent="", hole_name=None)


def _value_phrase(text: str, code_referent: str) -> SpecPhrase:
    return SpecPhrase(text=text, role="param", code_referent=code_referent, hole_name="value")


def _occurrence(text: str, code_referent: str, generated_source: str, commit_id: str):
    spec_text = f"filter rows where status equals {text}"
    phrases = (_structural_phrase(), _value_phrase(text, code_referent))
    binding = build_semantic_binding(spec_text, phrases, confidence=0.9)
    sketch = build_code_sketch_from_commit(
        binding, generated_source, commit_id, SlotCategory.EXPRESSION_STANDALONE.value, ("df",)
    )
    return spec_text, generated_source, binding, sketch


# ---------------------------------------------------------------------------
# merge_sketch_into_library: recurrence via structural pattern, not sketch_id
# ---------------------------------------------------------------------------


def test_merge_creates_an_unlicensed_candidate_on_first_occurrence():
    spec_text, src, binding, sketch = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    lib = SketchLibrary()
    merged = merge_sketch_into_library(lib, sketch, binding)
    assert merged.licensed is False
    assert merged.source_commit_ids == ["c1"]


def test_merge_matches_a_recurrence_with_different_code_by_structural_pattern():
    # Same structural pattern and spec_template, but the second generation
    # uses a different quote style -- a different code_template/sketch_id --
    # which is exactly the case exact-sketch_id matching cannot see.
    _, _, binding1, sketch1 = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    _, _, binding2, sketch2 = _occurrence(
        "pending", '"pending"', 'def f(df):\n    return df[df["status"] == "pending"]\n', "c2",
    )
    assert sketch1.sketch_id != sketch2.sketch_id  # different code_template

    lib = SketchLibrary()
    merge_sketch_into_library(lib, sketch1, binding1)
    merged = merge_sketch_into_library(lib, sketch2, binding2)

    assert merged.source_commit_ids == ["c1", "c2"]
    assert len(lib.sketches) == 1  # merged into the first sketch, not a second entry


# ---------------------------------------------------------------------------
# license_sketch
# ---------------------------------------------------------------------------


def test_license_refuses_below_recurrence_threshold():
    spec_text, src, binding, sketch = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    result = license_sketch(
        sketch, incoming_spec_text=spec_text, incoming_source=src, min_recurrence=2,
    )
    assert isinstance(result, SketchLicense)
    assert result.licensed is False
    assert "recurrence" in result.reason


def test_license_grants_when_pattern_recurs_and_generalizes():
    _, _, binding1, sketch1 = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    spec2, src2, binding2, sketch2 = _occurrence(
        "pending", '"pending"', 'def f(df):\n    return df[df["status"] == "pending"]\n', "c2",
    )
    lib = SketchLibrary()
    merge_sketch_into_library(lib, sketch1, binding1)
    merged = merge_sketch_into_library(lib, sketch2, binding2)

    result = license_sketch(
        merged, incoming_spec_text=spec2, incoming_source=src2, min_recurrence=2,
    )
    assert result.licensed is True
    assert result.recurrence == 2
    assert result.mdl_gain > 0


def test_license_refuses_when_incoming_spec_no_longer_fits_template():
    _, _, binding1, sketch1 = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    lib = SketchLibrary()
    merged = merge_sketch_into_library(lib, sketch1, binding1)
    merged.source_commit_ids.append("c2")  # force recurrence without a real 2nd occurrence

    result = license_sketch(
        merged,
        incoming_spec_text="a completely different sentence shape entirely",
        incoming_source="def f(df):\n    return df\n",
        min_recurrence=2,
    )
    assert result.licensed is False
    assert "fits the template" in result.reason


def test_license_refuses_when_template_does_not_reproduce_the_occurrence():
    _, _, binding1, sketch1 = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    lib = SketchLibrary()
    merged = merge_sketch_into_library(lib, sketch1, binding1)
    merged.source_commit_ids.append("c2")

    # Same spec shape (fits the template's token pattern), but the "generated"
    # source for this occurrence implements something structurally different
    # from what swapping the hole value in the template would produce.
    result = license_sketch(
        merged,
        incoming_spec_text="filter rows where status equals pending",
        incoming_source="def f(df):\n    return df.query('status == \"pending\"')\n",
        min_recurrence=2,
    )
    assert result.licensed is False
    assert "does not reproduce" in result.reason


def test_license_refuses_via_mdl_gate_when_template_does_not_compress():
    sketch = CodeSketch(
        sketch_id="sid1",
        structural_signature="sig1",
        spec_template="do X to {value}",
        code_template="def f():\n    return {value}  " + ("# padding " * 50),
        params=(SketchParam(hole_name="value", spec_role="param", safe_swap_set=None),),
        source_commit_ids=["c1", "c2"],
        hole_values_original={"value": "1"},
        hole_code_referents={"value": "1"},
    )
    result = license_sketch(
        sketch,
        incoming_spec_text="do X to 2",
        incoming_source="def f():\n    return 2\n",
        min_recurrence=2,
    )
    assert result.licensed is False
    assert "compress" in result.reason


# ---------------------------------------------------------------------------
# find_sketch_match honors the licensed gate
# ---------------------------------------------------------------------------


def _slot_spec(spec_text: str) -> SlotSpec:
    return SlotSpec(
        slot_id="s1",
        source_span=("f.py", 1, 1),
        spec_text=spec_text,
        spec_hash="h",
        spec_equivalence_key="h",
        free_variables=["df"],
        control_context="",
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=None,
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_qualname="f",
        enclosing_function_span=(1, 1),
        enclosing_function_source="def f(df): pass",
    )


def test_find_sketch_match_ignores_unlicensed_sketches():
    spec_text, src, binding, sketch = _occurrence(
        "active", "'active'", "def f(df):\n    return df[df['status'] == 'active']\n", "c1",
    )
    lib = SketchLibrary()
    merged = merge_sketch_into_library(lib, sketch, binding)
    assert merged.licensed is False

    assert find_sketch_match(_slot_spec(spec_text), lib) is None

    merged.licensed = True
    result = find_sketch_match(_slot_spec(spec_text), lib)
    assert result is not None
