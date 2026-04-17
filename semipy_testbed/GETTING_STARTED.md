# GETTING STARTED: Semipy Testbed for Collaborators

Welcome! This guide explains what the testbed is for and how to use it for your infrastructure work (docker, kernel gateway, validation, iteration).

## TL;DR

You now have a **simplified code generation engine** that:
1. Takes a natural language spec (or template)
2. Calls an LLM (OpenRouter) to generate Python code
3. Validates the code by running it in isolation (subprocess or docker)
4. Returns a compiled function you can use immediately

**No framework complexity**: no version control, no portal persistence, no decision logic.

## Why This Matters

The full semipy framework is powerful but complex (DAGs, versioning, reactivity, caching). You wanted to:
- Test code generation without framework overhead
- Work on infrastructure: docker sandboxing, stateless execution, kernel gateway integration
- Have a clean testbed where data flows through gist programs correctly

This testbed does exactly that.

## Your Workflow

### Phase 1: Understand the Pipeline

```bash
# Set up
cd /path/to/semipy-package
export OPENROUTER_API_KEY='sk-...'

# Run basic example
python semipy_testbed/examples/basic_semi.py

# Run data-driven example
python semipy_testbed/examples/apache_log_simple.py

# Read the README
cat semipy_testbed/README.md
```

You'll see:
- Generated Python code (from LLM)
- Gist source (minimal executable)
- Validation output (syntax OK, execution OK, type OK)
- Test results

### Phase 2: Experiment with Generation

The core API is simple:

```python
from semipy_testbed import infer_semiformal

result = infer_semiformal(
    user_spec="Your task description",
    free_variables={"var_name": value},
    sample_input={"args": [value], "kwargs": {}},
    expected_type=str,  # or list, dict, etc.
)

if result.success:
    output = result.compiled_function(test_input)
    print(output)
```

Try modifying:
- `user_spec`: more/less detailed, different task
- `sample_input`: different test data
- `expected_type`: what you expect back
- `verbose=True`: see the pipeline step-by-step

### Phase 3: Work on Docker Execution

The testbed already supports subprocess and Docker:

```python
from semipy_testbed import infer_semiformal, configure

# Use Docker for gist execution
configure(use_docker=True, timeout=30)

result = infer_semiformal(
    user_spec="...",
    free_variables={...},
    sample_input={...},
    expected_type=str,
)
```

Examine the gist that would run:
```python
print(result.gist_source)  # See what runs in the container
```

Build and test the Docker image:
```bash
cd semipy_testbed/docker
docker build -f Dockerfile.gist -t semipy-gist:latest .

# Run a gist manually
echo 'print("__GIST_RESULT__", repr("hello"), flush=True)' > test.py
docker run --rm -v $PWD:/work semipy-gist:latest /work/test.py
```

### Phase 4: Integrate with Kernel Gateway

Once gist execution is solid, you'll add kernel gateway:

**Current flow:**
```
semipy_testbed → gist executor → subprocess/docker → result
```

**With kernel gateway:**
```
semipy_testbed → gist builder → kernel gateway HTTP request → docker kernel → result
kernel gateway (stateless, horizontal scaling)
```

The gist source is the **HTTP request body**:
- Send POST to `/api/kernels/execute` with gist code
- Kernel gateway runs it in isolated kernel
- Extract result from stdout marker

This testbed gives you clean gist programs (imports + function + test invocation + result marker) so kernel gateway integration is straightforward.

### Phase 5: Validation & Iteration

The testbed has built-in validation at three levels:

1. **Syntax**: Is it valid Python?
2. **Execution**: Does it run to completion?
3. **Type**: Does the return value match expected type?

Check validation details:
```python
result = infer_semiformal(...)

if not result.success:
    print(f"Error: {result.error}")
    print(f"Source:\n{result.source_code}")
    print(f"Validation output:\n{result.execution_stderr}")
```

If validation fails:
1. Improve the spec (be more specific)
2. Add user source context (helps LLM infer structure)
3. Use sample input that matches reality
4. Specify `expected_type` if you have one

## Common Tasks

### Task 1: Generate a Function for Your Domain

```python
from semipy_testbed import infer_semiformal

# Your specific use case
result = infer_semiformal(
    user_spec="Process [your domain] data: input X should produce Y",
    free_variables={"data": your_sample_data},
    sample_input={"args": [your_sample_data], "kwargs": {}},
    expected_type=your_return_type,
    user_source_code=open("your_file.py").read(),  # context
)

if result.success:
    # Use the function
    output = result.compiled_function(new_data)
else:
    print(f"Generation failed: {result.error}")
```

### Task 2: Debug Generation

```python
result = infer_semiformal(...)

print("Generated Python code:")
print(result.source_code)

print("\nMinimal gist (runs in isolated env):")
print(result.gist_source)

print("\nExecution output:")
print(f"stdout: {result.execution_stdout}")
print(f"stderr: {result.execution_stderr}")
```

### Task 3: Test in Docker Before Deployment

```python
from semipy_testbed import configure, infer_semiformal

# Test with Docker
configure(use_docker=True)

result = infer_semiformal(
    user_spec="...",
    free_variables={...},
    ...
)

if result.success:
    print("✓ Function works in Docker!")
    # Now safe to deploy to kernel gateway / K8s / etc
else:
    print(f"✗ Failed in Docker: {result.error}")
```

### Task 4: Work with Structured Data

```python
from dataclasses import dataclass
from semipy_testbed import infer_semiformal

@dataclass
class Person:
    name: str
    age: int
    email: str

# LLM sees your dataclass and generates compatible code
result = infer_semiformal(
    user_spec="Parse a line of text into a Person dataclass",
    free_variables={"line": "Alice, 30, alice@example.com"},
    sample_input={"args": ["Alice, 30, alice@example.com"], "kwargs": {}},
    expected_type=Person,
    user_source_code=open(__file__).read(),  # Include dataclass def
)

if result.success:
    person = result.compiled_function("Bob, 25, bob@example.com")
    print(person)
```

## File Structure

Everything is under `semipy_testbed/`:

```
semipy_testbed/
├── __init__.py              # What you import from
├── config.py                # Minimal config (API key, timeout, etc.)
├── types.py                 # SimpleInferenceResult, etc.
├── inference.py             # Main: infer_semiformal()
├── gist_builder.py          # Assembles minimal executable
├── gist_executor.py         # Subprocess/Docker execution
├── validator.py             # Syntax/execution/type validation
├── README.md                # Full documentation ← READ THIS
├── SETUP_SUMMARY.py         # This setup summary
├── examples/
│   ├── README.md            # Example walkthrough
│   ├── basic_semi.py        # Email domain, text classification
│   ├── apache_log_simple.py # Data-driven with real logs
│   └── data/
│       ├── sample_logs.txt  # Test data
│       ├── sample.csv       # CSV test data
│       └── requirements.txt # Gist dependencies (pandas, numpy)
└── docker/
    └── Dockerfile.gist      # Python 3.11 + deps
```

## Key Concepts

### Gist

A **gist** is a minimal, self-contained script:
```python
import some_module

def generated_function(arg1, arg2):
    # Generated code here
    return result

__GIST_RESULT__ = generated_function(value1, value2)
print("__GIST_RESULT__", repr(__GIST_RESULT__), flush=True)
```

Why?
- No framework imports (testbed not in gist)
- No user infrastructure code (isolated)
- Can run in subprocess or any container
- Result extraction via marker in stdout

### Validation

Three stages:
1. **Syntax**: `ast.parse()` — OK if parses
2. **Execution**: run gist in sandbox — OK if completes without exception
3. **Type**: result matches `expected_type` — OK if `isinstance(result, expected_type)`

### Results

`SimpleInferenceResult` has:
- `success`: Did it all work?
- `compiled_function`: The actual callable
- `source_code`: Full generated Python
- `gist_source`: Minimal executable
- `execution_stdout/stderr`: Sandbox output
- `error`: Failure reason

## Troubleshooting

### API Key Issues

```bash
# Check it's set
echo $OPENROUTER_API_KEY

# Set it
export OPENROUTER_API_KEY='sk-...'

# Or in Python
import os
os.environ['OPENROUTER_API_KEY'] = 'sk-...'
```

### Docker Not Running

```bash
# Start Docker daemon
sudo systemctl start docker  # Linux
open --applications Docker    # macOS
```

### Generation Timeout or Slow

- LLM calls are slow (network I/O)
- Increase `timeout` in config if gist execution is slow
- Some models are slower than others (`temperature`, `max_tokens`)

### Gist Execution Fails

```python
result = infer_semiformal(...)

# Check what was generated
print(result.gist_source)

# Run it manually to debug
with open("debug_gist.py", "w") as f:
    f.write(result.gist_source)

# Then run
# python debug_gist.py  (or docker run if using Docker)
```

## Next Steps

1. **Run the examples** → understand the workflow
2. **Read README.md** → full API documentation
3. **Experiment with specs** → try your own use cases
4. **Test Docker** → verify gist execution in container
5. **Integrate kernel gateway** → once gist flow is solid
6. **Deploy** → to your infrastructure (K8s, etc.)

## Questions?

- **Architecture**: See `CLAUDE.md` in repo root
- **Detailed docs**: See `semipy_testbed/README.md`
- **Examples**: See `semipy_testbed/examples/README.md`
- **Code**: All modules are self-contained and well-commented

## Remember

This testbed is **intentionally minimal**. You're not working with:
- Version control / commits / DAGs
- Portal persistence / dispatch modules
- Reactivity / dependency graphs
- Console UI / streaming displays
- Decision logic (REUSE/ADAPT/GENERATE)

You're working with:
- **Specs** → LLM → **Generated code** → **Gist** → **Execution** → **Result**

That's it. Clean. Simple. Perfect for infrastructure work.

Good luck!
