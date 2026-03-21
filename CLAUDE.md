# CLAUDE.md

This file provides guidance when working with code in this repository.

## What is semipy

A runtime semiformal system. The `@semiformal` decorator and `semi()` let users express underspecified logic (natural-language conditions, extraction rules). On first invocation, an LLM generates a Python function via an **agentic pipeline** (OpenRouter + pydantic_ai with tools); the function is validated and cached. Subsequent calls reuse the cached implementation with no LLM invocation.

**Slot identity vs reuse:** `slot_id` stays unique per source line (and spec text), but `spec_equivalence_key` (in `SlotSpec`) fingerprints the durable meaning: template text, free-variable names/order, expected return type, slot category, and output names. If a new call site has no commits yet, `resolver.resolve` can **REUSE** another slot’s compiled implementation from the dispatch module when the equivalence key matches (e.g. the same `semi(f"...")` template in another notebook cell).

**REUSE vs data changes:** `spec_equivalence_key` ignores runtime values, so the same template with new inputs still resolves to **REUSE**. After REUSE, `execute_slot` runs `verify_runtime_execution` unless `runtime_input_fingerprint` on the commit matches current inputs (fingerprint is set at commit creation; it is not rewritten on each reuse, to avoid portal churn on per-row `apply`). On verify failure, resolution falls through to **ADAPT**. Verify **failures** log when `verbose` is on. A data-agnostic guard catches empty-string returns from non-empty string inputs (before the TypeAdapter branch) to force ADAPT when a reused implementation silently fails for a new input format. Open-ended NL can still pass execution checks but be wrong semantically; use `expected_type` or explicit checks where needed.

**Observation harvesting:** On the first GENERATE for a slot with scalar inputs, `_harvest_caller_series_samples` walks the call stack to find a Series or list that contains the current value. When found (e.g. from a `DataFrame.apply()` call), it pre-seeds `slot.input_observation_samples` with all unique values so the generation prompt sees input variety, not just the first row. Subsequent observations accumulate per-call via `_record_slot_input_observations`.

**Branch-head resolution:** `_head_commit` (resolver) and `_get_active_commit` (store/dispatch) both use `most_recent_branch_head` from `semipy.history` to pick the newest branch head across all branches, not only `default_branch`. This ensures ADAPT commits (which go to a new branch) are used as the parent source for subsequent ADAPTs and are written to the dispatch module.

**Jupyter / IPython:** Code runs from a temp file whose basename changes every kernel restart (`.../ipykernel_*/NNNNNNNN.py`), which used to produce a new portal each time. `execute_slot` now resolves the portal anchor with `session_anchor.resolve_portal_anchor`: for paths containing `ipykernel`, the anchor is `os.getcwd()` (so the same working directory shares one portal and dispatch module). Override with `configure(session_source="/path/to/notebook.ipynb")` or env `SEMIPY_SESSION_SOURCE` when multiple notebooks share one cwd and need separate caches.

## Commands

```bash
uv sync
source .venv/bin/activate
```

## Environment

- **OPENROUTER_API_KEY** in `.env` or environment (required for generation).
- Optional: **E2B_API_KEY** for sandboxed gist execution (otherwise subprocess fallback).
- Optional: **SEMIPY_PIPELINE_TRACE** (`1`/`true`/`yes`) for full prompt, decision, reasoning, and tool-call dumps after each generation (env-only, not in SemiConfig).
- Python >= 3.10. Uses `uv` for dependency and environment management.

## Console UX

When `configure(verbose=True)` (default), `effective_stream_display_mode(verbose=True)` uses **peek**: Rich `Live` rolling tail plus a phase strip in the terminal; Jupyter uses throttled `Panel` updates. When `verbose=False`, stream UI is off. Peek line count, timeline, and elapsed strip are fixed in `semipy.agents.config` (`STREAM_PEEK_LINES`, `STREAM_TIMELINE`, `STREAM_SHOW_ELAPSED`). Completed model **reasoning** parts print as **Reasoning** panels in peek mode as well as the rolling tail.

Pipeline messages use plain language (e.g. “No reusable implementation; creating a new one.”, “Implementing code…”) instead of “cache miss” / “Calling agent”. If a **downstream API** (e.g. Matplotlib) raises after `semi()` returns, pass `expected_type` so generation validates the shape, or reuse structured outputs from another slot instead of duplicating the same field with a second `semi()`.

**STATEMENT_BLOCK + schema:** For `#>` slots with a **single** output name, lowering infers `expected_type` from the enclosing method’s return annotation when the method returns that output name. If that type is **concrete** (not `Any` or plain `dict` / `dict[...]`), commit-time execution validation uses **`semipy.type_adapter.type_adapter_for`** (pydantic `TypeAdapter` with a **defining-module** namespace). Pydantic’s public `TypeAdapter.rebuild(_types_namespace=...)` still pairs that namespace with the **caller frame’s** `globals` (often `semipy.agents.validator`), which can leave dataclass schemas incomplete (`class-not-fully-defined`). `type_adapter_for` calls `_init_core_attrs` with **both** globals and locals set to the same module dict (or an explicit `globals_namespace=` for `exec` contexts). Use `type_adapter_for(T)` in user code instead of raw `TypeAdapter(T)` when validating the same dataclass types the pipeline validates. Loose `dict[...]` annotations stay permissive. The user prompt includes a short STATEMENT_BLOCK contract when strict typing applies. With **PEP 563** (`from __future__ import annotations`), `decorator._type_hints_for_lowering` uses `typing.get_type_hints` on the decorated callable so `SlotSpec.expected_type` is a resolved type, not a string forward reference (which would break `TypeAdapter` with `class-not-fully-defined` and force repeated generation).

**PDF paths as slot inputs:** `execute_slot` calls `materialize_runtime_document_inputs` so existing `.pdf` paths (`Path` or string) on top-level slot kwargs or on attributes of `self` are replaced with extracted text before resolve/generate/call. Override backend with env `SEMIPY_DOCUMENT_PDF_BACKEND` (`auto`, `liteparse`, `llama_cloud`) and layout with `SEMIPY_DOCUMENT_LAYOUT_HEAVY` (`1`/`true`/`yes`). Very large PDFs still load whole text into memory; the agent tool `read_document_context` can chunk for model context during generation.

## Package layout

- **Root** (`semipy/`): `types.py`, `models.py`, `decorator.py`, `template.py`, `semi_fn.py`, `resolver.py`, `store.py`, `documents.py` (internal: `load_document_text`, `materialize_runtime_document_inputs` at slot boundary; agent `read_document_context` uses the same loader). Not exported from `semipy.__init__`.
- **agents/** (`semipy/agents/`): Agentic pipeline: config, agent, generator, gist, executor, validator, profiler, tools, console_io, console_messages (tool line formatters), console_view (terminal Live timeline + peek), compiler, resolution_advisor (stub schedule; cross-slot guard is execution verify). All LLM, tools, validation, and UX live here.
- **history/** (`semipy/history/`): Version control (Merkle DAG): `version_control.py` with Commit, Branch, Slot, Portal; create_commit, add_commit_to_slot, walk_history, find_branch_by_fingerprint, etc.
- **reactivity/** (`semipy/reactivity/`): Data flow and reactive invalidation: `reactive.py` (DependencyGraph, SlotRef, add_dependency, is_stale, mark_downstream_stale, persistence); `flow.py` (DataFlow, create_flow, extract_flow, profile_output, `attach_producer_flow` so list outputs can carry `_semi_flow` for downstream slot edges).

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
          -> tools: profile_data_and_flow, read_upstream_context, read_file_context, read_document_context, build_and_run_gist, validate_output
          -> extract generated_source from deps or response
        -> validate() [agents.validator] -> create_commit [history] -> save_portal -> write_dispatch_module()
        -> load and execute new function
    -> Execute function with runtime arguments
```

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

Exports from `semipy/__init__.py`: `semiformal`, `semi`, `SemiConfig`, `configure`, `get_config`, `Decision`, `SemiCallError`, `SemiGenerationError`, `compute_spec_equivalence_key`, `register_tool`, `parse_tool_refs`, `GistBuilder`, `Gist`, `GistExecutor`, `ExecutionResult`, `SemiAgentDeps`, `ProfileDataResult`, `GistRunResult`, `OutputValidationResult`, `DocumentContextResult`, `DependencyGraph`, `SlotRef`, `DataFlow`, `attach_producer_flow`, and library helpers (`load_library`, `run_sleep_phase`, `AbstractionLibrary`, `LibraryPrimitive`, `ASTPattern`).

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
- When testing the code, act an user and actually using theagentic tool you builtin to run, inspect ande debug; the goal is not to see if the code runs, but to see if the output is what you expected.

## Rules

- Keep CLAUDE.md up to date with the project.
- Use `.claude/skills/code-explorer/SKILL.md` before making changes; use `.claude/skills/code-simplifier/SKILL.md` after changes when appropriate.
- Provide a plan and explanation for non-trivial changes; make changes that work for all use cases, not a single case.