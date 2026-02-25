"""
Tests for data flow: create_flow, extract_flow, profile_output, _flow_from_inputs.
"""
from __future__ import annotations

import pandas as pd
import pytest

from semipy.reactivity import (
    FLOW_ATTR,
    DataFlow,
    SlotRef,
    create_flow,
    extract_flow,
    profile_output,
    _flow_from_inputs,
)


def test_create_flow() -> None:
    f = create_flow("s1", "slot1", "c1", output_profile={"columns": ["a", "b"]})
    assert f.producing_slot == SlotRef("s1", "slot1")
    assert f.producing_commit_id == "c1"
    assert f.output_profile == {"columns": ["a", "b"]}


def test_extract_flow_missing_returns_none() -> None:
    class Plain:
        pass
    assert extract_flow(Plain()) is None
    assert extract_flow(1) is None


def test_extract_flow_returns_flow_when_present() -> None:
    f = create_flow("s1", "s1", "c1")
    obj = type("Obj", (), {})()
    setattr(obj, FLOW_ATTR, f)
    assert extract_flow(obj) is f


def test_profile_output_none() -> None:
    assert profile_output(None) == {"type": "none"}


def test_profile_output_dict() -> None:
    p = profile_output({"x": 1, "y": 2})
    assert p.get("type") == "dict" or "keys" in p
    keys = p.get("keys", [])
    assert set(keys) >= {"x", "y"}


def test_profile_output_list_of_dicts() -> None:
    p = profile_output([{"a": 1}, {"a": 2}])
    assert p.get("columns") == ["a"] or "columns" in p


def test_profile_output_dataframe_like() -> None:
    df = pd.DataFrame({"c1": [1], "c2": [2]})
    p = profile_output(df)
    assert p.get("columns") == ["c1", "c2"]


def test_flow_from_inputs_single_with_flow() -> None:
    f = create_flow("s1", "slot1", "c1")
    obj = type("Obj", (), {})()
    setattr(obj, FLOW_ATTR, f)
    combined = _flow_from_inputs(obj)
    assert combined is f


def test_flow_from_inputs_no_flow_returns_none() -> None:
    assert _flow_from_inputs(object(), object()) is None


def test_flow_from_inputs_combines_two() -> None:
    f1 = create_flow("s1", "slot1", "c1", upstream_chain=[])
    f2 = create_flow("s1", "slot2", "c2", upstream_chain=[])
    o1, o2 = type("Obj", (), {})(), type("Obj", (), {})()
    setattr(o1, FLOW_ATTR, f1)
    setattr(o2, FLOW_ATTR, f2)
    combined = _flow_from_inputs(o1, o2)
    assert combined is not None
    assert len(combined.upstream_chain) == 2
    assert combined.upstream_chain[0] == f1.producing_slot
    assert combined.upstream_chain[1] == f2.producing_slot
