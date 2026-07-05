"""Frontier-kernel Phase 4 prerequisite: metamorphic relations beyond strings.

The registry was string-only (whitespace/trailing-newline invariance, both
identity transforms on non-strings) -- vacuous for any non-string slot, since
the transform returns the input unchanged. dict_key_order_invariance and
list_permutation_invariance give records and collections a real, structurally
different transformed input to check against.
"""
from __future__ import annotations

from semipy.contract.relations import get_relation, is_relation_nonvacuous, relation_names


def test_dict_key_order_invariance_reverses_key_order_but_keeps_keys_and_values():
    transform, relation = get_relation("dict_key_order_invariance")
    original = {"a": 1, "b": 2, "c": 3}
    transformed = transform(original)
    assert transformed == original  # same mapping...
    assert list(transformed.items()) != list(original.items())  # ...different order
    assert relation == "equal"


def test_dict_key_order_invariance_is_identity_for_non_dict_and_single_key_dict():
    transform, _ = get_relation("dict_key_order_invariance")
    assert transform("not a dict") == "not a dict"
    assert transform({"only": 1}) == {"only": 1}


def test_list_permutation_invariance_reverses_element_order():
    transform, relation = get_relation("list_permutation_invariance")
    original = [1, 2, 3]
    transformed = transform(original)
    assert sorted(transformed) == sorted(original)
    assert transformed != original
    assert relation == "equal"


def test_list_permutation_invariance_is_identity_for_non_list_and_short_list():
    transform, _ = get_relation("list_permutation_invariance")
    assert transform("abc") == "abc"
    assert transform([1]) == [1]
    assert transform([]) == []


def test_get_relation_returns_none_for_unknown_name():
    assert get_relation("not_a_real_relation") is None


def test_relation_names_includes_the_new_relations():
    names = relation_names()
    assert "dict_key_order_invariance" in names
    assert "list_permutation_invariance" in names


def test_is_relation_nonvacuous_true_when_the_transform_actually_perturbs_the_value():
    assert is_relation_nonvacuous("dict_key_order_invariance", {"a": 1, "b": 2}) is True
    assert is_relation_nonvacuous("list_permutation_invariance", [1, 2, 3]) is True
    assert is_relation_nonvacuous("whitespace_invariance", "hello") is True


def test_is_relation_nonvacuous_false_when_the_relation_does_not_apply_to_this_shape():
    # A dict-shaped relation applied to a string, or a collection too small to
    # reorder, returns the value unchanged -- vacuous, carries no evidence.
    assert is_relation_nonvacuous("dict_key_order_invariance", "a string") is False
    assert is_relation_nonvacuous("list_permutation_invariance", [1]) is False
    assert is_relation_nonvacuous("whitespace_invariance", 42) is False


def test_is_relation_nonvacuous_false_for_unknown_relation():
    assert is_relation_nonvacuous("not_a_real_relation", "hello") is False
