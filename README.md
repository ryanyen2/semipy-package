# semipy

Runtime semiformal system: use the `@semiformal` decorator and `semi()` to express underspecified logic (natural-language conditions, extraction rules, ellipsis regions in methods). The first time a slot runs, an **agentic pipeline** (OpenRouter + pydantic_ai with tools) generates a Python function; it is validated, committed to a versioned portal, and written to a dispatch module. Later calls **reuse** that implementation with optional runtime verification, so routine work is not re-sent to the model.

## Setup with uv

[uv](https://docs.astral.sh/uv/) is used for dependency and environment management.

**Install uv** (if needed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Create a virtual environment and install the package** (from the repo root):

```bash
cd /path/to/semipy-package
uv sync
source .venv/bin/activate
```

**Run examples**:

```bash
# Set OPENROUTER_API_KEY in .env or environment
uv run python examples/use_csv_kit.py
uv run python examples/use_weather_kit.py
uv run python examples/use_sponsorship_canonicalizer.py
uv run python examples/use_contract_intelligence.py
uv run python examples/apache_log_semiformal_stages.py --stage 1
```

To run without activating the venv:

```bash
uv run python examples/use_csv_kit.py
```

## Configuration

- **OPENROUTER_API_KEY** in the environment or in a `.env` file at the project root (required for generation).
- Optional: **E2B_API_KEY** for sandboxed gist execution; without it, a subprocess fallback is used.
- Optional: **SEMIPY_PIPELINE_TRACE** (`1` / `true` / `yes`) for full prompt, reasoning, and tool-call dumps after each generation (environment only).
- Optional: **SEMIPY_SESSION_SOURCE** to pin portal identity for notebooks (see `CLAUDE.md`).
- Optional: **SEMIPY_DOCUMENT_PDF_BACKEND**, **SEMIPY_DOCUMENT_LAYOUT_HEAVY** when passing PDF paths into slots (see `CLAUDE.md`).
- `semipy.configure(...)` for `openrouter_model`, `validator_model`, `cache_dir`, `max_retries`, `use_e2b`, `gist_timeout`, `verbose`, `session_source`, and other `SemiConfig` fields. Unknown keys are ignored.

## Usage

```python
from semipy import semiformal, semi

@semiformal
def filter_errors(rows):
    return [r for r in rows if semi(f"is {repr(r['level'])} an error or warning?")]

# First run: agent generates, validates, and caches a predicate (streaming output in the terminal by default).
# Later runs: load cached implementation from the dispatch module; verify when inputs change.
```

In `@semiformal` methods you can use `#>` comment blocks and inline `#>` on `...` placeholders for STATEMENT_BLOCK slots, optional `#<` reasoning lines (see `CLAUDE.md`), and `semi(...)` for EXPRESSION slots.

See `examples/use_csv_kit.py`, `examples/csv_kit/table.py`, `examples/use_weather_kit.py`, `examples/weather_kit/ops.py`, `examples/use_sponsorship_canonicalizer.py`, `examples/use_contract_intelligence.py`, and `examples/apache-log-usecase.md` (with `examples/apache_log_semiformal_stages.py`) for fuller patterns.

## Artifacts and cache

- **Portal**: `.semiformal/{session_id}.portal.json` stores the DAG (slots, commits, branches, refs).
- **Dispatch module**: `.semiformal/runtime/{module_name}.semi.py` holds generated function source for import.
- Deleting `.semiformal/` forces regeneration (use the library’s cache-clear helpers where provided in examples).

## Architecture (summary)

- **Lowering** (`semipy/lowering.py`): Finds `#>` blocks, inline specs, and `semi()` calls; builds `SlotSpec` (including `spec_equivalence_key` for durable meaning). `#<` lines are stripped for identity so annotations do not churn slot ids.
- **Resolution** (`semipy/resolver.py`, `semipy/slot_resolver.py`): REUSE cached implementation when equivalence and verification allow; ADAPT from a parent commit when verify fails; GENERATE when needed. Cross-call-site **donor** REUSE matches the same equivalence key. Runtime PDF paths can be materialized to text at the slot boundary (`semipy/documents.py`).
- **Generation** (`semipy/agents/`): `SemiAgent.generate` runs a pydantic_ai agent with tools (`profile_data_and_flow`, `read_upstream_context`, `read_file_context`, `read_document_context`, `build_and_run_gist`, `validate_output`, etc.). Output is validated (`agents/validator.py`) and committed (`semipy/history/version_control.py`).
- **Reactivity** (`semipy/reactivity/`): Optional `DependencyGraph`, `DataFlow`, and `attach_producer_flow` for downstream tracking.
- **Library** (`semipy/library/`): Optional abstraction primitives (`load_library`, `AbstractionLibrary`, …).

For slot identity, verify gates, Jupyter anchoring, STATEMENT_BLOCK typing, and `#<` / `#>` behavior, see **`CLAUDE.md`**.

## Code conventions

Use `from __future__ import annotations`, type hints, and `pathlib.Path` for I/O. Follow project rules in `.cursor/rules/` and `CLAUDE.md` (data-agnostic logic, no placeholder code, no emoji in code or docs).
