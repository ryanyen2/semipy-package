# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is semipy

A runtime semiformal system. The `@semiformal` decorator and `semi()` function let users express underspecified logic (natural-language conditions, extraction rules). On first invocation, an LLM generates a Python function which is validated and cached; subsequent calls reuse the cached function with no LLM invocation.

## Commands

```bash
# Install dependencies
uv sync

# Activate virtualenv
source .venv/bin/activate

# Run examples
uv run python examples/use_wrangling.py

# Run tests
pytest
```

## Environment

Requires `OPENAI_API_KEY` in `.env` or environment. Python >= 3.10. Uses `uv` for dependency/environment management.

## Architecture

### Core data flow

```
@semiformal decorated function
  -> semi(f"semantic prompt with {variables}")
    -> _identify_call_site() [file:line:func -> site_id]
    -> load_portal() (cached in-memory per session_id)
    -> resolver.resolve(portal, usage, fingerprint, constants)
      -> REUSE: load function from dispatch module, optionally add ref, return compiled function
      -> ADAPT / GENERATE: build GenerationSpec with DAG context (parent_sources, lineage_summary)
        -> SemiAgent.generate(GenerationSpec)
        -> dag.create_commit() -> add_commit_to_slot()
        -> store.save_portal() + write_dispatch_module()
        -> load and execute new function
    -> Execute function with runtime arguments
```

### Module roles

| Module | Role |
|--------|------|
| `types.py` | Core dataclasses (SemiCallSite, PromptTemplate, CacheEntry, GenerationSpec, Decision, Usage, etc.) |
| `config.py` | SemiConfig singleton; `get_config()` / `configure()` |
| `decorator.py` | `@semiformal`; source inspection + context injection via `contextvars` |
| `template.py` | AST-based f-string decomposition; structural fingerprint; loop-variant vs constant variables |
| `semi_fn.py` | `semi()` entry point; call-site identification, portal/resolver/store flow, agent invocation |
| `dag.py` | Merkle DAG: Commit, Branch, Slot, Portal; create_commit, add_commit_to_slot, walk_history, find_branch_by_fingerprint |
| `resolver.py` | resolve(portal, usage, fingerprint, constants) -> REUSE / ADAPT / GENERATE (ResolutionResult) |
| `store.py` | load_portal, save_portal, write_dispatch_module, load_function_from_dispatch; `.semiformal/{session_id}.portal.json`, `runtime/{module_name}.semi.py` |
| `compiler.py` | _compile_source() to turn generated Python source into a callable |
| `generator.py` | DSPy/LM wrapper; system + user prompt construction |
| `validator.py` | 3-stage validation: AST parse, type check, execution test with sample input |
| `agent.py` | Generate-validate-retry loop; uses GenerationSpec.decision and parent_sources for ADAPT/FORK prompts |
| `console_io.py` | Rich-based console output; DAG-aware logs (print_dag_reuse, print_dag_adapt, print_dag_generate); print_slot_history (git-log-style) |

### Key abstractions

- **SemiCallSite**: identifies where `semi()` is called (filename, lineno, func_qualname -> site_id SHA256)
- **PromptTemplate**: decomposed f-string with `template_parts` and classified `variable_names`
- **Usage**: concrete `semi()` invocation; `usage_id()` = hash of site_id + template + constants
- **Decision**: REUSE, ADAPT, FORK, GENERATE, MERGE (resolution outcome). ADAPT = same structure, adapt from parent commit (e.g. new prompt/constants).
- **Commit**: one generated implementation in the DAG (commit_id, parent_ids, generated_source, template_fingerprint, constants_snapshot, operation_signature, message, decision)
- **Slot**: per-call-site DAG (commits, branches, refs: usage_id -> commit_id); function_name_base for dispatch
- **Portal**: per-session container (session_id, source_file, module_name, slots by slot_id)

### Cache model (DAG versioning)

- One portal per session (source file): `.semiformal/{session_id}.portal.json` with full DAG (slots, commits, branches, refs)
- Dispatch module: `.semiformal/runtime/{module_name}.semi.py`; only functions referenced by refs are written; DISPATCH maps usage_id -> function name
- Resolution: refs[usage_id] -> REUSE; else operation_signature match -> REUSE (and add ref); else branch with same fingerprint -> ADAPT; else GENERATE (new branch or new slot)
- Logging: One-line logs in natural language: "Call from {source}. {generation}. Code at {path}." Source = file:line (function), generation = Reused/Adapted/New + commit id, path = generated code (.semi.py). print_slot_history(slot) for git-log-style history

### Public API

Exports from `semipy/__init__.py`: `semiformal`, `semi`, `SemiConfig`, `configure`, `get_config`, `Decision`, `SemiGenerationError`.

## Code conventions

- Use `from __future__ import annotations` and type hints in all modules
- Use `pathlib.Path` for file I/O; normalize filenames for call-site identity
- Implementation must be **case-independent** and **data-agnostic** -- no hardcoded case-sensitive logic or data-type-specific branches
- No placeholder/dummy/stub code -- every path must be real, runnable
- No keyword matching or fixed pattern lists -- logic driven by prompt and context
- No emoji in code, comments, or documentation
- LLM model references: use `gpt-5-mini` or `gpt-5` only (not gpt-4)
- Always use Context7 MCP for library/API documentation lookup
- Prefer existing dependencies; introduce new ones only with user awareness

## Design document

See `.claude/plans/PLAN.md` for the full architectural design and rationale.
