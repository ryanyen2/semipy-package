"""U9: concurrent-role lane model. Offline (asserts the rendered model)."""
from __future__ import annotations

from semipy.orchestration.console_lanes import RoleLanesModel, make_lanes_sink


def test_single_active_role_renders_one_line():
    m = RoleLanesModel()
    m.set_phase("coder", "active")
    m.push("coder", "writing function")
    lines = m.render_lines()
    assert len(lines) == 1
    assert lines[0].startswith("coder [active]")
    assert "writing function" in lines[0]


def test_two_concurrent_roles_render_two_distinct_lanes():
    m = RoleLanesModel()
    m.set_phase("explorer", "active")
    m.push("explorer", "scanning deps")
    m.set_phase("version-checker", "active")
    m.push("version-checker", "gathering evidence")

    lines = m.render_lines()
    assert m.active_roles() == ["explorer", "version-checker"]
    assert any("explorer" in l and "scanning deps" in l for l in lines)
    assert any("version-checker" in l and "gathering evidence" in l for l in lines)


def test_untouched_lanes_are_not_rendered():
    m = RoleLanesModel()
    m.set_phase("verifier", "done")
    # Only the verifier was touched; the other five lanes stay hidden.
    assert m.render_lines() == ["verifier [done]"]


def test_push_tail_is_bounded_and_newline_split():
    from semipy.orchestration.console_lanes import TAIL_LINES

    m = RoleLanesModel()
    for i in range(5):
        m.push("coder", f"line{i}\n")
    lane = m.lanes["coder"]
    # Bounded to exactly TAIL_LINES, keeping the most recent lines (not a vacuous
    # <= bound that would pass even if maxlen regressed upward).
    assert len(lane.tail) == TAIL_LINES
    assert list(lane.tail)[-1] == ""  # trailing newline opened a fresh empty line
    assert "line4" in list(lane.tail)


def test_as_renderable_builds_without_error():
    m = RoleLanesModel()
    m.set_phase("coder", "active")
    m.push("coder", "x")
    assert m.as_renderable() is not None


# --- sink selection (verbose / terminal gating) ---------------------------

def test_sink_none_when_not_verbose():
    assert make_lanes_sink(verbose=False, is_terminal=True) is None


def test_sink_none_when_piped():
    assert make_lanes_sink(verbose=True, is_terminal=False) is None


def test_sink_present_for_verbose_terminal():
    assert isinstance(make_lanes_sink(verbose=True, is_terminal=True), RoleLanesModel)
