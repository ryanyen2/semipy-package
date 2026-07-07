---
date: 2026-07-04
topic: incremental-formalization
status: unifying thesis (supersedes the framing question left open by the 07-03 and 07-04 brainstorms)
related:
  - docs/brainstorms/2026-07-03-programmable-neural-programs-requirements.md
  - docs/brainstorms/2026-07-03-programmable-neural-programs-design-inspiration.md
  - docs/brainstorms/2026-07-04-learning-around-generation-pipeline-synthesis.md
---

# Incremental Formalization

> **A semiformal program is a program that converges toward formality under an execution
> oracle.** At every moment, each part of a slot's implementation is either *frozen* (formal:
> fixed, guaranteed, versioned) or *molten* (fuzzy: adaptive, LLM-mediated). The boundary
> between them — the **formalization frontier** — moves only on execution evidence: parts
> freeze when the oracle can no longer distinguish them from a fixed artifact, and melt when
> a failing case blames them. The version-control DAG is not storage for code blobs; it is
> **the ledger of frontier moves**. Learning *is* the trajectory of the frontier.

This is the one idea. Everything in the two prior brainstorms is either a mechanism inside it,
an instrument of it, or cut.

---

## 1. Why the prior plans read as a Frankenstein

The 07-04 synthesis is a *learning loop around the generator* (curate priors, optimize prompts,
calibrate triggers). The 07-03 requirements are a *runtime representation* (typed holes, lift,
solidification lattice). Each is internally coherent, but implemented together they are two
research programs stapled at the hip: one improves the *pipeline*, one improves the *program*,
and neither states what the system as a whole is converging *to*. GEPA-tuned prompts plus a
semiring interpreter plus a curation policy is a pile of mechanisms with no invariant.

The user intuition is correct: the novelty is **incremental formalization** — over time the
system learns the *shape* of the program, so that only small regions still require adaptation,
while accumulated shape carries guarantees. Both prior docs contain fragments of this
(the solidification lattice; the promotion test; the sketch library; C3 "delta not rewrite")
but neither names it as the object of study. Once named, it dictates what stays and what goes.

---

## 2. The core identity: hardness ≡ version-control cost of change

Represent a slot's implementation as a typed tree (the recognized combinator region of the
07-03 IR where it applies; an opaque block elsewhere). Every node carries a **hardness state**:

| State | Execution | What a change costs | Guarantee carried |
|---|---|---|---|
| **molten** | LLM-interpreted per call (memoized) | nothing — variation is runtime behavior | type + abstention only |
| **plastic** | generated code, current head | a **commit** (ADAPT: local regeneration) | passes current cases |
| **frozen** | fixed code + deopt guard | a **branch** (structural event) | evidence ledger: cases + a failed counterexample search |

The identity that fuses "version control", "continuous learning", and "weights-as-program"
into one thing:

> **The hardness of a node is defined as the version-control operation required to change it.**

- Molten change = no VC event (it is interpretation).
- Plastic change = commit (ordinary iteration).
- Frozen change = branch (a semantic commitment was violated; history must fork, because the
  old commitment is still valid for the old regime).

Under this identity, "the slots learn the shape of the program" has a precise meaning: the
frontier advances (nodes move molten → plastic → frozen), the DAG records each advance with
its justification, and the still-molten residue is exactly the part of the program that is
*irreducibly semantic for the evidence seen so far*. Continuous learning is not a subsystem;
it is the observable movement of this frontier. Weights-as-program is not a competing
approach; it is one hardness tier (§4).

semipy already visualizes this without knowing it: the VS Code extension's *opacity =
durability* channel is literally the frontier rendered. The product story is "watch your
program solidify."

---

## 3. The calculus: freeze, melt, branch, merge

Four operators, each gated by the execution oracle, each a first-class DAG event.

### freeze(node)

A node may freeze only when three gates pass:

1. **Reproducibility** — a fixed artifact reproduces the node's behavior on *held-out*
   evidence (the interpreted-mode promotion test, generalized from whole-slot to node).
2. **Compression** — freezing shrinks the evidence-weighted description length of the portal
   (DreamCoder/Stitch MDL gate; a shape seen once is negative compression and does not freeze).
3. **Failed counterexample search** — a budgeted, germ-seeded discriminating-input search
   (the decisions subsystem's machinery) *tries to find an input where candidate
   crystallizations diverge, and fails*. Freezing is licensed by the absence of discoverable
   forks, not by pass-rate on happenstance cases (the Weiss RNN→DFA stopping rule).

The freeze commit records its justification: the case set, the search budget spent, the
candidates rejected. This is the **evidence ledger** — the contract subsystem, given a new job.

### melt(node) — blame decides locality

A failing end-to-end case replays the trace; the shallowest node whose monitor (type contract
+ metamorphic relations) fails is blamed (Wadler–Findler: the well-typed symbolic surround is
never blamed).

- **Blamed node is plastic or molten** → local re-adaptation, one commit, the rest of the tree
  untouched. This is the ordinary case and the direct cash-out of "only several sections
  require adaptation": adaptation cost is proportional to the molten region, not the slot.
- **Blamed node is frozen** → a structural fault. The freeze's semantic commitment was wrong
  *for this input*. Never patch a frozen node in place — that would silently rewrite history
  that other evidence still supports. Two escalations, chosen by the same MDL objective that
  gated the freeze:

### The structural-rewrite question, answered

*"What if a new case requires a complete rewrite of structure?"* — the case blames a frozen
node (possibly the root). Two responses:

- **Generalize in place**: melt up to the lowest frozen ancestor that must change, and
  refreeze a single structure that covers the old evidence *and* the new case
  (anti-unification of the two shapes). Choose this when one structure describes both case
  populations more cheaply than two.
- **Branch with a regime guard**: fork the slot. The old branch keeps its frozen structure
  and its guarantees — every case it ever passed still passes, forever. The new branch melts
  the blamed structure and re-crystallizes around the new evidence. The two branches acquire
  a **regime guard**: a predicate discriminating their case populations, learned by the same
  divergence-clustering the decisions subsystem already does. Runtime dispatch selects the
  branch by guard. Choose this when the joint description as one structure is longer than as
  two guarded structures.

So branching is not an exception path; it is what version control is *for* in this paradigm:
**a branch is a regime**. Distribution shift produces forks, not regressions. And:

### merge(branches)

When a candidate unifying structure passes both branches' case sets and a fresh
counterexample search finds no input that separates them, and MDL favors unification, the
branches merge. Merging is a *verified event*, never shape-congruence alone (the 07-04
false-merge risk, now a theorem obligation rather than a heuristic warning).

**The monotone-safety invariant** (the paradigm's headline guarantee): once a case passes on
a frozen node, it never silently regresses — any violation necessarily produces a visible
melt-or-branch event in the ledger. Formal programs get this from static semantics;
semiformal programs get it from the calculus.

---

## 4. Artifact formalization vs. weight freezing: the same operator

The either/or dissolves under one observation: **freezing is always shape-first; parameters
freeze last.** Every tier has the same schema — a frozen shape with molten residue inside:

- A **sketch** is a frozen code skeleton with molten *literal* parameters (semipy's library
  already is incremental formalization across slots — it just isn't recursive or evidence-gated).
- A **local kernel** (Text-to-LoRA-style adapter) is a frozen computation graph with molten
  *continuous* parameters.
- A **frozen code node** is shape and parameters both fixed.

"Artifact formalization" and "weight incremental freezing" are the freeze operator
instantiated in two parameter spaces. The calculus is tier-agnostic; the lattice
(interpreted-LLM → kernel → symbolic) from 07-03 is the hardness scale, and a tier transition
is just a freeze/melt with a different artifact type.

**Decision: v1 instantiates the code tier only.** It is fully verifiable by the oracle, needs
no GPUs, and carries all three claims (§7). The weight tier is a stated rung of the same
lattice, added later for the offline/cost story — this keeps PAW as a cited backend, not a
reconstruction target, exactly as 07-03 already resolved.

---

## 5. Every existing subsystem re-slots (nothing is wasted)

| Subsystem | Role in the calculus |
|---|---|
| **Version-control DAG** (`history/`) | The ledger. Commit = plastic change or freeze; branch = regime fork; merge = verified unification. The unit of versioning becomes the *frontier move + justification*, not the code blob. |
| **Contract** (`contract/`) | The evidence ledger backing every freeze; per-case outcomes (07-04 P0) are the raw material. Retirement-on-spec-change already models "user moved the frontier from above." |
| **Decisions** (`decisions/`) | An open `#?` fork *is* a molten structural node with enumerated candidate crystallizations. `pick` = a human-performed freeze; `assert` = a human-supplied monitor. **Human steering and autonomous learning are the same commit type** — the frontier is steerable. The germ-seeded discriminating-input search is the counterexample gate that licenses every freeze and every merge. |
| **Sketch library** (`library/`) | Cross-slot freezing: a shape that recurs freezes *as a shared skeleton* with per-site molten parameters (recurrence-gated, 07-04 P2). INSTANTIATE = reusing frozen shape, re-melting only parameters. |
| **Interpreted mode** (`interpreted.py`) | The molten tier, already built — including the honest non-convergence story (summarize/judge never promote). Today's promotion is whole-slot, one-shot, binary; the calculus makes it per-node, evidence-gated, reversible. |
| **Reactivity** (`reactivity/`) | Frontier moves propagate: an upstream freeze/melt invalidates exactly the downstream consumers of the changed node (Hazel fill-and-resume gives the resume semantics). |
| **Routing** (`routing.py`) | The deterministic cascade stays; "when to be flexible" stops being a calibration problem because the melt rule *is* the trigger — blame, staleness, and guard trips, all oracle-derived. |
| **07-03 IR / lift / blame** | The substrate. The factoring rule (`cata(formal) . fmap(leaf)`) is a *free frontier move*: derived structure freezes at birth, so pushing leaves deep maximally shrinks the molten surface. Blame is the melt-localization mechanism. Shape-invariance becomes a supporting mechanism, not the headline. |
| **VS Code extension** | The frontier made visible: opacity = hardness, `#?` = molten structure, branches = regimes in the version tree. |

---

## 6. What gets cut or demoted (the anti-Frankenstein list)

- **GEPA / prompt optimization (07-04 P4–P5): cut from the research core.** It learns the
  *generator*, not the *program* — a different contribution that competes for identity and
  adds a reward-hacking surface. Keep as an engineering appendix at most. (07-04 §7 Q3,
  resolved: no.)
- **Calibrated trigger ladder (07-04 P3): subsumed.** The melt rule replaces the residual
  scalar; keep only the deterministic cheapest-first cascade that already exists.
- **Weight/kernel tier (Text-to-LoRA rung): deferred**, stated as lattice generality (§4).
- **Full semiring-parameterized interpreter: trimmed to what the frontier needs** — a typed
  decomposition with per-node hardness, monitors, and molten-node execution. Candidate-set
  and confidence modes are nice-to-haves, not v1.
- **Kept and re-founded**: P0 (persisted case outcomes → evidence ledger), P1
  (execution-based selection → choosing *which* crystallization freezes), C4
  (discriminating-input search → the freeze/merge license; still the funded substrate),
  P2 (recurrence-gated abstraction → cross-slot freezing).

---

## 7. Claims and evaluation (lifetime benchmarks, not snapshots)

The evaluation object is a slot's *lifetime* under an input stream — this is itself a framing
novelty; the verify-LLM-code literature evaluates single generations.

1. **Amortization.** LLM cost per input decreases as the frontier advances; frozen fraction
   F(slot) rises for shape-stable slots and correctly plateaus below 1 for irreducibly
   semantic ones (the counterexample search keeps finding forks — honest non-convergence).
2. **Locality.** Adaptation touches only the molten region: measure regenerated-region size
   per change vs. the whole-slot-regeneration baseline (current semipy) and vs.
   recompile-everything (PAW-style).
3. **Safety under regime shift.** Inject a distribution shift mid-stream: a branch with a
   regime guard forms; every previously-passing case still passes (zero silent regressions,
   the monotone-safety invariant); baselines either regress the old regime or fork the
   whole artifact by hand.

Baselines: current semipy (binary whole-slot promotion), regenerate-always, one-shot compile
(PAW-style). Ablations: freeze without the counterexample gate (expect false freezes → later
branch storms); branch without MDL (expect speciation churn); melt without blame (expect
whole-slot thrash).

---

## 8. Novelty positioning (fact-checked 2026-07-04)

Web-checked against the current neighborhood; the lane is open.

- **Intent formalization as a grand challenge** (arXiv:2603.17150) *names the problem* —
  reliable coding requires formalizing intent — but proposes no lifetime mechanism. We supply
  one: formalization as an evidence-gated runtime process with a ledger.
- **Verify-once / CEGIS / spec-synthesis** (AutoSpec arXiv:2404.00762, SCAFFOLD-CEGIS
  arXiv:2603.08520, DL4C constrained-decoding work): hardening happens *at generation time,
  once*. No lifetime, no melt, no versioned frontier, no regimes.
- **Gradual typing / migratory typing / gradual verification** (Siek & Taha 2006;
  Tobin-Hochstadt & Felleisen; Bader, Aldrich & Tanter 2018): the closest conceptual
  ancestor — programs migrate from dynamic to static with blame at boundaries. But migration
  is *human-driven* and migrates *types/proofs*; ours is *evidence-driven* and migrates
  *semantics*. Wadler–Findler blame carries over intact ("the interpreter can't be blamed").
- **"Incremental formalization" in HCI** (Shipman & McCall, ~1994): the term's lineage —
  systems should let users add formal structure gradually instead of demanding it upfront.
  We mechanize the same principle with an execution oracle doing the formalizing. Cite it;
  it strengthens rather than threatens the claim.
- **Specification mining / invariant detection** (Ammons et al. 2002; Daikon): infer formal
  properties from executions, but never change the program's execution regime, and nothing
  is versioned or reversible.
- **Tiered JIT + deoptimization**: our operational metaphor, with the decisive difference
  that a JIT is *semantics-preserving by construction* (the spec is total), so it never needs
  a ledger or a branch. Freezing under underspecification is a *semantic commitment*, which
  is exactly why the evidence ledger and the branch-as-regime exist.
- **DreamCoder / Stitch / LILO**: compression-driven freezing of shared structure — our MDL
  gate — but no melt, no regimes, no underspecified source, no runtime.
- **PAW** (arXiv:2607.02512): total one-shot freeze into an opaque artifact; the anti-thesis
  that motivates incrementality, structure, and reversibility.
- **ContextBranch** (arXiv:2512.13914): version-control semantics for LLM *conversations*;
  no program artifact, no oracle, no crystallization.
- **Class-incremental weight freezing** (PackNet/EWC lineage; e.g. arXiv:2512.03537):
  freezes to prevent forgetting, with no program semantics and no oracle-gated justification.

The composite claim — *a calculus of oracle-gated freeze/melt/branch/merge over a typed
program with underspecified parts, where version control is the ledger of semantic
commitments and branches are regimes* — has no occupant.

---

## 9. Minimal implementation path (order of dependency, not a schedule)

1. **Evidence ledger**: persist per-case outcomes (07-04 P0, unchanged — it was already the
   prerequisite for everything).
2. **Node-level representation**: generalize the sketch schema (skeleton + holes) to a
   recursive typed decomposition with a hardness field per node; opaque-block fallback keeps
   the Python-surround identity.
3. **freeze**: wire the three gates — held-out reproducibility (generalize interpreted-mode
   promotion), MDL (Stitch-style bounded search over the portal), counterexample license
   (existing germ-seeded search, budgeted).
4. **melt**: per-node monitors + trace-replay blame (07-03 R12); plastic melt = existing
   ADAPT, localized.
5. **branch/merge with regime guards**: extend the DAG event vocabulary; learn guards from
   the decisions clustering; verified merge.
6. **Surface it**: hardness → opacity, freeze justifications → hover card, regimes → version
   tree. The extension already has the visual language.

Each step is independently shippable and independently measurable against §7.

---

## 10. Open questions

- **Granularity floor**: how fine does node-level hardness go before monitor coverage (no
  contract, no blame) makes freezing unjustifiable? Likely answer: freeze only nodes with at
  least one discriminating case *and* one metamorphic relation; otherwise they stay plastic.
- **Guard representation**: regime guards must be cheap, deterministic predicates (frozen
  themselves). Learned from case-population diffs — but what language? (Start: the same
  typed-predicate fragment sketch parameters use.)
- **Budget policy for the counterexample search** (07-04 §7 Q4 survives): the freeze license
  is the main LLM cost center; MDL gain should set the search budget (spend more to license
  freezes that compress more).
- **Cross-slot frontier**: does a library-level freeze (shared skeleton) create a coupling
  where one slot's melt must notify siblings? (Reactivity's edge model likely covers it.)
