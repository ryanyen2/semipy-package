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
        -> SemiAgent.generate(spec)
          -> pydantic_ai Agent (OpenRouter) with tools
          -> run_stream_events(prompt, deps) -> streaming (reasoning, tool calls, response)
          -> tools: profile_data_and_flow, read_upstream_context, read_file_context, build_and_run_gist, validate_output
          -> extract generated_source from deps or response
        -> validate() -> create_commit -> save_portal -> write_dispatch_module()
        -> load and execute new function
    -> Execute function with runtime arguments
```

### Module roles

| Module | Role |
|--------|------|
| `types.py` | Core dataclasses: SemiCallSite, PromptTemplate, CacheEntry, GenerationSpec, Decision, Usage, ValidationResult, etc. |
| `config.py` | SemiConfig (openrouter_api_key, openrouter_model, validator_model, e2b_api_key, use_e2b, gist_timeout, ...); get_config() / configure() |
| `models.py` | Pydantic models for agent tool I/O: ProfileDataResult, GistRunResult, OutputValidationResult, SemiAgentDeps, etc. |
| `decorator.py` | @semiformal; source inspection and context injection via contextvars |
| `template.py` | AST-based f-string decomposition; structural fingerprint; loop-variant vs constant variables |
| `semi_fn.py` | semi() entry point; call-site identification, portal/resolver/store flow, agent invocation; passes user_source_code and enclosing_function_source into GenerationSpec |
| `dag.py` | Merkle DAG: Commit, Branch, Slot, Portal; create_commit, add_commit_to_slot, walk_history, find_branch_by_fingerprint |
| `resolver.py` | resolve(portal, usage, fingerprint, constants) -> REUSE / ADAPT / GENERATE (ResolutionResult) |
| `store.py` | load_portal, save_portal, write_dispatch_module (one impl per slot: active commit), load_function_from_dispatch |
| `compiler.py` | _compile_source() to turn generated Python source into a callable |
| `generator.py` | pydantic_ai Agent (OpenRouter) + tools; get_semi_agent(); SYSTEM_PROMPT |
| `agent.py` | SemiAgent: async generate_async(spec) and sync generate(spec); prompt building; stream event handling; validate and retry with feedback |
| `gist.py` | GistBuilder(spec).build(generated_source) -> Gist; AST-based assembly of minimal runnable script for sandbox validation |
| `executor.py` | GistExecutor: execute_sync/execute_async (E2B or subprocess); ExecutionResult |
| `validator.py` | validate() (AST, type, execution); validate_with_gist() for gist-based validation; _extract_function_source |
| `console_io.py` | Rich-based console output; DAG logs; streaming (print_reasoning_block, print_response_block, print_tool_call, print_tool_result, print_gist_execution) |
| `tools.py` | Tool refs in prompts ({TOOL(...)}); parse_tool_refs, register_tool; inject_tools_into_system_prompt (legacy) |

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

## Rules

- Keep CLAUDE.md up to date with the project.
- Use `.claude/skills/code-explorer/SKILL.md` before making changes; use `.claude/skills/code-simplifier/SKILL.md` after changes when appropriate.
- Provide a plan and explanation for non-trivial changes; make changes that work for all use cases, not a single case.
