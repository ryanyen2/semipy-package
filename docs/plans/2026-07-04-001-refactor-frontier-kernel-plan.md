---
date: 2026-07-04
topic: the frontier kernel — a new inference engine + the refactor that builds it
status: method proposal + refactoring plan (implements the 07-04 incremental-formalization thesis)
related:
  - docs/brainstorms/2026-07-04-incremental-formalization-thesis.md
  - docs/brainstorms/2026-07-03-programmable-neural-programs-requirements.md
  - docs/brainstorms/2026-07-04-learning-around-generation-pipeline-synthesis.md
---

# Program Annealing: Sequential Inference over a Behavioral Version Space

**The method in one sentence:** a semiformal program is executed as **oracle-gated
maintenance of a behavioral version space** — the LLM proposes candidate implementations,
the execution oracle eliminates the ones evidence rules out, and a calculus of four
certified moves (freeze/melt/branch/merge) advances each part of the program from
LLM-interpreted toward fixed code as evidence licenses it — so that "the program" at any
moment is the current frontier state, and version control is the ledger of the moves that
produced it. A Bayesian reading (LLM = prior, oracle = likelihood, the surviving candidate
set = a posterior's support) is a useful guide to the knobs; the guarantees in §4 are proved
for the version-space object directly and do *not* depend on it.

This document has three parts. **Part I** is the method and its math — the new inference
engine. **Part II** is the novelty case relative to PAW and the rest of the neighborhood.
**Part III** is the refactoring plan that rebuilds semipy around this engine as one cohesive
pipeline, absorbing continuous learning, incremental formalization, and past-program
management as facets of a single algorithm rather than subsystems stapled together.

---

# Part I — The method

## 1. The reframe that produces the engine

PAW asks: *how do we compile a spec into a reusable artifact?* — a point estimate, computed
once. The 07-04 thesis asks: *how does a program converge to formality under evidence?* —
and gives the calculus (freeze/melt/branch/merge) but not the inference-theoretic object the
calculus operates on. This proposal supplies that object:

> **A slot never holds "an implementation." It holds a posterior over implementations,
> represented as a typed tree whose frozen nodes are collapsed (delta) posteriors and whose
> molten nodes are particle populations. Executing the slot is sampling from this posterior;
> learning is posterior update; formalization is certified posterior collapse.**

Three identifications ground this correspondence, and each lands on machinery semipy already
has (the correspondence guides design; §4's guarantees are proved for the version-space
object, not derived from a program-space posterior):

1. **The LLM is an amortized sampler of the semantic prior** `q(p | s)` — the distribution
   over programs a competent reader would take spec `s` to mean. This is precisely what
   pretraining internalizes and precisely what the LLM *cannot* do: score its own samples
   against reality. (The 07-04 synthesis's unanimous finding: verbalized confidence is
   epistemically vacuous for code.)
2. **The execution oracle is the likelihood** `L(E | p)`: contract-case replay, type
   contracts, metamorphic relations, effect-script diffs. Deterministic, cheap, trusted.
3. **The decisions subsystem is already a particle filter that doesn't know it.** The
   multi-candidate draw is proposal sampling from `q(p | s, E)`; divergence clustering is
   the *behavioral quotient* (collapsing particles that are observationally equal on the
   evidence); the `#?` forks are the posterior's surviving modes rendered to the user; the
   germ-seeded discriminating-input search is query-by-committee active learning over the
   particle committee. `pick` is a human-supplied posterior collapse; `assert` is a
   human-supplied likelihood term. Nothing new has to be invented here — it has to be
   *promoted from an opt-in surface to the engine*.

Read as an inference algorithm, the closest analogy is **an SMC-style loop over typed program
trees — proposal = LLM, likelihood = execution, resampling ≈ version-control commit,
rejuvenation ≈ melt, mixture splitting ≈ branch** — but the analogy is a guide, not the
claim; what is actually maintained is a finite behavioral version space pruned by the oracle
and collapsed by certificate (§3). We call the runtime the **frontier kernel** and the method
**program annealing** (a node is heated —
molten, adaptive — while evidence is scarce or contradictory, and cools into frozen
structure as evidence accumulates; a blaming counterexample locally reheats it — annealing
out the defect rather than recasting the whole part).

## 2. Formal objects

Fix a slot with spec `s` (NL + types from the formal surround). Let:

- `P` — the typed program space: terms over the combinator core
  (`map/filter/fold/branch/compose` + typed fuzzy leaves) with opaque Python blocks as
  fallback (the 07-03 recognition boundary; guarantees hold in the recognized region).
- `f*` — the user's intent: a partial function `X ⇀ Y` accessible **only** through oracles
  (cases, assertions, blame on end-to-end failures). Underspecification means `f*` is not
  determined by `s`; it is progressively pinned down by evidence.
- `E_t` — the evidence ledger at time `t`: typed input/output records with pass/fail
  outcomes, metamorphic relations, user assertions, each tagged with provenance and a
  train/held-out split.
- `D` — the (unknown, possibly shifting) input distribution, observed as the call stream.

**State.** A semiformal program state is `σ_t = (T_t, h_t, Π_t, E_t)`:

- `T_t` — a typed tree (the node-level generalization of the sketch schema: skeleton +
  holes, recursively).
- `h_t : nodes(T_t) → {molten, plastic, frozen}` — the hardness field. **molten** = no
  committed code, executed per call by the LLM (memoized); **plastic** = a committed code
  artifact at the current head, replaceable by one commit; **frozen** = a fixed code artifact
  plus a deopt guard, changeable only by a ledgered branch/melt. The **frontier** is the
  boundary of `h⁻¹(frozen)`.
- `Π_t(v)` for each non-frozen node `v` — a finite population `{p_1, …, p_k}` of candidate
  subprograms, *quotiented by behavioral equivalence on `E_t`*: `p ≡_E p'` iff they agree
  (under the node's observational equivalence `≈_Y`, below) on every input in `E`. Particles
  carry no probability weight — survival is binary (a particle that fails any evidence item is
  eliminated), and the head is chosen by execution score (Phase 2), not by posterior mass. A
  frozen node is the degenerate population `{p̂}` plus a deopt guard.

**What is maintained.** Per node, the object is a **behavioral version space**: the set of
proposed programs consistent with the evidence so far, quotiented by `≈_Y` (Mitchell's version
space and Lau's version-space algebra are the lineage — see Part II). The LLM supplies
candidates; the oracle eliminates the inconsistent ones; no program-space density is ever
computed. A Bayesian reader may see `q(p|s)` as a prior and a 0/1 `L(E_t|p)` as a likelihood,
making the surviving set the support of a posterior — but the algorithm uses only membership
(survived / eliminated) and execution score, never a normalized weight, so nothing downstream
depends on the density existing. We compare programs only by the pushforward of their behavior
onto the evidence, never syntactically — the version space over `≈_Y`-classes is the object
that matters for execution.

**Disagreement mass (the central scalar).** For a population `V` at node `v`, relative to a
type-indexed **observational equivalence** `≈_Y` on the node's output type:

```
Δ(V) = Pr_{x ~ D_v} [ ∃ p, p' ∈ supp(V) : p(x) ≉_Y p'(x) ]
```

where `D_v` is the input distribution *at that node* (pushforward of `D` through the frozen
surround), and `≈_Y` is supplied by the type layer: exact equality for canonical types
(labels, ints, parsed records); normalization/tolerance/projection for floats, unordered
collections, and timestamp/UUID-bearing records; effect-script equivalence for effectful
nodes; and **declared incomparable** for free text. A node whose output type has no usable
`≈_Y`, or whose behavior is not deterministic-or-seeded (so `p(x)` is ill-defined), is
**never freeze-eligible** and routes to the interpreted tier by construction — this is where
honest non-convergence (§4) comes from, not by accident but as the intended behavior for
summarize/judge. `Δ` is the mass where survivors still disagree under `≈_Y`, the quantity the
germ-seeded search probes; `Δ(V) = 0` means the remaining uncertainty is *behaviorally
invisible under `≈_Y`* — any survivor may be committed without observable consequence.
**This is the method's scope, stated plainly:** its guarantees are strong exactly for nodes
whose output type admits a decidable `≈_Y` and whose behavior is deterministic-or-seeded
(typed transforms, extraction, classification, parsing, seeded effectful steps), and it
deliberately abstains elsewhere rather than freezing something it cannot check — so
"works for general programming tasks" means *the code-tier certificates cover the
comparable-output fragment, and everything else stays honestly molten*, not that every
imaginable slot freezes.

**Execution semantics.** `execute(σ, x)`: traverse `T`; frozen/plastic nodes run their
artifact; a molten node either (a) runs its current MAP particle while logging the
committee's disagreement on `x` (cheap mode), or (b) runs interpreted (per-call LLM,
memoized) when no particle survives or the node is irreducibly semantic (current
`interpreted.py` behavior, generalized from whole-slot to node). Every execution appends to
`E`. `ABSTAIN` is a first-class value that short-circuits composition.

## 3. The four moves as posterior updates, with their licenses

Each calculus operator from the thesis becomes a *typed inference move with a stated
statistical license*. This is what turns the calculus from a design into a method.

### 3.1 `freeze(v)` — certified posterior collapse (optimal stopping)

Freezing is a **decision-theoretic stopping problem**, not a heuristic promotion. Costs:
`c_m` per molten/interpreted call, `c_f ≈ 0` per frozen call, `c_e` per melt event (the
cost of a wrong commitment surfacing later: regeneration + user disruption), arrival rate
`λ` for this slot. Per call, staying molten costs `λ · c_m`; freezing risks
`λ · γ_e · Δ(V) · c_e`, where `γ_e` is the fraction of disagreement inputs that actually
surface as failures. Setting the two per-call rates equal (`λ` cancels — both scale with call
volume) gives the **myopic break-even index**:

```
freeze when  Δ(V) ≤ ε*   where   ε* = c_m / (γ_e · c_e)
```

`ε*` is a static break-even, not the full optimal-stopping solution — a complete treatment
adds the option value of staying molten (evidence keeps arriving, so `Δ` is still shrinking)
and treats `c_e` as a one-time melt cost rather than a per-call charge; that refinement is
Proposition 5 territory. At face value `ε*` still does the useful work: cheap slots with
catastrophic error costs freeze late or never; expensive slots with tolerant consumers freeze
early. The honest claim is not that calibration is *eliminated* but that it is **relocated
into interpretable cost estimation** — the one opaque "when to be flexible" threshold is
replaced by three auditable costs (`c_m`, `c_e`, `γ_e`), each with a ledger-based estimation
procedure (§7), instead of a scalar tuned against a metric. (`λ` does not appear in `ε*`; it
re-enters only in IDS budget allocation, §4, where absolute call volume matters.)

Since `Δ` is not observable, freezing requires a **certificate**, which is where the three
gates from the thesis acquire their statistical meaning:

- **Counterexample license = a hypothesis test on Δ.** Model the discriminating-input
  search as a sampler with **detection efficiency `γ`**: on a node whose disagreement mass is
  `μ`, each query surfaces a disagreeing input with probability `≥ γ·μ` — i.e. the search is
  at least `γ` times as efficient as i.i.d. sampling from `D_v` (`γ ≥ 1` is what germ seeding
  and committee-split steering are meant to buy, and it is directly testable by injecting
  known disagreements and measuring hit rate). Under this model, if the true mass exceeds the
  target (`Δ(V) > ε`), each query independently misses with probability `≤ 1 − γε`, so a
  failed search of

  ```
  n ≥ log(δ) / log(1 − γε)
  ```

  queries rejects `H₀ : Δ(V) > ε` at confidence `1 − δ`. (This `γ·μ` model is what makes the
  formula follow, and makes §3.1 consistent with Proposition 4's per-epoch bound
  `1 − (1−γε)^B`.) Freezing is licensed by the
  *absence of discoverable forks at a stated power and budget* — the Weiss RNN→DFA stopping
  rule, now with a quantitative reading. The license (budget spent, power assumed, ε, δ) is
  recorded in the freeze event: **the ledger stores certificates, not vibes.**
- **MDL gate = Bayesian model selection.** Freezing node `v` to `p̂` is accepted only if it
  shrinks the evidence-weighted description length of the portal
  (`ℓ(p̂) + ℓ(E | p̂) < ℓ(molten v) + ℓ(E | Π)` — log posterior odds). A shape seen once is
  negative compression and does not freeze. The same objective — this is the load-bearing
  unification — decides *generalize-in-place vs branch* (§3.3) and *merge* (§3.4).
- **Held-out reproducibility = protection against selection overfitting.** The committed
  particle must reproduce evidence *not used to select it* (the ledger's holdout split —
  the interpreted-mode promotion test, generalized). Without this, freezing selects the
  particle that memorized the cases (the 07-04 reward-hacking risk, closed structurally).

### 3.2 `melt(v)` — rejuvenation, localized by blame

A failing end-to-end case `e†` is a **zero-likelihood event** for some committed particle.
Trace replay over `T` finds the shallowest node whose monitor (type contract + metamorphic
relations) fails — Wadler–Findler blame: the well-typed frozen surround is never blamed.
Then:

- **Blamed node plastic/molten** → local rejuvenation: resample that node's population from
  the proposal *conditioned on the updated ledger*, `q(p | s, E ∪ {e†})` — one commit, rest
  of the tree untouched. This is why delta-adaptation beats whole-slot rewrite and why it
  is not a style preference: rejuvenation is a **local kernel move that preserves the
  posterior already accumulated in unblamed subtrees**. Whole-slot regeneration throws away
  certified posterior mass; the engine makes that a type error.
- **Blamed node frozen** → structural fault: the freeze's certificate has been *empirically
  falsified* (a disagreement input was found post-hoc that the licensed search missed).
  Never patch in place — that silently rewrites a commitment other evidence still supports.
  Escalate to §3.3, and *feed `e†` back to the search as a germ*: every certificate
  falsification is training signal for the searcher's detection power.

Adaptation cost is therefore proportional to `|molten region|`, not `|slot|` — the
locality claim, now a property of the inference algorithm rather than an aspiration.

### 3.3 `branch(v, g)` — mixture discovery (regime = mixture component)

When one structure cannot explain both the old evidence and the new case cheaply, the
posterior is better modeled as an **input-gated mixture**:

```
p(y | x) = Σ_k  1[g_k(x)] · p_k(y | x)
```

with guards `g_k` from a **closed typed-predicate DSL** (comparisons and null/empty/shape
tests over the node's typed inputs) that must be designed for this purpose — it does *not*
exist today (current decision guards are free-text LLM strings, and sketch parameters are
token-substitution holes, not predicates; see Phase 5). Guards are proposed by the LLM from
the divergence clusters, then compiled into the DSL and validated; a guard that does not
compile is rejected and the node stays molten rather than dispatching on an unverified
predicate. The MDL objective arbitrates: **generalize in place**
(anti-unify, one structure) when the joint description is shorter; **branch with a regime
guard** when two guarded structures are shorter. A branch is a mixture component with its
own frozen sub-posterior and its own guarantees — distribution shift produces forks, not
regressions. Runtime dispatch evaluates guards (deterministic, frozen) before the tree.

### 3.4 `merge(b₁, b₂)` — verified mixture collapse

Merge when a candidate unified structure (i) passes both branches' ledgers, (ii) survives a
fresh separation search (same hypothesis test as §3.1, applied to the *pair*), and (iii)
MDL favors the union. Merge-on-shape-congruence is structurally impossible — the false-merge
risk becomes a discharged proof obligation.

## 4. What the theory buys (the paper's formal spine)

Stated as propositions with the proof shape; full proofs are paper work, not code work.

1. **Monotone safety** (the headline invariant), stated at two levels. *(a) Node-local
   (unconditional):* a frozen node is an immutable, deterministic-or-seeded artifact, so on
   any input it previously passed it returns the same value — it cannot silently regress
   itself. *(b) End-to-end (conditional on a replay obligation):* a previously-passing
   end-to-end case can still break when an upstream melt/branch shifts a frozen node's input
   distribution `D_v`; the claim "any such violation produces a ledger event" holds only if
   every melt/branch/merge triggers replay of the affected downstream consumers' case ledgers
   (the affected set comes from reactivity's edge model), with the replay outcome recorded on
   the event. Under that obligation — whose replay cost is an explicit cost-model line item —
   regressions are never silent: they surface as a melt or branch in the ledger. Formal
   programs get level (a) from static semantics; semiformal programs get (a)+(b) from the
   calculus plus the replay obligation. No baseline (regenerate-always, PAW-style recompile,
   current whole-slot semipy) has either.
2. **Blame soundness.** The frozen well-typed surround is never blamed; if node monitors
   are complete for a failure class, the blamed node is the true fault site. (Wadler–Findler
   transplanted; the "no contract, no blame" granularity floor from the thesis becomes a
   side condition: nodes without a discriminating case and a metamorphic relation are not
   freeze-eligible.)
3. **Certified freezing (PAC-indistinguishability), within the sampled population.** Under
   the detection-efficiency assumption, a licensed freeze guarantees
   `Pr_{x~D_v}[frozen output ≉_Y some surviving candidate's output] ≤ ε` w.p. `≥ 1 − δ`. Read
   this precisely: it certifies that the committed program is indistinguishable (under `≈_Y`)
   from *the other survivors the LLM actually sampled* — **not** that it is close to the true
   intent `f*`. The two coincide only under the proposal-coverage assumption (Prop 4): if
   every LLM sample shares the same systematic error, `Δ(V) = 0` while the program is wrong,
   and the certificate — honestly — certifies only that the committee agreed. This is the
   central limitation, stated in the proposition rather than buried.
4. **Honest non-convergence** (conditional converse of 3). *Assume proposal coverage:* on any
   region of mass `≥ ε` where truth-consistent and truth-violating behaviors differ, the
   proposal `q(p|s,E)` places mass `≥ β` on a behaviorally distinct alternative. Then if no
   program in `P` is `ε`-close to `f*` (irreducibly contextual intent: summarize, judge), the
   committee keeps producing disagreeing survivors, the search finds a counterexample each
   epoch w.p. `≥ 1 − (1−γε)^B`, and freezing stays unlicensed — the node stays molten rather
   than silently wrong. **Without proposal coverage this claim fails** (correlated LLM samples
   can agree on a wrong answer), so proposal coverage is named alongside detection efficiency
   as the *second* load-bearing assumption (§7), and the free-text-`≈_Y`-incomparable route
   (§2) is the unconditional fallback that keeps summarize/judge molten regardless of sample
   correlation. Scoped this way — node-level certified refusal to formalize at a stated power
   and budget — the property has no occupant in the synthesis / gradual-typing /
   continual-learning neighborhood (selective-prediction abstains per *output*; this abstains
   per *program part* over a lifetime, with a ledger).
5. **Amortization / regret.** Under the stopping rule, expected lifetime cost is within an
   additive term (search budget + time-to-freeze) of the clairvoyant policy that knows
   `f*` from call one; committee-based discrimination inherits query-by-committee's
   information-gain efficiency over random case accumulation. (This is the "work on the
   math" growth area: a clean regret bound vs the clairvoyant under stationary `D`, and a
   switching-cost bound under regime shift where branches pay `O(#regimes)`, not
   `O(#inputs)`.)

**Connections the ML/RL audience will recognize** (and that guide the algorithm's knobs):
freeze = optimal stopping / one-armed bandit with switching costs (`ε* = c_m/(γ_e c_e)`);
counterexample budgeting = information-directed sampling — spend search budget in
proportion to expected MDL gain, so freezes that compress more are certified harder (this
answers the thesis's open budget-policy question); the draw+cluster committee = QBC active
learning; melt = SMC rejuvenation with a posterior-conditioned proposal; the sketch library
= the sleep-abstraction phase of a wake-sleep loop (DreamCoder's shape, but online,
per-node, reversible, and ledgered); the whole engine = test-time-compute scaling where the
verifier is an oracle and the compute is *amortized across a program's lifetime* instead of
spent per query.

---

# Part II — Novelty positioning

The thesis §8 fact-check stands (the freeze/melt/branch/merge calculus with
version-control-as-ledger has no occupant). The inference framing adds a second,
independent novelty layer and answers "is this just X?" for the new neighbors it exposes:

- **vs PAW** (arXiv:2607.02512): PAW compiles the prior's point estimate once —
  a total, irreversible, structureless freeze with no likelihood ever consulted. We run the
  posterior: partial, certified, reversible, structured. PAW's artifact tier remains one
  rung of our hardness lattice (a frozen computation graph with molten continuous
  parameters — §4 of the thesis), so PAW is a *backend we can host*, not a competitor we
  must beat on its benchmark.
- **vs SMC-steering of LLMs / probabilistic-program inference** (grammar-constrained SMC,
  sequential Monte Carlo over token sequences): those infer a *single output* under a
  fixed target; our state is a typed program tree, the target itself accretes (evidence
  arrives over a lifetime), resampling events are durable version-control commits, and the
  posterior collapse is certified by active search, not by particle exhaustion.
- **vs CEGIS / verify-once synthesis**: CEGIS is our inner loop run exactly once at
  generation time with a formal spec. We have no formal spec — the likelihood is grown by
  the same process that consumes it — and the loop never terminates, it *anneals*.
- **vs gradual typing / gradual verification**: migration is human-driven and migrates
  types/proofs; ours is evidence-driven and migrates semantics. Blame transplants intact.
- **vs DreamCoder/Stitch/LILO**: compression-gated freezing of shared structure, but no
  melt, no regimes, no runtime, no underspecified source, and offline. Our wake-sleep is
  online and reversible with a safety invariant.
- **vs continual learning (EWC/PackNet lineage)**: freezes parameters to prevent
  forgetting with no semantics and no justification; our monotone-safety invariant is the
  *semantic* version of anti-forgetting, and branches-as-regimes is the semantic version
  of task-conditional capacity.
- **vs version-space learning (Mitchell version spaces; Lau's version-space algebra for
  programming-by-demonstration; FlashFill / PROSE)** — the eponymous and closest neighbor,
  and the one a synthesis reviewer will raise first. They maintain a set of programs
  consistent with accumulating examples and refine it as evidence arrives — exactly our
  per-node core. The differences *are* the contribution: their space is an *enumerable,
  hand-built DSL*, ours is unenumerable and sampled from an *LLM proposal*; their consistency
  is *syntactic membership*, ours is *behavioral* (over `≈_Y`) and collapsed by a *certificate
  of indistinguishability under `D_v`*, not mere consistency; and they have no execution
  lifetime, no reversible melt/branch/merge, and no versioned ledger of regimes. The honest
  one-liner: *a behavioral version space with an LLM proposal, a certified collapse operator,
  and a reversibility calculus — which classical VSA has none of.*

The composite claim for the paper, stated for what the propositions actually establish:
**program annealing — oracle-gated maintenance of a per-node behavioral version space over an
underspecified program, with a calculus of four certified moves (freeze/melt/branch/merge)
whose licenses are a committee-disagreement test, an MDL gate, and held-out reproducibility,
where version control is the ledger of moves and branches are regimes — carrying the
monotone-safety invariant and the (proposal-coverage-conditional) honest-non-convergence
property as its formal payload.** The Bayesian/SMC reading is the intuition, not the claim.
The calculus layer and the behavioral-version-space layer are each individually unoccupied
(thesis §8; the VSA delta above); together they are a paradigm-level object of study rather
than a metric delta — but the paper earns that framing only with the evaluation and proofs
attached (Phase 3), not by positioning alone.

---

# Part III — The refactoring plan

## 5. Target architecture: one kernel, everything else re-slots

The refactor's aggressive move: **stop treating decisions, interpreted mode, contracts,
routing, and the sketch library as five subsystems.** They are five partial views of the
one engine, and the code should say so. New package:

```
semipy/kernel/
  tree.py        # typed hardness tree: Node(id, type, hardness, artifact | population),
                 #   opaque-block fallback; whole-slot = single-node degenerate tree
  population.py  # particle population per node: draw, behavioral quotient, weights
                 #   (absorbs decisions/draw.py, cluster.py, divergence.py)
  evidence.py    # the evidence ledger: per-case outcomes + provenance + holdout split
                 #   (re-founds contract/ as the likelihood store)
  oracle.py      # the likelihood: case replay, monitors, metamorphic relations,
                 #   discriminating-input search with power/budget accounting
                 #   (absorbs decisions/discriminate.py, germs.py + executor glue)
  blame.py       # trace replay over the tree; shallowest-failing-monitor localization
  operators.py   # freeze / melt / branch / merge — the ONLY four mutations of σ;
                 #   each returns a ledger event with its certificate
  policy.py      # the derived decision layer: stopping rule (ε* from the cost model),
                 #   IDS budget allocation, MDL accounting
  anneal.py      # the engine loop: execute(σ, x) → typed value | ABSTAIN, + frontier moves
```

The four runtime `Decision`s become **derived views of frontier state**, not primitives:
`GENERATE` = initialize a fully-molten tree and draw its populations; `REUSE` = no blame,
no staleness → run the tree as-is; `ADAPT` = melt(blamed) + local refreeze; `INSTANTIATE` =
adopt a cross-slot frozen shape and re-melt only its parameters. `routing.py`'s
deterministic cheapest-first cascade survives as `policy.py`'s fast path (fingerprint match
→ skip everything), but its *decisions* are now consequences of the calculus, not a
parallel authority. The `Decision` enum stays exported for UX/back-compat as a computed
label.

Re-slotting map (delta from the thesis §5 — now with concrete code motion):

| Today | Becomes | Code motion |
|---|---|---|
| `decisions/` (opt-in surface) | the molten-node representation — **the core** | `draw/cluster/divergence` → `kernel/population.py`; `discriminate/germs` → `kernel/oracle.py`; `runmodes.py` (comparability check, cost guard, seeded decision-structure quotient) → `kernel/oracle.py` + `kernel/population.py` — a node whose output is not comparably reproducible is pinned to the interpreted tier with a ledgered "no-comparable-signal" event, exempt from freeze and non-convergence certification; `#?` surfacing + `pick`/`assert` (`resolve.py`, `surface.py`) stay as the human-in-the-loop rendering of population modes (a human freeze is the same event type as an autonomous one) |
| `contract/` | the evidence ledger / likelihood | `ContractCase` (`contract/models.py:85`) gains persisted outcome + holdout tag **and an optional `node_id`**; `runner.py` writes outcomes; blame's trace replay doubles as the capture path (each replay materializes node-local (input, output) pairs for interior nodes, so Phase 3's per-node gates have node-local case sets, not only slot-level ones); retirement-on-spec-change = "user moved the frontier from above" |
| `interpreted.py` | the molten execution tier, per-node | `interpret_call` (`interpreted.py:99`) becomes `anneal.py`'s molten-node handler; `attempt_promotion` (`interpreted.py:319`) becomes `operators.freeze` with the three gates (its held-out check is gate 3, generalized) |
| `history/` | the ledger of frontier moves | `Commit/Branch/Slot/Portal` (`version_control.py`) gain an event vocabulary: `FreezeEvent(certificate)`, `MeltEvent(blame)`, `BranchEvent(guard)`, `MergeEvent(license)`; the versioned unit becomes *move + justification*, code blob attached |
| `library/` | cross-slot freezing (wake-sleep abstraction) | `merge_sketch_into_library` (`sketch.py:330`) re-gated: recurrence (N commits) + separation search + MDL replaces the single-shot 0.6-confidence gate; INSTANTIATE = frozen shape, re-melted parameters |
| `routing.py` | derived policy | `RoutingPolicy.decide` (`routing.py:113,124`) → thin adapter over `kernel/policy.py`; data-agnostic guards stay as monitors |
| `reactivity/` | frontier-move propagation | unchanged edge model; events carried are freeze/melt, invalidation = melt of downstream consumers' assumptions |
| `slot_resolver.py` (2,276 lines) | the integration seam, shrunk | `execute_slot` delegates to `anneal.execute`; the reuse gate (`slot_resolver.py:730`) becomes policy fast path; decision-wiring paths collapse |
| orchestration roles | the proposal's inner machinery | coder = proposal sampler; verifier/reuse-judge votes = *proposal-side* filters (never gates against the oracle — 07-04 C1 discipline); unwired `roles/verifier.py` voting gets wired here |

**What is cut** (confirming the thesis §6 anti-Frankenstein list, now enforced by
structure): GEPA/prompt optimization out of core (it learns the proposal, not the program —
optionally an appendix); the calibrated trigger ladder (subsumed by `ε*`); the weight/kernel
tier (deferred, stated as lattice generality); candidate-set/confidence interpreter modes
beyond what the committee needs.

## 6. Phases (dependency order; each independently shippable and measurable)

**Phase 0 — Evidence ledger** *(the prerequisite for everything; 07-04 P0 unchanged)*
Persist per-case outcomes with provenance and a holdout split. Touch:
`contract/models.py:85` (+outcome, +split), `contract/runner.py`, the reuse gate at
`slot_resolver.py:730`. Exit: every replay appends outcomes; ledger queryable per slot.

**Phase 1 — Hardness tree.** `kernel/tree.py`: recursive typed decomposition with a
hardness field, opaque-block fallback; every existing slot loads as a single-node tree
(zero-migration back-compat — legacy portals are degenerate trees). Lowering
(`lowering_ast.py`) recognizes combinator shapes where it can. Exit: portal round-trips trees;
the full test suite stays green (304 tests as of 2026-07-04) with single-node semantics
identical; **and** the measured fraction of the existing slot corpus (plus a small set of
representative general-purpose tasks) that lowers to *multi-node* trees is reported. That
fraction is a go/no-go gate: per-node freezing, blame, and locality — the entire delta over
whole-slot semipy and PAW — only fire on multi-node trees, so if most real slots lower to a
single opaque node, invest in lowering coverage (or rescope the claims) before building
Phases 3–5 on top of it.

**Status (2026-07-07):** the go/no-go fraction has now been measured on real generated
code, not the synthetic 15-function stand-in `tests/unit/test_kernel_tree.py` used until
now. 14 `@semiformal` slots spanning the same domains (map/filter/fold/branch/compose,
plus three genuinely opaque controls) were generated live against `gpt-5.5` (no hand-written
sources) and their `commitment_record`-backed generated source fed through
`multi_node_fraction`. Result: **11/14 = 0.786 lower multi-node** — in the same range as the
synthetic corpus's 0.80, so the go/no-go signal holds on real code, not just hand-written
fixtures built to be recognizable.

That number required one recognizer fix first, and it is load-bearing enough to record here.
The first real run measured **9/14 = 0.643**, because semipy's own generation convention
(a placeholder `result = ...` line, then the real `result = [comprehension]` assignment,
then a separately-returned, often dict-wrapped `return {"result": result}` — the
STATEMENT_BLOCK contract in `agents/agent.py`) systematically defeated
`_try_comprehension_run`, which only matched a comprehension sitting directly inside a bare
`return [...]`. Two slots (`keep_positive`, `keep_nonempty`) were misclassified opaque purely
because of this shape mismatch, and three more (`double_numbers`, `uppercase_strings`,
`filter_then_double`) counted as multi-node only via an incidental `if xs is None: ... else:
...` guard branch, with the real MAP/FILTER logic buried unrecognized inside the branch's
`else` arm. Fixed by isolating a comprehension-assignment (`name = [...]`) as its own
`_segment_top_level` run — exactly as `For`/`If` already are — and adding
`_try_assigned_comprehension_run` alongside `_try_comprehension_run`
(`semipy/kernel/tree.py`); both now share one `_comprehension_node` builder. This is a
recognizer generalization (a shape already inside the documented recognized region, matched
more permissively), not a scope expansion: no synthetic-corpus fixture used the
assign-then-return shape, so `tests/unit/test_kernel_tree.py`'s exact-fraction assertion
(12/15) was unaffected; two new regression tests cover the fixed shape, including the literal
observed real-generation source. Full suite: 377/377 green.

Three of the 14 real slots remain honestly opaque, for reasons distinct from the fix above
and *not* addressed here (documented as open gaps, not silently accepted): `filter_then_sum`
and `sum_pairs` use a for-loop whose body is more than one statement (a `try`/`except` guard
before the accumulate/append, or a tuple-unpack-via-`try` inside the loop) — out of
`_match_for_loop`'s single-statement-body scope; `clean_string` is a genuine expression
pipeline (`.strip().lower()` on a conditional) with no combinator shape at all, correctly
opaque. A fourth, broader gap the real corpus surfaced but this pass did **not** fix:
idiomatic builtin reductions (`"".join(...)`, `sum(...)`, `list(filter(...))`) are invisible
to the recognizer entirely, since it only matches `for`/comprehension AST shapes, never
builtin calls — `concat_strings` only counts as multi-node because of its own defensive
`if xs is None` branch, not because its `"".join(str(item) for item in xs)` body was
recognized as a fold. Real LLMs reach for these idioms often; whether to teach the
recognizer specific builtin-call shapes (still shape-based, not domain-specific) is an open
scope question for a later pass, not resolved by this measurement.

**Phase 2 — Population as the engine.** Move draw/cluster/divergence into
`kernel/population.py`; add the execution-based head-selection scoring rule (type validity
+ contract-pass fraction + cluster agreement — the 07-04 P1 "cheapest high-value change",
still true). Decisions stop being opt-in: the population *is* how a non-frozen node is
represented (the `#?` surface remains configurable). To keep the "never worse than today"
cost property, a non-frozen node's population **initializes with a single particle by
default** (cost-identical to today's single generation); committee draws beyond one are
allocated by `policy.py` budget once IDS lands (Phase 3), not taken unconditionally per slot.
Exit: GENERATE/ADAPT route through the population; head selection is execution-ranked.

**Phase 3 — `freeze`.** `kernel/operators.py` + `policy.py`: the three gates — held-out
reproducibility (generalize `attempt_promotion`), MDL accounting over the portal,
counterexample license with explicit `(ε, δ, γ, B)` recorded in a `FreezeEvent`
certificate. Interpreted-mode promotion is deleted as a separate path; it *is* node freeze.
Exit: frozen fraction `F(slot)` computable from the ledger; freeze events carry
certificates; summarize/judge slots demonstrably never freeze. **Evaluation milestone (the
paper's first evidence):** on a small slot stream, run certified freezing + honest
non-convergence against the regenerate-always and current-semipy baselines from thesis §7,
and commit the paper's experimental design here (which propositions get demos, which get
proofs) rather than deferring all of it. "Publishable after Phase 3" is only true with this
attached — the claim is unfalsifiable without an experiment.

**Phase 4 — `melt` + blame.** `kernel/blame.py`: per-node monitors, trace replay,
shallowest-failure localization; ADAPT rewired as melt(blamed) + local rejuvenation
(proposal conditioned on the updated ledger). Freeze-eligibility floor enforced (≥1
discriminating case + ≥1 *non-vacuous* metamorphic relation). **Prerequisite work item:** the
metamorphic-relation registry (`contract/relations.py`) is string-only today (two relations,
both identity on non-strings), so it must be extended with typed/structural relations per
input category — key-order and irrelevant-field invariance for records, element-permutation
for collections, idempotence for effectful upserts via shadow-world replay — or the floor is
satisfied only vacuously and most non-string slots never become freeze-eligible. Exit:
locality metric (regenerated-region size / slot size) measurable; falsified certificates feed
germs back to the search.

**Status (2026-07-07):** `melt` is implemented and live-wired, but narrower than "ADAPT
rewired as melt" above: it fires only inside `slot_resolver._run_generate_contract_gate`'s
retry loop, only for `"example"`-kind contract cases (a concrete input→expected-output pin —
the only kind with a literal-eval-able target `blame` can diff against), and only opt-in via
`config.melt_on_contract_failure` (default off). It tries a node-scoped patch first (blame →
one scoped LLM call for the blamed MAP/FILTER leaf → `patch_source`) and falls straight
through to today's full-function regeneration, unchanged, whenever melt is out of scope or
the patch doesn't pass the gate's own re-verification. The hardness tree is never persisted
for this — it's recomputed from the candidate's own source each attempt and discarded. ADAPT's
other trigger paths (equivalence mismatch, `force_regenerate`, semantic re-check) are
untouched. The freeze-eligibility floor and the metamorphic-relation registry extension called
out above are still not done.

**Phase 5 — `branch`/`merge` + regime guards.** Extend `history/version_control.py` event
vocabulary. **Design the guard DSL first** — a closed typed-predicate fragment plus a
compiler/validator from LLM-proposed guard strings into it (this does not exist today; §3.3);
guards that fail to compile keep the node molten instead of dispatching. Then: runtime guard
dispatch ahead of tree execution; verified merge behind the pair-separation search. Exit:
injected mid-stream distribution shift produces a guarded branch with zero regressions on the
old regime's ledger (the monotone-safety demo).

**Status (2026-07-07):** the guard DSL (`kernel/guard.py`) and `branch` are implemented and
live-wired; `merge` is not. `branch` fires only when `_run_generate_contract_gate`'s retry
budget is exhausted and a case is about to be quarantined (opt-in via
`config.branch_on_quarantine`, default off): `kernel.operators.synthesize_separating_guard`
finds a predicate separating the case's own input from the input that drove the conflicting
regeneration (a small closed template bank first, one scoped LLM call only if nothing in the
bank works — and the result is always independently re-evaluated against both concrete inputs
before being trusted, never taken on the LLM's say-so), `branch()` licenses it against the
guard DSL, and `kernel.tree.build_branch_wrapper` combines the two *whole* candidate
implementations behind it. This operates at the whole-function level, not "runtime guard
dispatch ahead of tree execution" as sketched above — every live slot is still one opaque node
(Phase 1's go/no-go fraction), so there is no sub-node tree to dispatch ahead of yet. `merge`
remains additive only: nothing yet produces two branch-shaped artifacts of the same slot for
it to act on, so there is still no live merge call site.

**Phase 6 — Collapse the legacy surface.** `routing.py` → adapter; `slot_resolver.py`
shrinks to the seam; sketch-library re-gating (recurrence + search + MDL); the four
`Decision`s become computed labels. This is the phase where the codebase *reads as one
engine*. Exit: no code path consults a confidence threshold that `policy.py` didn't derive.

**Phase 7 — Surface + instrumentation.** VS Code: opacity = hardness (already the visual
language), hover = the freeze certificate (budget spent, candidates rejected, ε/δ), version
tree = regimes; ledger export for the paper's lifetime metrics (F(slot) trajectory, cost
per input, locality, regression count). Evaluation design itself is deferred per scope —
but the instrumentation to *ever* run it lands here.

Sequencing note: Phases 0–2 are almost pure refactor (motion + one scoring function) and
de-risk the rest; Phase 3 is the first genuinely new algorithmic surface; Phases 4–5 are
where the paper's claims become demos. After Phase 2 the system is never worse than
today's; after Phase 3 it is already a publishable artifact (certified freezing alone,
with honest non-convergence, is a paper leg). **Ordering caveat:** Phase 6 (collapsing the
2,276-line `slot_resolver.py` seam) is the highest-blast-radius work and produces no paper
artifact — it should land *after* Phase 7's instrumentation/ledger export, not before. Under
any deadline, ship 0→5→7 (everything the research claim needs) and treat Phase 6 as post-paper
cleanup.

## 7. Risks, honestly

- **The detection-power assumption (`γ`) is the theory's load-bearing wall.** If the
  discriminating search is much weaker than assumed, certificates overstate confidence.
  Mitigations: the assumption is *testable* (falsified certificates are observable events —
  track the falsification rate as an empirical calibration of `γ`); budgets are
  IDS-allocated so high-stakes freezes get more search; and monotone safety holds
  regardless — a bad freeze costs a branch, never a silent regression. This should be a
  stated limitation, not a hidden one; it is also the most interesting knob for the math.
- **Proposal coverage / correlated committee is the *second* load-bearing wall** (Prop 4).
  The disagreement certificate is only as good as committee diversity: LLM samples are
  correlated, so `Δ(V) = 0` can mean "intent resolved" or "every candidate shares the same
  bug." A correlated-error freeze may never be falsified if the shared bug matches what the
  stream never exercises — so unlike a low-`γ` freeze it is *not* always caught by monotone
  safety. Mitigations: track committee behavioral diversity per draw as a ledger statistic and
  refuse high-stakes freezes below a diversity floor; diversify the proposal before licensing
  (temperature, prompt perturbation, model ensemble); treat every post-hoc falsification of a
  zero-`Δ` freeze as evidence of *proposal collapse*, not merely low search power. This and
  `γ` — not the cost-model defaults — are what the certificate framing lives or dies on (§8).
- **Cost model inputs (`c_e`, `λ`)** are estimates. Start with per-category defaults
  (extract/parse cheap-to-err, effectful slots expensive) and refine from ledger history;
  `ε*` degrades gracefully (a wrong `c_e` shifts *when* you freeze, not *whether* safety
  holds).
- **Monitor coverage floor.** No contract, no blame, no freeze — some trees will stay
  plastic at coarse granularity. Correct behavior, but manage expectations: `F(slot)`
  plateaus reflect monitor investment, not just semantic hardness.
- **Refactor blast radius.** `slot_resolver.py` is the 2,276-line seam everything routes
  through. Phases 0–2's motion-only discipline plus the degenerate-tree back-compat is the
  containment strategy; each phase keeps the full test suite green.

## 8. Decision asked of you

Part I fixes the method; Part III fixes the shape and order of the refactor. Two calls, in
priority order:

1. **The gating experiment — measure `γ` and committee diversity *before* the refactor
   (a Phase −1).** The two load-bearing assumptions — detection efficiency `γ` (§3.1) and
   proposal coverage / committee diversity (§7) — both underwrite every certificate, and both
   are measurable on *today's* codebase in days: seed known disagreements into
   `decisions/discriminate.py`'s germ search and measure hit rate for `γ`; sample the current
   multi-candidate draw and measure behavioral spread for diversity. If either is far from the
   assumed regime, the certificate framing (Part I) and the positioning (Part II) restructure —
   far cheaper to learn now than after Phases 1–5. This, not the cost model, is the decision
   the whole framing hinges on.
2. **Cost-model defaults** (`c_e` and `γ_e` per slot category; whether `λ` is estimated per
   slot or global — noting `λ` cancels out of `ε*` and matters only for IDS budget, §3.1).
   Lower stakes: per §7 a wrong cost input shifts *when* you freeze, not *whether* safety
   holds.

Everything from Phase 0 onward is buildable now; the Phase −1 measurement should run in
parallel and gate whether the certificate story survives contact with real LLM samples.
