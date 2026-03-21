from __future__ import annotations

from dataclasses import dataclass

from semipy.dataclass_utils import coerce_dataclass


@dataclass
class Box:
    a: int
    b: str


def test_coerce_dataclass_from_dict() -> None:
    d = {"a": 1, "b": "x"}
    v = coerce_dataclass(d, Box)
    assert isinstance(v, Box)
    assert v.a == 1 and v.b == "x"


def test_coerce_dataclass_passthrough_instance() -> None:
    b = Box(2, "y")
    assert coerce_dataclass(b, Box) is b
