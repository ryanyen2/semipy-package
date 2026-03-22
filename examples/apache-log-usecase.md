# Apache Log Parsing -- Semiformal Use Case

## Framing

**SRE onboarding a new Apache deployment into a structured observability pipeline.**

The real problem:

- Raw Apache error logs are useful for humans but downstream systems need structured events.
- Each deployment has a slightly different message ecology because enabled modules differ.
- Hand-authoring all regexes is expensive; line-by-line LLM classification is unstable and costly.
- The engineer compiles a parser from a local bootstrap slice, then reuses it for all future logs.

## Pipeline decomposition

The original version had one monolithic `semi()` call doing family discovery, regex
generation, field typing, ordering, and fallback policy all at once. The improved version
breaks the semiformal logic into two focused inferential sites inside an otherwise
formal pipeline:

| Stage | Formal / Semiformal | What it does |
|-------|---------------------|--------------|
| Prefix parsing | Formal (regex) | Extracts timestamp, level, body from each line |
| Body classification | **Semiformal** (`semi()`) | Classifies each body into a short event family name |
| Template generation | **Semiformal** (`semi()`) | Generates regex patterns with named groups per family |
| Compiled parser | Formal (regex) | Deterministic batch parsing with the generated templates |
| Runtime execution | Formal | Batch parse, status counts, failure buckets |

Each `semi()` call does one focused thing. The formal stages are unchanged by any
`semi()` regeneration -- they only depend on the compiled artifacts.

## Key demonstrations (notebook)

### GENERATE

First run: no cached implementation exists. The agent creates a classification function
and a template generator. Console shows "No reusable implementation; creating a new one."

### REUSE

Re-run the same cell or call the same template from a different cell. The
`spec_equivalence_key` matches a donor slot. Console shows "Using a matching cached
implementation." No LLM call, instant return.

### Runtime context independence

Changing host labels, deployment names, or log dates does not change the parser or
trigger recompilation. Same compiled artifact, different metadata.

### Edge cases and UNSEEN_TEMPLATE

When new log patterns arrive (client access errors, SSL failures, bind errors), the
narrow parser correctly reports them as `UNSEEN_TEMPLATE` -- it does not guess.

### Wider bootstrap

The engineer widens the bootstrap to include new patterns and updates the prompt context.
A different prompt template triggers a new GENERATE for a wider classification function.
The new templates cover the additional families. The formal parser is rebuilt.

### Comparison

Side-by-side status counts from the narrow vs wide parser show coverage improvement.

## Files

| File | Purpose |
|------|---------|
| `apache_log_compiler.py` | Simplified module: `classify_body`, `infer_templates`, `CompiledParser`, `build_parser` |
| `apache_log_demo.ipynb` | Interactive walkthrough notebook demonstrating all stages |
| `data/Apache_2k.log` | 2000 lines of Apache error log data |

## Data patterns in Apache_2k.log

The first ~120 lines contain three body families:

1. `workerEnv.init() ok /etc/httpd/conf/workers2.properties`
2. `mod_jk child workerEnv in error state N`
3. `jk2_init() Found child NNNN in scoreboard slot NN`

Later lines introduce additional patterns:

4. `jk2_init() Can't find child NNNN in scoreboard` (rare, ~line 785)
5. `mod_jk child init N -N` (very rare, ~line 796)
6. `[client IP] Directory index forbidden by rule: /path/` (scattered from ~line 773)

This natural distribution makes the data ideal for demonstrating:

- Narrow bootstrap captures families 1-3 only
- Full-dataset parse reveals families 4-6 as UNSEEN_TEMPLATE
- Wider bootstrap + updated prompt covers all families
