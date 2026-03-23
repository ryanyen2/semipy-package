# Apache Log Parsing -- Semiformal Use Case

## Framing

**SRE onboarding a new Apache deployment into a structured observability pipeline.**

Raw Apache error logs are useful for humans but downstream systems need structured
events. Each deployment has a slightly different message ecology because enabled
modules differ. Hand-authoring all regexes is expensive; line-by-line LLM
classification is unstable and costly.

The semiformal approach: compile a parser from a local bootstrap slice using two
LLM-generated slots, then reuse the compiled parser deterministically for all
future logs. When new patterns appear, extend the data and let the system verify
or regenerate.

## Files

| File | Purpose |
|------|---------|
| `apache_log_semiformal_stages.py` | Single-file demo: formal helpers + `ApacheLogPipeline` class with `@semiformal` and `semi()` slots, five stages |
| `data/Apache_2k.log` | 2000-line Apache error log |

## Pipeline decomposition

The pipeline has two layers:

### Formal layer (no LLM, deterministic)

- **Prefix regex** (`APACHE_ERROR_PREFIX`): extracts timestamp, level, and body
  from each log line. Lines that fail this regex get `PREFIX_FAIL`.
- **`CompiledParser`**: takes a list of `{family, pattern, fields}` dicts and
  applies them to body strings. Reports `OK`, `UNSEEN_TEMPLATE`, or `AMBIGUOUS`
  per line. No LLM invocation at parse time.
- **Error reporting**: malformed lines are handled entirely by the formal parser.
  The classifier is never re-invoked for error lines.

### Semiformal layer (LLM on first call, then cached)

Two specs, two different styles:

1. **`classify_body`** -- `@semiformal` decorator with `#>` STATEMENT_BLOCK.
   The spec text is static; runtime variation flows through the `body` parameter.
   The slot generates a keyword-based classifier once (GENERATE), then reuses it
   for every subsequent body (REUSE + verify).
   - Source: [`semipy/decorator.py`](../semipy/decorator.py) (decorator),
     [`semipy/lowering.py`](../semipy/lowering.py) (scan `#>` blocks)

2. **`infer_templates`** -- standalone `semi()` with f-string EXPRESSION.
   The prompt text changes when the family set changes (different `families_text`
   value interpolated into the f-string). However, the static template parts and
   variable name (`families_text`) remain the same, so the
   `spec_equivalence_key` stays constant. This means:
   - Same call site, same input -> REUSE (same fingerprint, skip verify)
   - Same call site, different input -> REUSE + verify (fingerprint differs)
   - Verify passes -> reuse the cached function with new data
   - Verify fails -> ADAPT (regenerate from the parent implementation)
   - Source: [`semipy/semi_fn.py`](../semipy/semi_fn.py) (template decomposition),
     [`semipy/resolver.py`](../semipy/resolver.py) (REUSE / ADAPT / GENERATE logic),
     [`semipy/slot_resolver.py`](../semipy/slot_resolver.py) (verify + fingerprint)

## Decision map

```
                  +------------------+
                  | execute_slot()   |
                  +--------+---------+
                           |
                    load portal, resolve
                           |
              +------------+------------+
              |                         |
        has commit?                no commit
              |                         |
         REUSE path               GENERATE path
              |                    (new LLM call)
     compute fingerprint               |
              |                   agent.generate()
     +--------+--------+               |
     |                  |          validate + commit
  same fp?          different fp        |
     |                  |          write dispatch
  skip verify     verify_runtime        |
     |            _execution()     return result
  return result         |
              +---------+---------+
              |                   |
          passed?             failed?
              |                   |
         return result       force_regenerate
                                  |
                            resolve(force=True)
                                  |
                           ADAPT / GENERATE
                           (with parent source
                            + failure context)
```

### Decision summary

| Situation | Decision | LLM call? |
|-----------|----------|-----------|
| First body classification | GENERATE | Yes (once) |
| Same template, same input fingerprint | REUSE (skip verify) | No |
| Same template, different input values | REUSE + verify | No |
| Verify fails (execution error, type mismatch, empty output) | ADAPT | Yes |
| f-string text changes (new families in prompt) | same slot (static template unchanged) | depends on verify |
| New call site, same `spec_equivalence_key` | REUSE (donor slot) | No |
| Malformed log lines | Formal parser (PREFIX_FAIL) | No |
| Body has no matching regex | Formal parser (UNSEEN_TEMPLATE) | No |
| Body matches multiple regexes | Formal parser (AMBIGUOUS) | No |

## Stages

### STAGE 1 -- Classify bootstrap bodies

Classify the 50 unique bodies from the first 120 lines. The first body triggers
GENERATE (LLM creates a keyword-based classifier). All remaining bodies REUSE
the cached implementation with runtime verification per input.

**Expected output**: 3 families (`jk_error`, `scoreboard_found`, `worker_init`).

### STAGE 2 -- Generate regex templates

Call `infer_templates` with the 3-family grouping. First call triggers GENERATE
(LLM creates a function that parses the families text and builds regex patterns
with named capture groups).

**Expected output**: 3 templates with named captures for variable parts.

### STAGE 3 -- Formal parse

Apply the compiled parser to all 2000 lines. No LLM calls.

**Expected output**: ~1944 OK, ~56 UNSEEN_TEMPLATE (patterns from later in the
log that the narrow bootstrap didn't cover).

### STAGE 4 -- Extension

Re-run the full pipeline with edge case lines appended. `classify_body` REUSE's
for all bodies (including new ones). `infer_templates` is called with the
expanded family grouping -- same slot, different runtime values -> REUSE + verify.

If the reused function handles the expanded input, verify passes and the result
covers all families. If it fails, the system would ADAPT (regenerate from the
parent implementation with the failure context).

**Expected output**: ~2005 OK (extended templates cover all families).

### STAGE 5 -- Error reporting

Pass deliberately malformed lines through the formal parser. The classifier is
NOT re-invoked. The formal parser reports PREFIX_FAIL for unparseable lines.

**Expected output**: 4 PREFIX_FAIL for bad lines, rest unchanged.

## Data patterns in Apache_2k.log

The first ~120 lines contain three body families:

1. `workerEnv.init() ok /etc/httpd/conf/workers2.properties`
2. `mod_jk child workerEnv in error state N`
3. `jk2_init() Found child NNNN in scoreboard slot NN`

Later lines introduce additional patterns:

4. `jk2_init() Can't find child NNNN in scoreboard` (rare, ~line 785)
5. `mod_jk child init N -N` (very rare, ~line 796)
6. `[client IP] Directory index forbidden by rule: /path/` (scattered from ~line 773)

Edge cases added in STAGE 4:

7. `[client IP] File does not exist: /path/`
8. `(98)Address already in use: make_sock: could not bind to address ...`
9. `mod_ssl: SSL handshake failed for client IP`
10. `[client IP] Invalid URI in request ...`
11. `Apache/2.0.54 configured -- resuming normal operations`

## Key source files

| File | Role |
|------|------|
| [`semipy/decorator.py`](../semipy/decorator.py) | `@semiformal` decorator, `#>` block scanning |
| [`semipy/lowering.py`](../semipy/lowering.py) | AST lowering: scan `#>` blocks, create `SlotSpec`, build scaffold |
| [`semipy/semi_fn.py`](../semipy/semi_fn.py) | `semi()` function, template decomposition, call site identification |
| [`semipy/resolver.py`](../semipy/resolver.py) | Resolution logic: REUSE / ADAPT / GENERATE based on equivalence keys |
| [`semipy/slot_resolver.py`](../semipy/slot_resolver.py) | `execute_slot`: load portal, verify, call agent, commit |
| [`semipy/agents/agent.py`](../semipy/agents/agent.py) | `SemiAgent.generate`: build prompt, stream LLM, validate |
| [`semipy/agents/validator.py`](../semipy/agents/validator.py) | `validate` (generation time) and `verify_runtime_execution` (reuse time) |
| [`semipy/agents/slot_call.py`](../semipy/agents/slot_call.py) | `bind_slot_arguments`, `invoke_slot`: positional fallback for name mismatch |
| [`semipy/runtime_fingerprint.py`](../semipy/runtime_fingerprint.py) | `compute_runtime_input_fingerprint`: hash runtime values for verify gating |
| [`semipy/history/version_control.py`](../semipy/history/version_control.py) | Commit, Slot, Portal DAG, branch management |

## Running

```bash
cd examples

# Fresh run through all stages
uv run python apache_log_semiformal_stages.py --fresh --stage 3

# Extension (uses cached implementations from stage 3)
uv run python apache_log_semiformal_stages.py --stage 4

# Error reporting
uv run python apache_log_semiformal_stages.py --stage 5

# Full trace of LLM prompts, reasoning, and tool calls
SEMIPY_PIPELINE_TRACE=1 uv run python apache_log_semiformal_stages.py --fresh --stage 2
```
