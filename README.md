# semipy

Write Python logic in natural language. `semipy` generates, validates, and caches real Python functions from your specs — no boilerplate, no hardcoded rules.

```python
from semipy import semiformal, semi, configure

configure(openai_api_key="sk-...")  # or set OPENAI_API_KEY in environment

@semiformal
def parse_log_line(line: str) -> dict:
    #> extract timestamp, level, and message from an Apache log line
    result = ...
    return result

# First call: agent generates a Python function and caches it.
# All later calls: load the cached implementation instantly.
row = parse_log_line('127.0.0.1 - - [10/Oct/2000:13:55:36] "GET /index.html" 200')
```

## Install

```bash
pip install semipy
```

For Jupyter notebook display:

```bash
pip install "semipy[jupyter]"
```

For PDF input materialization:

```bash
pip install "semipy[pdf]"
```

## API key

semipy uses the OpenAI API for code generation. Set your key in one of three ways:

**Environment variable** (recommended for scripts):

```bash
export OPENAI_API_KEY=sk-...
```

**`.env` file** at the project root (loaded automatically):

```
OPENAI_API_KEY=sk-...
```

**In code** (overrides env):

```python
from semipy import configure
configure(openai_api_key="sk-...")
```

## Configuration

```python
from semipy import configure

configure(
    openai_api_key="sk-...",       # defaults to OPENAI_API_KEY env var
    openai_model="gpt-4o",         # generation model (default: gpt-5.4)
    verbose=True,                  # rich terminal output during generation (default: True)
    cache_dir=".semiformal",       # where portal JSON and dispatch modules are stored
    max_retries=3,                 # agent retry limit on validation failure
    session_source=None,           # pin portal identity (useful for Jupyter; see below)
)
```

All fields are optional — call `configure()` with only what you want to override.

## Terminal output

When `verbose=True` (the default), semipy prints a live Rich panel showing the agent's reasoning, tool calls, and generated code as they stream in. This works in both terminal and Jupyter.

```
 Implementing code...
 ─────────────────────────────────────────────────
  Reasoning  The function needs to parse a standard Apache
             Combined Log Format line...
 ─────────────────────────────────────────────────
  Tool  build_and_run_gist  passed
 ─────────────────────────────────────────────────
  Reusing cached implementation; runtime verify passed.
  parse_log_line  GENERATE  a1b2c3d4  examples/logs.py:12
```

To silence all output: `configure(verbose=False)`.

To see full prompt, decision, and tool-call dumps, set the environment variable:

```bash
export SEMIPY_PIPELINE_TRACE=1
```

## Usage patterns

### `@semiformal` with `#>` spec blocks

```python
from semipy import semiformal

@semiformal
def extract_fields(record: str) -> dict:
    #> extract date, sender, and subject from an email header
    result = ...
    return result
```

The `#>` block is the spec. The `result = ...` is the slot anchor — the agent fills it in.

### Inline `semi()` for expressions

```python
from semipy import semiformal, semi

@semiformal
def classify_rows(rows):
    return [r for r in rows if semi(f"is {repr(r['status'])} a client error?")]
```

### Standalone `semi()` in any function

```python
from semipy import semi

def process(text):
    label = semi(f"classify '{text}' as positive, negative, or neutral")
    return label
```

### Multiple slots in one function

```python
from semipy import semiformal

@semiformal
def analyze(entry: str) -> dict:
    #> extract the IP address from the log entry
    ip = ...

    #> determine if the HTTP status code in the entry indicates an error
    is_error = ...

    return {"ip": ip, "error": is_error}
```

## Caching and reuse

Generated functions are stored in `.semiformal/` relative to your working directory:

- `.semiformal/<session>.portal.json` — versioned DAG of all commits, branches, and decisions.
- `.semiformal/runtime/<module>.semi.py` — compiled Python implementations for import.

On subsequent runs, semipy loads the cached implementation without calling the LLM. It re-verifies when it detects new input shapes, and runs ADAPT (re-generation from the prior commit) when verification fails.

To force regeneration, delete `.semiformal/` or the relevant portal file.

## Jupyter

In Jupyter notebooks, semipy detects the `ipykernel` environment automatically. The portal is keyed to `os.getcwd()` so one portal persists across kernel restarts.

Install the optional display extras for inline Rich output:

```bash
pip install "semipy[jupyter]"
```

If multiple notebooks share a working directory and need separate caches:

```python
configure(session_source="/path/to/my_notebook.ipynb")
```

## VS Code extension

The [Semipy VS Code extension](https://marketplace.visualstudio.com/items?itemName=semipy.semipy-vscode) adds:

- Syntax highlighting for `#>` spec lines (teal) and `#<` reasoning lines (green)
- Slot history tree in the Explorer panel
- Split-view to inspect generated `.semi.py` alongside your source
- Inlay hints and CodeLens showing commit id and decision

## Examples

See the `examples/` directory:

- `examples/apache_log_semiformal_stages.py` — staged walkthrough from simple extraction to INSTANTIATE
- `examples/use_contract_intelligence.py` — contract field extraction from PDF
- `examples/use_sponsorship_canonicalizer.py` — entity canonicalization pipeline

## License

MIT
