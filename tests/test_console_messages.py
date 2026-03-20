"""Unit tests for human-readable console formatters (no LLM)."""
from __future__ import annotations

import pytest

from semipy.agents.config import SemiConfig, effective_stream_display_mode
from semipy.agents.console_io import pipeline_generate_status, pipeline_resolution_message
from semipy.agents.console_messages import (
    format_tool_call_line,
    format_tool_result_line,
    tail_lines,
)
from semipy.models import GistRunResult, OutputValidationResult
from semipy.types import Decision, SemiCallError


def test_format_build_and_run_gist_call_no_source_dump() -> None:
    src = "def __slot_1__(self, n):\n    return n\n"
    line = format_tool_call_line(
        "build_and_run_gist",
        {"generated_function_source": src},
        debug=False,
    )
    assert "def __slot" not in line
    assert "Test generated" in line
    assert "__slot_1__" in line


def test_format_build_and_run_gist_result_human() -> None:
    out, ok = format_tool_result_line(
        "build_and_run_gist",
        GistRunResult(success=True, result_repr="{'a': 1}"),
        debug=False,
    )
    assert ok
    assert "success=True" not in out
    assert "finished" in out.lower()


def test_format_validate_output_result() -> None:
    out, ok = format_tool_result_line(
        "validate_output",
        OutputValidationResult(
            valid=True,
            message="Cannot validate type dict; assuming valid.",
            expected_type="dict",
            actual_type=None,
        ),
        debug=False,
    )
    assert ok
    assert "ok" in out.lower()


def test_tail_lines() -> None:
    text = "a\nb\nc\nd\ne"
    assert tail_lines(text, 2) == "d\ne"
    assert tail_lines(text, 10) == text


def test_semi_call_error_unhashable_dict_hint() -> None:
    err = SemiCallError("generated function raised", cause=TypeError("unhashable type: 'dict'"))
    text = str(err)
    assert "semi()" in text
    assert "expected_type" in text


def test_pipeline_messages_human() -> None:
    assert "reusable" in pipeline_resolution_message(Decision.GENERATE).lower()
    assert "implementing" in pipeline_generate_status(1, 3, retry=False).lower()
    assert "adjusting" in pipeline_generate_status(2, 3, retry=True).lower()


@pytest.mark.parametrize(
    ("stream", "verbosity", "expected"),
    [
        (False, "normal", "none"),
        (True, "quiet", "none"),
        (True, "debug", "full"),
        (True, "normal", "peek"),
    ],
)
def test_effective_stream_display_mode(stream: bool, verbosity: str, expected: str) -> None:
    c = SemiConfig()
    c.stream = stream
    c.console_verbosity = verbosity
    assert effective_stream_display_mode(c) == expected
