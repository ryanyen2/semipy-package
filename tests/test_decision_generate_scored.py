"""generate_scored's logprob extraction (mocked pydantic_ai response, no live network call)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.agents.generator import _extract_mean_logprob


# _extract_mean_logprob matches on type(part).__name__ == "TextPart" (it can't
# import pydantic_ai's real TextPart without a live model), so these fakes must
# be named exactly that to exercise the real branch.
@dataclass
class TextPart:
    provider_details: Optional[dict] = None


@dataclass
class ToolCallPart:
    pass


@dataclass
class _FakeResponse:
    parts: list = field(default_factory=list)


@dataclass
class _FakeRunResult:
    response: Any


def test_extracts_length_normalized_mean_logprob_from_text_part():
    part = TextPart(provider_details={
        "logprobs": [{"logprob": -0.1}, {"logprob": -0.3}, {"logprob": -0.2}],
    })
    run_result = _FakeRunResult(response=_FakeResponse(parts=[part]))
    score = _extract_mean_logprob(run_result)
    assert math.isclose(score, (-0.1 + -0.3 + -0.2) / 3)


def test_returns_none_when_no_text_part_present():
    run_result = _FakeRunResult(response=_FakeResponse(parts=[ToolCallPart()]))
    assert _extract_mean_logprob(run_result) is None


def test_returns_none_when_logprobs_missing_from_provider_details():
    part = TextPart(provider_details={})
    run_result = _FakeRunResult(response=_FakeResponse(parts=[part]))
    assert _extract_mean_logprob(run_result) is None


def test_returns_none_when_provider_details_missing_entirely():
    part = TextPart(provider_details=None)
    run_result = _FakeRunResult(response=_FakeResponse(parts=[part]))
    assert _extract_mean_logprob(run_result) is None


def test_returns_none_when_response_access_raises():
    class _ExplodingRunResult:
        @property
        def response(self):
            raise RuntimeError("no response yet")

    assert _extract_mean_logprob(_ExplodingRunResult()) is None


def test_uses_last_text_part_when_several_present():
    early = TextPart(provider_details={"logprobs": [{"logprob": -9.0}]})
    last = TextPart(provider_details={"logprobs": [{"logprob": -0.5}, {"logprob": -0.5}]})
    run_result = _FakeRunResult(response=_FakeResponse(parts=[early, last]))
    assert _extract_mean_logprob(run_result) == -0.5
