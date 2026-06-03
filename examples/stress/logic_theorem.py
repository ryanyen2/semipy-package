"""Stress: propositional theorem proving (genuinely algorithmic, recursive logic).

A formal-methods engineer wants a tautology checker and a counterexample finder for
propositional formulas. This forces the generated function to actually parse an
expression grammar and enumerate truth assignments -- not pattern-match keywords.
Known-answer cases below let us verify the output is *correct*, not merely typed.
"""
from __future__ import annotations

import os
from typing import Optional

from semipy import configure, semiformal

configure(verbose=True, cache_dir=os.environ.get("STRESS_CACHE", "examples/stress/.cache"))


@semiformal
def is_tautology(formula: str) -> bool:
    result = None
    #< intent: Check whether propositional formula is tautological
    #< by: parsing to AST, enumerating assignments, requiring every evaluation true
    #< unless: lexing, parse, or trailing-token error yields false
    #> return True iff the propositional logic {formula} is a tautology -- true under EVERY
    #> truth assignment of its variables. Parse the formula (operators and, or, not, ->,
    #> with parentheses; -> is right-associative and lowest precedence), enumerate all
    #> 2**n assignments of the distinct variables, evaluate, and require it holds in all
    return result


@semiformal
def find_counterexample(formula: str) -> Optional[dict]:
    result = None
    #< intent: Find falsifying assignment for propositional formula
    #< by: parsing formula, enumerating assignments, and evaluating until falsified
    #< unless: invalid syntax or tokens, yields empty assignment
    #> if the propositional {formula} is NOT a tautology, return one assignment (a dict
    #> mapping each variable name to a bool) under which it evaluates False; if it IS a
    #> tautology, return None. Same grammar as a tautology check (and/or/not/->)
    #< yields: result key holds counterexample assignment or null
    return result


# (formula, expected_is_tautology)
CASES = [
    ("A or not A", True),                                   # excluded middle
    ("not (A and B) -> (not A or not B)", True),            # De Morgan
    ("(A -> B) -> (not B -> not A)", True),                 # contrapositive
    ("((A -> B) and A) -> B", True),                        # modus ponens
    ("A or B", False),                                      # falsified by A=F,B=F
    ("(A -> B) -> (B -> A)", False),                        # converse is not valid
    ("A and (not A or B)", False),
]


if __name__ == "__main__":
    print("=== tautology checker ===")
    ok = 0
    for formula, expected in CASES:
        got = is_tautology(formula)
        verdict = "PASS" if got == expected else "**FAIL**"
        if got == expected:
            ok += 1
        print(f"  [{verdict}] is_tautology({formula!r}) = {got}  (expected {expected})")
    print(f"\n  {ok}/{len(CASES)} correct")

    print("\n=== counterexample finder (only meaningful for non-tautologies) ===")
    for formula, expected in CASES:
        if expected:
            continue
        cx = find_counterexample(formula)
        print(f"  find_counterexample({formula!r}) = {cx}")
