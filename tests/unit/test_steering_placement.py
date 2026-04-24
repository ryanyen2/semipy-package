"""Tests for the V2 steering surface writer: split-zone placement, promotion
detection, and minimum-set emptiness rules.
"""
from __future__ import annotations

from semipy.agents.skeleton_writer import (
    _find_anchor_line,
    _find_zone_e_anchor_end,
    _parse_zone_e,
    _parse_zone_p,
    _zone_e_lines,
    _zone_p_lines,
    detect_promoted_keys,
)
from semipy.agents.steering import (
    _derive_verified,
    _extract_return_shape,
    _should_skip_key,
)
from semipy.models import SteeringBlock, SteeringEntry


# ---------------------------------------------------------------------------
# Anchor detection
# ---------------------------------------------------------------------------


class _MockSlotSpec:
    def __init__(
        self,
        *,
        source_span=("/tmp/fake.py", 1, 1),
        spec_text: str = "",
        enclosing_function_source: str = "",
    ) -> None:
        self.source_span = source_span
        self.spec_text = spec_text
        self.enclosing_function_source = enclosing_function_source


def test_anchor_prefers_inline_over_promoted_arrow() -> None:
    file_lines = [
        "def baz():\n",
        "    #> verified: promoted\n",
        "    x = ... #> infer x\n",
        "    return x\n",
    ]
    anchor = _find_anchor_line(
        file_lines,
        "",
        _MockSlotSpec(source_span=("/tmp/f.py", 3, 3)),
    )
    # The inline (rank 4) line should win over the promoted `#> verified:` (rank 1).
    assert anchor == (2, "    ")


def test_anchor_falls_back_to_comment_block() -> None:
    file_lines = [
        "def foo():\n",
        "    #> spec line\n",
        "    return x\n",
    ]
    anchor = _find_anchor_line(
        file_lines,
        "",
        _MockSlotSpec(source_span=("/tmp/f.py", 2, 2)),
    )
    assert anchor == (1, "    ")


# ---------------------------------------------------------------------------
# Zone E anchor end (inline vs comment block)
# ---------------------------------------------------------------------------


def test_zone_e_anchor_end_inline() -> None:
    file_lines = ["def f():\n", "    x = ... #> spec\n", "    return x\n"]
    assert _find_zone_e_anchor_end(file_lines, 1, "    ") == 1


def test_zone_e_anchor_end_walks_comment_block() -> None:
    file_lines = [
        "def f():\n",
        "    #> spec line 1\n",
        "    #> spec line 2\n",
        "    return policy\n",
    ]
    # If anchor idx is the first `#>` line, Zone E must come after the last
    # contiguous `#>` at same indent.
    assert _find_zone_e_anchor_end(file_lines, 1, "    ") == 2


def test_zone_e_anchor_end_skips_multiline_call() -> None:
    """Multi-line ``semi(...)`` must land Zone E AFTER the closing paren."""
    file_lines = [
        "def main():\n",
        "    h = semi(\n",          # 1 anchor (rank 2 — `semi(`)
        '        f"prompt",\n',     # 2
        "        expected_type=dict[str, str],\n",  # 3 — nested brackets
        "    )\n",                  # 4 closer
        "    print(h)\n",           # 5
    ]
    assert _find_zone_e_anchor_end(file_lines, 1, "    ") == 4


# ---------------------------------------------------------------------------
# Zone P / Zone E parse with `#>` passthrough
# ---------------------------------------------------------------------------


def test_zone_p_passthroughs_arrow_lines() -> None:
    file_lines = [
        "def foo():\n",
        "    #< alt: old-alt\n",
        "    #> verified: promoted\n",
        "    #< commits: old-commits\n",
        "    x = ... #> infer x\n",
        "    #< yields: old-yields\n",
        "    return x\n",
    ]
    p_idxs, p_start = _parse_zone_p(file_lines, 4, "    ")
    # All `#<` lines above the anchor are collected; `#>` is pass-through.
    assert p_idxs == [1, 3]
    assert p_start == 1

    e_idxs, e_start = _parse_zone_e(file_lines, 4, "    ")
    assert e_idxs == [5]
    assert e_start == 5


def test_zone_p_stops_at_blank_line() -> None:
    file_lines = [
        "def foo():\n",
        "    #< orphaned-above-blank: would-be-old\n",  # 1
        "\n",                                             # 2 blank stops scan
        "    #< recent: close-to-anchor\n",               # 3
        "    x = ...\n",                                  # 4 anchor
    ]
    p_idxs, _ = _parse_zone_p(file_lines, 4, "    ")
    # Only idx 3 should be picked up — scan stops at the blank line (idx 2).
    assert p_idxs == [3]


# ---------------------------------------------------------------------------
# Promotion detection
# ---------------------------------------------------------------------------


def test_promotion_from_spec_text() -> None:
    spec = _MockSlotSpec(spec_text="verified: Mar 2025 -> result")
    assert detect_promoted_keys(spec) == {"verified": "Mar 2025 -> result"}


def test_promotion_from_enclosing_source() -> None:
    src = "\n".join([
        "def foo():",
        "    #< alt: something",
        "    #> intent: canonical objective",
        "    x = ...",
    ])
    spec = _MockSlotSpec(spec_text="infer something", enclosing_function_source=src)
    assert detect_promoted_keys(spec) == {"intent": "canonical objective"}


# ---------------------------------------------------------------------------
# Zone renderers skip promoted keys
# ---------------------------------------------------------------------------


def test_render_skips_promoted_keys() -> None:
    block = SteeringBlock(
        intent=SteeringEntry(value="extract pattern", input_sig="a"),
        verified=SteeringEntry(value="x -> y", input_sig="c"),
        by=SteeringEntry(value="ordered probe", input_sig="d"),
    )
    promoted = {"verified"}
    p = _zone_p_lines(block, "    ", promoted)
    e = _zone_e_lines(block, "    ", promoted)
    # Promoted `verified` must not appear in Zone E.
    assert not any("verified" in ln for ln in e)
    # Non-promoted keys should appear.
    assert any("#< intent:" in ln for ln in p)
    assert any("#< by:" in ln for ln in p)


# ---------------------------------------------------------------------------
# `_derive_verified` — rule-based (no LLM)
# ---------------------------------------------------------------------------


class _MockValidation:
    def __init__(self, passed: bool, stdout: str = "") -> None:
        self.passed = passed
        self.gist_stdout = stdout


class _MockSpec:
    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _MockEntry:
    def __init__(self, validation_result=None, generated_source: str = "") -> None:
        self.validation_result = validation_result
        self.generated_source = generated_source
        self.commitment_record = None


class _MockSlot:
    advisor_state: dict = {}
    slot_id = "x"


def test_derive_verified_populates_when_passed() -> None:
    spec = _MockSpec(
        prompt="infer X",
        sample_input={"runtime_values": {"a": "hello"}},
        session_input_observations={},
        upstream_lineage=[],
        slot_spec=None,
        decision="GENERATE",
        verify_failure_context="",
        expected_type=str,
    )
    entry = _MockEntry(
        validation_result=_MockValidation(True, stdout="'result-value'"),
        generated_source="def f(a):\n    return 'result-value'\n",
    )
    v = _derive_verified(spec, entry, _MockSlot())
    assert "result-value" in v.value
    assert v.input_sig  # non-empty signature


def test_derive_verified_empty_when_failed() -> None:
    spec = _MockSpec(
        prompt="x",
        sample_input={},
        session_input_observations={},
        upstream_lineage=[],
        slot_spec=None,
        decision="GENERATE",
        verify_failure_context="",
        expected_type=str,
    )
    entry = _MockEntry(validation_result=_MockValidation(False))
    v = _derive_verified(spec, entry, _MockSlot())
    assert v.value == ""
    assert v.input_sig


# ---------------------------------------------------------------------------
# `_extract_return_shape` grounding
# ---------------------------------------------------------------------------


def test_extract_return_shape_dict_literal() -> None:
    src = "def f(x):\n    return {'key': x, 'other': 1}\n"
    shape = _extract_return_shape(src)
    assert "dict" in shape
    assert "'key'" in shape


def test_extract_return_shape_bare_name_is_trivial() -> None:
    src = "def f(x):\n    return x\n"
    assert _extract_return_shape(src) == ""


def test_extract_return_shape_bad_syntax_empty() -> None:
    assert _extract_return_shape("def ??? :::") == ""


# ---------------------------------------------------------------------------
# Minimum-set emptiness rules
# ---------------------------------------------------------------------------


class _MockSlotSpecFull:
    def __init__(self, category: str = "expression", output_names=(), free_variables=("x",)) -> None:
        class _Cat:
            def __init__(self, v):
                self.value = v
        self.expected_category = _Cat(category)
        self.output_names = list(output_names)
        self.free_variables = list(free_variables)


def test_skip_given_for_single_param() -> None:
    spec = _MockSpec(
        prompt="x",
        slot_spec=_MockSlotSpecFull(free_variables=("date_str",)),
        session_input_observations={"date_str": ["a", "b", "c"]},
        expected_type=str,
        decision="GENERATE",
        verify_failure_context="",
    )
    entry = _MockEntry(validation_result=_MockValidation(True))
    assert _should_skip_key("given", spec, entry) is True


def test_skip_yields_for_statement_block_scalar() -> None:
    spec = _MockSpec(
        prompt="x",
        slot_spec=_MockSlotSpecFull(
            category="statement",
            output_names=("input_pattern",),
            free_variables=("date_str",),
        ),
        expected_type=str,
        decision="GENERATE",
        verify_failure_context="",
    )
    entry = _MockEntry(
        validation_result=_MockValidation(True),
        generated_source="def f(d):\n    return {'input_pattern': '%Y'}\n",
    )
    assert _should_skip_key("yields", spec, entry) is True


def test_always_emit_by() -> None:
    spec = _MockSpec(
        prompt="x",
        slot_spec=_MockSlotSpecFull(),
        decision="GENERATE",
        verify_failure_context="",
        expected_type=str,
    )
    entry = _MockEntry(validation_result=_MockValidation(True))
    assert _should_skip_key("by", spec, entry) is False


def test_skip_unless_when_no_exceptional_path() -> None:
    spec = _MockSpec(
        prompt="x",
        slot_spec=_MockSlotSpecFull(),
        decision="GENERATE",
        verify_failure_context="",
        expected_type=str,
    )
    entry = _MockEntry(
        validation_result=_MockValidation(True),
        generated_source="def f(x):\n    return x.upper()\n",
    )
    assert _should_skip_key("unless", spec, entry) is True


def test_emit_unless_when_raise_present() -> None:
    spec = _MockSpec(
        prompt="x",
        slot_spec=_MockSlotSpecFull(),
        decision="GENERATE",
        verify_failure_context="",
        expected_type=str,
    )
    entry = _MockEntry(
        validation_result=_MockValidation(True),
        generated_source="def f(x):\n    if not x:\n        raise ValueError('empty')\n    return x\n",
    )
    assert _should_skip_key("unless", spec, entry) is False
