"""Gist test invocation must not embed repr(instance) in source (SyntaxError)."""
from __future__ import annotations

from semipy.agents.gist import _build_test_invocation, _expr_for_gist_invocation
from semipy.types import GenerationSpec, SemiCallSite


def test_expr_for_gist_skips_object_repr() -> None:
    class X:
        pass

    assert _expr_for_gist_invocation(X()) == "None"
    assert _expr_for_gist_invocation(3) == "3"
    assert _expr_for_gist_invocation("a") == "'a'"
    assert _expr_for_gist_invocation([1, "b"]) == "[1, 'b']"


def test_build_test_invocation_valid_python_with_instance_arg() -> None:
    class Smart:
        pass

    spec = GenerationSpec(
        prompt="x",
        call_site=SemiCallSite(filename="f.py", lineno=1, func_qualname="m"),
        expected_type=int,
        sample_input={"args": (Smart(), 4), "kwargs": {}},
    )
    line = _build_test_invocation(spec, "fn")
    assert "Smart" not in line or "at 0x" not in line
    assert "None" in line
    assert "4" in line
    compile(line + "\npass", "<gist>", "exec")
