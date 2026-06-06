# CLAUDE.md

Guidance for working with code in this repository. Keep this file concise and
accurate; deep conceptual/architecture detail lives under [`docs/`](docs/).

## What is semipy

A runtime semiformal system. The `@semiformal` decorator and `semi()` let users
express underspecified logic (natural-language conditions, extraction rules). On
first invocation an LLM generates a Python function via an **agentic pipeline**
(OpenAI Responses API + `pydantic_ai`, one action-program tool); the function is
validated, version-controlled in a per-session DAG, and cached. Subsequent calls
reuse the cached implementation with no LLM invocation.

The distribution is **`semiformal-py`**; the import package is **`semipy`**.

## Commands

```bash
uv sync
source .venv/bin/activate
python -m pytest tests/ -q      # 213 unit tests, run offline (no API key)
ruff check semipy/              # lint
```

## Environment

- **OPENAI_API_KEY** (env or `configure(openai_api_key=...)`) — required for
  generation. The whole pipeline (generation, validation judging, sketch
  binding, contract maintainer) uses the OpenAI Responses API; default model
  `gpt-5.5` (`config.openai_model`).
- Optional **E2B_API_KEY** for sandboxed gist execution (subprocess fallback otherwise).
- Optional **SEMIPY_PIPELINE_TRACE** (`1`/`true`/`yes`) — full prompt/decision/
  reasoning/tool-call dumps after each generation (env-only).
- Python >= 3.11. Uses `uv` for dependency and environment management.

## Architecture at a glance

```
@semiformal / semi(f"...")
  -> identify call site (file:line:func -> site_id)
  -> load_portal()  (cached per session_id)
  -> resolve(portal, usage, fingerprint)            [resolver.py -> routing.py RoutingPolicy]
       REUSE        load cached fn from dispatch module (optionally verify)
       INSTANTIATE  substitute a learned sketch (no LLM)         [library/sketch.py]
       ADAPT/GENERATE  SemiAgent.generate -> validate -> create_commit -> write dispatch
  -> execute the function with runtime arguments
```

Full detail with math and a worked trace: [`docs/architecture.md`](docs/architecture.md).

### Key abstractions

- **SemiCallSite**: `filename, lineno, func_qualname -> site_id` (SHA256).
- **SlotSpec**: the lowered slot (category, free variables, expected type, output
  names, spec text). `spec_equivalence_key` fingerprints the *durable meaning*
  (template, free-var names/order, return type, category, output names) and
  ignores runtime values, so the same template with new data still REUSEs.
- **Decision**: `REUSE`, `ADAPT`, `GENERATE`, `INSTANTIATE`.
- **Commit / Slot / Portal**: the version-control DAG. A Portal (per session)
  holds Slots (per call-site); each Slot is a DAG of Commits on Branches. The
  active implementation is the **newest branch head across all branches**
  (`most_recent_branch_head`), not only `default_branch`.
- **GenerationSpec / SemiAgentDeps**: inputs/mutable state for one agent run.

### Cache model

- **One portal per project.** A project is the folder tree rooted at the nearest
  ancestor `.semiformal/` directory (git-style discovery via
  `session_anchor.resolve_project`), falling back to cwd. All source files under
  that root share one portal — so a learned implementation can be reused across
  files — and `session_id = hash(project_root)`, so two same-named files in
  different projects never collide.
- Portal: `.semiformal/{session_id}.portal.json` (full DAG, slots from every file
  in the project). Dispatch module: `.semiformal/runtime/{module}.semi.py` — one
  active compiled implementation per slot, imported at runtime.
- A per-portal lock (`slot_resolver._portal_lock`) serializes the portal
  read-modify-write within a project (different projects run concurrently); the
  per-slot single-flight lock prevents duplicate generation of the same slot.
- Legacy per-file portals (pre-0.3) auto-migrate: on first run their slots are
  merged into the project portal (`store.migrate_legacy_portals`).

### Portal / slot CLI (`python -m semipy`)

Portal maintenance + slot lifecycle (back the VS Code editor actions; `--portal`
points at a `.portal.json`):

- `slots --portal P [--file F] [--json]` — list slots (file:line, #versions, decision).
- `reset-slot --portal P --slot-id S` — wipe a slot (all versions + contract/ledger)
  so the next call regenerates it fresh (`version_lock.reset_slot`).
- `reset-version --portal P --slot-id S --commit-id C` — delete one version; the slot
  falls back to its previous commit (`version_lock.reset_version`).
- `regenerate` / `lock` / `unlock` / `rollback` / `rewind-spec` / `revert-effect` /
  `quarantine-cases` / `diagnostics` — existing per-slot operations.

## Subsystems (see `docs/` for depth)

- **Behavioral contract** (`semipy/contract/`) — records *why* each regeneration
  happened and *what its effect was* as content-addressed cases, so iterations
  cannot silently regress. `contract_enabled` is on by default (records contracts
  + change provenance + deterministic invariant seeding); the acceptance *gate*
  (`contract_gate`) and the LLM maintainer (`contract_maintainer`) default off.
  Detail: [`docs/behavioral-contract.md`](docs/behavioral-contract.md).
- **Effects** (`semipy/effects/`) — makes a program's real-world effect (DB, file,
  API) a reified, verifiable, version-controlled, revertable artifact: an
  effectful slot's function receives an `fx` capability and emits an
  `EffectScript` instead of touching the world. **Fully opt-in**: `effects_enabled`
  and all `effect_*` flags default off (except `effect_require_approval_external`).
  Detail: [`docs/effects.md`](docs/effects.md).
- **Sketch library** (`semipy/library/`) — learns parametric NL->code patterns
  after GENERATE/ADAPT so a later slot with the same shape but different literals
  can be satisfied by substitution (the INSTANTIATE decision, no LLM call).
  Detail: [`docs/sketch-library.md`](docs/sketch-library.md).
- **Reactivity** (`semipy/reactivity/`) — `reactive.py` (slot dependency graph,
  staleness) and `flow.py` (attach a `DataFlow` to a producer's output for
  downstream shape inference). Producer flow rides on `list`/`dict` results
  (`_SemiFlowList`/`_SemiFlowDict` wrappers) and dataclass instances, carrying the
  `producing_commit_id`; the statement-block proxy carries it across the
  single-output unwrap, so the canonical `#>` form wires producer->consumer edges
  (scalars can't carry flow). Invalidation is **pull-based**: `execute_slot`
  refreshes the consumer's incoming edges to the current call's inputs
  (`set_incoming_edges`, no ghost edges) and regenerates iff a consumed upstream's
  commit changed (`stale_against_inputs` vs `record_consumed`) — so mutual deps are
  caught without a graph cycle and settle without churn, and dropped deps don't
  over-invalidate. `docs/architecture.md` §8 has the detail.
- **Interpreted mode** (`semipy/interpreted.py`) — opt-in
  interpret-until-shape-stable slots (`semi(..., interpreted=True)` /
  `@semiformal(interpreted=True)`): the LLM runs per call (memoized) and the slot
  promotes itself to a normal cached commit once a synthesized residual reproduces
  **held-out** examples (validated in `GistExecutor`). Serves the irreducibly-
  semantic operators (summarize/judge — they never promote, interpret every row)
  and the shape-stable ones (extract/parse/classify — promote, then LLM-free).
  Detail: [`docs/interpreted-mode.md`](docs/interpreted-mode.md).
- **Orchestration** (`semipy/orchestration/`) — the generation pipeline as named
  roles (explorer, version-checker, coder, executor, verifier, surfacer) exchanging
  typed artifacts, driven by a code-driven `Orchestrator` over the existing
  `pydantic_ai` Responses stack (langroid was evaluated and dropped — too heavy a
  dependency tree). Correctness-first: a binary, evidence-grounded **alignment
  verifier** with multi-sample majority voting (`verifier_vote_samples`) and an
  **evidence-grounded reuse judge** with voting that biases ties toward ADAPT
  (`reuse_vote_samples`). Read-only roles fan out via `parallel.gather_readonly`;
  writers stay serial. Every LLM role abstains to a deterministic default with no
  API key. Detail: [`docs/orchestration.md`](docs/orchestration.md).

## Important runtime behaviors

- **Slot identity vs reuse.** `slot_id` is keyed on
  `filename:func_qualname:spec_text:ordinal`. Editing `#>` **spec text** mints a
  new slot (fresh contract). Editing only the surrounding formal code keeps
  `slot_id` but changes `spec_equivalence_key` (`spec_changed`), which retires
  the old contract cases before re-resolving. `#<` lines are *not* part of
  `spec_text`; `strip_skeleton_lines` blanks them before lowering so absolute
  line numbers and slot ordinals stay stable.
- **`#>` (spec) vs `#<` (reasoning surface).** `#>` is the user contract that
  feeds `spec_text`. `#<` lines are system-managed traces written by the skeleton
  writer in two zones around the slot anchor — Zone P (`intent`, `given`, `by`,
  `unless`) above, Zone E (`yields`, `verified`) below. `verified` is derived
  deterministically from the validation run, never LLM-synthesized. Promoting a
  `#<` line to `#>` extends the spec (changing `spec_text`).
- **Verify on REUSE.** REUSE skips verification when `runtime_input_fingerprint`
  matches; otherwise it runs `verify_runtime_execution`. Data-agnostic guards
  force ADAPT on empty-string returns from non-empty input and on identity
  passthrough (`return s`) for string slots.
- **Jupyter / IPython.** Temp-file basenames change per kernel restart;
  `session_anchor.resolve_portal_anchor` anchors `ipykernel` paths to
  `os.getcwd()` so one portal/dispatch persists across restarts. Override with
  `configure(session_source=...)` or `SEMIPY_SESSION_SOURCE`.
- **STATEMENT_BLOCK typing.** For a `#>` slot with a single output name, lowering
  infers `expected_type` from the enclosing return annotation; when concrete,
  validation uses `type_adapter.type_adapter_for(T)` (a `TypeAdapter` with a
  defining-module namespace). Prefer `type_adapter_for(T)` over raw `TypeAdapter`
  in user code validating the same dataclass types.
- **PDF inputs.** `execute_slot` calls `materialize_runtime_document_inputs` to
  replace existing `.pdf` paths on slot kwargs (or `self` attributes) with
  extracted text before resolve/generate/call.
- **Concurrency.** `slot_resolver._slot_singleflight_lock(slot_id)` gives one lock
  per slot so concurrent callers of the same slot generate once then REUSE;
  different slots stay concurrent.
- **Interpreted slots.** When `slot_spec.interpreted` and the slot has not promoted,
  `execute_slot` branches (before `resolve()`) into `_execute_interpreted_slot`:
  per-call LLM via `interpreted.interpret_call` (memoized; dict for multi-output
  `#>`, snapped label for `Literal`/`Enum` `expected_type`), examples accumulate in
  `slot.advisor_state` (JSON-safe, persisted), and `attempt_promotion` (up to 2
  codegen draws, held-out validation in `GistExecutor`) mints a `"PROMOTE"` commit
  via `_promote_interpreted_commit` once a residual reproduces held-out examples.
  After promotion the standard REUSE path owns the slot.

## Package layout

- **Root** (`semipy/`): core types (`types.py`, `models.py`), entry points
  (`decorator.py`, `semi_fn.py`), lowering (`lowering.py`, `lowering_ast.py`),
  resolution (`resolver.py`, `routing.py`), orchestration (`slot_resolver.py`:
  `execute_slot`), persistence (`store.py`), documents (`documents.py`), session
  identity (`session_anchor.py`), fingerprints (`runtime_fingerprint.py`),
  pydantic helpers (`type_adapter.py`), CLI (`cli.py`), inspection
  (`portal_inspect.py`, `diagnostics_export.py`), interpreted mode
  (`interpreted.py`).
- **agents/**: the agentic pipeline — `config`, `agent` (`SemiAgent.generate`),
  `generator` (the `pydantic_ai` OpenAI agent + the single `execute_action_program`
  tool), `executor` (gist/subprocess), `validator`, `profiler`, `compiler`,
  `slot_call`, `skeleton_writer` (`#<` writes), `steering`, `decision`,
  `program_analysis`, `tools`, `llm_utils`, and `console_*` (stream UX).
- **history/**: `version_control.py` (Commit/Branch/Slot/Portal DAG),
  `version_lock.py` (lock/rollback/unlock).
- **contract/**, **effects/**, **library/**, **reactivity/**: see Subsystems above.

## Public API

Exported from `semipy/__init__.py`: `semiformal`, `semi`, `interpreted`,
`InterpretedOp`, `SemiConfig`,
`configure`, `get_config`, `Decision`, `SemiCallError`, `SemiGenerationError`,
`compute_spec_equivalence_key`, `register_tool`, `parse_tool_refs`,
`GistExecutor`, `ExecutionResult`, `SemiAgentDeps`, `DependencyGraph`, `SlotRef`,
`DataFlow`, `attach_producer_flow`, `SlotContract`, `ContractCase`,
`ChangeRecord`, and the effects surface (`Effect`, `EffectScript`,
`EffectResult`, `EffectRefused`, `EffectRecorder`, `ArtifactBackend`,
`MemoryArtifactBackend`, `SqliteArtifactBackend`, `ExternalArtifactBackend`,
`register_artifact_backend`, `resolve_backend`, `revert`, `provenance_for`).

## Console UX

With `configure(verbose=True)` (default), generation streams a Rich `Live` peek
(rolling model tail + phase strip) in a terminal, or throttled `Panel` redraws in
Jupyter. The CLI narrates only the transient process and ends with a receipt
pointing at the editor (`Generated.` / `Reused cached implementation.`); the
durable record (decision, why, guarantees, effect-diff, ledger) is owned by the
portal and surfaced by the VS Code extension. Non-terminal output (piped/CI)
falls back to plain transient lines. Set `verbose=False` to silence.

## VS Code extension (`semipy-vscode/`)

Reads the portal JSON and surfaces one visual language (opacity = durability;
teal = spec/contract, soft-green = intended, amber = effect, red = regression):
a CodeLens health sentence, a hover Explanation Card, a gutter glyph, regressions
as Problems diagnostics, a version tree with checkout, and a steering control.
`semipy.sessionSource` must match the runtime's resolved `session_source`. The
`#<` steering vocabulary in the extension mirrors `semipy.models.SteeringBlock`
(`intent`/`given`/`by`/`unless`/`yields`/`verified`); keep `src/data/types.ts` in
sync with `store.py` / `contract/serialize.py` / `effects/ledger.py`.

## Code conventions

- `from __future__ import annotations` and type hints in all modules.
- `pathlib.Path` for file I/O; normalize filenames for call-site identity.
- Implementation must be **case-independent** and **data-agnostic**: no
  case-sensitive or data-type-specific branches, no keyword/pattern lists; logic
  is driven by prompt and context.
- No placeholder or stub code; every path must be real, runnable code.
- No emoji in code, comments, or documentation.
- LLM model references use OpenAI model ids (`config.openai_model`).
- Validate generated return values with `isinstance`; prefer
  `type_adapter.type_adapter_for(T)` over raw `TypeAdapter`.
- Prefer existing dependencies; introduce new ones only with user awareness.
- When testing, act as a user: run the agentic tool, inspect, and debug — the
  goal is not just that code runs, but that the output is what you expected.

## Rules

- Keep this CLAUDE.md and the `docs/` files accurate as the project evolves.
- Use `.claude/skills/code-explorer/SKILL.md` before non-trivial changes and
  `.claude/skills/code-simplifier/SKILL.md` after.
- Provide a plan and explanation for non-trivial changes; make changes that work
  for all use cases, not a single case.
