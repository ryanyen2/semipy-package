# semipy Runtime Architecture

This document describes the core **runtime** architecture of `semipy`: how a
semiformal program is decomposed into open regions, how those regions acquire a
durable identity, how an identity resolves to a cached implementation (or
triggers generation), and how generated implementations are version-controlled
in a content-addressed DAG. It is grounded in the source; every nontrivial
claim cites `file.py:symbol`.

The mental model is a build cache for *meaning*. A natural-language
specification is compiled — once — by an LLM into an ordinary Python function;
that function is fingerprinted, committed to a per-session DAG, and emitted into
a dispatch module. Subsequent calls with the same meaning reuse the compiled
function with **no LLM invocation**.

---

## 1. Overview: what a semiformal program is

A semiformal program interleaves ordinary Python (the *formal* part) with
*open regions* whose behavior is specified in natural language (the *informal*
part). There are three authoring surfaces:

1. **Decorated functions** — `@semiformal` (`decorator.py:semiformal`) marks a
   function (or every open-region method of a class) as semiformal. At decoration
   time, `_wrap_function` scans the body and rewrites it into a *scaffold* that
   routes each open region through the runtime.

2. **`#>` statement blocks** — a contiguous block of `#>` comment lines (or an
   inline `#> spec` tail on a placeholder statement such as `x = ...  #> spec`)
   inside a `@semiformal` function. Its text is the durable specification; the
   names it produces become the slot's outputs.

3. **Standalone `semi(f"...")`** — a call to `semi()` anywhere, in or out of a
   decorated function (`semi_fn.py:SemiProxy`). The f-string is decomposed into a
   stable template plus interpolated runtime values.

```python
@semiformal
def enrich(url: str) -> str:
    #> extract the registrable domain from the URL
    domain = ...
    return domain

# or, standalone:
host = semi(f"extract the domain from {url}")
```

A fourth, implicit category exists: if a decorated function contains **no**
`#>` block and **no** `semi()` call, the whole body becomes one
`FUNCTION_BODY` slot (`lowering.py:scan_informal_specs`, final branch). The four
categories are enumerated in `types.py:SlotCategory`:
`EXPRESSION`, `EXPRESSION_STANDALONE`, `STATEMENT_BLOCK`, `FUNCTION_BODY`.

### Lowering to a scaffold

`lowering.py:scan_informal_specs` parses the source with `ast`, walks the
function body, and emits one `SlotSpec` (`types.py:SlotSpec`) per open region —
recording its category, free variables, output names, expected type, and source
span. `lowering.py:lower_to_scaffold` then rewrites each region into a call to a
`__slot_N__` proxy: a `semi()` call expression becomes `__slot_N__(**kwargs)`; a
`#>` block becomes an assignment `out = __slot_N__(**kwargs)`; an inline
`... #> spec` placeholder has its `Ellipsis` value replaced. The scaffold is
compiled (`decorator.py:_compile_scaffold`) with each `__slot_N__` bound to a
slot proxy (`slot_resolver._make_slot_proxy`) that calls `execute_slot` at
runtime. `self`/`cls` is *not* slot data: `scan_informal_specs` computes
`receiver_names` and subtracts the receiver from every slot's `free_variables`.

The crucial property is that **the LLM never runs at import/decoration time** —
lowering is pure static analysis. Generation is deferred to the first call that
actually reaches a given slot.

---

## 2. Call-site identity and slot identity

Two distinct identities matter, for two distinct purposes.

### Call-site identity (diagnostics, standalone anchoring)

A `SemiCallSite` (`types.py:SemiCallSite`) records `(filename, lineno,
func_qualname)`. Its `site_id` is

$$
\texttt{site\_id} = \mathrm{SHA256}(\texttt{filename} \,\|\, \texttt{":"} \,\|\, \texttt{lineno} \,\|\, \texttt{":"} \,\|\, \texttt{func\_qualname})[{:}16]
$$

(`types.py:SemiCallSite.site_id`). This is the physical location of the call. It
is used for error messages and to anchor standalone `semi()` extraction, but it
is deliberately **not** the cache key — line numbers move when a file is edited.

### Slot identity (the cache key)

A slot's durable identity is computed by `lowering.py:_make_slot_id`:

$$
\texttt{slot\_id} = \mathrm{SHA256}(\texttt{filename} \,\|\, \texttt{":"} \,\|\, \texttt{func\_qualname} \,\|\, \texttt{":"} \,\|\, \texttt{spec\_text})[{:}16]
$$

The function signature still carries a `slot_ordinal` argument, but it is
explicitly `del`-eted and **not** mixed into the key. Each component is chosen
deliberately:

| Component | Why it is in the key |
| --- | --- |
| `filename` | Slots in different files are different slots. |
| `func_qualname` | Slots in different functions/methods are different (the receiver class is folded into the qualname in `semi_fn._get_call_frame_and_site`). |
| `spec_text` | The *meaning* — the `#>` text or the `semi()` template — is what the slot is. |
| `slot_ordinal` (excluded) | Ordinal drift was the source of phantom 0-commit duplicates: adding a new `#>` block *above* an existing one shifted every later ordinal and reminted otherwise-stable ids. |

This is why `#<` and `#>` edits behave differently:

- Editing or adding a `#>` line **changes `spec_text`**, hence changes `slot_id`.
  A brand-new slot (with a fresh, empty DAG) is minted; the old slot is orphaned.
  The contract is the spec, so a spec edit is a new contract.
- Editing a `#<` *reasoning* line does **not** change `spec_text`. The slot keeps
  its `slot_id`. (`#<` lines are stripped before scanning by
  `strip_skeleton_lines`, invoked in `decorator._wrap_function`, so they never
  perturb identity.)

During scanning, slots are sorted by source span and assigned final ids
(`scan_informal_specs`, the `replace(s, slot_id=_make_slot_id(...))` pass at the
end), so reordering unrelated slots does not remint a slot.

---

## 3. Spec equivalence key (the reuse fingerprint)

`slot_id` is *location-aware* (it pins file + function). The **spec equivalence
key** is location-*independent*: it fingerprints the durable *meaning* so two
call sites in different places — or different notebook cells — can share one
compiled implementation. It is computed by
`types.py:compute_spec_equivalence_key` as a hash of a 5-tuple:

$$
\texttt{eq\_key} = \mathrm{SHA256}\big(
  \underbrace{\texttt{spec\_text}}_{\text{the NL meaning}} \,\|\,
  \underbrace{\texttt{free\_vars}}_{\text{names, in order}} \,\|\,
  \underbrace{\mathrm{repr}(\texttt{expected\_type})}_{\text{return contract}} \,\|\,
  \underbrace{\texttt{category}}_{\text{slot kind}} \,\|\,
  \underbrace{\texttt{output\_names}}_{\text{produced names}}
\big)[{:}16]
$$

Concretely (from the source), the pre-image is
`f"{spec_text}\0{fv}\0{repr(expected_type)}\0{category.value}\0{outs}"` with
`fv = ",".join(free_variables)` and `outs = ",".join(output_names)`.

The two critical exclusions are **file path** and **line number**, and — by
construction — **runtime values**. The key fingerprints *structure*, not *data*.

### Worked example: same template, different data → REUSE

Suppose two notebook cells (same working directory, hence one portal — see §7)
each contain:

```python
# cell A
domA = semi(f"extract the domain from {url_a}")   # url_a = "https://mit.edu/x"
# cell B
domB = semi(f"extract the domain from {url_b}")   # url_b = "http://acm.org/y?z=1"
```

`semi_fn._extract_semi_template_from_source_line` decomposes each f-string into
the *template* `"extract the domain from {v0}"` plus a runtime value bound to
`v0`. Both call sites therefore have:

- `spec_text = "extract the domain from {v0}"`
- `free_variables = ["v0"]`
- `expected_type = type(None)` (no `expected_type=` passed)
- `category = EXPRESSION_STANDALONE`
- `output_names = []`

so they compute the **same** `eq_key`, even though `url_a != url_b`. Cell A
(reached first) generates and commits an implementation. Cell B has a different
`slot_id` (different line number → different physical location), so its own slot
has no commits — but `RoutingPolicy._best_donor` finds A's slot by matching
`eq_key` and resolves B to a **donor REUSE** (§4, Case 4). No second LLM call.

The same logic underwrites incremental data growth: re-running cell A with a
*third* URL keeps `eq_key` identical (values are excluded), so it also REUSEs.

---

## 4. The resolution decision

`routing.py:RoutingPolicy.decide` is the single, explicit decision procedure. It
evaluates signals in **strict priority order** and returns a `ResolutionResult`
carrying the decision plus the context the caller needs (parent commit ids,
parent sources, branch name, donor slot id). The `Decision` enum
(`types.py:Decision`) has exactly four members:
`REUSE`, `ADAPT`, `GENERATE`, `INSTANTIATE`.

Let the slot be $s$, the incoming `SlotSpec` be $\sigma$, and let
$\text{head}(s) = \texttt{most\_recent\_branch\_head}(s)$ be the newest commit
across *all* branches (§7). Equivalence holds when the stored snapshot's key
equals $\sigma$'s key ($\texttt{equiv\_ok}$, via `_equivalence_matches`). The
procedure is:

```
0.  s is None (slot absent from portal)            → GENERATE
L.  a commit is version-locked (locked_commit_id)  → REUSE(locked commit)   # bypasses all below
1.  force_regenerate = True
        head(s) exists                              → ADAPT  from head(s)
        else donor exists (eq_key match elsewhere)  → ADAPT  from donor
        else                                        → GENERATE
8.  prior_validation failed with failure_kind in
    {type_mismatch, empty_output, identity_return}  → ADAPT (from head, else GENERATE)
9.  semantic_result.decision == "adapt"             → ADAPT (from head, else GENERATE)
5.  s has commits AND not equiv_ok
        sketch match exists                         → INSTANTIATE
        else                                        → ADAPT  from head(s)
6/7. s has commits AND equiv_ok                     → REUSE(head(s))    # caller then verifies
    --- s has NO local commits ---
4.  donor found (another slot, same eq_key)         → REUSE(donor commit)
3.  sketch match exists                             → INSTANTIATE
2.  no commits, no donor, no sketch                 → GENERATE
```

Notes on the precedence:

- **Lock first.** `RoutingPolicy.decide` checks `version_lock.locked_commit_id`
  before anything else and short-circuits to `REUSE` of the pinned commit. This
  is how editor "check out version *vN*" works: it pins, it does not roll back.
- **`refs` short-circuit.** A slot's `refs[usage_id] → commit_id` map
  (`history.version_control.Slot.refs`) records which commit a given usage
  resolved to. `add_commit_to_slot` registers it on every commit so a known
  usage maps straight to its implementation.
- **Cases 8 and 9 are re-entry points.** After a REUSE candidate fails runtime
  verification (§5) or a semantic recheck, `execute_slot` re-invokes
  `RoutingPolicy.decide` with `prior_validation`/`semantic_result` set, turning
  the original REUSE into an ADAPT *from the failing head* — so the new
  implementation is adapted, not generated from scratch
  (`slot_resolver.py:execute_slot`, the `if force_regenerate:` re-decide).
- **REUSE is provisional.** A `decide()` returning REUSE only means "this head is
  a candidate"; the caller still runs the verify gate (§5) before trusting it.

ADAPT and GENERATE both flow into the agent (§6). ADAPT additionally carries
`parent_sources` — the failing implementation — so the LLM revises rather than
rewrites. ADAPT commits land on a **new branch** `b_{spec_hash[:8]}`; GENERATE
lands on `main` (`routing.py`, branch_name assignments).

---

## 5. Runtime input fingerprint and verify

REUSE is only safe if the cached implementation actually works for the *current*
input. `semipy` makes this cheap with a fingerprint, and robust with a verify
gate plus data-agnostic guards.

### The runtime input fingerprint

`runtime_fingerprint.py:compute_runtime_input_fingerprint` hashes the slot's
`runtime_values` into a stable 16-char digest. Keys are sorted so insertion
order is irrelevant; each value is rendered structurally by
`_fingerprint_value` (typed prefixes `s:`/`i:`/`f:`/`L:`/`D:`; pandas/numpy
objects collapse to `shape:dtype:head_hash` so a large frame does not need full
serialization). A commit stores the fingerprint of the input it was last
verified on in `Commit.runtime_input_fingerprint`.

### The verify gate

On a REUSE (`slot_resolver.py:execute_slot`, the
`if resolution.decision == Decision.REUSE` block):

$$
\texttt{skip\_verify} = (\texttt{stored\_fp} \neq \varnothing) \wedge (\texttt{stored\_fp} = \texttt{current\_fp})
$$

If the current input fingerprint equals the commit's stored fingerprint, verify
is **skipped** — the implementation already ran on exactly this shape. Otherwise
`validator.py:verify_runtime_execution` runs the cached function over sample
inputs and checks: it compiles, accepts the right arity, runs without raising,
and returns the expected type. Failure sets a typed `failure_kind`
(`ValidationResult.failure_kind`) and forces the ADAPT re-decide of §4.

Effectful slots skip the standard return-type verify (their function returns an
`EffectScript`, not a value); a dedicated reuse effect gate owns their
verification.

### Data-agnostic guards

Two guards in the validator catch silent failures that would otherwise pass a
naive type check — both are **data-agnostic** (no per-case logic):

- **Empty-string guard.** A non-empty string input that yields an empty string
  output is flagged `failure_kind="empty_output"` and forces ADAPT (validator,
  empty-output branch).
- **Identity-return guard.** `validator.py:_str_identity_passthrough_failure`
  rejects `return s` echoes: for a `str`-returning `FUNCTION_BODY` / `EXPRESSION`
  / `EXPRESSION_STANDALONE` slot with exactly one string input, if the (stripped)
  output equals the (stripped) input and the input is **at least 9 characters**
  (`len(sin) >= 9`, avoiding false positives on short canonical outputs like
  `"Mar 2025"`), it returns `failure_kind="identity_return"`. This catches
  generated code that does `return s` on a parse failure — which would pass an
  `isinstance(result, str)` check yet silently mishandle a new input format.

Both `failure_kind`s appear in §4 Case 8, so they ADAPT the failing head with
the failure reason threaded into the generation prompt
(`GenerationSpec.verify_failure_context`).

---

## 6. The agentic generation pipeline

When resolution is GENERATE or ADAPT, `execute_slot` builds a `GenerationSpec`
(`slot_resolver.py:build_generation_spec`) — prompt text, expected type, sample
input, parent sources (for ADAPT), enclosing scaffold, downstream-usage source,
and any curated contract examples — and calls
`agents/agent.py:SemiAgent.generate`.

### One model, one tool, an action program

The LLM backend is the **OpenAI Responses API** via `pydantic_ai`. The agent is
constructed in `agents/generator.py:_create_agent`:
`OpenAIResponsesModel(config.openai_model)` (default model `gpt-5.5`), keyed on
`OPENAI_API_KEY` (`generator.py:_create_openai_model`, which raises if the key is
absent). Reasoning summaries stream back as **Reasoning** parts.

The agent has exactly **one tool**: `execute_action_program(code: str)`
(`generator.py`, the `@agent.tool` inside `_create_agent`). The model does not
call separate "profile" / "validate" / "gist" tools. Instead it writes a Python
**action program** as a string, which is run in a sandbox prepended with a
helper preamble (`generator.py:_build_action_preamble`). The preamble injects
three helpers into the program's namespace:

| Helper | Purpose |
| --- | --- |
| `profile_slot()` | Returns a pre-computed data profile + observed input values. |
| `read_upstream()` | Returns parent implementation sources (used on ADAPT). |
| `build_and_run_gist(source, invocation_code)` | `exec`s a candidate `def` string and calls it, returning `{success, result, error}`. |

The preamble also injects user-defined type sources
(`generator._collect_user_type_sources`, emitted dependency-ordered so a field's
`Enum` is defined before the dataclass that references it) and a recording `fx`
shim so effectful candidates can be self-tested. The model iterates — profile,
draft a `source` string, test it via `build_and_run_gist`, fix, repeat — entirely
inside action-program turns.

### From candidate to commit

The model returns its answer as a **`CommitmentRecord`** structured output
(`agent.generate_async` extracts `final_output.generated_source`), not as prose
or a fenced block. `SemiAgent.generate` then:

1. **Validates** the source (`agents/validator.py:validate`): AST parse, single
   function, arity, type correctness, and a sandboxed execution check. Failure
   triggers a retry with the error fed back (`SemiAgent._build_retry_prompt`), up
   to `max_retries`.
2. **Compiles** it (`agents/compiler._compile_source`) and returns a
   `CacheEntry`.

Back in `execute_slot`, optional acceptance gates run before commit
(`_run_generate_contract_gate`, `_run_generate_effect_gate`), then
`history.version_control.create_commit` mints a commit, `add_commit_to_slot`
attaches it to the slot's branch and registers its `ref`, and
`store.write_dispatch_module` re-emits the dispatch module (§7). A skeleton-writer
pass may then add `#<` provenance lines back into the user's source (skipped for
standalone `semi()`, which has no `#>` block to annotate —
`_should_surface_skeleton`).

---

## 7. The DAG cache model

`semipy` version-controls generated implementations in a content-addressed
Merkle-style DAG (`history/version_control.py`). The containment hierarchy is:

```
Portal  (one per session)
└── Slot  (one per durable slot_id)
    ├── commits:  commit_id → Commit
    ├── branches: name → Branch(head=commit_id)
    └── refs:     usage_id → commit_id
```

- **`Commit`** (`version_control.Commit`, frozen) holds the `generated_source`,
  its `source_hash`, `parent_ids`, `decision`, `runtime_input_fingerprint`, and
  the serialized `commitment_record` / `change_record` / `source_snapshot`. Its
  id is content-addressed:
  $\texttt{commit\_id} = \mathrm{SHA256}\big(\mathrm{sort}(\texttt{parent\_ids}) \,\|\, \texttt{source\_hash}\big)[{:}20]$
  (`compute_commit_id`).
- **`Branch`** is a named pointer to a head commit. GENERATE writes `main`;
  ADAPT writes a fresh `b_<spec_hash>` branch — so an ADAPT does not overwrite the
  original implementation.
- **`Slot`** is the per-call-site DAG plus persisted metadata (its `slot_spec`
  snapshot, `advisor_state`, `contract`, `ledger`).
- **`Portal`** (`version_control.Portal`) is the per-session container, persisted
  as JSON at `{cache_dir}/{session_id}.portal.json`
  (`store.py:_portal_path`, `save_portal`/`load_portal`).

### One active implementation per slot

The single most important DAG rule: a slot's **active** implementation is the
**newest branch head across all branches**, by timestamp —
`version_control.most_recent_branch_head` (used by `routing._head_commit` and
`store._get_active_commit`). It is *not* simply `default_branch`'s head. Because
ADAPT commits land on a *new* branch, this rule ensures the freshest ADAPT — not
the stale original on `main` — is what runs and what gets written to dispatch.
(A version *lock* overrides this: `_get_active_commit` checks
`locked_commit_id` first, so a checked-out version runs unchanged.)

### The dispatch module

`store.py:write_dispatch_module` materializes the active commit of every slot
into a single importable Python file,
`{cache_dir}/runtime/{module_name}.semi.py`. For each slot it renames the
generated function to `{base}_{commit_id[:8]}`
(`store.function_name_for_commit`), appends provenance comments (category,
commit, decision, spec preview, steering keys), and registers
`DISPATCH[slot_id] = fn_name`. At REUSE time, `execute_slot` loads the function
by name from this module (`store.load_function_from_dispatch`, with a cached exec
namespace seeded by the user module's globals so generated code can reference
user types) and calls it directly — the LLM is not involved.

### Session identity

A portal is keyed by `session_id = SHA256(basename-without-.py)[:16]`
(`types.session_id_from_filename`). For Jupyter/IPython, the source file is an
ephemeral `ipykernel` temp path that changes every kernel restart, so
`session_anchor.resolve_portal_anchor` (called at the top of `execute_slot`)
remaps `ipykernel` paths to `os.getcwd()` — giving one shared portal and
dispatch module per working directory, which is exactly what lets sibling
notebook cells donor-REUSE each other (§3).

---

## 8. End-to-end worked example

Trace `host = semi(f"extract the domain from {url}")` across two calls.

### First call — `url = "https://www.mit.edu/research"` → GENERATE

1. **Entry.** `semi_fn.SemiProxy.__call__` → `_semi_standalone`. The call frame
   gives the `SemiCallSite`; `_full_call_statement` reads the (possibly
   multi-line) statement and `_extract_semi_template_from_source_line` decomposes
   the f-string into `spec_text = "extract the domain from {v0}"`,
   `free_variables = ["v0"]`, and `runtime_values = {"v0": "https://www.mit.edu/research"}`.

2. **Identity.** `compute_spec_equivalence_key` produces `eq_key`;
   `_make_slot_id(filename, func_qualname, 0, f"{spec_text}\0{eq_key}")` produces
   `slot_id`. A `SlotSpec` (category `EXPRESSION_STANDALONE`) is built and passed
   to `slot_resolver.execute_slot`.

3. **Portal load + resolve.** `execute_slot` resolves the portal anchor, loads
   the portal, and `_ensure_slot` creates an empty `Slot`. `RoutingPolicy.decide`
   sees a slot with no commits, no donor, no sketch → **GENERATE** (§4 Case 2).

4. **Generate.** `build_generation_spec` → `SemiAgent.generate`. The OpenAI
   Responses agent writes an action program: it calls `profile_slot()`, drafts a
   `source` string (a `def` that parses out the host and strips a leading
   `www.`), tests it with `build_and_run_gist(source, "fn('https://www.mit.edu/research')")`,
   observes `"mit.edu"`, and returns a `CommitmentRecord` whose
   `generated_source` is that function. `validate` passes.

5. **Commit + dispatch.** `create_commit` mints a commit on branch `main` with
   `runtime_input_fingerprint = compute_runtime_input_fingerprint({"v0": "https://www.mit.edu/research"})`.
   `add_commit_to_slot` attaches it and registers the `ref`.
   `write_dispatch_module` emits the function into
   `runtime/<module>.semi.py` and registers `DISPATCH[slot_id]`. The function is
   called; `host = "mit.edu"` is returned.

### Second call — `url = "http://acm.org/dl"` → REUSE

1. **Same identity.** Same source line ⇒ same `spec_text`, same `slot_id`, same
   `eq_key`. Only `runtime_values = {"v0": "http://acm.org/dl"}` differs.

2. **Resolve → REUSE candidate.** The slot now has a commit and
   `equiv_ok` is true → **REUSE** of the head commit (§4 Case 6/7).

3. **Verify decision.**
   `current_fp = compute_runtime_input_fingerprint({"v0": "http://acm.org/dl"})`.
   The commit's stored fingerprint is for the *mit.edu* input, so
   `current_fp ≠ stored_fp` ⇒ `skip_verify` is false ⇒
   `verify_runtime_execution` runs the cached function on the new input. It
   returns `"acm.org"` — a non-empty string, not equal to the (≥9-char) input,
   so the empty-string and identity guards (§5) pass; the type is `str` as
   expected. Verify **passes**.

4. **Call, no LLM.** The cached function is loaded from the dispatch module and
   called directly; `host = "acm.org"`. No generation occurred — the second call
   paid only a fingerprint hash, a verify run, and a function call.

Had the new input been a format the cached function mishandled — say it returned
the input unchanged (`return url`) — the identity guard would fire
(`failure_kind="identity_return"`), `execute_slot` would re-decide with
`prior_validation` set, and §4 Case 8 would route to **ADAPT from the failing
head**: the LLM revises the existing function (preserving the working `mit.edu`
/ `acm.org` branches) to also handle the new shape, commits the result on a
`b_<spec_hash>` branch, and `most_recent_branch_head` makes that ADAPT the active
implementation going forward.

---

## Summary

`semipy`'s runtime is a *meaning cache* with version control:

- **Lowering** (`lowering.py`) turns informal regions into `SlotSpec`s and a
  routed scaffold — statically, no LLM.
- **Identity** separates physical location (`site_id`) from durable meaning
  (`slot_id`, keyed on `spec_text` not line number) and from reusable structure
  (`eq_key`, which ignores file, line, and data).
- **Routing** (`routing.py:RoutingPolicy.decide`) is one ordered decision
  procedure over four decisions: REUSE, ADAPT, GENERATE, INSTANTIATE.
- **Verification** (`runtime_fingerprint.py` + `validator.py`) makes REUSE cheap
  (fingerprint skip) and safe (verify gate + data-agnostic empty/identity guards).
- **Generation** (`agents/`) is a single-tool, action-program agent over the
  OpenAI Responses API that returns a validated `CommitmentRecord`.
- **The DAG** (`history/version_control.py` + `store.py`) commits each
  implementation content-addressably, makes the newest branch head per slot the
  active one, and emits a flat dispatch module that subsequent calls import with
  no model invocation.
