# CLAUDE.md

This file provides guidance when working with code in this repository.

## What is semipy

A runtime semiformal system. The `@semiformal` decorator and `semi()` let users express underspecified logic (natural-language conditions, extraction rules). On first invocation, an LLM generates a Python function via an **agentic pipeline** (OpenRouter + pydantic_ai with tools); the function is validated and cached. Subsequent calls reuse the cached implementation with no LLM invocation.

**Slot identity vs reuse:** `slot_id` stays unique per source line (and spec text), but `spec_equivalence_key` (in `SlotSpec`) fingerprints the durable meaning: template text, free-variable names/order, expected return type, slot category, and output names. If a new call site has no commits yet, `resolver.resolve` can **REUSE** another slot’s compiled implementation from the dispatch module when the equivalence key matches (e.g. the same `semi(f"...")` template in another notebook cell).

**REUSE vs data changes:** `spec_equivalence_key` ignores runtime values, so the same template with new inputs still resolves to **REUSE**. After REUSE, `execute_slot` runs `verify_runtime_execution` unless `runtime_input_fingerprint` on the commit matches current inputs (fingerprint is set at commit creation; it is not rewritten on each reuse, to avoid portal churn on per-row `apply`). On verify failure, resolution falls through to **ADAPT**. Verify **failures** log when `verbose` is on. A data-agnostic guard catches empty-string returns from non-empty string inputs (before the TypeAdapter branch) to force ADAPT when a reused implementation silently fails for a new input format. Another guard rejects **identity** results (output string equals single non-empty input when the input is at least 9 characters) for `function_body` / `expression` / `standalone` slots with `str` returns — this catches generated code that does `return s` on parse failure, which would otherwise pass type checks and block ADAPT when new rows are added incrementally. Open-ended NL can still pass execution checks but be wrong semantically; use `expected_type` or explicit checks where needed.

**Observation harvesting:** When slot inputs are scalar-only, `_harvest_caller_series_samples` walks the call stack to find a Series or list that contains the current value and merges distinct values into `slot.input_observation_samples` (not only before the first commit), so incremental edits to a `DataFrame` still widen the observation set for prompts. Per-call values are still recorded via `_record_slot_input_observations`.

**Branch-head resolution:** `_head_commit` (resolver) and `_get_active_commit` (store/dispatch) both use `most_recent_branch_head` from `semipy.history` to pick the newest branch head across all branches, not only `default_branch`. This ensures ADAPT commits (which go to a new branch) are used as the parent source for subsequent ADAPTs and are written to the dispatch module.

**Jupyter / IPython:** Code runs from a temp file whose basename changes every kernel restart (`.../ipykernel_*/NNNNNNNN.py`), which used to produce a new portal each time. `execute_slot` now resolves the portal anchor with `session_anchor.resolve_portal_anchor`: for paths containing `ipykernel`, the anchor is `os.getcwd()` (so the same working directory shares one portal and dispatch module). Override with `configure(session_source="/path/to/notebook.ipynb")` or env `SEMIPY_SESSION_SOURCE` when multiple notebooks share one cwd and need separate caches.

**Sketch library (pattern learning):** After GENERATE/ADAPT, binding extraction updates `sketch_library.json` under `cache_dir` (disable with `configure(sketch_library_learning=False)`). The extraction prompt in `library/binding.py` instructs the model to mark **parametric** quoted literals or identifiers when the same NL shape could repeat with different values (so a later slot can **INSTANTIATE** via substitution). `classify_with_llm` tries OpenAI Responses (`openai_model`, default `gpt-5.4`) first, then OpenRouter chat completions (`validator_model`) if OpenAI is missing or empty. Default `sketch_library_learning_async=False` persists sketches before `execute_slot` returns so INSTANTIATE can run in the same process; set `sketch_library_learning_async=True` for a background thread (lower latency; sketches may lag). Users do not import sketch APIs; learning is internal to `execute_slot`.

**VS Code extension:** `semipy.sessionSource` must match `configure(session_source=...)` (same resolved path string the runtime uses for `session_id`). If the workspace root is the repo, use `${workspaceFolder}/examples` when semiformal code lives under `examples/`; if the workspace root is `examples/`, use `${workspaceFolder}`.

**Reasoning surfaces (`#<`) vs slot spec (`#>`):** For `@semiformal` functions, `lowering.scan_informal_specs` builds `SlotSpec` from contiguous `#>` comment blocks, inline `#>` on `...` statements, and `semi(...)` calls. Text on `#<` lines is **not** part of `spec_text`. Before compilation, `strip_skeleton_lines` replaces each `#<` line with a single `#` placeholder line so absolute line numbers stay aligned and `_make_slot_id` can key slots by **ordinal** inside the function (inserting or refreshing `#<` lines does not mint a new slot id). After GENERATE/ADAPT, `agents/skeleton_writer` may write new `#<` lines into the user’s source file; `#>` lines are never overwritten by that pass. Users who want to **lock in** an inference note as contract text edit `#<` into `#>` (or add a `#>` line in the same contiguous block): that changes `spec_text` and thus `spec_equivalence_key`, so the next resolution may GENERATE or ADAPT. See `examples/apache-log-usecase.md` (STAGE 7) for the promote workflow; STAGE 6 there covers pattern learning and **INSTANTIATE**. `_collect_hash_arrow_block_ranges` merges non-contiguous `#>` blocks within the same function when the gap contains no slot anchors (`name = ...` or `semi()` calls), so promoting a `#<` above code lines to `#>` folds it into the existing slot rather than creating a new one. After GENERATE/ADAPT, the skeleton writer canonicalizes indentation: move ``def`` to column 0 by subtracting its leading indent from every line; if the minimum indent of lines after ``def`` exceeds one logical level (4 spaces), reduce over-indented body lines; then prepend the on-disk ``def``-line indent to each line so the block matches the class/module column. Dispatch module sketch comments flatten multi-line `spec_template` to one line to prevent syntax errors.

**`#<` surface V2 — split-zone placement and minimum-set rules:** The skeleton writer places `#<` lines in two zones around each slot anchor line `A`. **Zone P** (provenance, above `A`) holds `goal`, `because`, `alt`, `given`; **Zone E** (effect, below `A` or below the last `#>` of a block) holds `commits`, `verified`, `yields`. Indent inherits from `A` so nested anchors (inside `for`/`if`/`with`) get correct indentation. **Minimum-set rules** (`_should_skip_key` in `steering.py`): `yields` is suppressed when the return annotation is a simple builtin (`str`, `int`, `float`, `bool`) and the generated code returns that type trivially; `given` is suppressed for single-parameter signatures; `because` is suppressed unless decision is ADAPT or there is a verify-failure context; `goal` is suppressed when the spec is already one short line; `commits` is always emitted. **`verified` is never LLM-synthesized** — `_derive_verified` derives it deterministically from `validation_result.gist_stdout` and `sample_input`. **Promotion detection** (`detect_promoted_keys` + `_detect_promoted_keys_from_file`): before rendering either zone, the writer scans the slot’s `#>` content for lines matching `#>\s*(key)\s*:`; any promoted key is omitted from `#<` (the `#>` line is the contract; a duplicate `#<` would be noise). The promoted value is stored in `advisor_state["steering_overrides"][key]` and `user_frozen=True` so subsequent synthesis does not fight the user’s promoted contract. **`spec_text` grounding:** `_build_synthesis_prompt` reads `slot_spec.spec_text` (the short on-disk `#>` text) — not `spec.prompt` (the full 500-line generation prompt) — so synthesis anchors on the correct slot spec even in large files with many slots.

**Semantic reuse check:** After verify passes on REUSE, `_should_semantic_check` evaluates whether the implementation covers observed input diversity. The check triggers when (a) the commit changed (first check for a new generation), or (b) the observation content fingerprint changed AND at least 1 REUSE call has occurred since the last check. This ensures new input patterns are evaluated promptly without excessive LLM calls on unchanging data.

**Downstream usage context:** When building a `GenerationSpec`, `build_generation_spec` reads the user's source file via `_read_user_source_for_context` and populates `user_source_code`. The LLM prompt includes this context so it can infer the expected output structure (e.g. dict keys, attribute names) from how callers consume the function's return value, even when the return type annotation is generic (like `list[dict]`).

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

When `configure(verbose=True)` (default), `effective_stream_display_mode(verbose=True)` uses **peek**: Rich `Live` rolling tail plus a phase strip in the terminal; Jupyter uses throttled `Panel` redraws inside a dedicated `ipywidgets.Output` (`clear_output` + one panel) so the stream does not append a long tail of panels. When `verbose=False`, stream UI is off. Peek line count, timeline, and elapsed strip are fixed in `semipy.agents.config` (`STREAM_PEEK_LINES`, `STREAM_TIMELINE`, `STREAM_SHOW_ELAPSED`). Completed model **reasoning** parts print as **Reasoning** panels in peek mode as well as the rolling tail.

Pipeline messages use plain language (e.g. "No reusable implementation; creating a new one.", "Implementing code...") instead of "cache miss" / "Calling agent". If a **downstream API** (e.g. Matplotlib) raises after `semi()` returns, pass `expected_type` so generation validates the shape, or reuse structured outputs from another slot instead of duplicating the same field with a second `semi()`. On the REUSE path, `slot_resolver.execute_slot` logs "Reusing cached implementation; runtime verify passed." (or "same input fingerprint") and notes "(from donor slot)" when the implementation comes from a different slot via `spec_equivalence_key` matching. Consecutive identical reuse lines for the same file and function are **batched** in `print_pipeline_log` (left column repeat count, collapsed lineno range when multiple `#>` lines differ only by line).

**ADAPT failure context:** When `verify_runtime_execution` fails and triggers ADAPT, `GenerationSpec.verify_failure_context` carries the error message from verification into the generation prompt. The ADAPT prompt in `agent.py` includes this failure reason so the LLM understands exactly what went wrong (type mismatch, execution failure, empty output, identity return) and can fix the specific issue without removing working branches.

**Slot argument binding:** `bind_slot_arguments` in `slot_call.py` maps slot `free_variables` names to the generated function's parameter names via keyword binding. When the generated function uses different names than the slot variables (common for standalone `semi()` where variables are auto-named `v0`, `v1`), the binder falls back to positional binding. The gist builder uses the same fallback when constructing test invocations.

**STATEMENT_BLOCK + schema:** For `#>` slots with a **single** output name, lowering infers `expected_type` from the enclosing method’s return annotation when the method returns that output name. If that type is **concrete** (not `Any` or plain `dict` / `dict[...]`), commit-time execution validation uses **`semipy.type_adapter.type_adapter_for`** (pydantic `TypeAdapter` with a **defining-module** namespace). Pydantic’s public `TypeAdapter.rebuild(_types_namespace=...)` still pairs that namespace with the **caller frame’s** `globals` (often `semipy.agents.validator`), which can leave dataclass schemas incomplete (`class-not-fully-defined`). `type_adapter_for` calls `_init_core_attrs` with **both** globals and locals set to the same module dict (or an explicit `globals_namespace=` for `exec` contexts). Use `type_adapter_for(T)` in user code instead of raw `TypeAdapter(T)` when validating the same dataclass types the pipeline validates. Loose `dict[...]` annotations stay permissive. The user prompt includes a short STATEMENT_BLOCK contract when strict typing applies. With **PEP 563** (`from __future__ import annotations`), `decorator._type_hints_for_lowering` uses `typing.get_type_hints` on the decorated callable so `SlotSpec.expected_type` is a resolved type, not a string forward reference (which would break `TypeAdapter` with `class-not-fully-defined` and force repeated generation).

**PDF paths as slot inputs:** `execute_slot` calls `materialize_runtime_document_inputs` so existing `.pdf` paths (`Path` or string) on top-level slot kwargs or on attributes of `self` are replaced with extracted text before resolve/generate/call. Override backend with env `SEMIPY_DOCUMENT_PDF_BACKEND` (`auto`, `liteparse`, `llama_cloud`) and layout with `SEMIPY_DOCUMENT_LAYOUT_HEAVY` (`1`/`true`/`yes`). Very large PDFs still load whole text into memory; the agent tool `read_document_context` can chunk for model context during generation.

## Package layout

- **Root** (`semipy/`): Core types (`types.py`, `models.py`, `domain_models.py`), entry points (`decorator.py`, `semi_fn.py`), lowering (`lowering.py`: `#>` / `#<`, `scan_informal_specs`, scaffolds), templates (`template.py`), resolution (`resolver.py`), orchestration (`slot_resolver.py`: `execute_slot`, portal load, verify, materialize documents), persistence (`store.py`: dispatch module, active commit), documents (`documents.py`), session identity (`session_anchor.py`), fingerprints (`runtime_fingerprint.py`), pydantic helpers (`type_adapter.py`), utilities (`dataclass_utils.py`), optional inspection (`portal_inspect.py`). Most are not exported from `semipy.__init__`; see each module’s docstrings.
- **agents/** (`semipy/agents/`): Agentic pipeline: `config`, `agent` (`SemiAgent.generate`), `generator` (pydantic_ai Agent + tools), `gist` / `executor`, `validator`, `profiler`, `tools`, `compiler`, `slot_call` (bind + invoke), `skeleton_writer` (`#<` file updates), `decision` (evidence helpers), `source_context`, `console_io`, `console_messages`, `console_view`, `resolution_advisor` (stub schedule; cross-slot guard is execution verify), `llm_utils`.
- **history/** (`semipy/history/`): Version control (Merkle DAG): `version_control.py` (Commit, Branch, Slot, Portal; create_commit, add_commit_to_slot, walk_history, branch heads).
- **library/** (`semipy/library/`): Optional abstraction library: `abstractions.py`, `compression.py`, `pattern_mining.py`, `injection.py`, `sleep.py`, `store.py`; loaded via `load_library` (exported).
- **reactivity/** (`semipy/reactivity/`): Data flow: `reactive.py` (DependencyGraph, SlotRef, staleness, persistence), `flow.py` (DataFlow, `attach_producer_flow`), `observer.py`, `events.py`, `propagation.py`, `impact.py`.

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

- One portal per session: `.semiformal/{session_id}.portal.json` with full DAG (slots, commits, branches, refs).
- Dispatch module: `.semiformal/runtime/{module_name}.semi.py`; **one active implementation per slot** (newest branch head across branches); compiled Python for each commit is emitted here for import.
- Resolution (simplified): match `spec_equivalence_key` for cross-slot **donor** REUSE; same slot with existing commits -> REUSE if verify passes (optionally skip verify when runtime input fingerprint matches); fingerprint or signature mismatch on a branch -> ADAPT; empty slot / no match -> GENERATE. `refs[usage_id]` short-circuits to a known commit when present.

### End-user flow (typical)

1. Author `@semiformal` code with `#>` blocks and/or `semi(f"...")`, or standalone `semi()` in normal functions.
2. First execution: `execute_slot` -> `resolver.resolve` -> GENERATE -> `SemiAgent.generate` -> validate -> `create_commit` -> save portal -> write dispatch module -> run generated function.
3. Later executions: REUSE imported function; optional `verify_runtime_execution` unless fingerprint matches; on failure, ADAPT with `verify_failure_context` in the prompt.
4. Optional: skeleton writer adds `#<` lines for traceability; user may promote lines to `#>` to change the contract.
5. Jupyter users rely on `session_anchor` (cwd for `ipykernel`); file-backed scripts use normalized source path for portal identity.

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
- **Type checking**: Generated function return values are validated with `isinstance`; when pydantic is available, prefer `semipy.type_adapter.type_adapter_for(expected_type)` over raw `TypeAdapter` so namespaces match the defining module (see STATEMENT_BLOCK section above).
- When testing the code, act an user and actually using theagentic tool you builtin to run, inspect ande debug; the goal is not to see if the code runs, but to see if the output is what you expected.

## Rules

- Keep CLAUDE.md up to date with the project.
- Use `.claude/skills/code-explorer/SKILL.md` before making changes; use `.claude/skills/code-simplifier/SKILL.md` after changes when appropriate.
- Provide a plan and explanation for non-trivial changes; make changes that work for all use cases, not a single case.