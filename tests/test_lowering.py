from __future__ import annotations

import ast

from semipy.lowering import lower_to_scaffold, scan_informal_specs
from semipy.types import SlotCategory


def test_scan_statement_block_formal_constraints_excludes_return() -> None:
    source = """
@semiformal
def f(x: int) -> int:
    #> compute y from x
    assert y > x
    return y
"""

    slots = scan_informal_specs(
        source,
        filename="t.py",
        func_qualname="f",
        first_lineno=1,
        type_hints={"return": int},
        globals_ns={"semiformal": object()},
    )
    assert len(slots) == 1
    slot = slots[0]
    assert slot.expected_category == SlotCategory.STATEMENT_BLOCK
    assert slot.output_names == ["y"]
    assert slot.formal_constraints
    assert not any(line.lstrip().startswith("return ") for line in slot.formal_constraints)


def test_scan_statement_block_output_names_excludes_globals() -> None:
    source = """
@semiformal
def f(x: int) -> int:
    #> compute y from x
    assert np.isfinite(y)
    return y
"""

    slots = scan_informal_specs(
        source,
        filename="t.py",
        func_qualname="f",
        first_lineno=1,
        type_hints={"return": int},
        globals_ns={"semiformal": object(), "np": object()},
    )
    assert len(slots) == 1
    slot = slots[0]
    assert slot.expected_category == SlotCategory.STATEMENT_BLOCK
    assert slot.output_names == ["y"]
    assert not any(line.lstrip().startswith("return ") for line in slot.formal_constraints)


def test_lower_to_scaffold_rewrites_semi_and_strips_decorators() -> None:
    source = """
@semiformal
def f(x: int) -> int:
    y = semi(f"add {x}", expected_type=int)
    return y
"""

    slots = scan_informal_specs(
        source,
        filename="t.py",
        func_qualname="f",
        first_lineno=1,
        type_hints={"return": int},
        globals_ns={"semiformal": object(), "int": int},
    )
    assert slots and all(s.expected_category == SlotCategory.EXPRESSION for s in slots)

    scaffold = lower_to_scaffold(source, slots)
    # Scaffold compilation should not recursively re-apply @semiformal.
    assert "@semiformal" not in scaffold
    assert "semi(" not in scaffold
    assert "__slot_" in scaffold

    # Ensure scaffold is valid python after lowering.
    ast.parse(scaffold)


def test_statement_block_output_names_excludes_loop_target() -> None:
    source = """
@semiformal
def f(x: int) -> int:
    #> compute merged_records from x
    merged_records = semi("return merged records", expected_type=list)
    # formal post-processing
    for rec in merged_records:
        assert isinstance(rec, dict)
    return len(merged_records)
"""

    # This function has a #> block, but also a standalone semi() inside.
    # We only care that the #> slot doesn't consider loop-target `rec` as output.
    slots = scan_informal_specs(
        source,
        filename="t.py",
        func_qualname="f",
        first_lineno=1,
        type_hints={"return": int},
        globals_ns={"semiformal": object(), "semi": object()},
    )
    statement_slots = [s for s in slots if s.expected_category == SlotCategory.STATEMENT_BLOCK]
    assert len(statement_slots) == 1
    assert statement_slots[0].output_names != ["rec"]
    # Free vars should not include the loop target either.
    assert "rec" not in statement_slots[0].free_variables

