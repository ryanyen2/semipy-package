from __future__ import annotations

from semipy.reactivity.flow import (
    FLOW_ATTR,
    attach_producer_flow,
    create_flow,
    extract_flow,
)


def test_attach_producer_flow_wraps_plain_list() -> None:
    flow = create_flow("sess", "slot_a", "commit1")

    class Row:
        pass

    inner = [Row()]
    out = attach_producer_flow(inner, flow)
    assert out is not inner
    assert extract_flow(out) is not None
    assert extract_flow(out).producing_slot.slot_id == "slot_a"
    assert extract_flow(out[0]) is not None


def test_attach_producer_flow_leaves_object_with_dict() -> None:
    flow = create_flow("sess", "s", "c")

    class Box:
        pass

    b = Box()
    out = attach_producer_flow(b, flow)
    assert out is b
    assert getattr(b, FLOW_ATTR, None) is not None
