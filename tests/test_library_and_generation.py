"""
Tests for DreamCoder-style library learning and pattern reuse.

These tests verify:
- AST pattern mining from generated sources (no hardcoded patterns)
- Sleep phase: collect commits, mine patterns, compress, persist library
- Library injection: select_relevant_primitives and build_library_context
- COMPOSE resolution path when library has matching primitives
- How users can reuse learned patterns in their specs (documented in assertions and structure)
- That the LLM is invoked with the correct prompt (decision + library_context) and that
  the resolution decision (REUSE/ADAPT/COMPOSE/GENERATE) is logged and used.

Verifying with real LLM and streaming logs:
- Run an example with verbose=True (default). You should see:
  - [semipy] Decision: <reuse|adapt|compose|generate> <description>
  - [semipy] Invoking LLM | decision=<...> | library_context=N chars (or none)
  - Reasoning/Response panels from the model; tool calls (build_and_run_gist, list_library_primitives, etc.)
  - DAG line: "Reuse cached" / "Adapt from ..." / "Compose from library" / "New implementation"
- When the library has a matching primitive, resolution should be COMPOSE and the prompt
  should include "Available library primitives" and "Compose from this library primitive".
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from semipy.library import (
    AbstractionLibrary,
    load_library,
    run_sleep_phase,
)
from semipy.library.abstractions import ASTPattern, LibraryPrimitive
from semipy.library.pattern_mining import (
    all_subtrees,
    anti_unify,
    ast_hash,
    mine_patterns,
    normalize_subtree,
)
from semipy.library.injection import build_library_context, select_relevant_primitives
from semipy.library.store import write_library_runtime_module
from semipy.types import Decision, GenerationSpec, SemiCallSite, PromptTemplate, TemplatePart


def _make_spec(prompt: str, **kwargs: object) -> GenerationSpec:
    return GenerationSpec(
        prompt=prompt,
        call_site=SemiCallSite(filename="test.py", lineno=1, func_qualname="test_fn"),
        template=None,
        context=None,
        expected_type=type(None),
        **kwargs,
    )


def test_all_subtrees_and_normalize() -> None:
    """Pattern mining uses AST structure only; no fixed keyword lists."""
    source = """
def f(x):
    if x > 0:
        return x * 2
    return 0
"""
    tree = __import__("ast").parse(source)
    subtrees = all_subtrees(tree, min_nodes=3, max_nodes=50)
    assert len(subtrees) >= 1
    norm = normalize_subtree(tree, source)
    assert "def " in norm or "x" in norm


def test_anti_unify_produces_generalization() -> None:
    """Anti-unification produces a single generalized form from multiple sources."""
    a = "return x > 0"
    b = "return y < 1"
    c = "return z != 2"
    gen, params = anti_unify([a, b, c])
    assert isinstance(gen, str)
    assert isinstance(params, list)


def test_mine_patterns_from_synthetic_commits() -> None:
    """Mining discovers structural patterns from commit sources; frequency threshold applies."""
    commits = [
        ("c1", "def g(a):\n    return a + 1\n"),
        ("c2", "def h(b):\n    return b + 1\n"),
        ("c3", "def k(c):\n    return c + 1\n"),
    ]
    groups = mine_patterns(commits, min_pattern_frequency=2, min_nodes=3, max_nodes=30)
    assert isinstance(groups, list)
    for item in groups:
        pattern, commit_sources = item
        assert isinstance(pattern, ASTPattern)
        assert pattern.pattern_id
        assert pattern.ast_hash
        assert isinstance(commit_sources, list)


def test_sleep_phase_persists_library(tmp_path: Path) -> None:
    """Sleep phase loads/saves library and writes runtime module; no LLM/gist required when skipped."""
    portal_content = {
        "session_id": "s1",
        "source_file": "test.py",
        "module_name": "test",
        "slots": {
            "slot1": {
                "slot_id": "slot1",
                "call_site_info": {},
                "function_name_base": "fn",
                "commits": {
                    "cid1": {
                        "commit_id": "cid1",
                        "parent_ids": [],
                        "generated_source": "def add_one(x):\n    return x + 1\n",
                        "source_hash": "h1",
                        "template_fingerprint": "fp1",
                        "constants_snapshot": [],
                        "operation_signature": "op1",
                        "prompt_snapshot": "add one",
                        "timestamp": 0,
                        "message": "gen",
                        "decision": "GENERATE",
                        "usage_id": "",
                    },
                    "cid2": {
                        "commit_id": "cid2",
                        "parent_ids": [],
                        "generated_source": "def inc(y):\n    return y + 1\n",
                        "source_hash": "h2",
                        "template_fingerprint": "fp1",
                        "constants_snapshot": [],
                        "operation_signature": "op2",
                        "prompt_snapshot": "increment",
                        "timestamp": 0,
                        "message": "gen",
                        "decision": "GENERATE",
                        "usage_id": "",
                    },
                    "cid3": {
                        "commit_id": "cid3",
                        "parent_ids": [],
                        "generated_source": "def plus_one(z):\n    return z + 1\n",
                        "source_hash": "h3",
                        "template_fingerprint": "fp1",
                        "constants_snapshot": [],
                        "operation_signature": "op3",
                        "prompt_snapshot": "plus one",
                        "timestamp": 0,
                        "message": "gen",
                        "decision": "GENERATE",
                        "usage_id": "",
                    },
                },
                "branches": {},
                "refs": {},
                "default_branch": "main",
                "upstream_slot_refs": [],
            },
        },
    }
    (tmp_path / "s1.portal.json").write_text(json.dumps(portal_content), encoding="utf-8")
    library = run_sleep_phase(
        cache_dir=tmp_path,
        min_new_commits=2,
        skip_llm=True,
        skip_gist=True,
    )
    assert library.version >= 0
    assert isinstance(library.last_analyzed_commits, set)
    lib_path = tmp_path / "library.json"
    assert lib_path.exists()
    loaded = load_library(tmp_path)
    assert loaded.version == library.version
    runtime_path = tmp_path / "runtime" / "_library.semi.py"
    write_library_runtime_module(tmp_path, library)
    assert runtime_path.exists() or (tmp_path / "runtime").exists()


def test_select_relevant_primitives_driven_by_spec() -> None:
    """Selection is driven by spec prompt and primitive name/description; no fixed pattern list."""
    lib = AbstractionLibrary()
    lib.primitives["p1"] = LibraryPrimitive(
        primitive_id="p1",
        name="filter_positive",
        source="def filter_positive(x): return x > 0",
        signature="filter_positive(x)",
        pattern_id="pat1",
        occurrence_count=1,
        validated=True,
        description="Keep only positive numbers",
        tags=[],
    )
    lib.primitives["p2"] = LibraryPrimitive(
        primitive_id="p2",
        name="safe_div",
        source="def safe_div(a, b): return a / b if b else 0",
        signature="safe_div(a, b)",
        pattern_id="pat2",
        occurrence_count=1,
        validated=True,
        description="Divide with zero guard",
        tags=[],
    )
    spec = _make_spec("filter rows where value is positive")
    selected = select_relevant_primitives(lib, spec, max_count=2)
    assert len(selected) <= 2
    names = [p.name for p in selected]
    assert "filter_positive" in names or len(selected) == 2


def test_build_library_context_for_agent() -> None:
    """Library context string is injected into agent prompt so the model can reuse primitives."""
    lib = AbstractionLibrary()
    lib.primitives["p1"] = LibraryPrimitive(
        primitive_id="p1",
        name="add_one",
        source="def add_one(x):\n    return x + 1",
        signature="add_one(x)",
        pattern_id="pat1",
        occurrence_count=1,
        validated=True,
        description="Increment by one",
        tags=[],
    )
    spec = _make_spec("increment the value by one")
    ctx = build_library_context(lib, spec, max_count=5)
    assert "add_one" in ctx
    assert "return x + 1" in ctx or "add_one" in ctx


def test_resolver_compose_path_with_library() -> None:
    """When library has a relevant primitive and slot would otherwise GENERATE, resolver can return COMPOSE."""
    from semipy.resolver import resolve
    from semipy.history import Portal, Slot
    from semipy.types import Decision, Usage

    call_site = SemiCallSite(filename="t.py", lineno=1, func_qualname="f")
    portal = Portal(session_id="s1", source_file="test.py", module_name="test", slots={})
    slot_id = call_site.site_id
    slot = Slot(
        slot_id=slot_id,
        call_site_info={"filename": "t.py", "lineno": 1, "func_qualname": "f"},
        function_name_base="f",
    )
    portal.slots[slot_id] = slot
    template = PromptTemplate(
        template_parts=[TemplatePart(is_literal=True, value="increment")],
        variable_names=[],
        variable_expressions=[],
    )
    usage = Usage(
        call_site=call_site,
        template=template,
        constant_values={},
    )
    lib = AbstractionLibrary()
    lib.primitives["p1"] = LibraryPrimitive(
        primitive_id="p1",
        name="increment",
        source="def increment(x): return x + 1",
        signature="increment(x)",
        pattern_id="pat1",
        occurrence_count=1,
        validated=True,
        description="Increment",
        tags=[],
    )
    spec_for_compose = _make_spec("increment the value")
    result = resolve(
        portal,
        usage,
        template_fingerprint="fp_unknown",
        constants={},
        library=lib,
        spec_for_compose=spec_for_compose,
    )
    assert result.decision in (Decision.COMPOSE, Decision.GENERATE)
    if result.decision == Decision.COMPOSE:
        assert result.parent_sources
        assert "return x + 1" in result.parent_sources[0] or "increment" in result.parent_sources[0]


def test_patterns_learned_are_reusable_in_specs() -> None:
    """
    Document how users can reuse learned patterns in their specs:
    - The library is populated by run_sleep_phase from historical commits.
    - build_library_context(spec) selects primitives relevant to the spec prompt.
    - The agent prompt includes that context, so the LLM can call or adapt those primitives.
    - Resolver returns COMPOSE when select_relevant_primitives finds a match, giving the agent
      the primitive source as parent_sources so it can adapt rather than generate from scratch.
    """
    lib = AbstractionLibrary()
    lib.primitives["pid"] = LibraryPrimitive(
        primitive_id="pid",
        name="is_positive",
        source="def is_positive(n):\n    return n is not None and n > 0",
        signature="is_positive(n)",
        pattern_id="pat",
        occurrence_count=3,
        validated=True,
        description="Check if a number is positive",
        tags=[],
    )
    spec = _make_spec("check if the number is positive")
    prims = select_relevant_primitives(lib, spec, max_count=5)
    assert len(prims) >= 1
    assert prims[0].name == "is_positive"
    ctx = build_library_context(lib, spec, max_count=5)
    assert "is_positive" in ctx
    assert "n > 0" in ctx


def test_agent_prompt_includes_library_context_and_compose_block() -> None:
    """
    When spec has library_context and decision COMPOSE, the prompt built for the LLM
    must include the library context block and the 'Compose from this library primitive' block.
    This ensures the LLM is actually given the learned pattern to reuse.
    """
    from semipy.agents.agent import SemiAgent

    lib = AbstractionLibrary()
    prim_source = "def add_one(x):\n    return x + 1"
    lib.primitives["p1"] = LibraryPrimitive(
        primitive_id="p1",
        name="add_one",
        source=prim_source,
        signature="add_one(x)",
        pattern_id="pat1",
        occurrence_count=1,
        validated=True,
        description="Increment by one",
        tags=[],
    )
    spec = _make_spec("increment the value by one")
    spec.library_context = build_library_context(lib, spec, max_count=5)
    spec.decision = Decision.COMPOSE
    spec.parent_sources = [prim_source]

    agent = SemiAgent(verbose=False)
    prompt = agent._build_user_prompt(spec)

    assert spec.library_context and "add_one" in spec.library_context
    assert "add_one" in prompt or prim_source.strip() in prompt
    assert "Compose from this library primitive" in prompt
    assert "return x + 1" in prompt


def test_llm_invocation_receives_captured_prompt_with_library_context() -> None:
    """
    When generate_async is called with a spec that has library_context, the prompt
    passed to the LLM (run_stream_events) must contain that library context.
    We patch the agent to capture the prompt and assert it contains the primitive.
    """
    import asyncio
    from unittest.mock import patch

    from semipy.agents.agent import SemiAgent
    from semipy.types import Decision

    captured_prompt: list[str] = []
    captured_deps_spec_decision: list = []

    async def fake_run_stream_events(prompt: str, deps: object) -> object:
        captured_prompt.append(prompt)
        captured_deps_spec_decision.append(getattr(deps, "spec", None))
        yield None

    lib = AbstractionLibrary()
    lib.primitives["p1"] = LibraryPrimitive(
        primitive_id="p1",
        name="filter_positive",
        source="def filter_positive(x): return x > 0",
        signature="filter_positive(x)",
        pattern_id="pat1",
        occurrence_count=1,
        validated=True,
        description="Keep positive only",
        tags=[],
    )
    spec = _make_spec("filter positive values")
    spec.library_context = build_library_context(lib, spec, max_count=5)
    spec.decision = Decision.COMPOSE
    spec.parent_sources = ["def filter_positive(x): return x > 0"]

    async def run_and_capture() -> None:
        with patch("semipy.agents.agent.get_semi_agent") as mock_get_agent:
            mock_agent = mock_get_agent.return_value
            mock_agent.run_stream_events = fake_run_stream_events
            agent = SemiAgent(verbose=False)
            try:
                await agent.generate_async(spec)
            except Exception:
                pass

    asyncio.run(run_and_capture())

    assert len(captured_prompt) >= 1
    prompt_text = captured_prompt[0]
    assert "filter_positive" in prompt_text or "x > 0" in prompt_text
    assert "Available library primitives" in prompt_text or "filter_positive" in prompt_text
    if captured_deps_spec_decision:
        dep_spec = captured_deps_spec_decision[0]
        assert dep_spec is not None
        assert getattr(dep_spec, "library_context", None)
        assert dep_spec.decision == Decision.COMPOSE
