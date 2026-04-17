# Semipy Testbed Examples

This directory contains example scripts that demonstrate the core functionality of semipy_testbed.

## Quick Reference

| Example | What It Shows | Difficulty |
|---------|---------------|-----------|
| `basic_semi.py` | Simple text processing, type handling | Beginner |
| `apache_log_simple.py` | File I/O, data-driven generation, real data | Intermediate |

## Running Examples

### Prerequisites

```bash
export OPENROUTER_API_KEY='sk-...'  # Set your API key
cd /path/to/semipy-package
```

### Example 1: Basic Semi (Text Processing)

```bash
python semipy_testbed/examples/basic_semi.py
```

**What happens:**
1. Generates a function to extract email domain
2. Generates a function to classify text
3. Generates a function to parse JSON
4. Tests each with multiple inputs

**Output format:**
```
============================================================
EXAMPLE 1: Extract Email Domain
============================================================
[TESTBED] Inference starting...
[TESTBED] Building generation prompt...
[TESTBED] Generated code (847 chars)
[TESTBED] Syntax OK
[TESTBED] Building gist...
[TESTBED] Gist built, function=extract_email_domain
[TESTBED] Validating execution...
[TESTBED] Validation passed!
[TESTBED] Inference complete! Function compiled: extract_email_domain

--- Result ---
Success: True
Function signature: ('email',)

Testing with various inputs:
  alice@example.com                 -> example.com
  bob.jones@company.org             -> company.org
  invalid-email                     -> 
  charlie@sub.domain.co.uk          -> sub.domain.co.uk
```

**What to observe:**
- Does the LLM understand the task?
- Are results what you expected?
- Are edge cases handled?

### Example 2: Apache Log Classification (Data-Driven)

```bash
python semipy_testbed/examples/apache_log_simple.py
```

**What happens:**
1. Loads Apache error logs from `data/sample_logs.txt`
2. Extracts event bodies (removes timestamp/level)
3. Generates a classifier function
4. Classifies all logs into families
5. Shows summary by family

**Output format:**
```
======================================================================
EXAMPLE: Apache Log Classification with Data Files
======================================================================
Loaded 10 log lines from sample_logs.txt
Extracted 10 event bodies

Sample bodies:
  - mod_fcgid: read timeout from pipe
  - PHP Fatal error: Out of memory
  - Apache/2.4.41 (Ubuntu) configured

Grouped into 3 families
  - mod_fcgid: 1 items
  - PHP: 1 items
  - Apache: 1 items

[TESTBED] Inference starting...
[TESTBED] Building generation prompt...
[TESTBED] Generated code (1250 chars)
[TESTBED] Syntax OK
[TESTBED] Gist built, function=classify_body
[TESTBED] Validation passed!

--- Result ---
Success: True

Classifying all extracted bodies:
Classification summary (7 families):
  access_denied       :  1 items
    Example: permission denied: /var/www/restricted
  connection          :  3 items
    Example: Failed to connect from socket
  memory              :  1 items
    Example: PHP Fatal error: Out of memory
  modsecurity         :  1 items
    Example: ModSecurity: Exec of tag allowed
  ssl                 :  1 items
    Example: SSL: Certificate verification failed
  startup             :  2 items
    Example: Apache/2.4.41 (Ubuntu) configured
  system              :  1 items
    Example: Suexec call failed
```

**What to observe:**
- How does context (user data) help the LLM?
- Are classifications meaningful?
- Does the function generalize to new logs?

## Understanding the Output

### Verbose Mode

When `verbose=True`, you see the pipeline in real time:

```
[TESTBED] Inference starting...            # Pipeline start
[TESTBED] Building generation prompt...    # Prompt construction
[TESTBED] Generated code (847 chars)       # LLM response received
[TESTBED] Syntax OK                        # AST validation passed
[TESTBED] Building gist...                 # Assembling minimal executable
[TESTBED] Gist built, function=fn_name    # Gist ready
[TESTBED] Validating execution...         # Running gist in sandbox
[TESTBED] Validation passed!              # Execution successful
[TESTBED] Inference complete! Function compiled: fn_name  # Done
```

### Result Object

Each example prints the `SimpleInferenceResult`:

```
Success: True                           # Did inference succeed?
Function signature: (...)              # Parameter names
Testing with various inputs:
  alice@example.com -> example.com    # Function works!
```

If `Success: False`, check the `error` field:

```
Success: False
Error: Generated code has syntax error: Line 5: invalid syntax
```

## Troubleshooting

### "OPENROUTER_API_KEY not set"

```bash
export OPENROUTER_API_KEY='your-api-key-here'

# Or set it in .env file
echo "OPENROUTER_API_KEY=sk-..." >> .env
source .env
```

### "ModuleNotFoundError: No module named 'semipy_testbed'"

Make sure testbed is on the path:

```bash
cd /path/to/semipy-package
export PYTHONPATH="$PWD:$PYTHONPATH"
python semipy_testbed/examples/basic_semi.py
```

### "Connection error" or "API error"

The OpenRouter API is not accessible. Check:
1. Internet connection is working
2. API key is valid (not expired)
3. API rate limits haven't been reached

### Generated function returns unexpected type

Specify `expected_type` in the call:

```python
result = infer_semiformal(
    user_spec="...",
    expected_type=dict,  # Make sure type matches
    ...
)
```

### Gist execution timeout

Increase the timeout:

```python
from semipy_testbed import configure
configure(timeout=60)  # 60 seconds instead of 30
```

## Customizing Examples

### Use different LLM model

```python
from semipy_testbed import configure

configure(model="openai/gpt-4o")  # Use GPT-4o via OpenRouter
```

### Add your own test data

```python
# In apache_log_simple.py, add to the bodies list:
bodies = [
    "Your custom log line here",
    "Another log line",
    # ... etc
]
```

### Use Docker for execution

```python
from semipy_testbed import configure

configure(use_docker=True)

result = infer_semiformal(
    user_spec="...",
    ...
)
```

## Performance Tips

1. **First run is slower** — API call + validation takes 3-5s
2. **CPU vs IO bound** — Most time is waiting for API, not computation
3. **Gist execution** — Subprocess (default) is fast (<1s); Docker adds overhead
4. **Temperature** — Lower temperature (0.5) is faster and more deterministic

## Next Steps

After running the examples:
1. Try the **quick test** script: `python semipy_testbed/run_quick_test.py`
2. Modify the specs and test custom LLMs
3. Add your own data files and examples
4. Integrate into your infrastructure (docker, kernel gateway, etc)

## Questions?

See the main [README.md](../README.md) for detailed API documentation.
