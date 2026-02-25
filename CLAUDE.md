# CLAUDE.md

This file provides guidance when working with code in this repository.

## What is semipy

A runtime semiformal system. The `@semiformal` decorator and `semi()` let users express underspecified logic (natural-language conditions, extraction rules). On first invocation, an LLM generates a Python function via an **agentic pipeline** (OpenRouter + pydantic_ai with tools); the function is validated and cached. Subsequent calls reuse the cached implementation with no LLM invocation.

## Commands

```bash
uv sync
source .venv/bin/activate
uv run python examples/use_csv_kit.py
uv run python examples/use_weather_kit.py
pytest  # use: uv sync --extra dev first
```

## Environment

- **OPENROUTER_API_KEY** in `.env` or environment (required for generation).
- Optional: **E2B_API_KEY** for sandboxed gist execution (otherwise subprocess fallback).
- Python >= 3.10. Uses `uv` for dependency and environment management.

## Package layout

- **Root** (`semipy/`): `types.py`, `models.py`, `decorator.py`, `template.py`, `semi_fn.py`, `resolver.py`, `store.py`. Entry point and core types; no subpackage imports for these.
- **agents/** (`semipy/agents/`): Agentic pipeline: config, agent, generator, gist, executor, validator, profiler, tools, console_io, compiler. All LLM, tools, validation, and UX live here.
- **history/** (`semipy/history/`): Version control (Merkle DAG): `version_control.py` with Commit, Branch, Slot, Portal; create_commit, add_commit_to_slot, walk_history, find_branch_by_fingerprint, etc.
- **reactivity/** (`semipy/reactivity/`): Data flow and reactive invalidation: `reactive.py` (DependencyGraph, SlotRef, add_dependency, is_stale, mark_downstream_stale, persistence); `flow.py` (DataFlow, create_flow, extract_flow, profile_output).

## Architecture

### Core data flow

```
@semiformal decorated function
  -> semi(f"semantic prompt with {variables}")
    -> _identify_call_site() [file:line:func -> site_id]
    -> load_portal() (cached in-memory per session_id)
    -> resolver.resolve(portal, usage, fingerprint, constants)
      -> REUSE: load function from dispatch module, optionally add ref, return compiled function
      -> ADAPT / GENERATE: build GenerationSpec (with user_source_code, enclosing_function_source)
        -> SemiAgent.generate(spec)  [agents.agent]
          -> pydantic_ai Agent (OpenRouter) with tools [agents.generator]
          -> run_stream_events(prompt, deps) -> streaming (reasoning, tool calls, response)
          -> tools: profile_data_and_flow, read_upstream_context, read_file_context, build_and_run_gist, validate_output
          -> extract generated_source from deps or response
        -> validate() [agents.validator] -> create_commit [history] -> save_portal -> write_dispatch_module()
        -> load and execute new function
    -> Execute function with runtime arguments
```

### Module roles

| Location | Module | Role |
|----------|--------|------|
| root | `types.py` | Core dataclasses: SemiCallSite, PromptTemplate, CacheEntry, GenerationSpec, Decision, Usage, ValidationResult, etc. |
| root | `models.py` | Pydantic models for agent tool I/O: ProfileDataResult, GistRunResult, OutputValidationResult, SemiAgentDeps, etc. |
| root | `decorator.py` | @semiformal; source inspection and context injection via contextvars |
| root | `template.py` | AST-based f-string decomposition; structural fingerprint; loop-variant vs constant variables |
| root | `semi_fn.py` | semi() entry point; call-site identification, portal/resolver/store flow, agent invocation |
| root | `resolver.py` | resolve(portal, usage, fingerprint, constants) -> REUSE / ADAPT / GENERATE (ResolutionResult) |
| root | `store.py` | load_portal, save_portal, write_dispatch_module, load_function_from_dispatch |
| agents | `config.py` | SemiConfig; get_config() / configure() |
| agents | `compiler.py` | _compile_source() to turn generated Python source into a callable |
| agents | `agent.py` | SemiAgent: generate(spec); prompt building; stream event handling; validate and retry with feedback |
| agents | `generator.py` | pydantic_ai Agent (OpenRouter) + tools; get_semi_agent(); SYSTEM_PROMPT |
| agents | `gist.py` | GistBuilder(spec).build(generated_source) -> Gist; minimal runnable script for sandbox validation |
| agents | `executor.py` | GistExecutor: execute_sync/execute_async (E2B or subprocess); ExecutionResult |
| agents | `validator.py` | validate() (AST, type, execution); validate_with_gist(); _extract_function_source |
| agents | `console_io.py` | Rich-based console output; DAG logs; streaming (print_reasoning_block, print_tool_call, etc.) |
| agents | `tools.py` | Tool refs in prompts ({TOOL(...)}); parse_tool_refs, register_tool |
| agents | `profiler.py` | profile_value() for data profiling in agent context |
| history | `version_control.py` | Commit, Branch, Slot, Portal; create_commit, add_commit_to_slot, walk_history, find_branch_by_fingerprint |
| reactivity | `reactive.py` | DependencyGraph, SlotRef; add_dependency, is_stale, mark_downstream_stale; load/save_dependency_graph |
| reactivity | `flow.py` | DataFlow, create_flow, extract_flow, profile_output; FLOW_ATTR |

### Key abstractions

- **SemiCallSite**: filename, lineno, func_qualname -> site_id (SHA256).
- **PromptTemplate**: decomposed f-string with template_parts and variable_names.
- **Usage**: concrete semi() invocation; usage_id() = hash of site_id + template + constants (+ expected_type when set).
- **Decision**: REUSE, ADAPT, FORK, GENERATE, MERGE.
- **Commit**: one generated implementation (commit_id, parent_ids, generated_source, template_fingerprint, constants_snapshot, operation_signature, message, decision).
- **Slot**: per-call-site DAG (commits, branches, refs: usage_id -> commit_id); function_name_base for dispatch.
- **Portal**: per-session container (session_id, source_file, module_name, slots).
- **GenerationSpec**: prompt, call_site, template, context, expected_type, sample_input, constant_values, variable_values, user_source_code, enclosing_function_source, parent_sources, decision, ...
- **SemiAgentDeps**: spec, gist_builder, executor, generated_source, reasoning_blocks, tool_calls_log (mutable state for pydantic_ai agent run).

### Cache model (DAG versioning)

- One portal per session: `.semiformal/{session_id}.portal.json` with full DAG.
- Dispatch module: `.semiformal/runtime/{module_name}.semi.py`; **one implementation per slot** (active commit = head of default_branch or most recent ref'd commit); all usage_ids in the slot map to that one function name.
- Resolution: refs[usage_id] -> REUSE; else operation_signature match -> REUSE (and add ref); else branch with same fingerprint -> ADAPT; else GENERATE.

### Public API

Exports from `semipy/__init__.py`: `semiformal`, `semi`, `SemiConfig`, `configure`, `get_config`, `Decision`, `SemiCallError`, `SemiGenerationError`, `register_tool`, `parse_tool_refs`, `GistBuilder`, `Gist`, `GistExecutor`, `ExecutionResult`, `SemiAgentDeps`, `ProfileDataResult`, `GistRunResult`, `OutputValidationResult`, `DependencyGraph`, `SlotRef`, `DataFlow`.

## Code conventions

- Use `from __future__ import annotations` and type hints in all modules.
- Use `pathlib.Path` for file I/O; normalize filenames for call-site identity.
- Implementation must be **case-independent** and **data-agnostic**: no hardcoded case-sensitive logic or data-type-specific branches.
- No placeholder or stub code; every path must be real, runnable code.
- No keyword matching or fixed pattern lists; logic driven by prompt and context.
- No emoji in code, comments, or documentation.
- LLM model references: use OpenRouter model ids (e.g. anthropic/claude-sonnet-4-6); see config.validator_model for fast validation model.
- Use Context7 MCP for library/API documentation when needed.
- Prefer existing dependencies; introduce new ones only with user awareness.
- **Type checking**: Generated function return values are validated with `isinstance`; when pydantic is available, `TypeAdapter(expected_type)` is used to produce clearer validation errors.

## Rules

- Keep CLAUDE.md up to date with the project.
- Use `.claude/skills/code-explorer/SKILL.md` before making changes; use `.claude/skills/code-simplifier/SKILL.md` after changes when appropriate.
- Provide a plan and explanation for non-trivial changes; make changes that work for all use cases, not a single case.

---

## Todo

Items below are partially done, naive, or fragile and should be revisited for generalization and edge cases.

- **Resolution / DAG**: Branch selection (default_branch vs most recent) and refs/usage_id handling may not cover all multi-branch scenarios; edge cases when slot has no commits or no refs.
- **Reactivity**: Staleness is per-slot; no fine-grained invalidation by usage_id. Downstream requirements (e.g. required_columns) are a single dict per slot and may not scale to many distinct downstream needs.
- **Flow / profile_output**: Duck-typing for "columns" and "dataframe_like" is minimal; complex or nested structures may not profile correctly. _flow_from_inputs merge logic (picking producing_slot from first valid flow) is a heuristic.
- **Validator**: AST and execution validation are robust; type validation depends on expected_type and optional TypeAdapter. Gist-based validation can flake on environment or timeout.
- **Agent / generator**: Tool ordering and system prompt are fixed; prompt building from GenerationSpec could be more modular. profile_data_and_flow depends on optional refs (example_glm); no fallback behavior when refs are missing.
- **Tools**: SEARCH/RAG and custom tools are documented in prompts; injection into system prompt is legacy-style. No formal contract for tool return types used by the agent.
- **Console I/O**: Rich output and file-link formatting assume a certain terminal/IDE; no headless or log-only mode beyond verbose flag.
