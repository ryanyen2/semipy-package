"""U6: alignment verifier voting + abstain behavior. Pure logic runs offline.

The LLM judge itself needs a key (verified live, not here). These tests pin the
vote-aggregation policy and the best-effort abstain paths that keep the suite
offline.
"""
from __future__ import annotations

import pytest

from semipy.agents.config import SemiConfig
from semipy.orchestration.roles.verifier import (
    AlignmentVerdict,
    aggregate_votes,
    verify_alignment,
)


def _aligned(reason="ok"):
    return AlignmentVerdict(aligned=True, reasoning=reason)


def _misaligned(reason="bad", samples=None):
    return AlignmentVerdict(aligned=False, reasoning=reason, failing_samples=samples or [])


# --- aggregate_votes (pure) ----------------------------------------------

def test_majority_aligned_passes():
    v = aggregate_votes([_aligned(), _aligned(), _misaligned()])
    assert v.passed is True
    assert v.alignment_verdict == "aligned"
    assert v.vote_count == 3


def test_majority_misaligned_fails():
    v = aggregate_votes([_misaligned(), _misaligned(), _aligned()])
    assert v.passed is False
    assert v.alignment_verdict == "misaligned"
    assert v.vote_count == 3


def test_tie_fails_biasing_toward_adapt():
    v = aggregate_votes([_aligned(), _misaligned()])
    assert v.passed is False  # strict majority required; tie -> fail


def test_no_votes_abstains():
    v = aggregate_votes([None, None])
    assert v.passed is True  # abstain: alignment layer does not block
    assert v.alignment_verdict is None
    assert v.vote_count == 0


def test_shrunken_electorate_abstains_no_quorum():
    # 1 survivor of 3 requested judges must NOT decide the gate (denominator
    # shrinkage): without a quorum, abstain rather than block on one vote.
    v = aggregate_votes([_misaligned(), None, None])
    assert v.passed is True
    assert v.alignment_verdict is None
    assert v.vote_count == 1


def test_quorum_met_with_majority_responding():
    # 2 survivors of 3 reach quorum; the majority among them decides.
    v = aggregate_votes([_misaligned(), _misaligned(), None])
    assert v.passed is False and v.alignment_verdict == "misaligned"


def test_single_requested_sample_decides_on_one_vote():
    # samples=1: one vote is a full electorate, so it decides (behavior unchanged).
    assert aggregate_votes([_aligned()]).passed is True
    assert aggregate_votes([_misaligned()]).passed is False


def test_failing_samples_and_reasons_collected_from_dissent():
    v = aggregate_votes([
        _misaligned("wrong on empty", samples=[{"input": "", "output": ""}]),
        _misaligned("also wrong", samples=[{"input": "x", "output": "x"}]),
        _aligned(),
    ])
    assert v.passed is False
    assert {"input": "", "output": ""} in v.failing_samples
    assert "wrong on empty" in v.reasons and "also wrong" in v.reasons
    # An aligned voter contributes no failing samples or reasons.
    assert len(v.failing_samples) == 2 and len(v.reasons) == 2


# --- verify_alignment best-effort paths ----------------------------------

def test_verify_alignment_abstains_without_io_pairs():
    v = verify_alignment(spec_text="uppercase it", implementation_source="def f(): ...", io_pairs=[])
    assert v.passed is True and v.alignment_verdict is None and v.vote_count == 0


def test_verify_alignment_abstains_without_api_key(monkeypatch):
    import semipy.agents.config as cfg_mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    original = cfg_mod._config
    fresh = SemiConfig()
    fresh.openai_api_key = None
    cfg_mod._config = fresh
    try:
        v = verify_alignment(
            spec_text="return text uppercased",
            implementation_source="def f(t): return t.upper()",
            io_pairs=[{"input": "hi", "output": "HI"}],
            samples=3,
        )
        # No key -> judges abstain -> aggregate abstains (does not block).
        assert v.passed is True and v.alignment_verdict is None
    finally:
        cfg_mod._config = original
