"""U5: code-explorer facts + parallel read-only gather. Offline (no LLM)."""
from __future__ import annotations

import time
from types import SimpleNamespace

from semipy.orchestration.artifacts import ExplorationResult
from semipy.orchestration.parallel import gather_readonly
from semipy.orchestration.roles import explorer


def _slot_spec():
    return SimpleNamespace(
        enclosing_function_source=(
            "def classify(line):\n"
            "    cleaned = line.strip()\n"
            "    level = extract_level(cleaned)\n"
            "    return label_for(level)\n"
        ),
        output_names=["label"],
        expected_type=str,
    )


# --- explorer (deterministic) --------------------------------------------

def test_explore_gathers_dependency_signatures():
    res = explorer.explore(_slot_spec(), {"line": "[error] boom"})
    assert isinstance(res, ExplorationResult)
    assert "extract_level" in res.dependency_signatures
    assert "label_for" in res.dependency_signatures
    assert any("label" in r for r in res.upstream_requirements)
    assert res.data_profile  # profiled the runtime input


def test_explore_degrades_on_unparseable_source():
    bad = SimpleNamespace(enclosing_function_source="def (:::", output_names=[], expected_type=None)
    res = explorer.explore(bad, None)
    assert res.dependency_signatures == [] and res.data_profile == ""


# --- gather_readonly (concurrency) ---------------------------------------

def test_gather_runs_thunks_concurrently():
    def slow(tag):
        def _t():
            time.sleep(0.3)
            return tag
        return _t

    start = time.monotonic()
    results = gather_readonly([slow("a"), slow("b"), slow("c")])
    elapsed = time.monotonic() - start

    assert results == ["a", "b", "c"]
    # Concurrent: well under the 0.9s serial sum (generous bound for CI jitter).
    assert elapsed < 0.7


def test_gather_isolates_failures_to_none():
    def ok():
        return "ok"

    def boom():
        raise RuntimeError("explorer blew up")

    results = gather_readonly([ok, boom, ok])
    assert results == ["ok", None, "ok"]  # one failure does not abort the others


def test_gather_empty():
    assert gather_readonly([]) == []
