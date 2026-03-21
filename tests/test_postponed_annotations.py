"""PEP 563 postponed annotations must resolve to real types for SlotSpec.expected_type."""
from __future__ import annotations

from dataclasses import dataclass

from semipy import semiformal


@dataclass
class _Thing:
    x: int


@semiformal
class _Container:
    def slot(self) -> _Thing:
        #> Build ir for return.
        return ir  # type: ignore[name-defined]


def test_statement_block_expected_type_is_class_not_string() -> None:
    ctx = _Container.slot._semipy_context  # type: ignore[attr-defined]
    assert len(ctx.slot_specs) == 1
    et = ctx.slot_specs[0].expected_type
    assert et is _Thing
    assert not isinstance(et, str)
