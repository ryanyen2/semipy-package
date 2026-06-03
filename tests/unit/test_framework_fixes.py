"""Regression tests for three general framework bugs found via the effects demo.

These are NOT effects-specific -- they affect standalone ``semi()`` usage and error
reporting generally -- so they live in their own file:

  1. Multi-line / ``return semi(...)`` template extraction (semi_fn): the call's
     template + interpolated variables must be recovered, else every call mints a
     new slot (no reuse) and bakes the formatted data into the prompt.
  2. ``SemiCallError.__str__`` must not infinitely recurse when raised without a
     ``__cause__``.
  3. The skeleton writer must NOT rewrite source for standalone ``semi()`` slots
     (rewriting shifts line numbers and breaks source-line extraction on reuse).
"""
from __future__ import annotations

import textwrap

from semipy.semi_fn import _extract_semi_template_from_source_line, _full_call_statement
from semipy.slot_resolver import _should_surface_skeleton
from semipy.types import SemiCallError, SlotCategory, SlotSpec


# --- 1. template extraction: multi-line + return semi(...) -----------------
def test_extract_multiline_return_semi():
    customer = {"id": 42, "tier": "gold"}
    src = (
        "return semi(\n"
        '    f"Upsert the customer {customer} into db://customers "\n'
        '    f"by its id."\n'
        ")"
    )
    tmpl, keys, vals = _extract_semi_template_from_source_line(
        source_line=src, globals_ns={}, locals_ns={"customer": customer}
    )
    assert tmpl == "Upsert the customer {v0} into db://customers by its id."
    assert keys == ["v0"]
    assert vals == {"v0": customer}


def test_extract_single_line_return_semi():
    x = 7
    tmpl, keys, vals = _extract_semi_template_from_source_line(
        source_line='return semi(f"double {x}")', globals_ns={}, locals_ns={"x": x}
    )
    assert tmpl == "double {v0}" and keys == ["v0"] and vals == {"v0": 7}


def test_extract_assignment_and_bare_forms():
    x = "NYC"
    for src in ('y = semi(f"expand {x}")', 'semi(f"expand {x}")'):
        tmpl, keys, vals = _extract_semi_template_from_source_line(
            source_line=src, globals_ns={}, locals_ns={"x": x}
        )
        assert tmpl == "expand {v0}" and vals == {"v0": "NYC"}, src


def test_extract_plain_string_no_vars():
    tmpl, keys, vals = _extract_semi_template_from_source_line(
        source_line='semi("just a constant prompt")', globals_ns={}, locals_ns={}
    )
    assert tmpl == "just a constant prompt" and keys == [] and vals == {}


def test_full_call_statement_spans_multiple_lines(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        textwrap.dedent(
            '''\
            def go(customer):
                return semi(
                    f"Upsert {customer} into db://t "
                    f"by id."
                )
            '''
        )
    )
    # the `return semi(` is on line 2; the statement spans lines 2-5
    stmt = _full_call_statement(str(f), 2)
    assert stmt.startswith("return semi(")
    assert 'f"by id."' in stmt
    tmpl, keys, vals = _extract_semi_template_from_source_line(
        source_line=stmt, globals_ns={}, locals_ns={"customer": {"id": 1}}
    )
    assert tmpl == "Upsert {v0} into db://t by id." and keys == ["v0"]


# --- 2. SemiCallError.__str__ must not recurse -----------------------------
def test_semicallerror_str_no_cause_does_not_recurse():
    e = SemiCallError("a deliberate message with no underlying cause")
    s = str(e)  # must not raise RecursionError
    assert "a deliberate message with no underlying cause" in s


def test_semicallerror_str_with_cause():
    try:
        raise ValueError("boom")
    except ValueError as cause:
        e = SemiCallError("wrapper", cause=cause)
        s = str(e)
    assert "boom" in s


# --- 3. skeleton writing is skipped for standalone semi() ------------------
def _spec(category):
    return SlotSpec(
        slot_id="s", source_span=("d.py", 1, 1), spec_text="x", spec_hash="h",
        spec_equivalence_key="k", free_variables=[], control_context="",
        expected_category=category, expected_type=type(None), output_names=[],
        formal_constraints=[], usage_hints=[], enclosing_function_source="",
        enclosing_function_qualname="d",
    )


def test_skeleton_skipped_for_standalone():
    assert _should_surface_skeleton(_spec(SlotCategory.EXPRESSION_STANDALONE)) is False
    # decorated/#>-block slots still get their skeleton surface written
    assert _should_surface_skeleton(_spec(SlotCategory.STATEMENT_BLOCK)) is True
    assert _should_surface_skeleton(_spec(SlotCategory.EXPRESSION)) is True
