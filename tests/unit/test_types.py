from __future__ import annotations

from semipy.types import _sha16, compute_spec_equivalence_key, SlotCategory


def test_sha16_deterministic():
    assert _sha16("hello") == _sha16("hello")
    assert len(_sha16("hello")) == 16
    assert _sha16("hello") != _sha16("world")


def test_sha16_empty():
    result = _sha16("")
    assert len(result) == 16


def test_compute_spec_equivalence_key_same():
    key1 = compute_spec_equivalence_key(
        "extract the domain",
        ["email"],
        str,
        expected_category=SlotCategory.EXPRESSION,
        output_names=[],
    )
    key2 = compute_spec_equivalence_key(
        "extract the domain",
        ["email"],
        str,
        expected_category=SlotCategory.EXPRESSION,
        output_names=[],
    )
    assert key1 == key2


def test_compute_spec_equivalence_key_different_spec():
    key1 = compute_spec_equivalence_key(
        "extract the domain",
        ["email"],
        str,
        expected_category=SlotCategory.EXPRESSION,
        output_names=[],
    )
    key2 = compute_spec_equivalence_key(
        "extract the username",
        ["email"],
        str,
        expected_category=SlotCategory.EXPRESSION,
        output_names=[],
    )
    assert key1 != key2


def test_compute_spec_equivalence_key_different_vars():
    key1 = compute_spec_equivalence_key(
        "extract the domain",
        ["email"],
        str,
        expected_category=SlotCategory.EXPRESSION,
        output_names=[],
    )
    key2 = compute_spec_equivalence_key(
        "extract the domain",
        ["address"],
        str,
        expected_category=SlotCategory.EXPRESSION,
        output_names=[],
    )
    assert key1 != key2
