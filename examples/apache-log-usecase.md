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
| `apache_log_semiformal_stages.py` | Single-file demo: formal helpers + `ApacheLogPipeline` with two `@semiformal` methods (`#>` blocks + optional `#<` reasoning), six documented stages (stage 6 is a manual spec-refinement workflow) |
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

**`#>` vs `#<`:** Lines starting with `#>` are the **durable natural-language spec**
for a STATEMENT_BLOCK (hashed into `spec_text` / `spec_equivalence_key`). Lines
starting with `#<` are **reasoning surfaces** the pipeline may insert after
generation; they are stripped when lowering so they do not change slot identity.
You can **promote** content from `#<` to `#>` to lock it into the contract (see
STAGE 6).

Two specs, two different styles:

1. **`classify_body`** -- `@semiformal` decorator with `#>` STATEMENT_BLOCK.
   The spec text is static; runtime variation flows through the `body` parameter.
   The slot generates a keyword-based classifier once (GENERATE), then reuses it
   for every subsequent body (REUSE + verify).
   - Source: [`semipy/decorator.py`](../semipy/decorator.py) (decorator),
     [`semipy/lowering.py`](../semipy/lowering.py) (scan `#>` blocks)

2. **`infer_templates`** -- `@semiformal` with a `#>` STATEMENT_BLOCK (ellipsis
   assignment to `templates`). The **spec text** is the contiguous `#>` comment
   block plus inline `#>` tails; it is **static** in source. The **family set and
   example bodies** arrive as the `bodies` parameter, so new data still resolves
   to REUSE with the same `spec_equivalence_key`, then verify runs unless the
   runtime input fingerprint matches the commit:
   - Same call site, same `bodies` shape/content fingerprint -> REUSE (may skip verify)
   - Same call site, different grouped bodies -> REUSE + verify
   - Verify passes -> cached function runs on the new data
   - Verify fails -> ADAPT (regenerate with parent source + failure context)
   - Source: [`semipy/lowering.py`](../semipy/lowering.py) (`#>` blocks),
     [`semipy/resolver.py`](../semipy/resolver.py),
     [`semipy/slot_resolver.py`](../semipy/slot_resolver.py)

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
| Edited `#>` spec text (e.g. promoted a line from `#<`) | new `spec_equivalence_key` | GENERATE or ADAPT |
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

### STAGE 6 -- Promote `#<` reasoning into durable `#>` spec (manual)

This stage is **not** a separate CLI flag; it is the workflow for turning
**inference annotations** into **contract text** the pipeline will always see.

After a GENERATE (or ADAPT), the agent may write **reasoning surfaces** into your
source as `#< ...` comments via [`semipy/agents/skeleton_writer.py`](../semipy/agents/skeleton_writer.py).
Those lines are **not** part of the slot’s `spec_text`: lowering only hashes
contiguous `#>` lines (and inline `#>` tails) for the STATEMENT_BLOCK. Before each
run, `#<` lines are **stripped** to single-character `#` placeholders in
[`strip_skeleton_lines`](../semipy/lowering.py) so line numbers and slot
**ordinals** stay stable without minting a new slot id when annotations churn.

**Why promote?** In the Apache demo, `classify_body` might get `#< [But] prefer
specific mod_jk signatures before generic worker init` after the first compile.
That is useful narrative, but it is **ephemeral** in the sense above until you
**commit it to the spec**: add the same idea as a **`#>` line** in the same
contiguous `#>` block as the main classifier prompt (or change a `#<` prefix to
`#>` **if** that line sits next to other `#>` lines so it becomes part of the
block). Then:

- The text is **preserved** as user spec: the skeleton writer does not overwrite
  `#>` lines; only `#<` is managed.
- `spec_text` changes, so `spec_equivalence_key` changes: the next run is a new
  contract---typically **GENERATE** if you cleared the cache, or **ADAPT** when
  verification fails against the old implementation under the new spec.

**Concrete SRE motivation**: you reviewed misclassified bootstrap bodies and
want the classifier to treat **scoreboard** lines and **mod_jk child** lines as
distinct families before falling back. You move that disambiguation from a `#<`
note into a `#>` line so every future generation and adaptation sees it as part
of the formal NL contract, not as optional commentary.

Re-run classification after editing (e.g. `uv run python apache_log_semiformal_stages.py --fresh --stage 1`) so the portal picks up the richer `#>` spec.

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
| [`semipy/agents/skeleton_writer.py`](../semipy/agents/skeleton_writer.py) | Optional post-commit write of `#<` reasoning lines into user source |
| [`semipy/runtime_fingerprint.py`](../semipy/runtime_fingerprint.py) | `compute_runtime_input_fingerprint`: hash runtime values for verify gating |
| [`semipy/session_anchor.py`](../semipy/session_anchor.py) | Portal anchor for Jupyter (`ipykernel`) vs file-backed sessions |
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
