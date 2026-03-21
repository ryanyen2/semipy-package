"""STATEMENT_BLOCK + single output: inner value validated with TypeAdapter when expected_type is strict."""
from __future__ import annotations

from dataclasses import dataclass

from semipy.agents.validator import _validate_basic_execution
from semipy.types import SlotCategory


@dataclass
class _Inner:
    a: int


def test_statement_block_typeadapter_accepts_valid_inner_dict() -> None:
    def fn() -> dict[str, object]:
        return {"out": {"a": 1}}

    r = _validate_basic_execution(
        fn=fn,
        expected_type=_Inner,
        sample_input={"args": (), "kwargs": {}},
        slot_category=SlotCategory.STATEMENT_BLOCK,
        output_names=["out"],
    )
    assert r.passed
    assert r.type_correct


def test_statement_block_typeadapter_rejects_bad_inner_shape() -> None:
    def fn() -> dict[str, object]:
        return {"out": {"not_a": 1}}

    r = _validate_basic_execution(
        fn=fn,
        expected_type=_Inner,
        sample_input={"args": (), "kwargs": {}},
        slot_category=SlotCategory.STATEMENT_BLOCK,
        output_names=["out"],
    )
    assert not r.passed
    assert not r.type_correct
    assert "TypeAdapter" in (r.error_message or "")


def test_typeadapter_uses_defining_module_for_nested_dataclasses() -> None:
    """Regression: Pydantic TypeAdapter must not use validator.py globals for user dataclasses."""
    import sys
    import types

    from semipy.agents.validator import _validate_value_with_typeadapter

    @dataclass
    class Row:
        k: str

    @dataclass
    class Doc:
        rows: list[Row]

    Row.__module__ = Doc.__module__ = "__main__"
    fake_main = types.ModuleType("__main__")
    fake_main.Row = Row
    fake_main.Doc = Doc
    old_main = sys.modules.get("__main__")
    sys.modules["__main__"] = fake_main
    try:
        ok, err = _validate_value_with_typeadapter({"rows": []}, Doc)
        assert ok, err
    finally:
        if old_main is not None:
            sys.modules["__main__"] = old_main


def test_statement_block_loose_dict_annotation_still_accepts_opaque_payload() -> None:
    from typing import Any

    def fn() -> dict[str, Any]:
        return {"out": {"anything": "goes"}}

    r = _validate_basic_execution(
        fn=fn,
        expected_type=dict[str, Any],
        sample_input={"args": (), "kwargs": {}},
        slot_category=SlotCategory.STATEMENT_BLOCK,
        output_names=["out"],
    )
    assert r.passed
