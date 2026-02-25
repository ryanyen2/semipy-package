# semipy

Runtime semiformal system: use the `@semiformal` decorator and `semi()` to express underspecified logic (e.g. natural-language conditions or extraction rules). The first time a `semi()` expression runs, an **agentic pipeline** (OpenRouter + pydantic_ai with tools) generates a Python function; that function is validated and cached. Later calls reuse the cached implementation, so there are no per-row LLM invocations.

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
```

To run without activating the venv:

```bash
uv run python examples/use_csv_kit.py
```

## Configuration

- Set **OPENROUTER_API_KEY** in the environment or in a `.env` file in the project root (required for generation).
- Optional: **E2B_API_KEY** for sandboxed gist execution; without it, a subprocess fallback is used.
- Optional: `semipy.configure(...)` to set `openrouter_model`, `validator_model`, `cache_dir`, `max_retries`, `use_e2b`, `gist_timeout`, `enable_execution_test`, `verbose`, `stream`, etc.

## Usage

```python
from semipy import semiformal, semi

@semiformal
def filter_errors(rows):
    return [r for r in rows if semi(f"is {repr(r['level'])} an error or warning?")]

# First run: agent generates and caches a predicate (streaming reasoning and tool calls in the terminal).
# Later runs: use cached function only.
```

See `examples/use_csv_kit.py`, `examples/csv_kit/table.py`, `examples/use_weather_kit.py`, and `examples/weather_kit/ops.py` for patterns.

## Cache

Generated functions are stored under `.semiformal/runtime/` (by default) as one `.semi.py` file per source file (session), plus a JSON portal (`.semiformal/{session_id}.portal.json`). Each call-site slot keeps one active implementation; all usages in that slot map to it. Delete `.semiformal/` to force regeneration.

## Architecture (summary)

- **Resolution**: Cache key is (site_id, template hash, constants). Resolution yields REUSE (cached), ADAPT (same structure, new params), or GENERATE (new implementation).
- **Generation**: When not REUSE, a pydantic_ai Agent (OpenRouter) runs with tools: profile_data_and_flow, read_upstream_context, read_file_context, build_and_run_gist, validate_output. Reasoning and tool calls stream to the terminal. The agent produces a Python function; it is validated (AST, type, execution) and then cached.
- **Gist validation**: Optional sandbox run (E2B or subprocess) assembles a minimal script from user context + generated function and executes it for extra confidence.

For full architecture and module roles, see `CLAUDE.md` and `.claude/plans/agentic-pipeline-refactor.md`.
