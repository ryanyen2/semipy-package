"""U1: ambiguity-germ taxonomy and detectors."""
from __future__ import annotations

import math

from semipy.decisions.germs import (
    BOUNDARY,
    COERCION,
    EMPTY,
    GROUPING_KEY,
    NULL,
    ORDERING,
    PRECISION,
    TIE,
    detect_germ_ids,
    detect_germs,
)


def test_null_germ_in_list_of_dicts():
    rows = [{"cover": 0.4}, {"cover": None}, {"cover": 0.7}]
    ids = detect_germ_ids(rows)
    assert NULL in ids


def test_empty_collection_germ():
    assert EMPTY in detect_germ_ids([])
    assert EMPTY in detect_germ_ids({})
    assert EMPTY in detect_germ_ids("")


def test_grouping_key_and_tie_on_repeated_values():
    rows = [
        {"site": "s1", "year": 2020, "cover": 0.4},
        {"site": "s1", "year": 2021, "cover": 0.5},
        {"site": "s2", "year": 2020, "cover": 0.6},
    ]
    ids = detect_germ_ids(rows)
    # 'site' repeats (s1 twice) -> candidate grouping axis; 'year' repeats too.
    assert GROUPING_KEY in ids


def test_precision_germ_on_nan_and_float():
    assert PRECISION in detect_germ_ids([float("nan")])
    assert PRECISION in detect_germ_ids([0.5])
    assert PRECISION in detect_germ_ids([math.inf])


def test_coercion_germ_on_mixed_types():
    assert COERCION in detect_germ_ids([1, "two", 3])


def test_boundary_germ_on_zero_and_negative():
    assert BOUNDARY in detect_germ_ids([0])
    assert BOUNDARY in detect_germ_ids([-5])


def test_ordering_germ_on_multi_element_sequence():
    assert ORDERING in detect_germ_ids([3, 1, 2])


def test_tie_germ_on_duplicates():
    assert TIE in detect_germ_ids([1, 1, 2])


def test_fully_specified_scalar_has_no_germ():
    # A single positive integer carries no structural ambiguity.
    assert detect_germ_ids(5) == set()
    assert detect_germ_ids("hello") == set()


def test_detection_is_deterministic():
    rows = [{"cover": None}, {"cover": 0.3}]
    assert detect_germs(rows) == detect_germs(rows)


def test_hits_carry_paths():
    rows = [{"cover": 0.4}, {"cover": None}]
    null_hits = [h for h in detect_germs(rows) if h.germ == NULL]
    assert null_hits
    assert null_hits[0].path == "$[1].cover"


def test_nested_walk_finds_deep_null():
    value = {"outer": {"inner": [1, None]}}
    assert NULL in detect_germ_ids(value)
