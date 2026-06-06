"""U4: coder bridge role-runner projection. Offline (stubbed agent, no LLM)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from semipy.orchestration.artifacts import GenerationResult
from semipy.orchestration.roles import coder
from semipy.types import Decision


def _fake_entry(source):
    return SimpleNamespace(generated_source=source, compiled_fn=None)


def test_project_entry_parses_function_name():
    gr = coder.project_entry(_fake_entry("def to_upper(t):\n    return t.upper()"), decision=Decision.GENERATE)
    assert isinstance(gr, GenerationResult)
    assert gr.generated_source.startswith("def to_upper")
    assert gr.function_name == "to_upper"
    assert gr.decision == "generate"  # Decision enum projected to its value


def test_project_entry_handles_unparseable_source():
    gr = coder.project_entry(_fake_entry("def (:::"))
    assert gr.function_name is None
    assert gr.decision is None


def test_code_uses_injected_agent_and_projects():
    captured = {}

    class StubAgent:
        def generate(self, spec):
            captured["spec"] = spec
            return _fake_entry("def f(x):\n    return x + 1")

    spec = SimpleNamespace(decision=Decision.ADAPT)
    gr = coder.code(spec, agent=StubAgent())
    assert gr.function_name == "f"
    assert gr.decision == "adapt"
    assert captured["spec"] is spec  # the role forwarded the spec to the agent


def test_generation_result_round_trips_json():
    gr = coder.project_entry(_fake_entry("def g():\n    return 1"), decision=Decision.GENERATE)
    reloaded = GenerationResult.model_validate(json.loads(gr.model_dump_json()))
    assert reloaded == gr
