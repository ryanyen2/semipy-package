# Semi-Formal Programming

A research writeup of the idea behind `semipy`, the concepts it is built from, and
how the pieces fit together. This is about the *paradigm* first and the
implementation second. Code references are given so you can follow any concept
into the source.

---

## 1. The thesis

In ordinary programming you specify everything before you run anything. The
program is a complete formal object: every branch, every type, every transform is
written down. Running it just executes what you already decided.

Semi-formal programming relaxes that. You write a program where **some parts stay
informal** — described in natural language or as an underspecified rule — and those
parts are **only turned into real code when they are actually used**, using the
real values flowing through the program at that moment as evidence.

Two claims sit underneath this:

1. **Specification is expensive and often premature.** A lot of code is written to
   handle inputs the author is guessing at. If you defer the decision until you can
   see the actual input, you specify less and specify it better.
2. **Use is the best oracle.** The shape of the data, the way a result is consumed
   downstream, the formats that actually show up — these are far more informative
   than a spec written in advance. So let the first real use decide the
   implementation, and let later uses correct it.

So the slogan is: *describe the intent formally enough to be unambiguous about
what you want, leave the mechanism informal, and commit to a mechanism only when a
concrete call forces the question.*

The rest of this document is the set of concepts you need to make that work, and
the data structures that hold them together.

---

## 2. The slot: a hole with a description

The atom of the paradigm is the **slot** — a region of a program whose *intent* is
written down but whose *implementation* is not. A slot is a hole with a
description attached.

There are three surface forms, all of which lower to the same internal object:

```python
@semiformal
def to_month_year(date_str: str) -> str:
    #> infer the input date format and return it formatted as "%b %Y"
    formatted = ...
    return formatted
```

```python
value = semi(f"normalize the phone number {raw} to E.164")
```

```python
@semiformal
def classify(text: str) -> str:
    ...   # whole body is the slot
```

- `#>` lines are the **specification** (the contract you mean). Text on `#>` lines
  is the durable description.
- `semi(f"...")` is an inline slot: the f-string is the spec, the interpolated
  variables are its inputs.
- A bare `...` body with no `#>` makes the whole function one slot.

**Data structure.** A slot lowers to a `SlotSpec` (`semipy/types.py`):

```
SlotSpec(
  slot_id, spec_text, spec_hash, spec_equivalence_key,
  free_variables, expected_type, expected_category, output_names,
  formal_constraints, enclosing_function_source, ...
)
```

`expected_category` (`SlotCategory`) records *where* the hole sits — an inline
expression, a standalone `semi()`, a `#>` block that produces named locals, or a
whole function body. That category decides how the generated function is called
and what it is expected to return.

**How the user interacts.** You write the description and leave the mechanism as
`...`. You never call the generator directly. The decorator and `semi()` do the
lowering: `lowering.scan_informal_specs` reads the source, finds the `#>` blocks
and `semi()` calls, and builds one `SlotSpec` per hole.

The important property: a slot is **identified by its intent**, not by the code
that fills it. The same hole can hold different code over time, and the same intent
in two places can share one implementation. Sections 4 and 6 make that precise.

---

## 3. Commit-during-use: the central mechanism

A slot has no implementation until a concrete call needs one. The first time
`to_month_year("03/14/2025")` runs, the system:

1. takes the slot's description **and the actual argument** `"03/14/2025"`,
2. asks a code-generating agent to produce a Python function that satisfies the
   description for inputs that look like this,
3. validates that the function runs and returns the right kind of value,
4. **commits** it — writes it to a cache as a versioned implementation,
5. calls it and returns the result.

Every later call with a similar input skips all of that and just runs the cached
function. The cost of generation is paid once, lazily, at the first real use.

This is the literal meaning of "only commit during use": the program is
incomplete on disk, and each hole is filled the first time control actually
reaches it with real data. The runtime values are not a side detail — they are the
evidence the generator is given (`GenerationSpec.sample_input`,
`runtime_profile_scalar_only`, observed values per parameter).

The orchestration of this lives in one function, `slot_resolver.execute_slot`. Its
job each call: figure out whether an implementation already exists and still fits,
and if not, make one.

---

## 4. Identity: when are two things "the same"?

The whole system rests on three notions of sameness. Getting these right is most
of the engineering.

**Call-site identity** — `SemiCallSite(filename, lineno, func_qualname)` hashed to a
`site_id`. This is "the same physical place in the source."

**Slot identity** — `slot_id`. Computed by `lowering._make_slot_id` from
`(filename, func_qualname, spec_text)`. Note what is *not* in it: the line number
and the ordinal. Inserting or deleting code above a slot does not change its
identity, but **editing the description does**. Editing the spec text mints a new
slot; the old one is simply abandoned. This is deliberate — a changed description
is a changed contract, so it deserves a fresh history.

**Meaning identity** — `spec_equivalence_key`
(`types.compute_spec_equivalence_key`). A hash of the durable *meaning*: the
template text, the free-variable names and order, the return type, the slot
category, and the output names. It excludes the file path and line. Two slots in
two different files with the same template and the same shape produce the same
equivalence key — which is what lets one slot's implementation be **reused** by
another (Section 6).

The distinction between `slot_id` and `spec_equivalence_key` is the source of two
different behaviors:

- Change the **description text** → new `slot_id` → new slot, fresh start.
- Change the **surrounding formal code** (signature, types, the interpolated
  variables) while the description text is unchanged → same `slot_id`, but the
  equivalence key changes → the system notices and re-decides (Section 7).

**Session identity** — one program run maps to one **Portal**
(`session_anchor.resolve_portal_anchor`, `session_id_from_filename`). A file-backed
script keys the portal on its normalized path. A Jupyter kernel keys it on the
working directory, because the notebook's temp filename changes on every restart
and would otherwise mint a new cache each time. Override with
`configure(session_source=...)`.

---

## 5. The cache as version control

Once a slot has an implementation, where does it live, and what happens when the
description or the data changes? The answer is a small version-control system, one
per slot.

**Data structures** (`semipy/history/version_control.py`):

- **`Commit`** — one generated implementation. Holds the `generated_source`, its
  `parent_ids` (so implementations form a DAG, not a list), the `decision` that
  produced it, fingerprints of the spec and the inputs, and — added by the contract
  subsystem — a `change_record` of why it exists and what it changed (Section 9).
- **`Branch`** — a named pointer to a head commit. Adaptations go onto new branches
  so alternative implementations of the same slot can coexist.
- **`Slot`** — the per-slot container: all its commits, its branches, a `refs` map
  from a concrete usage to the commit that served it, the observed inputs, and the
  behavioral contract.
- **`Portal`** — the per-session container: all slots for one program run, plus a
  `spec_map` used to write the runnable module.

This is a Merkle-style DAG: commit ids are content-addressed
(`compute_commit_id` from parents + source hash), so the same source under the same
parents is the same commit, and history is tamper-evident and dedup-friendly.

**Where the code actually runs from.** The portal is metadata (a JSON file). The
executable code lives in a generated **dispatch module**,
`.semiformal/runtime/{session}.semi.py`, which contains one active function per
slot plus a `DISPATCH` table mapping `slot_id → function name`
(`store.write_dispatch_module`). On reuse, the system imports the function from this
module and calls it. The dispatch module is a normal, readable, importable Python
file — you can open it and see exactly what was synthesized.

---

## 6. Resolution: reuse, adapt, or generate

On each call, `execute_slot` must decide what to do with the slot. That decision is
made by one policy object, `routing.RoutingPolicy.decide`, which evaluates signals
in a fixed priority order and returns a `Decision`:

- **GENERATE** — no implementation exists; make one from scratch.
- **REUSE** — an implementation exists and still fits; use it.
- **ADAPT** — an implementation exists but fails for the current input or the
  contract changed; modify it (the old commit is the parent of the new one).
- **INSTANTIATE** — a learned pattern matches; fill in its blanks instead of
  generating (Section 10).

The decision uses the identities from Section 4 plus two runtime fingerprints:

- `runtime_input_fingerprint` — a stable hash of the actual argument values
  (`runtime_fingerprint.py`). If the current inputs match what a commit was last
  verified against, the system can skip re-checking entirely.
- `spec_equivalence_key` — if a different slot in the same session has the same
  meaning and an implementation, this slot can REUSE that **donor** implementation
  without generating anything.

The simplified rule: same meaning + a passing check → REUSE; meaning or signature
changed → ADAPT; nothing to start from → GENERATE; a known exact prior usage →
jump straight to its commit.

**How the user interacts.** They don't. Resolution is invisible. The only thing a
user sees is that the first call is slow (generation) and the rest are fast
(reuse), and that the system quietly regenerates when something it relied on stops
holding.

---

## 7. Evolution: implementations change as data changes

This is what makes semi-formal programming more than "cache an LLM call." An
implementation generated from the first input is a guess about all future inputs.
The system treats it as provisional and **checks it against new evidence**.

Three mechanisms drive evolution, in increasing cost:

**Runtime verification (cheap, every reuse).**
`agents.validator.verify_runtime_execution` runs the cached function on the new
input and applies guards: did it raise, did it return the wrong type, did it return
an empty string for a non-empty input, did it just echo its input back (the
classic "parser failed, returned the original string" bug). A guard failure means
the implementation does not actually handle this input → fall through to ADAPT,
carrying the failure reason into the next generation prompt.

**Input observation (passive).** Every call records the values it saw
(`slot_observations._record_slot_input_observations`), and when the inputs are
scalars the system walks the call stack to find the Series or list they came from
and harvests its other values too (`_harvest_caller_series_samples`). This means
that even on the first call, the generator can be told "you will also see these
other values," so it generalizes earlier. Observations are bucketed by a
**structural fingerprint** that normalizes away digits, so `03/14/2025` and
`03/20/2025` are recognized as the same *pattern* and only a genuinely new shape
counts as new evidence.

**Semantic check (expensive, occasional).** Type-correct is not the same as
right. When new input patterns appear, an intent judge
(`agents.decision.evaluate_reuse_semantics`) runs the implementation over a sample
of observed inputs and asks a model whether the outputs actually satisfy the
description. It is rate-limited and only fires on genuinely new patterns, so it
does not run on every call.

The net effect: an implementation born from US slash-dates will, when a textual
date or a new format shows up and the guards or the judge flag it, **adapt** into a
multi-format parser — and the old commit stays in history as the parent.

---

## 8. The synthesis pipeline

When the decision is GENERATE or ADAPT, the work goes to an agent
(`agents.agent.SemiAgent.generate`). This is a single function-writing loop, not a
freeform chat:

1. **Build a `GenerationSpec`** — the description, the sample input, the enclosing
   function source, how the result is consumed downstream, the parent
   implementation (for ADAPT), and the failure reason that triggered this
   (`build_generation_spec`).
2. **Run a tool-using model** (`agents.generator`) that can profile the data, read
   surrounding context, build and run a candidate in a sandbox, and validate its
   output before committing to it. The tools matter: the model is not guessing
   whether its code runs, it is *running* it on the real sample and seeing the
   result.
3. **Validate** the final function (`agents.validator`): it parses, it has exactly
   one function, it runs on the sample, and its return matches the expected type
   (using a pydantic `TypeAdapter` bound to the right module namespace when the
   type is a concrete user dataclass).
4. **Commit** the validated source, write the dispatch module, return the function,
   and call it.

The generated code is held to the project's standing rules: it must be
data-agnostic and case-independent — no hardcoded values, no fixed keyword lists,
no branch that only works for one dataset. The logic has to come from the
description and the observed shape, not from memorized specifics.

---

## 9. Memory of decisions: the behavioral contract

A system that regenerates code on its own has a failure mode: each new version can
silently forget what an earlier version got right. Fixing the input that broke
today can break an input that worked yesterday, and nothing notices.

The **behavioral contract** is the mechanism that prevents that. It is a durable,
growing record attached to each slot (`Slot.contract`, package
`semipy/contract/`) that captures *why* each change happened and *what* its effect
was, and enforces prior decisions as runnable checks.

**The unit is a `ContractCase`** — one assertion about one input *pattern*, tagged
with the reason it exists. Three kinds, deliberately small and data-agnostic:

- **invariant** — a structural property from a fixed vocabulary: `non_empty`,
  `non_identity` (the output is not just the input echoed back), `type_match`,
  `category_preserving`, `idempotent`. These are the validator's transient guards,
  promoted into permanent, carried-forward checks.
- **metamorphic** — a relation that must hold across a meaning-preserving input
  change (e.g. adding surrounding whitespace must not change the result). Drawn
  from a fixed registry. This is how you test something with no exact oracle: you
  do not know the right output, but you know how it must (not) move.
- **example** — a pinned input→output pair (a golden-master). Used sparingly, only
  for canonical low-cardinality results, because pinning a data-dependent output is
  brittle.

Each case also stores the input pattern it covers, the `reason` it was added, the
`effect` it pins, the commit it came from, and a status (`active` / `superseded` /
`quarantined`) — superseded cases are kept, never deleted, so the trail of past
decisions survives.

**The contract is executable.** `contract.runner.run_contract` runs all active
cases against a candidate implementation in one subprocess and reports which prior
decisions it violates. It is wired in two places in `execute_slot`: when reusing
(reject a reused implementation that breaks a recorded decision) and after
generating (a new implementation must satisfy the carried cases before it is
committed; if it cannot, it is regenerated, then the conflicting case is
quarantined so the system still makes progress).

**Effect tracing is the interesting part.** When an implementation changes, the
system runs *both* the old and the new version over the same set of inputs and
diffs the results per pattern (`contract.change.compute_effect_diff`). Each changed
output is classified: **intended** (it landed on the input that triggered the
change, or the old version was already wrong there) or **unintended** (a
regression). That diff *is* the traced effect of the change, stored on the commit
as a `ChangeRecord` — and an unintended change can fail the gate. This is how the
system catches "you fixed format A but quietly changed the output for format B."

**A separate pass maintains the contract.** After a successful generation, a
maintainer (`contract.maintainer`) seeds the data-agnostic invariants from the new
implementation's actual behavior (so they hold by construction) and, optionally,
asks a model to propose a few high-value cases — preferring invariants and
metamorphic relations over brittle examples, and verifying every proposal actually
holds before adding it. It runs as a separate step from generation: one pass writes
the implementation, a later pass writes the checks. They are never the same pass.

**A subtlety worth stating.** When the surrounding formal code changes (a type, a
signature) but the description text is the same, the slot keeps its identity but
its meaning has shifted, so the old cases are retired (`retire_active_cases`) and
re-seeded under the new meaning — otherwise the contract would enforce the old
behavior against the user's new intent. When the *description* changes, a new slot
is minted anyway, so the question does not arise.

This subsystem is the direct answer to "make iteration robust": the reason for
every change is recorded, the effect of every change is measured, and prior
decisions are enforced rather than hoped for.

---

## 10. Abstraction: learning patterns across slots

Generating from scratch every time is wasteful when the same *shape* of
description recurs with different values ("extract the {field} from {source}").
After a generation, a binding-extraction step (`semipy/library/`) looks at the
description and the code it produced and, when the relationship is clear, records a
**sketch**: a parameterized template plus the code skeleton that realizes it.

When a later slot's description matches a sketch's shape, resolution can
**INSTANTIATE** it — substitute the new values into the skeleton — instead of
calling the model again. This is the system slowly building a library of its own
reusable abstractions from what it has already had to write. It is optional and
internal; users do not call it.

---

## 11. Data flow between slots

Slots are not independent. The output of one `semi()` often feeds another. The
reactivity layer (`semipy/reactivity/`) tracks this: results carry a `DataFlow`
tag identifying the slot that produced them, and a `DependencyGraph` records edges
between producing and consuming slots. When an upstream slot's implementation
changes, its downstream slots are marked stale and will re-resolve on their next
call. This is the same idea as a spreadsheet or a build system — staleness
propagates along dependencies — applied to generated implementations.

---

## 12. The human loop: surfacing and promotion

Because the system is making decisions on the user's behalf, it has to show its
work and let the user overrule it. Two devices do this.

**Surfacing with `#<`.** After generating or adapting, a skeleton writer
(`agents.skeleton_writer`) writes `#<` comment lines back into the user's source
near the slot. These are the system's *inferences*, in a small fixed vocabulary:
what it took the intent to be, what input shape it assumed, the strategy it used,
the fallback behavior, and — from the contract — what has been verified and how
many checks now hold. `#<` lines are advisory: they do not change the slot's
identity (they are not part of the description), so the system can refresh them
freely.

**Promotion.** If the user agrees with an inference, they turn a `#<` line into a
`#>` line. That moves it from "the system's guess" into "the contract." Because the
description text now changed, the slot's identity and meaning change, and the next
resolution honors the promoted constraint as a hard requirement. This is the
intended workflow: the system proposes, the user promotes what they want to lock
in, and the informal gradually becomes formal — but only the parts that earned it.

This closes the loop of the thesis. You start with intent and a hole. Use fills the
hole. The system shows you what it inferred. You promote what you want to keep. What
remains informal stays cheap to change; what you promote becomes a contract the
system will defend.

---

## 13. What is hard, and what is unsettled

Candor matters more than polish in a research writeup, so:

- **There is no exact oracle.** A natural-language description does not pin a unique
  function. The system leans on type checks, structural guards, metamorphic
  relations, and an occasional model judgment — but an implementation can pass all
  of them and still be subtly wrong. The contract narrows this; it does not close
  it.
- **Knowing *when* to regenerate is the core tension.** Regenerate too eagerly and
  the system is slow and unstable; too rarely and it serves stale, wrong code. The
  current answer is a stack of cheap-to-expensive checks (fingerprint skip → guards
  → contract → semantic judge) gated by how much the input pattern actually moved.
  The thresholds are heuristics.
- **Pinned examples are brittle.** Any check that hardcodes an expected output fights
  the reality that real data drifts. The system's bias toward invariants and
  metamorphic relations is a direct response, but it means some real regressions are
  only caught structurally, not exactly.
- **Determinism is partial.** The same description and the same data converge to the
  same cached function, so steady-state runs are deterministic. But the moment of
  generation depends on a model, so two cold starts can produce different (both
  valid) implementations. The DAG records which one you got; it does not make the
  choice reproducible.
- **State accumulates.** Every changed description mints a new slot, and history is
  never deleted, so a long-lived, much-edited program grows dead slots and retired
  cases. Compaction is understood as future work, not solved.

None of these sink the paradigm; they are the shape of the problem. The bet is that
for a large class of real glue code — parsing, extraction, normalization,
classification of messy inputs — describing intent and letting use commit the
mechanism is a better trade than specifying every case up front, *provided* the
system remembers why it changed its mind and can prove it did not break what
already worked. That proviso is what most of the engineering, and most of this
document, is about.

---

## Map: concept → where it lives

| Concept | Construct / data structure | Source |
| --- | --- | --- |
| Slot (hole + description) | `SlotSpec`, `SlotCategory` | `types.py`, `lowering.py` |
| Commit-during-use | `execute_slot` | `slot_resolver.py` |
| Identities | `site_id`, `slot_id`, `spec_equivalence_key`, `session_id` | `types.py`, `lowering.py`, `session_anchor.py` |
| Versioned cache | `Commit`, `Branch`, `Slot`, `Portal` (Merkle DAG) | `history/version_control.py`, `store.py` |
| Runnable code | dispatch module + `DISPATCH` table | `store.write_dispatch_module` |
| Resolution | `RoutingPolicy.decide`, `Decision` | `routing.py`, `resolver.py` |
| Evolution | verify guards, observations, intent judge | `agents/validator.py`, `slot_observations.py`, `agents/decision.py` |
| Synthesis | `SemiAgent.generate`, `GenerationSpec`, tools | `agents/agent.py`, `agents/generator.py` |
| Memory of decisions | `ContractCase`, `SlotContract`, `ChangeRecord` | `contract/` |
| Pattern learning | sketches, INSTANTIATE | `library/` |
| Data flow | `DataFlow`, `DependencyGraph` | `reactivity/` |
| Human loop | `#<` surfacing, promotion to `#>` | `agents/skeleton_writer.py`, `agents/steering.py` |
