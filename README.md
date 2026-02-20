# semipy

Runtime semiformal system: use the `@semiformal` decorator and `semi()` to express underspecified logic (e.g. natural-language conditions or extraction rules). The first time a `semi()` expression runs, an LLM generates a Python function; that function is cached and reused for all later calls, so there are no per-row LLM invocations.

## Setup with uv

[uv](https://docs.astral.sh/uv/) is used for dependency and environment management.

**Install uv** (if needed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Create a virtual environment and install the package** (from the repo root):

```bash
cd /path/to/semipy-package
uv venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
uv pip install -e .
```

Or install in one step with sync (uses `pyproject.toml` and `uv.lock`):

```bash
uv sync
source .venv/bin/activate
```

**Run examples**:

```bash
# Ensure OPENAI_API_KEY is set (e.g. in .env)
uv run python examples/use_wrangling.py
```

To use the project without activating the venv:

```bash
uv run python examples/use_wrangling.py
```

## Configuration

- Set `OPENAI_API_KEY` in the environment or in a `.env` file in the project root.
- Optional: call `semipy.configure(...)` to set `model`, `cache_dir`, `max_retries`, or `enable_execution_test`.

## Usage

```python
from semipy import semiformal, semi

@semiformal
def filter_errors(rows):
    return [r for r in rows if semi(f"is {repr(r['level'])} an error or warning?")]

# First run: generates and caches a predicate. Later runs: use cached function only.
```

See `examples/use_wrangling.py`, `examples/wrangler.py`, and `examples/extend_wrangler.py` for patterns.

## Cache

Generated functions are stored under `.semiformal/runtime/` (by default) as session entry modules (one `.semi.py` file per source file) plus a JSON index. Delete that directory to force regeneration.

Optional: install `automerge` for Automerge-backed index storage (`.index.automerge`). On macOS, building from source requires Rust ([rustup](https://rustup.rs)); Linux may get a prebuilt wheel. The package works without automerge (uses JSON only).

See `.claude/plans/PLAN.md` for architecture and module roles.
