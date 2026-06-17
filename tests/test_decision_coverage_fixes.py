"""Coverage-gap fixes surfaced by end-to-end evaluation: F1, F2, F3.

All offline and deterministic -- fake candidate generators, no API key, so the
LLM-labeling layer abstains and we assert on the deterministic structure.
"""
from __future__ import annotations

from semipy.decisions.divergence import observe_effectful, observe_pure
from semipy.decisions.draw import resolve_with_decisions
from semipy.orchestration.roles.decision_classifier import classify_divergence

# ---------------------------------------------------------------------------
# F1: an agreeing initial draw escalates to max before concluding no-fork.
# ---------------------------------------------------------------------------

# A pool where the minority fate ("last word only") is the 4th candidate, so an
# initial draw of 3 sees only the majority and would (pre-fix) conclude no-fork.
_KEEP = "def f(name):\n    parts = name.split()\n    return {'first': parts[0], 'last': ' '.join(parts[1:])}\n"
_LAST = "def f(name):\n    parts = name.split()\n    return {'first': parts[0], 'last': parts[-1]}\n"
_POOL = [_KEEP, _KEEP, _KEEP, _LAST, _KEEP]


def _pool_gen(pool):
    def gen(i):
        return pool[i] if i < len(pool) else pool[-1]

    return gen


def test_f1_escalation_surfaces_minority_fork_absent_from_initial_three():
    sample = [{"name": "Maria de la Cruz"}]
    out = resolve_with_decisions(
        generate_candidate=_pool_gen(_POOL),
        free_variables=["name"],
        sample_rows=sample,
        slot_id="f1",
        initial_candidates=3,
        max_candidates=5,
        use_llm=False,
    )
    # The minority fork is only present at draw index 3 -> requires escalation.
    assert out.diverged
    assert out.divergence.n_candidates == 5


def test_f1_unanimous_pool_still_concludes_no_fork():
    sample = [{"name": "Maria de la Cruz"}]
    out = resolve_with_decisions(
        generate_candidate=_pool_gen([_KEEP] * 5),
        free_variables=["name"],
        sample_rows=sample,
        slot_id="f1b",
        initial_candidates=3,
        max_candidates=5,
        use_llm=False,
    )
    assert not out.diverged
    assert not out.has_decisions


# ---------------------------------------------------------------------------
# F2: a divergence along two axes (output schema + a value) factors into two
# decisions instead of one conflated fork.
# ---------------------------------------------------------------------------

# Same semantic content, three behaviors: (a) keep-rest with long keys, (b)
# last-word with long keys, (c) keep-rest with short keys. Axes: surname span
# (value) and key naming (schema).
_LONG_KEEP = "def f(name):\n    p = name.split()\n    return {'first_name': p[0], 'last_name': ' '.join(p[1:])}\n"
_LONG_LAST = "def f(name):\n    p = name.split()\n    return {'first_name': p[0], 'last_name': p[-1]}\n"
_SHORT_KEEP = "def f(name):\n    p = name.split()\n    return {'first': p[0], 'last': ' '.join(p[1:])}\n"


def test_f2_factors_schema_axis_from_value_axis():
    cands = {"a": _LONG_KEEP, "b": _LONG_KEEP, "c": _LONG_LAST, "d": _SHORT_KEEP}
    div = observe_pure(cands, free_variables=["name"], sample_rows=[{"name": "Maria de la Cruz"}])
    assert div.diverged()
    decisions = classify_divergence(div, germ="output", example_in={"name": "Maria de la Cruz"}, use_llm=False)
    kinds = {d.germ for d in decisions}
    # One decision is the schema axis; another is the value axis within the
    # dominant schema -- not a single 3-way conflated fork.
    assert "output_shape" in kinds
    assert len(decisions) == 2
    shape = next(d for d in decisions if d.germ == "output_shape")
    fates = {b.fate_label for b in shape.branches}
    assert any("first_name" in f for f in fates) and any("first" in f for f in fates)
    # Value decision lives within the dominant (long-key) schema: keep-rest vs last-word.
    value = next(d for d in decisions if d.germ != "output_shape")
    assert len(value.branches) == 2


def test_f2_single_schema_value_fork_stays_one_decision():
    # All share the same key set -> no schema axis -> keep the single decision.
    cands = {"a": _LONG_KEEP, "b": _LONG_KEEP, "c": _LONG_LAST}
    div = observe_pure(cands, free_variables=["name"], sample_rows=[{"name": "Ada B C"}])
    decisions = classify_divergence(div, germ="output", example_in={"name": "Ada B C"}, use_llm=False)
    assert len(decisions) == 1
    assert decisions[0].germ != "output_shape"


# ---------------------------------------------------------------------------
# F3: the two-pass world exposes update-vs-create that an empty world hides.
# ---------------------------------------------------------------------------

# Both candidates read first (so the read effect cannot distinguish them); they
# branch differently on the result. Always-create ignores the lookup; upsert
# updates when a row exists. In an empty world the read returns nothing and BOTH
# create -> identical. Only the seeded "exists" pass splits them.
_ALWAYS_CREATE = """
def save(user, fx):
    fx.read('users', {'id': user.get('id')})
    fx.create('users', user)
"""
_UPSERT = """
def save(user, fx):
    existing = fx.read('users', {'id': user.get('id')})
    if existing:
        fx.update('users', user, {'id': user.get('id')})
    else:
        fx.create('users', user)
"""
_USER = {"user": {"id": 7, "name": "Ada"}}


def test_f3_empty_world_alone_hides_update_vs_create():
    div = observe_effectful({"a": _ALWAYS_CREATE, "b": _UPSERT}, free_variables=["user"],
                            runtime_values=_USER, seed_existing=False)
    # Without the seeded pass, the upsert's read returns nothing -> it creates too,
    # so the two candidates look identical. This is the hidden-fork failure (F3).
    assert not div.diverged()


def test_f3_two_pass_surfaces_update_vs_create():
    div = observe_effectful({"a": _ALWAYS_CREATE, "b": _UPSERT}, free_variables=["user"],
                            runtime_values=_USER, seed_existing=True)
    # With the seeded pass, the upsert updates when the row exists while the
    # always-create candidate still creates a duplicate -> a real fork.
    assert div.diverged()
    assert len(div.clusters) == 2
