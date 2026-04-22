from __future__ import annotations

import textwrap

from semipy.lowering_ast import (
    _is_hash_arrow,
    _is_skeleton_placeholder_line,
    _is_slot_anchor_line,
    _collect_hash_arrow_block_ranges,
    strip_skeleton_lines,
)
from semipy.lowering import scan_informal_specs, _make_slot_id
from semipy.types import SlotCategory


# --- _is_hash_arrow ---

def test_is_hash_arrow_basic():
    assert _is_hash_arrow("#> extract the domain")
    assert _is_hash_arrow("# > also a spec")


def test_is_hash_arrow_indented():
    assert _is_hash_arrow("    #> indented spec")
    assert _is_hash_arrow("    # > indented with space")


def test_is_hash_arrow_negative():
    assert not _is_hash_arrow("# regular comment")
    assert not _is_hash_arrow("#< reasoning line")
    assert not _is_hash_arrow("x = 1")
    assert not _is_hash_arrow("")


# --- _is_skeleton_placeholder_line ---

def test_is_skeleton_placeholder_line():
    assert _is_skeleton_placeholder_line("    #")
    assert _is_skeleton_placeholder_line("#")


def test_is_skeleton_placeholder_negative():
    assert not _is_skeleton_placeholder_line("    #< reasoning")
    assert not _is_skeleton_placeholder_line("    #> spec")
    assert not _is_skeleton_placeholder_line("    # text")
    assert not _is_skeleton_placeholder_line("x = 1")


# --- _is_slot_anchor_line ---

def test_is_slot_anchor_ellipsis():
    assert _is_slot_anchor_line("result = ...")
    assert _is_slot_anchor_line("    name = ...")


def test_is_slot_anchor_semi():
    assert _is_slot_anchor_line("x = semi(f'parse {row}')")
    assert _is_slot_anchor_line("    return semi(f'classify {text}')")


def test_is_slot_anchor_negative():
    assert not _is_slot_anchor_line("x = 1")
    assert not _is_slot_anchor_line("")
    assert not _is_slot_anchor_line("# comment")


# --- _collect_hash_arrow_block_ranges ---

def test_collect_single_block():
    lines = ["#> first spec", "#> second spec line", "result = ..."]
    ranges = _collect_hash_arrow_block_ranges(lines)
    assert len(ranges) == 1
    assert ranges[0] == (0, 1)


def test_collect_multiple_blocks_with_anchor():
    lines = [
        "#> spec one",
        "result_a = ...",
        "#> spec two",
        "result_b = ...",
    ]
    ranges = _collect_hash_arrow_block_ranges(lines)
    assert len(ranges) == 2


def test_collect_empty():
    lines = ["x = 1", "y = 2"]
    assert _collect_hash_arrow_block_ranges(lines) == []


# --- strip_skeleton_lines ---

def test_strip_skeleton_lines_replaces_hash_lt():
    source = "def f():\n    #< reasoning note\n    x = 1\n"
    stripped = strip_skeleton_lines(source)
    assert "#<" not in stripped
    assert "x = 1" in stripped


def test_strip_skeleton_lines_preserves_spec():
    source = "def f():\n    #> spec text\n    x = ...\n"
    stripped = strip_skeleton_lines(source)
    assert "#>" in stripped


# --- _make_slot_id ---

def test_make_slot_id_deterministic():
    id1 = _make_slot_id("file.py", "MyClass.method", 0, "extract domain")
    id2 = _make_slot_id("file.py", "MyClass.method", 0, "extract domain")
    assert id1 == id2
    assert len(id1) == 16


def test_make_slot_id_differs_on_spec():
    id1 = _make_slot_id("file.py", "f", 0, "extract domain")
    id2 = _make_slot_id("file.py", "f", 0, "extract username")
    assert id1 != id2


# --- scan_informal_specs ---

def test_scan_informal_specs_empty_function():
    source = textwrap.dedent("""\
        def f(x):
            return x
    """)
    specs = scan_informal_specs(source, "test.py", "f", 1)
    assert len(specs) == 1
    assert specs[0].expected_category == SlotCategory.FUNCTION_BODY


def test_scan_informal_specs_hash_arrow():
    source = textwrap.dedent("""\
        def extract(email):
            #> extract the domain from the email address
            domain = ...
            return domain
    """)
    specs = scan_informal_specs(source, "test.py", "extract", 1)
    assert len(specs) == 1
    assert specs[0].expected_category == SlotCategory.STATEMENT_BLOCK
    assert "domain" in specs[0].spec_text


def test_scan_informal_specs_semi_call():
    source = textwrap.dedent("""\
        def classify(text):
            label = semi(f"classify {text} as positive or negative")
            return label
    """)
    specs = scan_informal_specs(source, "test.py", "classify", 1)
    assert any(s.expected_category == SlotCategory.EXPRESSION for s in specs)
