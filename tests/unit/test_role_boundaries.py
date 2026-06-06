"""U10: the offline-default contract for LLM-backed roles.

Every LLM-backed orchestration role routes model construction through
``make_responses_model``, which is the single offline gate: with no API key it
returns ``(None, None)`` and each role degrades to a deterministic default. These
tests pin that contract so the suite stays offline and the degradations stay safe.
"""
from __future__ import annotations

import pytest

from semipy.agents.config import SemiConfig


@pytest.fixture
def no_key(monkeypatch):
    import semipy.agents.config as cfg_mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    original = cfg_mod._config
    fresh = SemiConfig()
    fresh.openai_api_key = None
    cfg_mod._config = fresh
    try:
        yield fresh
    finally:
        cfg_mod._config = original


def test_make_responses_model_is_the_single_offline_gate(no_key):
    from semipy.orchestration.runtime import make_responses_model

    for role in ("coder", "verifier", "surfacer", "explorer", None):
        model, settings = make_responses_model(role)
        assert model is None and settings is None


def test_verifier_abstains_without_key(no_key):
    from semipy.orchestration.roles.verifier import verify_alignment

    v = verify_alignment(
        spec_text="uppercase the text",
        implementation_source="def f(t): return t.upper()",
        io_pairs=[{"input": "hi", "output": "HI"}],
        samples=3,
    )
    assert v.passed is True and v.alignment_verdict is None


def test_reuse_judge_defaults_to_reuse_without_votes():
    # The aggregator's no-vote path (all judges abstained) -> reuse.
    from semipy.agents.decision import aggregate_semantic_votes

    assert aggregate_semantic_votes([None, None, None]).decision == "reuse"


def test_explorer_is_deterministic_and_keyless(no_key):
    # Read-only role: produces facts with no key, no network.
    from types import SimpleNamespace

    from semipy.orchestration.roles import explorer

    res = explorer.explore(
        SimpleNamespace(
            enclosing_function_source="def f(x):\n    return g(x)\n",
            output_names=["y"],
            expected_type=str,
        ),
        {"x": "data"},
    )
    assert "g" in res.dependency_signatures


def test_coder_does_not_silently_abstain_without_key(no_key):
    # A GENERATE slot has nothing to fall back to: the coder must surface the
    # missing-key error rather than return an empty result.
    from types import SimpleNamespace

    from semipy.orchestration.roles import coder

    with pytest.raises(Exception):
        coder.code(SimpleNamespace(decision=None, max_retries=0))
