# The Behavioral Contract Subsystem

> Module: `semipy/contract/`
> Gates: `semipy/slot_resolver.py`
> Config: `semipy/agents/config.py`

The behavioral contract is `semipy`'s mechanism for **version-controlling the
behavior** of an LLM-generated function — not just its source, but *why* each
regeneration happened and *what effect* it had on observed behavior. Where the
Merkle DAG in `semipy/history/` records the *implementation* of every commit,
the behavioral contract records the **durable, accumulating set of properties
that implementation must keep satisfying**, together with provenance for each
property. The guiding invariant is *never forget*: a later regeneration cannot
silently drop a decision an earlier one made.

This document is grounded in the code as it actually runs. Treat it as
authoritative over informal descriptions elsewhere.

---

## 1. Motivation

A semiformal slot compiles a natural-language specification into a concrete
Python function on first call, then reuses it. The function is *generated*, so
it is also *re-generatable*: when a new input pattern fails verification, or a
new format arrives, the pipeline can ADAPT — regenerate the function with extra
context. Regeneration is the source of two failure modes that plague any
LLM-in-the-loop system:

1. **Silent forgetting.** The previous implementation handled date format `A`
   correctly. A new row in format `B` triggers ADAPT. The model, focused on
   `B`, emits a function that handles `B` but quietly breaks `A`. Nothing in a
   naive pipeline notices: the new function passes its own validation, gets
   committed, and the regression ships.

2. **Lost rationale.** A commit message says `"adapted for new input"`. Six
   regenerations later nobody — human or model — knows *which* decisions were
   load-bearing, so each regeneration re-litigates them from scratch.

The behavioral contract closes both gaps. Each slot carries an executable,
content-addressed set of **cases** — properties derived from prior decisions —
plus a **change record** per commit that classifies every behavior change as
intended or a regression. Cases are *carried forward*: a regeneration is
checked against them before it is allowed to commit. The system literally
cannot forget a decision it has recorded as a case.

---

## 2. The Data Model

A `SlotContract` (`semipy/contract/models.py`) is the per-slot container. It
holds a `version` counter and a dict of `ContractCase`s keyed by a
content-addressed `case_id`. It is persisted on `Slot.contract` and serialized
in `store.py`; `contract/access.py` is the only bridge between the persisted
dict and the live object (`get_contract` / `save_contract`).

A `ContractCase` is exactly one of three **kinds**:

| `kind`        | What it asserts                                                                 |
| ------------- | ------------------------------------------------------------------------------- |
| `invariant`   | A structural property from a fixed, data-agnostic vocabulary (see below).       |
| `metamorphic` | A named relation: output is unchanged under a meaning-preserving input transform. |
| `example`     | A pinned `input -> output` (golden master / characterization). Used sparingly.  |

### The invariant vocabulary

`INVARIANT_NAMES` in `models.py` is closed and deliberately small:

```python
INVARIANT_NAMES = (
    "non_empty",            # output is not None / not an empty container
    "non_identity",         # output is not a verbatim echo of the input
    "type_match",           # output is the expected (builtin) type
    "category_preserving",  # output keeps the same structural shape
    "idempotent",           # f(f(x)) == f(x)
)
```

`non_empty` / `non_identity` / `type_match` promote the validator's *transient*
runtime guards (`empty_output` / `identity_return` / `type_mismatch`) into
*persisted, carried-forward* cases. The vocabulary is intentionally
case-independent and data-agnostic: there is no keyword list, no per-dataset
logic. `metamorphic` relations come from a closed registry (Section 4),
likewise data-agnostic.

### Status lifecycle

Every case has a `status` of `active`, `superseded`, or `quarantined`:

```
            supersede(old, new, why)                 quarantine(id, why)
  active ───────────────────────────► superseded     active ──────────────► quarantined
   │  (deliberate behavior change;                       (kept, not enforced;
   │   audit trail preserved)                             e.g. spec changed,
   ▼                                                      cap reached, unsatisfiable)
```

Only `active` cases are enforced (`SlotContract.active()`). Superseded and
quarantined cases are **never deleted** — they remain in `cases` for the audit
trail. `supersede` (`models.py`) marks the old case `superseded`, records
`superseded_by` and the `supersede_reason`, and adds the replacement;
`quarantine` marks a case inert with a reason.

### Content addressing

A case is identified by the hash of *three* things — its kind, the **input
pattern** it concerns, and the **assertion** it makes:

$$
\text{case\_id} = H\bigl(\text{kind} \,\Vert\, \text{input\_fingerprint} \,\Vert\, \text{assertion\_key}\bigr)
$$

In `compute_case_id` (`models.py`) this is `sha256(...)[:16]` over
`f"{kind}\0{input_fingerprint}\0{akey}"`, where `akey` is built by
`_assertion_key`:

| `kind`        | `assertion_key`                                     |
| ------------- | --------------------------------------------------- |
| `example`     | `example:{expected_type}:{expected_repr}`           |
| `invariant`   | `invariant:{invariant}:{expected_type}`             |
| `metamorphic` | `metamorphic:{relation}:{sorted relation_param}`    |

The payload — `input_sample`, `reason`, `effect`, `decision`,
`origin_commit_id`, timestamps — is **not** hashed. The consequence is
**idempotent re-seeding**: deriving "the same assertion over the same input
pattern" twice yields the same `case_id`, so `SlotContract.add` refreshes the
existing active case (updating only `reason`/`effect`/`updated_ts`) instead of
duplicating it. This is what lets the maintainer run after every commit without
the contract bloating.

Each case also carries the **provenance** that answers *why*: `reason` (the
triggering failure or usage), `effect` (what behavior it pins), `decision`
(`GENERATE`/`ADAPT`/...), and `origin_commit_id`.

---

## 3. The Structural Input Fingerprint

`structural_input_fingerprint` (`semipy/contract/fingerprint.py`) is the single
source of truth for "same input pattern". It is what makes one case cover a
*family* of concrete inputs and what lets the effect-diff reason by pattern
rather than by exact value. (`slot_observations._obs_content_fingerprint`
delegates to it.)

The core is **digit normalization** in `normalize_token`:

```python
_DIGIT_RE = re.compile(r"\d+")        # any run of digits
def normalize_token(value, *, prefix_len=24):
    return _DIGIT_RE.sub("N", str(value))[:prefix_len]
```

Every maximal run of digits collapses to a single `N`, and the result is
truncated to 24 characters. Two values that differ only in numeric content —
IDs, dates, IPs — normalize to the *same* token.

### Worked example: two dates, one bucket

Consider a slot with a single free variable `raw` and two observed inputs:

```
raw = "03/14/2025"   and   raw = "03/20/2025"
```

`normalize_token` rewrites each digit run to `N`:

```
"03/14/2025"  ──►  "N/N/N"
"03/20/2025"  ──►  "N/N/N"
```

The per-row fingerprint joins `f"{k}={normalize_token(v)}"` over the
(non-internal, free-variable-restricted, sorted) keys and hashes the result:

$$
\text{fp} = \operatorname{sha256}\bigl(\texttt{"raw=N/N/N"}\bigr)[:16]
$$

Both dates produce the identical string `raw=N/N/N`, hence the **same**
fingerprint. They land in **one** pattern bucket. A `type_match(str)` case
seeded from `03/14/2025` automatically governs `03/20/2025` too — no second
case is minted, and the effect-diff treats a change on either as a change to the
same pattern.

Implementation details that matter:

- Keys are restricted to the slot's `free_variables` when any are present, so
  the fingerprint reflects only the values the slot consumes.
- Internal keys (`self`, anything starting with `_`) are dropped
  (`_is_internal_key`).
- An empty input maps to a fixed sentinel fingerprint.

Note this is a *coarsening*: a format change from `03/14/2025` to `2025-03-14`
yields a **different** token (`N-N-N` vs `N/N/N`) and therefore a different
bucket — which is exactly the case Section 9 walks through.

---

## 4. The Executable Contract

The contract is not documentation; it runs. `run_contract`
(`semipy/contract/runner.py`) executes a slot's active cases against a candidate
implementation and reports which prior decisions it violates. No LLM is
involved.

### One rich-record gist

`_build_contract_gist` emits a standalone script that imports the candidate
source, runs it over every case's `input_sample`, and prints a **rich
per-row record** as JSON:

```python
_rec = {
  'error':     <exception name or None>,
  'type':      type(eff).__name__,
  'repr':      repr(eff)[:600],
  'is_empty':  eff is None or (has __len__ and len == 0),
  'eq_primary': isinstance(eff,str) and eff.strip() == primary.strip(),
  'json':      json.dumps(eff, default=str),
  'shape':     'dict:k1,k2,...'  or  type name,
}
```

The whole batch runs in **one subprocess** — a second pass runs only when
*relational* cases (`idempotent`, `metamorphic`) are present, because those need
the function applied to a transformed input as well.

### Projection to the effective output

A subtle but load-bearing detail: a single-output `STATEMENT_BLOCK` slot returns
`{name: value}`, but the caller consumes `value`. The runner threads
`output_names` into the gist and projects:

```python
_eff = _out
if _err is None and isinstance(_out, dict) and len(_OUTPUT_NAMES) == 1 and _OUTPUT_NAMES[0] in _out:
    _eff = _out[_OUTPUT_NAMES[0]]
```

So `non_empty` / `non_identity` / `type_match` check the value downstream code
actually uses, not the dict wrapper (which would defeat the guard — the wrapper
is never empty and is always a `dict`).

### Assertion evaluation

`_eval_single` evaluates the non-relational cases against a record:

- **An exception on a previously-working input fails *every* invariant** — a
  raise on a recorded input is a regression no matter which property the case
  pins.
- `non_empty` → fails iff `is_empty`.
- `non_identity` → fails iff the output equals the input *and* the input is a
  string of at least `_IDENTITY_MIN_LEN = 9` stripped characters (mirrors the
  validator, avoiding false positives on short canonical outputs).
- `type_match` → fails iff `rec['type'] != expected_type`.
- `category_preserving` → fails iff `rec['shape']` changed.
- `example` → fails iff the type *or* the repr differs from the pinned values.

Relational cases are checked in the second pass: `metamorphic` compares the base
record's `json` against the transformed run's `json` (must be equal);
`idempotent` re-feeds the output and compares (only when the output type is
`str`, i.e. the same type as the input). `_values_equal` treats any errored run
as unequal.

### Violation → existing ADAPT route

The key piece of plumbing: a violated case is mapped to a validator
`failure_kind` via `_FAILURE_KIND`, so the existing `RoutingPolicy` ADAPT route
consumes it **with no new routing code**:

```python
_FAILURE_KIND = {
    "non_empty":            "empty_output",
    "non_identity":         "identity_return",
    "type_match":           "type_mismatch",
    "category_preserving":  "type_mismatch",
    "idempotent":           "type_mismatch",
    "example":              "type_mismatch",
    "metamorphic":          "type_mismatch",
}
```

`ContractRunResult.as_validation_result()` packages the first failure as a
`ValidationResult(passed=False, failure_kind=...)` — a drop-in for the
verify-failure path. Each `CaseFailure.message` is phrased to feed the
regeneration prompt directly: *"Contract case [type_match] violated: ... This
case exists because: ..."*, surfacing the recorded rationale to the model.

**Safety principle:** the runner *never blocks on an inability to test*. If a
case input cannot be rehydrated, if the gist fails to run, or if the record
count mismatches, the case is **skipped**, not failed (`run_contract` returns
`passed=True` rather than spuriously forcing ADAPT).

---

## 5. Effect Tracing

`compute_effect_diff` (`semipy/contract/change.py`) answers *what did this change
actually do?* It runs **both** the parent implementation and the new one over
the **union of contract-case inputs** (deduplicated by fingerprint), then
classifies every changed output.

For each input pattern $i$, let $\text{old}_i$ and $\text{new}_i$ be the parent
and candidate outputs (serialized via the gist's `json`, or `error:<name>` on a
raise). A change is recorded iff $\text{old}_i \neq \text{new}_i$. For each such
change:

$$
\text{intended}(i) \;=\; \bigl(\text{fp}_i = \text{fp}_{\text{trigger}}\bigr)
\;\lor\; \text{parentWasWrong}(i)
$$

where

$$
\text{parentWasWrong}(i) \;=\;
\bigl(\text{parent raised on } i\bigr)
\;\lor\;
\bigl(\text{parent's own recorded case failed on } i\bigr).
$$

In code:

```python
intended = (fp == triggering_fp) or parent_was_wrong
if not intended:
    unintended += 1
```

The second disjunct (`parent_was_wrong`) is evaluated for `example`/`invariant`
cases by re-running `_eval_single` against the parent record: if the parent
*violated its own recorded case* on input $i$, then changing the output there is
a fix, not a regression.

Every changed pattern becomes an `EffectDiffEntry(input_fingerprint, input_repr,
old_repr, new_repr, intended)`. These roll up into a `ChangeRecord` with
`unintended_count` and `n_compared`, stored on `Commit.change_record` as the
**real "what changed" provenance** — replacing the generic commit message. Its
`summary()` reads e.g. *"ADAPT: 1 intended, 0 unintended over 3 input
pattern(s)"*, and `regression_summary` renders the unintended entries into a
corrective instruction for the regeneration prompt (*"Preserve the previous
output for these: ..."*).

`portal_inspect.py` surfaces the head commit's record as
`change: <reason> | effect=+X changed, Y unintended`.

---

## 6. The Two Gates

Two hooks in `semipy/slot_resolver.py` enforce the contract at runtime. **Both
require `contract_gate`, which is OFF by default** — see Section 10.

### The REUSE gate — `_run_reuse_contract_gate`

Runs *after* `verify_runtime_execution` passes on a REUSE. It loads the slot's
active cases and runs them against the cached implementation. If a carried case
is violated, it returns `(failure_message, validation_result)`, which the caller
uses to **force ADAPT**: the violated case's `reason` becomes the
`verify_failure_context`, so the regeneration prompt knows precisely which prior
decision the reused impl just broke. If `contract_gate` is off, or no active
cases exist, or the contract can't run, it returns `(None, None)` — no
objection. It never raises.

This is what catches silent forgetting *on reuse*: a cached function that
worked on old data but violates a recorded invariant on a new input shape is
kicked into ADAPT instead of returning a wrong answer.

### The GENERATE/ADAPT gate — `_run_generate_contract_gate`

Runs *after* `SemiAgent().generate`, *before* `create_commit`. It does two
things, gated differently:

1. **Effect tracing always runs when `contract_enabled`** (default on). Even
   with the acceptance gate off, every GENERATE/ADAPT computes its
   `ChangeRecord` via `compute_effect_diff` and attaches it to the commit. This
   is the always-on provenance.

2. **Acceptance enforcement runs only when `contract_gate`** (default off).
   `_assess(src)` runs `run_contract` over the active cases and computes the
   effect diff. The candidate is rejected and regenerated when either:
   - a case fails (`not cr.passed`), or
   - `contract_block_regressions` is on **and** `change.unintended_count > 0`.

   The retry loop appends the specific problem (the failure message, or
   `regression_summary`) to `verify_failure_context` and calls
   `SemiAgent().generate` again, up to `contract_gate_max_retries` (default 1).

After the budget is exhausted, any still-failing case ids are returned so the
caller **quarantines** them — the system keeps making progress instead of
deadlocking on an unsatisfiable case:

```python
ids = cr.failing_case_ids()   # quarantined by the caller
return entry, ids, change_record_to_dict(change)
```

---

## 7. The Maintainer

`maintain_contract` (`semipy/contract/maintainer.py`) runs after a successful
GENERATE/ADAPT (scheduled like sketch learning; sync by default, optionally
async via `contract_maintainer_async`). It builds the candidate input set —
triggering input first, then diverse harvested observations, deduplicated by
fingerprint, capped at `_MAX_SEED_PATTERNS = 8` — and captures the new impl's
actual behavior over them with the same rich-record gist. It has two layers.

### Layer 1 — Deterministic invariant seeding (always, when `contract_enabled`)

`_seed_invariants` derives carried-forward `invariant` cases **from the new
impl's observed outputs**, so the cases hold *by construction* — the contract is
self-consistent the moment it is written. For each input pattern it seeds:

- `non_empty` — only when the output type *can* be empty (skipped for scalars
  `int`/`float`/`bool`/`complex`/`NoneType`, where `is_empty` is vacuously
  False).
- `non_identity` — only for a `str` output that transforms a `str` input of at
  least 9 stripped characters (i.e. the output is not a passthrough echo).
- `type_match` — only when the slot's `expected_type` is a *safe builtin*
  (`str`/`int`/`float`/`bool`/`list`/`dict`/`tuple`) and the impl actually
  returns it.

This layer needs no LLM and runs whenever `contract_enabled` is on. It is what
gives the acceptance gate something to enforce **even with the LLM pass off**.

### Layer 2 — Selective LLM pass (only when `contract_maintainer`, default off)

When enabled, the model is shown the spec, the new source, indexed
`{input -> output}` samples, the change record, and the existing active cases,
and proposes (a) a few canonical golden-master `example` cases, (b)
`metamorphic` relations from the fixed registry, and (c) supersedes for
`example` cases this commit deliberately broke. The prompt strongly prefers
data-agnostic checks and caps examples at `contract_max_new_examples` (default
3).

This pass uses the **OpenAI Responses API** with `config.openai_model` (default
`gpt-5.5`) via `pydantic_ai`'s `OpenAIResponsesModel`. It does **not** use any
other provider. If no OpenAI key is configured, `_create_model` returns
`(None, None)` and the pass is a no-op.

Crucially, **every proposed case is verified to actually hold on the new source**
(via `run_contract`) before it is added — a proposal that does not hold is
dropped, preserving self-consistency. Supersedes only touch `active` `example`
cases.

Finally `_enforce_cap` quarantines the oldest active cases beyond
`contract_max_cases` (default 25), bounding latency and portal size. The
maintainer **never deletes**: caps and supersedes quarantine, preserving the
audit trail.

---

## 8. Spec-Change Retirement

When the slot's *meaning* changes, cases derived under the old meaning must not
fight the new intent. There are two distinct cases, distinguished by how slot
identity reacts.

**Case A — editing the `#>` spec text.** `spec_text` is part of `_make_slot_id`
(keyed on `filename:func_qualname:spec_text`). Editing it mints a **brand-new
slot** with a fresh, empty contract. The old slot — and all its cases — is
simply orphaned, so stale cases can never be enforced against the new meaning.
No explicit retirement is needed.

**Case B — changing the surrounding formal code, NL text unchanged.** Editing
the signature/free-variables, the return type, the slot category, or the
interpolated variables of a `semi(f"...")` keeps the same `slot_id` but changes
`spec_equivalence_key`. `execute_slot` detects this (`spec_changed`,
`slot_resolver.py`) and calls `retire_active_cases(slot, "spec changed")`
(`contract/access.py`) **before** resolving:

```python
spec_changed = old_eq != slot_spec.spec_equivalence_key
if spec_changed:
    force_regenerate = True
    if getattr(config, "contract_enabled", True):
        retire_active_cases(slot, "spec changed")   # quarantine all active cases
```

`retire_active_cases` quarantines every active case (kept for audit) so neither
the gate nor the effect-diff fights the new intent, then the maintainer re-seeds
under the new spec. Because cases are content-addressed, a still-valid invariant
(e.g. `non_empty`) **reactivates the same `case_id`** while a now-invalid one
(e.g. a `str` `type_match` after the type became `int`) stays retired.

Plain `#<` reasoning-surface edits trigger *neither* path — they are not part of
`spec_text` and do not change `spec_equivalence_key`.

---

## 9. Worked Example: a Date-Normalization Slot

Consider a `@semiformal` function whose slot normalizes a raw date string:

```python
@semiformal
def clean(raw: str) -> str:
    result = ""
    #> normalize the date to ISO 8601 (YYYY-MM-DD)
    return result
```

Assume `contract_enabled=True` (default) and, for the gate to act,
`contract_gate=True` (opt-in).

### Step 1 — first GENERATE

First call with `raw = "03/14/2025"`. No commit exists, so the slot GENERATEs an
implementation that parses `MM/DD/YYYY` and emits ISO. The maintainer runs
deterministic seeding. The captured record for the pattern `raw=N/N/N` shows a
non-empty `str` output that differs from the input, so it seeds:

| case (`invariant`) | input pattern | provenance                  |
| ------------------ | ------------- | --------------------------- |
| `non_empty`        | `raw=N/N/N`   | reason=`initial behavior`   |
| `non_identity`     | `raw=N/N/N`   | (output `2025-03-14` ≠ in)  |
| `type_match(str)`  | `raw=N/N/N`   | decision=`GENERATE`         |

All three hold by construction. Subsequent calls with `03/20/2025` REUSE — same
fingerprint `raw=N/N/N`, no new work, and the REUSE gate passes.

### Step 2 — a new format arrives

A row arrives with `raw = "2025-03-14"` — ISO format, fingerprint `raw=N-N-N`, a
**different** bucket. Two ways the system catches a regression here, both real:

- **REUSE gate.** The cached `MM/DD/YYYY` parser, run on `2025-03-14`, either
  raises (parse failure) or returns an empty/echoed string. `run_contract`
  evaluates the carried cases against the cached impl on the new input. An
  exception fails every invariant; an empty result fails `non_empty`. The gate
  returns the violation, forcing ADAPT with the case `reason` as failure
  context.

- **Effect-diff at the GENERATE/ADAPT gate.** Once ADAPT produces a candidate
  that handles ISO, `compute_effect_diff` runs the parent and the candidate over
  the union of contract inputs (`raw=N/N/N` and `raw=N-N-N`). Suppose the
  candidate accidentally breaks the old `03/14/2025` pattern. Then for
  `fp = raw=N/N/N`: the output changed, `fp != triggering_fp` (the trigger was
  `raw=N-N-N`), and the parent was *not* wrong there — so
  `intended = False`, `unintended_count` increments. With
  `contract_block_regressions` on, the gate appends `regression_summary`
  (*"Preserve the previous output for ... `'03/14/2025'`: was `'2025-03-14'` ..."*)
  and regenerates.

### Step 3 — the change record

The accepted ADAPT commits with a `ChangeRecord`:

```text
ADAPT: 1 intended, 0 unintended over 2 input pattern(s)
  effect_diff:
    raw=N-N-N  "2025-03-14"  old: error:ValueError  new: "2025-03-14"  intended=True
    raw=N/N/N  "03/14/2025"  (unchanged — omitted)
```

The change on the triggering pattern is `intended=True` (its fingerprint equals
`triggering_fp`); the old pattern is unchanged and never appears. The maintainer
re-seeds, and because cases are content-addressed, `non_empty`/`type_match(str)`
for `raw=N/N/N` **keep the same `case_id`** (reactivated, not duplicated), while
a fresh trio is seeded for `raw=N-N-N`. The slot now provably handles both
formats, and the audit trail records exactly *why* the second implementation
exists and that it cost **zero** regressions.

---

## 10. Configuration: What Runs by Default

The defaults are deliberate. **Recording is on; enforcement is off** until a
project opts in. From `semipy/agents/config.py`:

| Flag                          | Default  | Effect                                                                       |
| ----------------------------- | -------- | --------------------------------------------------------------------------- |
| `contract_enabled`            | **on**   | Records contracts, change records, **deterministic** invariant seeding.     |
| `contract_gate`               | **off**  | Executable acceptance gate (REUSE + GENERATE/ADAPT enforcement).            |
| `contract_gate_max_retries`   | `1`      | Regeneration retries to satisfy violated cases before quarantine.           |
| `contract_block_regressions`  | on       | Unintended effect-diff fails the gate (only matters when `contract_gate`).   |
| `contract_max_cases`          | `25`     | Cap on active cases (executed per gate; enforced by the maintainer).        |
| `contract_max_new_examples`   | `3`      | Max golden-master examples the LLM maintainer pins per commit.              |
| `contract_maintainer`         | **off**  | The LLM proposal pass (examples + metamorphic relations + supersedes).      |
| `contract_maintainer_async`   | off      | Run the maintainer in a background thread.                                  |

**With defaults (`contract_enabled` only), what actually runs:**

- Deterministic invariant seeding after every GENERATE/ADAPT.
- Effect tracing (`ChangeRecord` on every commit), surfaced in
  `portal_inspect.py`.
- Spec-change retirement (Section 8).

**What does *not* run by default:** the acceptance gate (no REUSE/GENERATE
enforcement — a violation is recorded but does not force ADAPT) and the LLM
maintainer pass (no model is called for the contract; seeding is purely
deterministic). All flags are read with `getattr(config, ..., default)`, and the
two persisted fields (`Slot.contract`, `Commit.change_record`) load with `{}`
defaults, so existing portals migrate with no rewrite.

To enforce: set `contract_gate=True`. To let the model enrich the contract with
golden masters and metamorphic relations: set `contract_maintainer=True` (uses
the OpenAI Responses API, `gpt-5.5`).
