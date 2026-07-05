"""Frontier-kernel Phase 5: the regime-guard DSL (compile_guard + dispatch)."""
from __future__ import annotations

from semipy.kernel.guard import compile_guard, dispatch


def test_compiles_and_evaluates_simple_comparisons():
    g = compile_guard("x > 0")
    assert g is not None
    assert g.evaluate({"x": 5}) is True
    assert g.evaluate({"x": -5}) is False


def test_compiles_chained_and_boolean_comparisons():
    g = compile_guard("0 <= x < 10")
    assert g is not None
    assert g.evaluate({"x": 5}) is True
    assert g.evaluate({"x": 15}) is False

    g2 = compile_guard("x > 0 and y < 10")
    assert g2.evaluate({"x": 1, "y": 1}) is True
    assert g2.evaluate({"x": 1, "y": 100}) is False

    g3 = compile_guard("not (x is None)")
    assert g3.evaluate({"x": 1}) is True
    assert g3.evaluate({"x": None}) is False


def test_compiles_isinstance_len_and_type_calls():
    assert compile_guard("isinstance(x, int)").evaluate({"x": 1}) is True
    assert compile_guard("isinstance(x, (int, float))").evaluate({"x": 1.5}) is True
    assert compile_guard("len(items) == 0").evaluate({"items": []}) is True
    assert compile_guard("type(x) is int").evaluate({"x": 1}) is True


def test_compiles_attribute_and_subscript_access():
    # The user's literal merge-conflict example: message-kind dispatch.
    g = compile_guard("msg.kind == 'insert'")
    assert g.evaluate({"msg": type("M", (), {"kind": "insert"})()}) is True
    assert g.evaluate({"msg": type("M", (), {"kind": "delete"})()}) is False

    g2 = compile_guard('row["status"] == "active"')
    assert g2.evaluate({"row": {"status": "active"}}) is True
    assert g2.evaluate({"row": {"status": "inactive"}}) is False


def test_compiles_bare_truthy_value_and_membership():
    assert compile_guard("labels").evaluate({"labels": ["a"]}) is True
    assert compile_guard("labels").evaluate({"labels": []}) is False
    assert compile_guard("x in (1, 2, 3)").evaluate({"x": 2}) is True


def test_rejects_arbitrary_function_calls():
    assert compile_guard("os.system('rm -rf /')") is None
    assert compile_guard("foo()") is None
    assert compile_guard("x.append(1)") is None


def test_rejects_non_predicate_and_malformed_syntax():
    assert compile_guard("x = 1") is None  # not an expression
    assert compile_guard("lambda x: x") is None
    assert compile_guard("[x for x in y]") is None
    assert compile_guard("(x := 1)") is None
    assert compile_guard("not not not (((") is None  # syntax error


def test_evaluate_returns_false_rather_than_raising_on_a_missing_free_variable():
    g = compile_guard("x > 0")
    assert g.evaluate({}) is False  # NameError inside evaluate -> False, not raised


def test_dispatch_returns_the_first_matching_guard_index_or_none():
    guards = [compile_guard("x < 0"), compile_guard("x == 0"), compile_guard("x > 0")]
    assert dispatch(guards, {"x": 5}) == 2
    assert dispatch(guards, {"x": 0}) == 1
    assert dispatch(guards, {"x": -5}) == 0


def test_dispatch_returns_none_when_no_guard_matches():
    guards = [compile_guard("x < 0"), compile_guard("x == 0")]
    assert dispatch(guards, {"x": 5}) is None
    assert dispatch([], {"x": 5}) is None
