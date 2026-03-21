from __future__ import annotations

import ast

from semipy.lowering import scan_informal_specs


def test_semi_free_variables_only_from_prompt_not_all_locals() -> None:
    src = '''
def main() -> None:
    parser = 1
    args = 2
    doc_path = 3
    jurisdiction = 4
    min_risk = 5.0
    x = semi(
        f"Comma-separated subset of {list(ALLOWED_CATEGORIES)} to include, or empty for all. "
        f"Jurisdiction {jurisdiction!r}.",
        expected_type=str,
    )
'''
    specs = scan_informal_specs(
        src,
        filename="t.py",
        func_qualname="main",
        first_lineno=1,
        type_hints={},
        globals_ns={"ALLOWED_CATEGORIES": ("a", "b")},
    )
    semi_specs = [s for s in specs if "jurisdiction" in s.spec_text or "ALLOWED" in s.spec_text]
    assert len(semi_specs) == 1
    fv = semi_specs[0].free_variables
    assert "parser" not in fv
    assert "args" not in fv
    assert "doc_path" not in fv
    assert "jurisdiction" in fv
    assert "ALLOWED_CATEGORIES" in fv


def test_semi_headline_uses_doc_path_only() -> None:
    src = '''
def main() -> None:
    doc_path = None
    headline = semi(
        f"One line for {doc_path.name!r}.",
        expected_type=str,
    )
'''
    specs = scan_informal_specs(
        src,
        filename="t.py",
        func_qualname="main",
        first_lineno=1,
        type_hints={},
        globals_ns={},
    )
    semi_specs = [s for s in specs if "One line" in s.spec_text]
    assert semi_specs[0].free_variables == ["doc_path"]
