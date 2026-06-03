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


# --- placeholder-return idiom: ``result = <placeholder>; #> ...; return result`` ---
# A variable initialized to a trivial placeholder only so the skeleton parses, then
# read after the #> block, is the block's OUTPUT -- not a pre-existing input. It must
# not be subtracted away as "defined before" (which yielded output_names=[] and made
# the slot a side-effect block that must return None).

def test_scan_placeholder_return_is_output():
    source = textwrap.dedent("""\
        def parse_invoice(raw_text):
            result = None
            #> extract the vendor and total in cents from {raw_text}
            return result
    """)
    specs = scan_informal_specs(
        source, "test.py", "parse_invoice", 1,
        type_hints={"raw_text": str, "return": dict},
    )
    assert len(specs) == 1
    s = specs[0]
    assert s.expected_category == SlotCategory.STATEMENT_BLOCK
    assert s.output_names == ["result"]
    assert "result" not in s.free_variables  # the output is not also an input
    assert s.free_variables == ["raw_text"]


def test_scan_placeholder_variants_and_return_type():
    for init, ret in [("''", str), ("0", int), ("[]", list), ("{}", dict), ("bytearray()", bytes)]:
        source = textwrap.dedent(f"""\
            def f(x):
                out = {init}
                #> compute out from {{x}}
                return out
        """)
        specs = scan_informal_specs(source, "test.py", "f", 1, type_hints={"x": str, "return": ret})
        assert specs[0].output_names == ["out"], init
        assert specs[0].expected_type is ret, init


def test_scan_side_effect_block_stays_empty():
    # No placeholder var read after the block -> genuinely a side-effect block.
    source = textwrap.dedent("""\
        def log_it(x):
            #> write x to the telemetry sink
            return None
    """)
    specs = scan_informal_specs(source, "test.py", "log_it", 1, type_hints={"x": str, "return": type(None)})
    assert specs[0].output_names == []


def test_scan_method_excludes_self_from_free_variables():
    # The method receiver is not slot data: it must not become a generated-function
    # parameter, a profiled input, or part of the reuse fingerprint.
    source = textwrap.dedent("""\
        def triage(self, ticket):
            result = None
            #> analyze the {ticket} and produce a triage result
            return result
    """)
    specs = scan_informal_specs(source, "a.py", "A.triage", 1, type_hints={"ticket": str, "return": dict})
    assert specs[0].free_variables == ["ticket"]
    assert specs[0].output_names == ["result"]


def test_scan_method_excludes_cls():
    source = textwrap.dedent("""\
        def make(cls, spec):
            out = None
            #> build something from {spec}
            return out
    """)
    specs = scan_informal_specs(source, "a.py", "A.make", 1, type_hints={"spec": str, "return": dict})
    assert "cls" not in specs[0].free_variables
    assert specs[0].free_variables == ["spec"]


def test_scan_toplevel_function_named_self_is_not_a_receiver():
    # A top-level function (no dot in qualname) whose param happens to be 'self' is
    # NOT a method, so 'self' stays a real input.
    source = textwrap.dedent("""\
        def f(self):
            out = None
            #> use {self}
            return out
    """)
    specs = scan_informal_specs(source, "a.py", "f", 1, type_hints={"self": str, "return": dict})
    assert specs[0].free_variables == ["self"]


def test_scan_returned_real_value_not_hijacked_as_output():
    # ``x`` carries a real computed value before the block: it is a genuine input the
    # block consumes, not the block's output. Do not force it to an output.
    source = textwrap.dedent("""\
        def f(a):
            x = compute(a)
            #> adjust x according to policy
            return x
    """)
    specs = scan_informal_specs(source, "test.py", "f", 1, type_hints={"a": str, "return": str})
    assert specs[0].output_names == []
