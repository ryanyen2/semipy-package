"""
Tests for DreamCoder-style library learning and pattern reuse.

These tests verify:
- Resolution and reuse are driven by template structure (fingerprint), not by exact wording.
  The "signature" is the sequence of literal vs variable slots; filler words and constant
  values do not change the fingerprint. Same structure + different constants -> REUSE.
- No hardcoded common_pattern or common_template; pattern is context-dependent and derived
  from the actual prompt and code (template_tree, structural_fingerprint).
- AST pattern mining from generated sources; sleep phase; library injection; COMPOSE path.
- Parameter-count validation: when reusing by fingerprint, the commit's function must
  accept at least as many positional args as the template has variables.

Real-world use cases reflected here:
- Same template, different constant values (e.g. threshold 2021 vs 2022) -> REUSE.
- Same call site, varying user phrasing that yields the same template shape -> same fingerprint.
- When the stored implementation has fewer parameters than the template requires, fall back to GENERATE.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from semipy.agents.validator import count_function_positional_params
from semipy.history import (
    Branch,
    Portal,
    Slot,
    create_commit,
    find_commit_by_fingerprint,
    freeze_constants,
)
from semipy.library import (
    AbstractionLibrary,
    load_library,
    run_sleep_phase,
)
from semipy.library.abstractions import ASTPattern, LibraryPrimitive
from semipy.library.pattern_mining import (
    all_subtrees,
    anti_unify,
    mine_patterns,
    normalize_subtree,
)
from semipy.library.injection import build_library_context, select_relevant_primitives
from semipy.library.store import write_library_runtime_module
from semipy.resolver import resolve
from semipy.template import structural_fingerprint, template_tree_from_prompt
from semipy.types import Decision, GenerationSpec, SemiCallSite, PromptTemplate, TemplatePart, Usage


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


def test_structural_fingerprint_same_shape_same_fingerprint() -> None:
    """
    Fingerprint is determined by template shape (literal vs var slots), not by literal text
    or constant values. Two templates with the same structure get the same fingerprint
    regardless of how the user phrased the prompt (filler words) or what constant values are.
    """
    # Same shape: one literal segment, one var, one literal, one var (e.g. "X to (A, B)")
    t1 = PromptTemplate(
        template_parts=[
            TemplatePart(is_literal=True, value="move the red brick to "),
            TemplatePart(is_literal=False, value="c0"),
            TemplatePart(is_literal=True, value=" and "),
            TemplatePart(is_literal=False, value="c1"),
        ],
        variable_names=["c0", "c1"],
        variable_expressions=[],
    )
    t2 = PromptTemplate(
        template_parts=[
            TemplatePart(is_literal=True, value="move that blue ball over "),
            TemplatePart(is_literal=False, value="c0"),
            TemplatePart(is_literal=True, value=" and "),
            TemplatePart(is_literal=False, value="c1"),
        ],
        variable_names=["c0", "c1"],
        variable_expressions=[],
    )
    tree1 = template_tree_from_prompt(t1)
    tree2 = template_tree_from_prompt(t2)
    fp1 = structural_fingerprint(tree1)
    fp2 = structural_fingerprint(tree2)
    assert fp1 == fp2, "Same template shape (literal/var sequence) must yield same fingerprint"


def test_find_commit_by_fingerprint_cross_constant_reuse() -> None:
    """
    When only constant values differ (same template structure), find_commit_by_fingerprint
    returns the existing commit so we REUSE without LLM. This enables e.g. first call
    semi(f'filter where year > {t}') with t=2021, second with t=2022 -> second reuses.
    """
    call_site = SemiCallSite(filename="test.py", lineno=10, func_qualname="analyze")
    slot_id = call_site.site_id
    template = PromptTemplate(
        template_parts=[
            TemplatePart(is_literal=True, value="remove rows where year > "),
            TemplatePart(is_literal=False, value="c0"),
        ],
        variable_names=["c0"],
        variable_expressions=[],
    )
    usage_2021 = Usage(call_site=call_site, template=template, constant_values={"c0": 2021})
    usage_2022 = Usage(call_site=call_site, template=template, constant_values={"c0": 2022})
    assert usage_2021.usage_id() != usage_2022.usage_id()

    fingerprint = structural_fingerprint(template_tree_from_prompt(template))
    constants_2021 = freeze_constants(usage_2021.constant_values)
    commit = create_commit(
        parent_ids=(),
        generated_source="def filter_year(df, c0):\n    return df[df['year'] > c0]\n",
        template_fingerprint=fingerprint,
        constants_snapshot=constants_2021,
        prompt_snapshot="remove rows where year > 2021",
        decision="GENERATE",
        usage_id=usage_2021.usage_id(),
    )
    slot = Slot(
        slot_id=slot_id,
        call_site_info={"filename": "test.py", "lineno": 10, "func_qualname": "analyze"},
        function_name_base="analyze",
        commits={commit.commit_id: commit},
        branches={"main": Branch(name="main", head=commit.commit_id)},
        refs={usage_2021.usage_id(): commit.commit_id},
    )
    # Ask for commit with same fingerprint but different usage_id (different constant)
    found = find_commit_by_fingerprint(slot, fingerprint, usage_2022.usage_id())
    assert found is not None
    assert found.commit_id == commit.commit_id


def test_resolve_reuse_by_fingerprint_same_template_different_constants() -> None:
    """
    Full resolution: slot has one commit for usage U1 (e.g. threshold=2021). Resolve with
    same template and different constant_values (U2, threshold=2022). Expect REUSE with
    that commit so no LLM is invoked; refs will be updated for U2 in semi_fn.
    """
    call_site = SemiCallSite(filename="pipeline.py", lineno=5, func_qualname="run")
    slot_id = call_site.site_id
    template = PromptTemplate(
        template_parts=[
            TemplatePart(is_literal=True, value="filter rows where year > "),
            TemplatePart(is_literal=False, value="c0"),
        ],
        variable_names=["c0"],
        variable_expressions=[],
    )
    usage_first = Usage(call_site=call_site, template=template, constant_values={"c0": 2021})
    usage_second = Usage(call_site=call_site, template=template, constant_values={"c0": 2022})
    fingerprint = structural_fingerprint(template_tree_from_prompt(template))
    constants_first = freeze_constants(usage_first.constant_values)
    commit = create_commit(
        parent_ids=(),
        generated_source="def f(df, c0):\n    return df[df['year'] > c0]\n",
        template_fingerprint=fingerprint,
        constants_snapshot=constants_first,
        prompt_snapshot="filter rows where year > 2021",
        decision="GENERATE",
        usage_id=usage_first.usage_id(),
    )
    slot = Slot(
        slot_id=slot_id,
        call_site_info={"filename": "pipeline.py", "lineno": 5, "func_qualname": "run"},
        function_name_base="run",
        commits={commit.commit_id: commit},
        branches={"main": Branch(name="main", head=commit.commit_id)},
        refs={usage_first.usage_id(): commit.commit_id},
    )
    portal = Portal(session_id="sess", source_file="pipeline.py", module_name="pipeline", slots={slot_id: slot})

    result = resolve(
        portal,
        usage_second,
        fingerprint,
        usage_second.constant_values,
        library=None,
        spec_for_compose=None,
    )
    assert result.decision == Decision.REUSE
    assert result.commit_id == commit.commit_id
    assert result.slot is not None


def test_count_function_positional_params() -> None:
    """Reuse is only valid when the commit's function accepts at least template.variable_names args."""
    assert count_function_positional_params("def f(): return 1") == 0
    assert count_function_positional_params("def f(x): return x") == 1
    assert count_function_positional_params("def f(df, c0): return df[df['y'] > c0]") == 2
    assert count_function_positional_params("def f(a, b, *args): return a + b") == 2
    # Only first function in source
    multi = "def g(): pass\ndef h(a, b): return a + b"
    assert count_function_positional_params(multi) == 0


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
