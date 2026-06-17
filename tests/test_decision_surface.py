"""U8: inline #? decision surface and slot-identity preservation."""
from __future__ import annotations

from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.surface import (
    format_decision_line,
    is_decision_line,
    render_open_decisions,
    strip_decision_lines,
)
from semipy.lowering import scan_informal_specs
from semipy.lowering_ast import strip_skeleton_lines


def _decision(status="open"):
    d = Decision(
        germ="null",
        axis_label="null cover",
        branches=[
            Branch("skip", ["a", "b", "c"], 0.6),
            Branch("count as 0", ["d", "e"], 0.4),
        ],
    )
    d.status = status
    return d


def test_two_branch_decision_renders_one_line_with_labels_and_weights():
    line = format_decision_line(_decision())
    assert line.startswith("#? null cover:")
    assert "skip (60%)" in line
    assert "count as 0 (40%)" in line


def test_resolved_decision_renders_nothing():
    dset = DecisionSet(slot_id="s", decisions=[_decision(status="resolved")], candidates={})
    assert render_open_decisions(dset) == []


def test_open_decision_renders_one_line():
    dset = DecisionSet(slot_id="s", decisions=[_decision()], candidates={})
    assert len(render_open_decisions(dset)) == 1


def test_strip_skeleton_blanks_decision_lines_preserving_line_count():
    src = "x = 1\n    #? null cover: skip (60%) | count as 0 (40%)\ny = 2\n"
    stripped = strip_skeleton_lines(src)
    assert stripped.count("\n") == src.count("\n")
    assert "#?" not in stripped
    # The line becomes a blank '#', preserving indentation budget.
    assert "    #\n" in stripped


def test_decision_and_reasoning_lines_coexist_in_strip():
    src = "#> spec line\n#< by: something\n#? null cover: skip (60%) | count as 0 (40%)\nz = 1\n"
    stripped = strip_skeleton_lines(src)
    assert "#>" in stripped  # spec preserved
    assert "#<" not in stripped  # reasoning blanked
    assert "#?" not in stripped  # decision blanked


_SRC_NO_FORK = '''\
def report(rows):
    #> average cover per site
    out = group(rows)
    return out
'''

_SRC_WITH_FORK = '''\
def report(rows):
    #? null cover: skip (60%) | count as 0 (40%)
    #> average cover per site
    out = group(rows)
    return out
'''


def test_adding_decision_line_does_not_change_slot_id():
    base = scan_informal_specs(strip_skeleton_lines(_SRC_NO_FORK), "f.py", "report", 1)
    forked = scan_informal_specs(strip_skeleton_lines(_SRC_WITH_FORK), "f.py", "report", 1)
    assert [s.slot_id for s in base] == [s.slot_id for s in forked]
    assert len(base) == len(forked) == 1


def test_strip_decision_lines_removes_them():
    src = "a = 1\n#? x: p (50%) | q (50%)\nb = 2\n"
    out = strip_decision_lines(src)
    assert "#?" not in out
    assert "a = 1" in out and "b = 2" in out


def test_is_decision_line():
    assert is_decision_line("  #? foo: a (50%) | b (50%)")
    assert not is_decision_line("#< by: x")
    assert not is_decision_line("#> spec")
