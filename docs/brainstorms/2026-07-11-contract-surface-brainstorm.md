---
date: 2026-07-11
topic: the contract surface — annealing the specification, not just the program
status: brainstorm / design assessment (answers six open problems as one design)
related:
  - docs/plans/2026-07-04-001-refactor-frontier-kernel-plan.md
  - docs/brainstorms/2026-07-04-incremental-formalization-thesis.md
  - docs/architecture.md
---

# The Contract Surface: Annealing the Specification, Not Just the Program

## 0. The diagnosis

Six problems were raised against the current system:

1. **Decision legibility.** A user of a semipy-built library cannot understand *why*
   an output is what it is without reading hidden generated source. We cannot expect
   users to debug the informal layer.
2. **Gist correctness against external sources.** Is the generated implementation
   actually right for the file / PDF / API it was built against — and how would we know?
3. **Data at scale.** What are the execution semantics when the input is 100,000 rows,
   not a sample string?
4. **Runtime adjustment.** How does code adapt *during* a run, not just between calls?
5. **Test-case construction.** Where do test cases that capture *intent* come from?
6. **The verification gate.** The semantic checks are weak (type/arity/non-empty/
   non-identity) or LLM-mediated. How does the system know the output reflects what the
   user expects the program to do?

These are not six problems. They are one problem seen from six angles, and the
frontier-kernel plan makes the root cause visible by contrast:

> **Program annealing formalizes the *implementation* — the specification stays
> informal forever.** The `#>` line never gets harder. Everything the kernel learns
> about intent (contract cases, guards, relations, certificates) is stored as internal
> bookkeeping for the freeze decision, never materialized as the thing the user reads,
> disputes, ships, or is protected by.

Every symptom follows: decisions are illegible because the only legible artifact is
code (1); a gist's correctness is unbounded because the certificate never states *what
inputs it covers* (2); 100k rows are awkward because the contract has no per-element
granularity (3); mid-run adaptation is undefined because a call is atomic with respect
to evidence (4); test cases are scarce because the only trusted label source is the
user, consulted ad hoc (5); and the verify gate is weak because the oracle can only be
as strong as the contract, and the contract is thin (6).

The design answer is one object with six views:

> **The contract surface.** Every slot (eventually: every node) owns a materialized,
> human-readable, machine-checkable contract `C(v)` — types, scope guard, evidence
> cases with provenance, metamorphic relations, an inverse where one exists, regime
> guards, and the freeze certificate with its explicit input scope. `C(v)` is
> simultaneously the *oracle* (what verification checks), the *explanation* (what the
> user reads when they ask "why"), the *feedback channel* (disputing an explanation
> creates evidence), and the *shipping artifact* (what a built library exports).
> Incremental formalization then has its missing dual: the program anneals toward
> fixed code, and the specification anneals toward a formal contract, and the two
> converge toward each other under the same evidence stream.

This is not a rival plan to the frontier kernel. It is the user-facing semantics of
the same engine: every mechanism below lands as an extension of an existing phase
(mapped in §8). Design-by-contract (Meyer) is the obvious ancestor; the delta — and it
is the load-bearing delta, not decoration — is that here the contract is *elaborated
from an informal spec and grown by an oracle-gated loop*, rather than hand-written,
and it carries a certificate stating exactly how much of it is earned.

One architecture rule unifies everything below, generalizing the guard-DSL discipline
already in Phase 5:

> **The LLM proposes; the checker disposes.** The LLM may propose contracts, relations,
> guards, inverses, and cases — but nothing it proposes becomes part of the oracle
> until it is compiled, executed, or adjudicated by something that is not an LLM.
> LLM judgment appears nowhere in the trust hierarchy (§7) as a gate.

---

## 1. The object

For a node `v`, the contract surface is:

```
C(v) = ( T,          -- input/output types + ≈_Y (already the type layer)
         S,          -- the scope guard: a checkable predicate over input profiles,
                     --   stating WHERE the certificate applies (new, §3)
         E,          -- evidence cases: (input, expected | relation, outcome,
                     --   provenance, holdout tag)  (Phase 0 ledger, now public)
         R,          -- metamorphic relations, typed per category (Phase 4 registry)
         q,          -- an inverse/partial inverse where the slot admits one (new, §6)
         G,          -- regime guards (Phase 5 guard DSL)
         K )         -- the freeze certificate (ε, δ, γ, budget) — now scoped by S
```

Two directions of use:

- **Inward** (problems 2, 3, 5, 6): `C(v)` is what the oracle checks. Strengthening
  verification *means* growing `C(v)`; there is no other lever. §§5–7 give the three
  cheap growth mechanisms (relations, inverses, mining the data stream).
- **Outward** (problems 1, 4, and shipping): `C(v)` is what the user sees. Every
  explanation is a pointer into `C(v)`; every dispute is an edit to `E`; a built
  library exports `C(v)` next to the frozen code.

The rest of this document works through the six problems in an order that lets each
mechanism build on the previous one.

---

## 2. Problem 1 — decision legibility: answerable outputs

**Current state (honest).** The Rich panel, `SEMIPY_PIPELINE_TRACE`, the VS Code
freeze-certificate hover, and the ledger export are all *developer*-facing and all
*event*-oriented: they answer "what did the kernel do," never "why is this particular
output what it is." A user of a library built on semipy has no surface at all.

**The mechanism: `why(value)`.** Slot results already carry a `DataFlow` (producing
slot, producing commit). Extend it to an explanation query — `semipy.why(value)` (and
per-element `why(value, row=i)` for collection outputs, §4) — that returns, in order
of trust:

1. the spec text and the resolved slot/commit (with the computed decision label);
2. the certificate `K` and its scope `S`: *"this implementation is certified to ε=…,
   δ=… on inputs matching ‹profile›; your input is inside/outside that scope"*;
3. the nearest evidence: the contract cases in `E` most similar to this input, with
   their pinned outcomes — "it returned X because the ledger pins X on these
   neighboring cases";
4. the regime: which guard `g ∈ G` routed this input, and the counterfactual — "your
   input took regime A because `field x` was null; non-null would route to B";
5. for committee-era nodes: which alternatives were eliminated and by *which evidence
   item* (blame the evidence, not the code).

**The principle that keeps this from being decoration:** explanations must be
*extensional* — made of things the user can independently check (cases, guards,
scopes, certificates) — never *intensional* (generated code, or an LLM's verbal
rationale, which is exactly the verbalized confidence the 07-04 synthesis found
epistemically vacuous). This is why-provenance in Buneman's sense (why-provenance for
query results, Buneman–Khanna–Tan 2001) applied to a program whose semantics *is* a
ledger, plus Wadler–Findler blame for the "which part is responsible" leg — both
already in the kernel's conceptual vocabulary.

**The loop-closing property.** An explanation the user disagrees with is not a dead
end: disputing item (3) — "no, on this input the answer should be Y" — *is* an
`assert`, i.e. new evidence, i.e. a melt trigger. The explanation surface and the
feedback channel are the same object. This is what "users should not debug the
source" concretely cashes out to: **users dispute evidence; only the kernel touches
code.**

**Shipping (`semipy build`).** For library authors: a build step distills the portal
to frozen artifacts + their contract surfaces, and ships a no-LLM runtime (dispatch
module + guards + `why`). End users get: deterministic code, a readable contract per
entry point, scope checks at the boundary (out-of-scope inputs fail *loudly with the
scope stated* instead of silently misbehaving), and `why()` without any model call.
An end-user bug report is a certificate falsification event, which is exactly the
germ-feedback signal §3.2 of the plan already wants.

---

## 3. Problem 2 — gist correctness against external sources: scoped certificates

**Current state (honest).** The action-program agent tests candidates against the
*observed sample input* (`profile_slot()` + `build_and_run_gist`). `documents.py`
materializes a PDF into text at input time with no durable identity for the source.
The freeze certificate (Phase 3) states (ε, δ, γ, B) but not *what inputs the search
explored* — so it implicitly claims coverage of all of `D_v` while having only ever
observed a sample of it. For an external source, `D_v` is precisely the thing that
drifts.

**The mechanism: every certificate carries an input scope `S`.** `S` is a checkable
predicate over the input profile — schema (columns/fields + types), value ranges,
null/empty rates, encodings, observed enum values — synthesized from the profiles of
every input in the evidence ledger at freeze time, expressed in (an extension of) the
Phase 5 guard DSL so it is compiled and executable, never free text. Then:

- **The certificate becomes honest:** `K` certifies indistinguishability on `D_v`
  *restricted to `S`* — which is all the evidence ever supported. "Is the gist
  correct?" becomes "correct on what?", with the answer materialized and readable.
- **`S` is the deopt guard.** At call time, in-scope inputs run the frozen artifact
  with fingerprint-fast-path semantics. An out-of-scope input is *not a failure* — it
  is outside the warrant: it routes to verify (or the molten tier), and the ledger
  records a scope-extension event. This replaces the current exact-match fingerprint
  skip with a membership check (fixing the concrete defect in §9.1).
- **External sources get identity.** A file/URL/API input is recorded in the ledger as
  (locator, snapshot fingerprint, profile). Source drift — the API added a field, the
  log format changed, the PDF's layout generation changed — is detected as a scope
  violation at the boundary and treated as *distribution shift*, i.e. a branch trigger
  with a regime guard (Phase 5), not silent breakage and not a whole-slot rewrite.

**Prior work, named.** The guard-plus-deopt structure is speculative optimization from
JITs (Hölzle–Chambers–Ungar polymorphic inline caches and deoptimization; the guard
discipline of V8/PyPy): compile for the cases you have evidence for, guard the
assumption, deoptimize gracefully outside it. The profile-drift detection is what
TFX Data Validation and Deequ (Schelter et al.) do for ML pipelines. The delta that
makes this ours rather than a bolt-on: **the guard is minted by the same evidence that
licensed the freeze and is part of the certificate** — scope, certificate, and
explanation are one object, and a scope violation feeds the same melt/branch calculus
as any other counterexample.

**Theory note.** Scoping strictly *strengthens* Proposition 3: PAC-indistinguishability
was already only provable on the distribution the search explored; `S` states that
region instead of leaving it implicit. Nothing is weakened; a hidden overclaim is
removed.

---

## 4. Problem 3 — 100k rows: the element-level frontier

**Current state (honest).** Three defects at scale:

- The DataFrame fingerprint is `shape : dtypes : hash(head(5))`
  (`runtime_fingerprint._fingerprint_value`). Two same-shape frames with identical
  first five rows and different tails produce the *same* fingerprint, so verify is
  skipped on data the implementation has never seen — the fast path is silently
  unsound at exactly the scale where it matters (§9.1).
- Verify executes the whole function on the whole input and checks the *aggregate*
  (runs, right type, non-empty). There is no notion of row 50,231 being wrong while
  the other 99,999 are fine.
- The evidence model treats a call as one case. A 100k-row call is not one case; it is
  100k draws from `D_v` — which the current design pays for (cost) without collecting
  on (evidence).

**The mechanism: for collection nodes, the unit of routing is the element.** The
Phase 1 tree already recognizes MAP/FILTER/FOLD shapes; this is the consumer that
makes that recognition pay:

1. **Bulk path.** The frozen artifact runs over all elements — plain compiled Python,
   full speed, no model anywhere.
2. **Per-element monitors.** Element-level type contract + `≈_Y` sanity + scope guard
   `S` evaluated per element (cheap predicates, vectorizable for frames). Elements
   that pass are done.
3. **Residual quarantine.** Failing elements do not abort the run and do not poison
   the bulk: they `ABSTAIN` into a quarantined partition carrying provenance (source
   row ids). Default policy: return bulk results + a typed report of the quarantined
   partition. Opt-in policy (budgeted): route the residual through the molten/
   interpreted tier per element — code for the 99.7%, LLM for the tail. The budget
   gate is mandatory: 100k × LLM must be unreachable by default.
4. **Anneal the residual.** After the run, melt/branch runs *on the quarantined
   partition's profile*: if the failures share a guard-expressible signature (they
   almost always do — a null field, a second date format, an encoding), Phase 5
   synthesizes the separating guard and the node branches into a guarded regime; if
   not, the tail stays honestly molten.

**Cost model.** This is the regime where the freeze economics become dramatic: with
per-element routing, the effective arrival rate at a collection node is rows/call, so
`λ` is 10³–10⁵× a scalar slot's. Freezing the bulk regime early and spending the
counterexample budget there is exactly what IDS allocation (§4 of the plan) already
prescribes; nothing new is needed in the policy layer.

**Big data as an asset, not a burden.** With 100k real rows, `D_v` is *observable*:
`Δ(V)` can be estimated by running the survivor committee over a stratified sample of
actual rows instead of synthesizing inputs, and the discriminating-input search
becomes a *search over real data* — better coverage of the true distribution, and
every disagreement it finds is a real row the user can adjudicate (this is the
mining leg of §6). The detection-efficiency assumption `γ` gets easier to satisfy on
collection slots, not harder.

**Prior work, named.** Semantic-operator systems — LOTUS (Patel et al. 2024),
Palimpzest, DocETL (Shankar et al. 2024) — optimize LLM-vs-code physical plans for
declarative semantic queries; EVAPORATE (Arora et al. 2023) trades direct LLM
extraction against synthesized-code extraction and ensembles code with weak
supervision. Those are the closest neighbors for "code for the bulk, model for the
hard part." The deltas: (i) they choose a plan per *query*; semipy anneals per *node*
over a program's lifetime, with certificates and monotone safety across runs;
(ii) their residual routing is plan choice; ours is per-element **blame** — a monitor
failure with provenance that feeds the same freeze/melt/branch calculus as every
other counterexample; (iii) they have no user-visible contract; here the quarantine
report *is* a contract object (which rows, which monitor, which scope violation).

**Verify at scale, for opaque nodes.** Where the tree does not decompose (whole-frame
opaque transform), verify runs on a stratified sample with a stated bound: for sample
size n and zero observed monitor failures, the failure mass is ≤ ε with confidence
1−δ for n ≥ log(δ)/log(1−ε) — the same certificate arithmetic as §3.1 of the plan,
reused verbatim. Sampled verify replaces whole-input verify above a size threshold;
the certificate records that it was sampled and at what power.

---

## 5. Problem 4 — runtime adjustment: the run as a ledgered transaction

**Current state (honest).** Adaptation happens *between* calls; a call is atomic with
respect to evidence. `melt` fires only inside the generate-contract-gate retry loop
(opt-in, example-kind cases only — the Phase 4 status note). For a long-running map or
a streaming pipeline, "adjust the code throughout the runtime" is currently undefined.

**The mechanism.** A long run is a fast segment of the same evidence stream, so give
it transaction semantics over the ledger:

- **Checkpoints.** A collection run checkpoints (element index, ledger delta) at
  boundaries. Mid-run monitor failures quarantine and continue (§4) — the tail never
  aborts the bulk.
- **Mid-run frontier moves.** At a checkpoint, the kernel may melt/branch the node —
  e.g. the quarantine rate crossed a threshold and a guard was found — and *resume*
  with the new artifact, under one obligation: the new artifact must replay-pass the
  ledgered cases of the already-processed prefix (monotone safety *within* a run,
  the same replay obligation Proposition 1(b) already imposes across runs). Elements
  processed before the move are never silently reinterpreted; if the new regime
  changes their outputs, that is a ledgered branch event and the run report says so.
- **Streaming = online regime detection.** For unbounded streams the branch trigger
  cannot be one-shot; run a sequential test on the per-element monitor-failure rate
  (CUSUM / Wald's SPRT — cheap, classical, correct for this) and treat a detected
  change point as the branch trigger. Prior work, named: drift detection in stream
  learning (DDM, ADWIN — Bifet & Gavaldà). The delta, again: the *response* to drift
  is not a model swap but a certified, guarded, reversible branch with the old
  regime's ledger intact — drift produces forks, not regressions, exactly the Phase 5
  monotone-safety demo, now with the trigger made sequential.

This is deliberately the smallest correct answer: no new calculus, no new operator —
the run borrows the existing four moves and adds only checkpointing and a sequential
trigger.

---

## 6. Problem 5 — test-case construction: label-free oracles first, users last

**Current state (honest).** Evidence arrives from replayed contract cases, user
`assert`s, and the germ-seeded discriminating search. The metamorphic registry is
string-only (two relations — the Phase 4 status note admits the floor is currently
satisfiable only vacuously). Labels come from exactly one trusted source: the user,
consulted ad hoc. That is the scarcity.

**The design principle: order oracles by label cost, and spend the user last.**

**(a) Metamorphic relations — label-free, planned, must actually land.** The Phase 4
prerequisite (typed relations per category: key-order and irrelevant-field invariance
for records, permutation invariance for collections, idempotence for normalizers and
effectful upserts via shadow-world replay) is the cheapest evidence there is: no
labels, pure execution. This brainstorm adds nothing here except priority: it is the
precondition for everything else in this section. Prior work, named: metamorphic
testing (Chen et al. 1998); property-based testing (QuickCheck; Hypothesis) supplies
the input-generation discipline.

**(b) Round-trip laws via synthesized inverses — the bidirectional-programming leg.**
A large fraction of semipy slots are parse / extract / normalize / serialize — i.e.
one leg of a lens (Foster–Greenwald–Moore–Pierce–Schmitt). For such a slot, ask the
proposal sampler for the *other* leg `q` (a printer for a parser, an embedder for an
extractor), and check the round-trip laws on the survivor committee:

```
p(q(y)) ≈_Y y    on generated/observed outputs y      (PutGet)
q(p(x)) ≈_X x    where the slot is lossless on X      (GetPut)
```

Every round-trip violation is a **counterexample with its label built in** — the
inverse converts the committee's own outputs into test cases for free. This is where
bidirectional programming is load-bearing rather than decorative: not "make programs
bidirectional" as a feature, but *use the inverse as a second, cheap oracle*.

Two theory obligations, stated so we do not fool ourselves:

- **Asymmetric trust.** `q` is LLM-proposed and therefore untrusted. A round-trip
  failure must be treated as a *disagreement signal* (it blocks certification and
  seeds the discriminating search), **never** as direct elimination of `p` — otherwise
  a wrong inverse silently eliminates correct candidates. Under this rule a bad `q`
  can only cause false alarms (extra melts, delayed freezes), never a bad freeze:
  the failure mode is conservative. A round-trip case is promoted to eliminating
  evidence only after user adjudication or committee-unanimity under relations.
- **Scope.** Lossy slots (summarize, judge) have no lens; they are exactly the
  `≈_Y`-incomparable population the kernel already routes to the interpreted tier.
  Honest non-convergence covers them; the inverse leg simply does not apply — say so
  rather than pretending it generalizes.

**(c) Mine the stream — the data is the test set.** For collection slots (§4), the
discriminating search runs over *real rows*: find rows where survivors disagree
(committee QBC over actual data), where relations fail, or where round-trips break.
These cost no labels to *find*; the user is asked to adjudicate only the maximally
informative disputed rows — "here are 3 rows where candidates disagree; which output
is right?" — which is classical query-by-committee, already in the plan's vocabulary,
now pointed at real data instead of synthesized inputs.

**(d) The user adjudicates; the user never writes tests.** `pick`/`assert` and the
`why()`-dispute channel (§2) are the only user-facing case-creation surfaces. Cases
are presented as concrete adjudications with provenance, not as a test-authoring
chore. Every adjudication lands in `E` with provenance `user`, the strongest trust
tier.

---

## 7. Problem 6 — the verification gate: a trust hierarchy with no LLM gate in it

**Correcting the premise first.** Today's runtime verify is execution-based, not an
LLM — but semantically weak (compiles, arity, runs, right type, non-empty,
non-identity). The LLM appears as a judge in the semantic re-check and reuse-judge
paths. The thesis already draws the correct line (C1: LLM votes are proposal-side
filters, never gates against the oracle); the actual gap is the oracle's *semantic
strength* — and §§3–6 are precisely the program for strengthening it. Assembled, the
oracle becomes a hierarchy ordered by trust:

| Tier | Oracle | Cost | Trust |
|---|---|---|---|
| 1 | types + scope/regime guards (compiled predicates) | ~0 | checked |
| 2 | contract example cases (ledgered, holdout-split) | replay | checked, needs labels |
| 3 | metamorphic relations (typed registry) | replay | checked, label-free |
| 4 | round-trip / inverse laws (§6b) | replay | checked, label-free, conservative |
| 5 | committee disagreement + licensed search | budgeted | statistical (ε, δ, γ, B) |
| 6 | user adjudication (pick / assert / dispute) | user time | ground truth |

**LLM judgment appears nowhere in this table.** The LLM proposes candidates, relations,
guards, inverses, and disputed rows to surface — and everything it proposes is
compiled, executed, or adjudicated before it gates anything ("the LLM proposes; the
checker disposes," §0). The reuse-judge and semantic-recheck paths survive only as
proposal-side heuristics for *when to spend oracle budget*, never as the oracle.

**The honest answer to "how does it know the output reflects user intent":** it knows
*exactly to the extent of the contract* — no more — and the design makes that extent
(i) visible (`why()`, the certificate scope), (ii) growable at low label cost
(relations, inverses, mining), and (iii) never overclaimed (out-of-scope inputs route
to verify/molten instead of being silently trusted; incomparable outputs never
freeze). The system's epistemic state about intent is a first-class, inspectable
object rather than an implicit property of whatever the validator happens to check.
That is the same honesty structure as Proposition 3's "certifies agreement with the
sampled committee, not closeness to `f*`" — now applied uniformly to the whole
verification story.

---

## 8. Where each mechanism lands (no new phases; extensions of existing ones)

| Mechanism | Extends | Concrete motion |
|---|---|---|
| Contract surface object `C(v)` | Phase 0 ledger + `contract/` | materialize; make it the export unit |
| Scoped certificates `S` + deopt guard (§3) | Phase 3 `FreezeEvent` + Phase 5 guard DSL | profile→predicate synthesis at freeze; membership check replaces fingerprint equality on the fast path |
| External-source identity (§3) | Phase 0 provenance + `documents.py` | (locator, snapshot fp, profile) on ledger entries |
| Element-level frontier (§4) | Phase 1 tree (MAP/FILTER consumers) + Phase 4 monitors | per-element monitors, quarantine partition, residual anneal |
| Sampled verify with (ε, δ) (§4) | Phase 3 certificate arithmetic | size-thresholded; certificate records sampling power |
| Run-as-transaction + sequential trigger (§5) | Phase 4 melt + Phase 5 branch | checkpointing; CUSUM/SPRT on monitor-failure rate |
| Typed metamorphic registry (§6a) | Phase 4 prerequisite (already named) | unchanged, re-prioritized: it gates §§6b–c |
| Inverse synthesis + round-trip oracle (§6b) | Phase 2 population + Phase 3 gates | second proposal call per lens-shaped node; conservative round-trip evidence tier |
| Stream mining / QBC on real rows (§6c) | plan §4 IDS + `decisions/` search | search domain = observed data |
| `why(value)` + dispute channel (§2) | Phase 7 surface + reactivity `DataFlow` | provenance query over ledger; dispute → `assert` |
| `semipy build` (§2) | Phase 7 + `store.py` dispatch | portal → frozen artifacts + contracts + no-LLM runtime |

Nothing above requires a fifth operator or a change to the calculus. The paper gains a
second leg that is genuinely distinct from program annealing yet provably the same
machinery: **the specification and the implementation anneal toward each other** — the
contract surface is the specification's frontier state, and `why()` is its rendering.

---

## 9. Concrete defects found while grounding this (fix independently of everything above)

1. **Fingerprint tail-blindness.** `runtime_fingerprint._fingerprint_value` collapses
   a DataFrame to `shape : dtypes-hash : hash(head(5))`. Same shape + same first five
   rows + different tail ⇒ identical fingerprint ⇒ `skip_verify` ⇒ the cached
   implementation runs unverified on data it has never seen. The fast path is unsound
   at exactly the 100k-row scale problem 3 asks about. Near-term fix: include a
   content signature beyond the head (e.g. sampled-row hash or ndarray-style byte
   prefix, which `np.ndarray` already gets); real fix: replace equality with the
   scope-membership check of §3.
2. **Verify's aggregate blindness on collections.** `verify_runtime_execution` checks
   the aggregate result only; a 0.3% per-row failure rate is invisible. Subsumed by §4,
   but a cheap interim monitor (null-rate / empty-rate delta on output vs input) is
   worth having.
3. **External inputs have no ledger identity.** `documents.py` materializes PDF text
   with no durable (locator, snapshot, profile) record — a silent-drift channel today,
   independent of the full §3 design.

## 10. Risks, honestly

- **Surface-area creep.** `C(v)` bundles seven components; the failure mode is a
  Frankenstein contract object nobody maintains. Containment: every component except
  `S` already exists in some form in the plan; `C(v)` is a *view*, not a new store —
  if a component has no backing evidence, it is absent, not stubbed.
- **Scope guards can overfit the observed profile** (too-tight `S` ⇒ constant deopts ⇒
  molten-cost regression). Mitigation: `S` synthesized at the coarsest level that
  separates the evidence (MDL again — the same objective that arbitrates
  generalize-vs-branch arbitrates tight-vs-loose scope), and deopt frequency is a
  ledger statistic that feeds scope relaxation.
- **Inverse quality.** If lens-shaped slots are rarer in real corpora than expected,
  §6b's yield is low. Measurable early: classify the existing slot corpus (the Phase 1
  go/no-go corpus) by lens-shape; if the fraction is small, §6b demotes to an appendix
  and §6a/§6c carry the evidence-growth story.
- **Per-element monitors cost.** Vectorizable predicates are cheap on frames; opaque
  Python objects per element are not. The monitor set must be tiered by cost, and the
  certificate must record which tier actually ran.
