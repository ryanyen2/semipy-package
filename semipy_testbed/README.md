# Semipy Testbed: Simplified Code Generation for LLMs

A minimal, self-contained test environment for code generation, gist construction, and isolated execution — perfect for developing and testing generated code in containerized environments.

## What is Semipy Testbed?

**Semipy Testbed** is a stripped-down version of the semipy framework that focuses on the core code-generation pipeline while removing framework complexity:

- **No version control** — no DAGs, branches, or commit history
- **No resolution logic** — always calls the LLM to generate (no REUSE/ADAPT decisions)
- **No persistence** — all state is in-memory
- **No UI overhead** — silent by default, verbose mode on demand
- **Docker-ready** — gist executor supports subprocess and container execution

It's designed for teams who want to:
1. Quickly test code generation ideas with an LLM
2. Validate generated code in isolated execution environments
3. Work on infrastructure (docker, kernel gateway, data orchestration) without full framework bundling
4. Debug the gist construction pipeline (how data flows into generated code)

## Quick Start

### Prerequisites

- Python 3.10+
- `pip install openrouter pydantic-ai` (for LLM generation)
- OpenRouter API key (set `OPENROUTER_API_KEY` env var)
- Optional: Docker (for containerized gist execution)

### Installation

```bash
# Clone and enter the workspace
cd /path/to/semipy-package

# Install testbed in development mode
pip install -e .

# Or just add to PYTHONPATH
export PYTHONPATH="$PWD:$PYTHONPATH"
```

### Your First Inference

```python
from semipy_testbed import infer_semiformal

result = infer_semiformal(
    user_spec="Extract the domain from an email address",
    free_variables={"email": "alice@example.com"},
    sample_input={"args": ["alice@example.com"], "kwargs": {}},
    expected_type=str,
    free_variable_names=["email"],
    verbose=True,
)

if result.success:
    print(result.compiled_function("bob@example.com"))
    # Output: example.com
else:
    print(f"Error: {result.error}")
```

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────┐
│ User Code (semi() call or @semiformal function)         │
└────────────┬────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────┐
│ Parse Spec (natural language or template)              │
│ Build: free_variables, sample_input, expected_type     │
└────────────┬────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────┐
│ Call OpenRouter LLM                                    │
│ Prompt includes: user spec, data samples, context code │
└────────────┬────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────┐
│ Validate Generated Code                                │
│ 1. Syntax check (AST parse)                            │
│ 2. Build gist (minimal executable with test invocation)│
│ 3. Execute gist in subprocess/docker                   │
│ 4. Check output against expected type                  │
└────────────┬────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────┐
│ Return SimpleInferenceResult                           │
│ - compiled_function: callable Python function          │
│ - source_code: full generated Python                   │
│ - gist_source: minimal executable script               │
│ - execution_stdout/stderr: sandbox output              │
│ - error: None if success, string otherwise             │
└─────────────────────────────────────────────────────────┘
```

### Core Modules

#### `config.py`
Minimal configuration (in-memory state).

**Usage:**
```python
from semipy_testbed import configure, get_config

configure(
    openrouter_api_key="sk-...",
    model="anthropic/claude-sonnet-4-6",
    timeout=30,
    use_docker=False,
)

config = get_config()
```

#### `inference.py`
Main pipeline: orchestrates parse → generate → validate → return.

**Key function:**
```python
def infer_semiformal(
    user_spec: str,                              # Natural language spec
    free_variables: dict[str, Any],              # Runtime values (e.g. {"text": "hello"})
    sample_input: dict[str, Any],                # Test data: {"args": [...], "kwargs": {...}}
    expected_type: type,                         # Return type hint
    free_variable_names: list[str],              # Ordered parameter names
    user_source_code: str = None,                # Your source for context (optional)
    use_docker: bool = False,                    # Execute gist in Docker?
    verbose: bool = False,                       # Print debug info?
) -> SimpleInferenceResult:
```

#### `gist_builder.py`
Assembles minimal standalone executable from generated function.

**What a gist contains:**
1. `import` statements (extracted from generated code)
2. Generated function definition
3. Test invocation with sample data
4. Result marker (`__GIST_RESULT__`) for output extraction

**Example gist output:**
```python
import re
from typing import List

def classify_log_family(body: str) -> str:
    # Generated function (from LLM)
    if "timeout" in body.lower():
        return "timeout"
    # ... more logic ...
    return "unknown"

__GIST_RESULT__ = classify_log_family("Failed to connect after 30s timeout")
print("__GIST_RESULT__", repr(__GIST_RESULT__), flush=True)
```

#### `gist_executor.py`
Runs gist code in subprocess or Docker container.

**Execution flow:**
1. Write gist to temp file
2. Run in subprocess with timeout
3. Capture stdout/stderr
4. Extract result from `__GIST_RESULT__` marker
5. Clean up

**Docker: How it works**
- Image: `python:3.11-slim` + common deps (pandas, numpy, requests)
- Gist code passed to container stdin
- User data mounted read-only at `/data`
- Environment includes `SEMIPY_GIST_USER_SOURCE` (path to user code)
- Result extracted from stdout marker

#### `validator.py`
Validates generated code at three levels:

1. **Syntax validation**: AST parse
2. **Execution validation**: build gist + run
3. **Type validation**: return value matches expected type

**API:**
```python
from semipy_testbed.validator import validate_all

report = validate_all(
    source_code=generated_code,      # Generated Python
    gist_source=gist.source,         # Minimal executable
    expected_type=str,               # Expected return type
    sample_input=sample_input,       # Test data
    timeout=30,
    use_docker=False,
)

if report.passed:
    print("All validation passed!")
else:
    print(f"Validation error: {report.error_message}")
```

#### `types.py`
Core data types:

```python
@dataclass
class SimpleInferenceResult:
    success: bool                        # Did inference succeed?
    compiled_function: Callable | None   # The compiled function
    source_code: str                     # Full generated Python
    gist_source: str                     # Minimal executable
    execution_stdout: str                # Sandbox stdout
    execution_stderr: str                # Sandbox stderr
    execution_result: str                # Extracted result
    error: Optional[str]                 # Error message
    reasoning: Optional[str]             # LLM reasoning (if verbose)
```

## Examples

### Example 1: Simple Standalone Function

```python
from semipy_testbed import infer_semiformal

# Email domain extraction
result = infer_semiformal(
    user_spec="Extract the domain (part after @) from an email. Return empty string if invalid.",
    free_variables={"email": "alice@company.com"},
    sample_input={"args": ["alice@company.com"], "kwargs": {}},
    expected_type=str,
    verbose=True,
)

if result.success:
    # Test multiple inputs
    for email in ["bob@example.org", "invalid-email", "charlie@sub.domain.co.uk"]:
        domain = result.compiled_function(email)
        print(f"{email} -> {domain}")
```

### Example 2: Data-Driven Generation (Apache Logs)

```python
from semipy_testbed import infer_semiformal
from pathlib import Path

# Load data
logs = Path("data/sample_logs.txt").read_text().splitlines()
sample_logs = logs[:10]

# Generate classifier
result = infer_semiformal(
    user_spec=(
        f"Classify these Apache error logs into families: {sample_logs[:3]}. "
        "Return short snake_case family names like 'timeout', 'permission', 'memory'."
    ),
    free_variables={"body": sample_logs[0]},
    sample_input={"args": [sample_logs[0]], "kwargs": {}},
    expected_type=str,
    verbose=True,
)

if result.success:
    # Test on all logs
    for log in sample_logs:
        family = result.compiled_function(log)
        print(f"{log[:40]}... -> {family}")
```

### Example 3: Passing User Source Context

```python
from dataclasses import dataclass
from semipy_testbed import infer_semiformal

@dataclass
class PersonRecord:
    name: str
    age: int
    email: str

# User source shows the expected structure
user_source = """
from dataclasses import dataclass

@dataclass
class PersonRecord:
    name: str
    age: int
    email: str

def parse_line(csv_line: str) -> PersonRecord:
    pass
"""

result = infer_semiformal(
    user_spec="Parse a CSV line (name,age,email) into a PersonRecord dataclass.",
    free_variables={"csv_line": "Alice,30,alice@example.com"},
    sample_input={"args": ["Alice,30,alice@example.com"], "kwargs": {}},
    expected_type=PersonRecord,  # Type hint
    user_source_code=user_source,  # Context for LLM
    verbose=True,
)

if result.success:
    record = result.compiled_function("Bob,25,bob@example.com")
    print(f"Name: {record.name}, Age: {record.age}")
```

### Example 4: Docker Execution

```python
from semipy_testbed import infer_semiformal, configure

# Use Docker for gist execution
configure(use_docker=True)

result = infer_semiformal(
    user_spec="Calculate Fibonacci of n",
    free_variables={"n": 10},
    sample_input={"args": [10], "kwargs": {}},
    expected_type=int,
    use_docker=True,  # Force Docker
    verbose=True,
)

if result.success:
    fib_10 = result.compiled_function(10)
    print(f"Fibonacci(10) = {fib_10}")
```

## Running The Tests

Run the provided examples:

```bash
# Make sure API key is set
export OPENROUTER_API_KEY='sk-...'

# Basic example (email domain extraction, text classification)
python semipy_testbed/examples/basic_semi.py

# Data-driven example (Apache log classification)
python semipy_testbed/examples/apache_log_simple.py
```

## Working with Docker

### Build the Gist Image

```bash
cd semipy_testbed/docker
docker build -f Dockerfile.gist -t semipy-gist:latest .
```

### Run a Gist Manually

```bash
# Create a test gist
cat > test_gist.py << 'EOF'
def greet(name: str) -> str:
    return f"Hello, {name}!"

result = greet("World")
print("__GIST_RESULT__", repr(result), flush=True)
EOF

# Run in Docker
docker run --rm semipy-gist:latest test_gist.py
```

### Using Docker in testbed code

```python
from semipy_testbed import infer_semiformal, configure

configure(use_docker=True)  # All gists will run in Docker

result = infer_semiformal(
    user_spec="...",
    free_variables={...},
    sample_input={...},
    expected_type=str,
)
```

## Advanced Usage

### Custom LLM Models

```python
from semipy_testbed import configure

# Use a different OpenRouter model
configure(
    model="openai/gpt-4-turbo",  # or any OpenRouter model ID
    temperature=0.5,             # Lower = more deterministic
    max_tokens=8192,
)
```

### Debugging: Inspect Generated Code

```python
result = infer_semiformal(...)

# Print the generated Python source
print("Generated source:")
print(result.source_code)

# Print the minimal gist that runs in sandbox
print("\nGist (run in isolation):")
print(result.gist_source)

# Inspect execution output
print("\nExecution output:")
print(f"STDOUT: {result.execution_stdout}")
print(f"STDERR: {result.execution_stderr}")
```

### Error Investigation

```python
result = infer_semiformal(...)

if not result.success:
    print(f"Error: {result.error}")
    print(f"\nGenerated code:\n{result.source_code}")
    # The code failed validation; check:
    # 1. Does the spec make sense?
    # 2. Is sample_input realistic?
    # 3. Does expected_type match?
```

## Performance Considerations

### Gist Execution Timeout
Default is 30 seconds. Adjust if you have long-running functions:
```python
configure(timeout=60)
```

### LLM Calls
Every `infer_semiformal()` call makes one OpenRouter API call (no caching). This is by design for the testbed to keep it simple.

### Docker Overhead
First call: ~1-2s (image pull if not cached). Subsequent calls: ~500-800ms per gist execution.

Use subprocess (default) for development, Docker for production/sandboxing.

## Common Issues

### "OPENROUTER_API_KEY not set"
```bash
export OPENROUTER_API_KEY='your-key-here'
```

### Docker "Cannot connect to daemon"
Make sure Docker is running:
```bash
sudo systemctl start docker  # Linux
open --applications Docker   # macOS
```

### Gist execution timeout
Increase timeout or simplify the generated code:
```python
configure(timeout=60)  # 60 seconds instead of 30
```

### "Could not build gist: no function definition"
The LLM didn't generate a proper function. Check:
1. Is `user_spec` clear?
2. Is `sample_input` realistic?
3. Add `user_source_code` for context?

### Type validation fails
Ensure `expected_type` matches what the LLM is likely to return:
```python
# If you expect a list of dicts
result = infer_semiformal(
    user_spec="...",
    expected_type=list,  # or list[dict]
    ...
)
```

## Extending the Testbed

### Custom Validators

```python
from semipy_testbed import infer_semiformal
from semipy_testbed.validator import ValidationReport

# After inference
result = infer_semiformal(...)

# Add custom validation
if result.success:
    custom_checks = my_validation_logic(result.compiled_function)
    if not custom_checks:
        print("Custom validation failed")
```

### Custom Executor

```python
from semipy_testbed.gist_executor import SimpleGistExecutor

class CustomExecutor(SimpleGistExecutor):
    def execute(self, gist_source, env_vars=None, ...):
        # Your custom execution logic
        pass
```

### Custom Gist Builder

```python
from semipy_testbed.gist_builder import SimpleGistBuilder

class CustomBuilder(SimpleGistBuilder):
    def build(self, generated_source):
        # Customize gist assembly
        pass
```

## Architecture Decisions

### Why always GENERATE?
The testbed is for exploring code generation in isolation. For production use with caching and reuse, use the full semipy framework.

### Why no version control?
Keeps the testbed minimal. The full framework has rich history tracking; the testbed focuses on one-shot inference.

### Why Docker support?
To support teams running generated code in replicated environments (K8s, container orchestration, etc).

### Why Gist isolation?
Generated code is untrusted. Running it in isolated subprocess/container prevents resource exhaustion and side effects on the main process.

## File Structure

```
semipy_testbed/
├── __init__.py                    # Public API
├── config.py                      # Minimal configuration
├── types.py                       # Core data types
├── inference.py                   # Main pipeline
├── gist_builder.py                # Assemble minimal executable
├── gist_executor.py               # Run gist (subprocess/docker)
├── validator.py                   # Validate syntax/execution/type
├── examples/
│   ├── basic_semi.py              # Simple examples
│   ├── apache_log_simple.py       # Data-driven example
│   └── data/
│       ├── sample_logs.txt        # Test data
│       ├── sample.csv             # CSV test data
│       └── requirements.txt       # Gist dependencies
├── docker/
│   ├── Dockerfile.gist            # Gist execution image
│   └── docker-compose.yml         # Optional compose file
└── README.md                      # This file
```

## Contributing

Found a bug? Have an idea? The testbed is designed to be a lightweight exploration tool. PRs welcome!

## Resources

- **Semipy Main**: `/Users/r4yen/Desktop/Research/semi-formal/repo/semipy-package/semipy/`
  - For the full framework with versioning, resolution logic, and reactivity.
- **Examples**: `semipy_testbed/examples/` — run these to see the pipeline in action
- **CLAUDE.md**: Project conventions and architecture

## License

Same as semipy main package.
