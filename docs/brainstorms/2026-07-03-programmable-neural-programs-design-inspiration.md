# Programmable Neural Programs — Design-Inspiration Digest

**Date:** 2026-07-03
**Status:** Research inputs for an in-progress brainstorm (not yet a requirements doc).
**Purpose:** Synthesize six parallel literature streams into borrowable design mechanisms for
a research direction: extending semipy so that the *neural* component of a semiformal program
is a **typed, composable hole** executed by **our own neurosymbolic interpreter**, rather than a
monolithic compiled artifact (as in Program-as-Weights, arXiv:2607.02512).

Each mechanism is tagged **TAKE** (adopt), **ADAPT** (adopt with modification), or **LEAVE**
(anti-pattern to avoid). Sources cited inline with arXiv IDs.

---

## 0. The refined thesis (post-research)

> A semiformal program is a **typed IR with typed neural holes (leaves)**, executed by a **single
> interpreter parameterized by an execution semiring and a set of effect handlers**. Structure
> (types, containers, control flow, composition) lives in the symbolic IR; neural computation is
> confined to typed leaves `spec : InType -> OutType`. Three mechanisms distinguish it from both
> PAW and current semipy:
>
> 1. **Shape-invariance via a lift** — a scalar leaf lifts over containers with no recompile,
>    governed by a *decidable* element-wise/holistic test and a *factoring rule* that minimizes
>    neural surface area.
> 2. **A solidification lattice** — interpreted-LLM -> local neural kernel -> symbolic code, as
>    reversible tiered compilation for semantics, gated by a compression objective and an
>    adversarial (counterexample) stopping rule.
> 3. **Compositional reliability** — every leaf is a contract boundary with blame, abstention, and
>    construction-time type guarantees, giving O(pipeline-length) fault localization that PAW's
>    opaque binary cannot.

---

## 1. The IR and the typed hole

- **Hole closures + indeterminate results (run past an unresolved leaf).** Hazel (arXiv:1805.00155,
  POPL 2019). Evaluation proceeds past an empty hole to a *final-but-indeterminate* value; each hole
  instance carries a closure `<hole_id, captured typed env>`. Our interpreter evaluates the symbolic
  IR to an indeterminate value where each un-resolved leaf is a closure; the formal surround still
  computes concrete intermediate structure. **TAKE** — the exact semantics for "leaf not yet resolved."
- **fill-and-resume.** Hazel. Filling a hole *resumes* from the captured state instead of restarting;
  when a leaf promotes, resume the closure. Dovetails with reactivity's pull-based staleness. **TAKE.**
- **Non-empty holes as type membranes.** Hazel. A value that fails its declared type is wrapped as a
  membrane and evaluation proceeds; feed the located, typed fault to the contract guard instead of a
  hard error. **ADAPT** (gate which membranes are user-visible — Hazel spawns one per inconsistency).
- **Surround-derived hole type + valid hole fits.** GHC typed holes; Agda interactive holes. A hole's
  type is *computed* from signature + application context, never declared; "valid hole fits" list
  in-scope bindings whose types match. Maps to STATEMENT_BLOCK type inference (already in semipy) and
  makes INSTANTIATE precise: a cached fn whose typed signature matches the derived hole type is an
  LLM-free candidate. **TAKE.**
- **Leaf as a contextual-modal box.** Hazel's CMTT basis: a hole is a metavariable `[Gamma |- A]` —
  a value of `A` relative to captured context `Gamma`. A neural leaf is `box(InType -> OutType)` over
  its free-var context; the Lift functor is the contextual reindexing that reuses one boxed leaf under
  a changed container/context. **Novel PL-theory framing for the headline claim.**

## 2. The interpreter / evaluator (central fork: RESOLVED toward symbolic control)

- **Every system that worked at multi-step put control flow in a symbolic host; the one that put it in
  a neural controller (NPI, arXiv:1511.06279) needed full trace supervision and generalized poorly.**
  This vindicates Option A (symbolic evaluator + neural typed leaves). **TAKE the fork.**
- **Neural confined to grammar-legal leaf cells.** Binder (arXiv:2210.02875): host program runs
  deterministically; only `f(...)` cells route to one shared LM. Model our IR leaf as a grammar-legal
  `spec : InType -> OutType` expression; the evaluator owns everything else. **TAKE.**
- **Types gate composition at the boundary.** Neural Module Networks (arXiv:1511.02799): only
  type-compatible modules wire. Reject ill-typed leaf wiring at lowering, not runtime; use real Python
  types, not a closed type set. **ADAPT.**
- **Semiring-parameterized evaluator + confidence-carrying leaves.** Scallop (arXiv:2304.04812),
  DeepProbLog (arXiv:1805.10872). A neural predicate emits `(value, confidence)`; a provenance semiring
  propagates confidence and bounds cost via top-k. Parameterize our VM by an execution semiring so
  *one* evaluator runs concrete / confidence-tracking / top-k-candidate modes over the same IR.
  **TAKE (highest leverage).**
- **Op registry + trace-as-explanation.** VisProg (arXiv:2211.11559): dispatch ops by name over a shared
  namespace; the execution trace *is* the rationale (feeds the contract subsystem for free). **TAKE.**
- **Typed object-API dispatch with a validated escape hatch.** ViperGPT (arXiv:2303.08128): typed
  methods plus a general `simple_query` that still declares a return type. **ADAPT** — keep a catch-all
  leaf but require a declared `OutType` so it can be validated.
- **Recursive decomposition of an unresolved leaf.** Code as Policies (arXiv:2209.07753): undefined
  functions expand on demand while loops/branches stay symbolic. Let an unresolved leaf expand into a
  *sub-IR of typed leaves* (bounded recursive lowering) — the multi-step composition PAW lacked. **ADAPT.**
- **Addressable program store, not the controller.** NPI (arXiv:1511.06279): a key->subprogram memory
  maps onto the sketch library (retrieve solidified leaves by shape-key). **ADAPT the memory, LEAVE the
  neural controller.**

## 3. The lift and the element-wise / holistic taxonomy (shape-invariance)

- **Frame/cell decomposition = the Lift node's operational core.** Remora (arXiv:1912.13451, ESOP 2014);
  Iverson's J rank. A function has a *cell rank*; an argument's shape splits into a **frame** (prefix)
  and **cells** (suffix matching expected rank); the op runs once per cell. **frame = formal container,
  cell = neural leaf.** **TAKE as the definitional split.**
- **Principal-frame agreement = broadcasting.** Remora/J. Unequal frames: the longest is principal;
  shorter args are prefix-replicated; mismatch is a static error. Rule for multi-arg leaves and shared
  context (e.g. one shared parameter across a column). **TAKE.**
- **Naperian (representable) functors: shape-in-the-type, lift = fmap.** Gibbons, *APLicative Programming
  with Naperian Functors* (ESOP 2017). A scalar `f : a -> b` lifts to any rank by iterated `fmap`, with
  dimensions tracked statically — *no recompile*. **TAKE — strongest citation for the headline claim.**
- **Traversable = the exact "effectful element-wise" class.** McBride & Paterson, *Applicative
  Programming with Effects* (JFP 2008). `traverse : Applicative f => (a -> f b) -> t a -> f (t b)` is
  the shape-preserving effectful map; our fuzzy leaf is effectful (LLM = effect), so **an operator lifts
  for free iff it can be written `traverse leaf`.** **TAKE — the formal home of an element-wise leaf.**
- **Catamorphism vs map = the taxonomy boundary.** Meijer et al., *Bananas, Lenses, Envelopes and Barbed
  Wire* (FPCA 1991). A genuine fold/cata collapses structure to a summary; "rank / dedup /
  summarize-negatives" are catamorphisms, not maps. **TAKE as the formal criterion.**
- **Parametricity: "no recompile" is a free theorem.** Wadler, *Theorems for Free!* (FPCA 1989).
  Functoriality (`map (g . f) = map g . map f`) is free; a parametric container op *must* be an `fmap`.
  **TAKE as the theoretical justification.**
- **jax `vmap` in_axes/out_axes = the practical Lift API.** Adds a batch axis via per-primitive batching
  rules (a trace transform, not a recompile); `in_axes=None` marks a broadcast/shared arg vs `0` mapped.
  Adopt as the Lift node's interface. **ADAPT.**

**The lift condition (adopt verbatim).**
*Definition:* `f : A -> B` lifts to `F[A] -> F[B]` for free iff the container op `O` equals `fmap_F f`
(equivalently `traverse` of effectful `f`): shape-preserving and `O(xs)[i]` depends only on `xs[i]`,
not on position `i` nor on siblings.
*Decidable test:* element-wise iff (a) `O(xs)[i] = O(singleton(xs[i]))[0]` and (b) `O` commutes with
every reindexing `r` (permute/filter/partition/transpose): `O . map r = map r . O`. Fail (a) => reads
siblings; fail (b) => reads position; either => irreducibly aggregate `[A] -> X`.

**The factoring rule (the big reframe).** Most "holistic" tasks are not irreducible; they factor as
`cata(formal) . fmap(neural-leaf)` — e.g. `rank = formal-sort . map(score : str -> float)`,
`summarize-negatives = formal-summarize . filter . map(sentiment)`. Normalize every fuzzy operator
toward this form, pushing the neural leaf as deep as possible; only the residue where the neural
judgment itself must see siblings (near-duplicate merge, "pick the most representative") stays a true
`[str] -> X` leaf. This is "minimize neural surface area" as an algorithm, with 40 years of precedent
(SQL planners push scalar functions down past aggregates; MapReduce's map/reduce split).

## 4. The leaf substrate (spec -> neural)

- **Hypernetwork one-pass spec -> adapter (the leaf-compiler primitive).** Text-to-LoRA
  (arXiv:2506.06105): a hypernetwork emits a LoRA for a frozen base from an NL description in one
  forward pass; compresses hundreds of task LoRAs, zero-shot to unseen descriptions. This is PAW's
  compile step *without* the opaque-binary problem (the artifact is an inspectable LoRA). **TAKE.**
- **Train the compiler with end-to-end SFT, not weight reconstruction.** Text-to-LoRA. SFT-through-the-
  adapter generalizes better to unseen specs; reconstruction overfits the oracle set. **TAKE.**
- **Encode-once, reuse across inputs.** HINT (arXiv:2212.10315): instruction -> inserted PEFT computed
  once, never reprocessed per input. Justifies the caching claim (compute the leaf's specialization once
  from the spec, reuse for all runtime inputs). **TAKE.**
- **Generated adapter as a warm start.** HyperTuning (arXiv:2211.12485): using generated params as
  *initialization* for a light finetune beats using them as-is. Graceful-degradation ladder: compile ->
  if validation fails, cheap finetune from the warm start rather than from scratch. **ADAPT.**
- **Context -> adapter for data-dependent leaves.** Generative Adapter (arXiv:2411.05877): map runtime
  context to a low-rank adapter in one pass, cache per-context. **ADAPT.**
- **Reuse-by-description = neural INSTANTIATE.** Text-to-LoRA keys generation on the description
  embedding, so near-identical specs map to near-identical adapters. Key the adapter cache on
  `spec_equivalence_key`'s durable-meaning embedding; nearest-neighbor retrieve; INSTANTIATE. **TAKE.**
- **Per-instance attention fusion for *entangled* specs.** AdapterFusion (arXiv:2005.00247):
  non-destructive learned composition, originals stay frozen. Use only for irreducibly simultaneous
  multi-aspect specs. **ADAPT.**
- **Gradient-free coefficient search as a fallback.** LoRAHub (arXiv:2307.13269): compose existing LoRAs
  via coefficient optimization from a few examples. **ADAPT** (efficiency, not accuracy — see risks).

## 5. The solidification lattice (iteration machinery)

- **MDL / compression-gain as the crystallization objective.** DreamCoder (arXiv:2006.08381) + Stitch
  (arXiv:2211.16605). An abstraction earns its place iff it shrinks the *joint* description length of
  library + all solved programs. Replace any "seen N times" threshold with a compression-gain test; a
  pattern reused once is negative compression and should not crystallize. **TAKE** (promotion criterion),
  **ADAPT** (compress over NL-spec+code pairs, not a pure DSL).
- **Bounded top-down compression search, LLM-free.** Stitch: branch-and-bound with a compression-utility
  upper bound; 3-4 OOM faster than DreamCoder's version-space refactoring. Run offline over the cached
  slot implementations already in the commit DAG to propose parametric sketches. **ADAPT.**
- **Symbolic compression + LLM auto-documentation.** LILO (arXiv:2310.19791): an "AutoDoc" LLM names and
  documents opaque abstractions so later synthesis retrieves them *by meaning*. When a leaf crystallizes,
  attach an NL description so the reuse judge and sketch retrieval can match new specs semantically —
  the missing bridge from NL specs to symbolic library entries. **TAKE.**
- **Assess by executing, not by inspecting syntax.** Write/Execute/Assess (arXiv:1906.04604): score the
  *execution* of a partial program. Promote on execution-convergence, never on code appearance. **ADAPT.**
- **Emit a reusable, auditable program, not an answer.** ALCHEmist (arXiv:2407.11004): ask the LLM to
  generate a label-*program* (stored, reused locally, ~500x cheaper, auditable/editable). External
  validation of our interpreted -> symbolic PROMOTE; add contract-guarded held-out validation they lack.
  **TAKE (as framing + citation).**
- **Counterexample refinement as the "is-this-extractable?" oracle.** Weiss et al. (arXiv:1711.09576):
  L* extracts a DFA from an RNN, converging iff the dynamics are near-regular; when not, refinement keeps
  finding counterexamples. "Keep finding discriminating inputs => do not crystallize" is our stay-fuzzy
  rule, and plugs into the decisions subsystem's germ-seeded discriminating-input search. **ADAPT** —
  use adversarial counterexample pressure, not just held-out accuracy, as the crystallize switch.
- **Distill with rationales (middle rung only).** Distilling Step-by-Step (arXiv:2305.02301): train a
  small model to predict label *and* rationale; reaches shape-stability with fewer examples. Relevant
  when a leaf is too fuzzy for code but too costly to keep calling a frontier model. **ADAPT.**

**Two emergent structural ideas for the lattice:**
- **Two-level objective (portal, not per-leaf).** Crystallizing a leaf is worth it if it becomes a
  *primitive the LLM composes in higher slots*, amortizing future search. Inner loop = per-leaf
  shape-stability; outer loop = corpus compression across the portal.
- **Tiered-JIT deoptimization (reversible lattice).** The lattice is tiered compilation for *semantics*.
  Borrow the deopt guard: a crystallized symbolic leaf keeps a cheap runtime guard that, on a contract
  violation or out-of-distribution input, falls back to the interpreted-LLM tier and re-collects
  examples. "Stays fuzzy" and "crystallized" become points on a *reversible* continuum, not terminal
  states.

## 6. The typed boundary and composed verification (blame / abstention)

- **Constrain by construction (FSM/grammar index over the vocabulary).** Outlines (arXiv:2307.09702):
  compile a regex/CFG to an FSM, mask disallowed logits per token; valid output by construction, near-
  constant overhead. Compile each leaf's `OutType` (enum -> alternation, `Literal` -> fixed set, record
  -> JSON-schema grammar, list -> repetition) to a decoding constraint on the *local* model. Strictly
  stronger than post-hoc `isinstance`. **TAKE** where we own the logits.
- **Grammar-constrained decoding without finetuning; input-dependent grammars.** GCD (arXiv:2305.13971):
  constrained small models can beat finetuned baselines, especially data-scarce. Derive an input-
  dependent grammar per call from the slot's free-vars/return-type. **TAKE** — constraint *improves*, not
  just polices.
- **Incremental parse up to schema level.** PICARD (arXiv:2109.05093): reject tokens that cannot extend
  to a valid parse. Drive a decode-time checker: "does this partial output still typecheck against
  `OutType` and satisfy pydantic field constraints?" **ADAPT.**
- **Higher-order contracts + blame.** Findler & Felleisen (ICFP 2002); Wadler & Findler,
  *Well-Typed Programs Can't Be Blamed* (ESOP 2009). Casts at a trusted/untrusted boundary carry
  blame labels; the more-typed side is never blamed. Symbolic interpreter = trusted; neural leaf =
  untrusted; wrap every leaf in a contract whose blame label is its `commit_id`/`site_id`. **TAKE — the
  theoretical backbone for compositional reliability.**
- **Selective prediction (abstention with a risk-coverage bound).** Geifman & El-Yaniv (arXiv:1705.08500):
  add a reject option at a target risk. Every leaf's real type is `OutType | ABSTAIN`; the interpreter
  treats `ABSTAIN` as a first-class IR value that short-circuits composition (Maybe/Result monad) instead
  of propagating a wrong-but-typed value. **ADAPT.**
- **Metamorphic relations as an oracle substitute.** Chen et al. review (ACM CSUR 2018): assert relations
  between outputs of related inputs (paraphrase/permutation invariance). Add metamorphic contract cases
  for oracle-free leaves (summarize/judge). **TAKE** — closes the oracle gap for irreducibly-semantic
  leaves.
- **Layered guarantee policy.** constrain (own logits) -> validate-and-repair (re-prompt with the
  pydantic error; needed for closed-API leaves) -> bounded retry -> abstain. **ADAPT.**

**Emergent idea — "the interpreter can't be blamed."** Rebrand Wadler-Findler: well-typed *symbolic* code
is never blamed. Each leaf is a monitored contract boundary (its `OutType` contract + metamorphic
relations) labeled by `commit_id`. When a *composed* program fails an end-to-end contract case, replay
the failing trace over the reified typed IR; the first leaf whose monitor fails *is* the blamed party —
O(pipeline-length) fault localization **without ground truth on intermediate values**. This directly
answers PAW's 10-function-pipeline failure. Model **abstention as negative blame** (leaf hands
responsibility back to the context/under-specified spec) vs a contract-violating value as **positive
blame** (regenerate that leaf). End-to-end reliability floor >= product of (1 - per-leaf error); each
abstention converts a silent compounding error into a detected, localized halt.

---

## 7. Cross-stream convergences (the ideas that multiple streams independently produced)

1. **Composition = sequencing typed leaves, not merging weights, and it comes with blame.** Stream 3
   (adapter merging is lossy; sequence instead), Stream 2 (confidence-carrying leaves + abstention gate),
   Stream 1 (Smyth unevaluation = backward blame to the responsible leaf), Stream 6 (Findler-Felleisen
   blame; first failing monitor is blamed) all converge: composed neurosymbolic programs get
   confidence-tracking + O(length) fault localization + targeted per-leaf regeneration. This is the
   compositionality dividend PAW's opaque binary structurally cannot have.

2. **Lift and solidification are one applicative-effect structure.** Stream 5: an operator lifts iff it
   is `traverse leaf`, where `traverse : (a -> f b) -> t a -> f (t b)`. Stream 2: a leaf is an algebraic
   effect, and solidification is *swapping the handler* of `f`. So the lift (fmap/traverse) and the
   maturity lattice (which handler runs `f`: LLM / local kernel / symbolic) are governed by the same
   applicative `f`, which the semiring parameterization selects.

3. **Metamorphic invariance IS the decidable lift condition.** Stream 6's oracle-free metamorphic test
   "reordering the input must not change the output" is exactly Stream 5's condition (b) "commutes with
   reindexing." So whether a leaf is element-wise (liftable) is *runtime-checkable* by permutation-
   invariance metamorphic contracts — the taxonomy is both formally defined and empirically testable
   with machinery we already have (contract cases).

4. **The solidification lattice is reversible tiered-JIT with an MDL gate and a counterexample stop.**
   Stream 4: MDL compression-gain decides *what* crystallizes; Weiss counterexample pressure decides
   *whether* it can (stay-fuzzy rule); tiered-JIT deopt guards make it *reversible*; ALCHEmist validates
   the interpreted->symbolic move; LILO AutoDoc bridges NL<->symbolic library. Stream 1 (Hazel
   fill-and-resume) supplies the incremental re-eval when a leaf changes tier.

5. **The typed hole has a complete lifecycle across streams.** Type derived from the surround (Stream 1
   GHC-style; Stream 5 frame/cell) -> realized by an adapter keyed on the spec embedding (Stream 3) ->
   output guaranteed by constrained decoding (Stream 6) -> run-past-if-unresolved via hole closure
   (Stream 1) -> matured along the lattice (Stream 4) -> monitored with blame/abstention in composition
   (Stream 6/2/1).

---

## 8. Consolidated risks / negative lessons

- **Do not put control flow in the model** (NPI needed trace supervision, generalized poorly). Loops and
  branches belong in the symbolic VM.
- **Do not co-train composable holes** (NMN modules co-adapted, lost reusability). Keep the shared small
  model frozen; isolate per-leaf capacity (PEFT) so leaves stay swappable.
- **Do not compose leaves by weight arithmetic at scale** (TIES-Merging arXiv:2306.01708: sign conflicts
  + redundancy cause large drops; LoRAHub gains efficiency, not accuracy; task arithmetic arXiv:2212.04089
  needs near-orthogonal tasks). Sequence typed leaves; merge only irreducibly entangled specs, via learned
  fusion, with a verify gate.
- **Type-validity is not semantic-correctness.** A leaf forced to emit a valid `Label` will confidently
  emit the *wrong* label. Constrained decoding (A) does nothing for compositional correctness (B) — pair
  it with abstention + contracts or you get silent, well-typed errors.
- **Do not constrain the leaf's chain-of-thought** (Let Me Speak Freely, arXiv:2408.02442: tight format
  restriction can hurt reasoning). Let the leaf reason free-form; constrain only the final typed emission.
- **Held-out accuracy over-promotes fuzzy leaves** (Weiss: extraction silently diverges when behavior is
  not finite-state). Require adversarial counterexample pressure before crystallizing summarize/judge-style
  leaves.
- **Undocumented abstractions do not get reused** (LILO). A crystallized leaf without an NL description
  wastes the lift.
- **Do not build abstraction discovery as exhaustive refactoring** (DreamCoder was slow/memory-heavy;
  Stitch's whole contribution was replacing it). Use bounded compression search.
- **Blame is only as good as contract coverage; grammars cannot express cross-field/semantic constraints.**

---

## 9. How this sharpens the contribution and the open forks

**Contribution structure (three legs, each with a formal spine + an empirical test):**
1. *Shape-invariance*: frame/cell + Naperian `fmap` + `traverse`; decidable element-wise test =
   permutation-invariance metamorphic contract; factoring rule `cata(formal) . fmap(leaf)`. Benchmark:
   FuzzyBench-derived tasks wrapped in container/arity variations; measure recompile cost + accuracy vs
   PAW.
2. *Solidification*: reversible tiered lattice with MDL crystallization gate + counterexample stop +
   deopt guard. Benchmark: cost/accuracy/inspectability of a slot over its lifetime; fraction of leaves
   that reach symbolic vs stay neural.
3. *Compositional reliability*: leaf = contract boundary with blame + abstention + constrained decoding.
   Benchmark: reimplement PAW's 10-function tool-calling pipeline as a composed typed-hole program;
   measure glue eliminated, fault-localization accuracy, end-to-end correctness.

**Fork recommendations (research-informed):**
- **Interpreter (was Fork A/B/C): Option A confirmed** — symbolic control + one shared leaf-executor —
  *refined* to a single interpreter parameterized by `(execution semiring x effect-handler set)`, so
  interpreted / cached / confidence / candidate modes are one evaluator, and solidification is a handler
  swap.
- **IR expressiveness: lean to a restricted combinator core** (map/traverse/filter/fold/branch/compose +
  typed `Fuzzy` leaf) embedded in Python. The lift theory, the factoring rule, blame monitoring, and
  Hazel dynamics are all cleanest over a small typed core; full-Python surround can wrap it.
- **Leaf substrate: staged** — start with interpreted-LLM leaves + constrained decoding + the lattice;
  cite/adopt Text-to-LoRA as the local-kernel rung when the local/offline story is needed. This avoids
  rebuilding PAW's compiler + 10M dataset (not our novelty) while keeping the option open.
- **Venue: the straddle is now well-supported** — a PL abstraction (CMTT holes, blame, rank polymorphism,
  catamorphisms) with NLP-benchmark teeth (the three benchmarks above). Strong enough for a PL venue as
  well as an ML one.

---

## 10. Master citation list

**Typed holes / synthesis:** Hazel arXiv:1805.00155 (POPL 2019); Smyth arXiv:1911.00583 (ICFP 2020);
Myth (Osera & Zdancewic, PLDI 2015); Synquid arXiv:1510.08419 (PLDI 2016); GHC typed holes (GHC User's
Guide); Agda interactive holes.
**Neurosymbolic interpreters:** NMN arXiv:1511.02799; N2NMN arXiv:1704.05526; NPI arXiv:1511.06279;
Binder arXiv:2210.02875; VisProg arXiv:2211.11559; ViperGPT arXiv:2303.08128; Code as Policies
arXiv:2209.07753; DeepProbLog arXiv:1805.10872; Scallop arXiv:2304.04812.
**Spec->adapter / PEFT:** Text-to-LoRA arXiv:2506.06105; HINT arXiv:2212.10315; HyperTuning
arXiv:2211.12485; Generative Adapter arXiv:2411.05877; AdapterFusion arXiv:2005.00247; LoRAHub
arXiv:2307.13269; TIES-Merging arXiv:2306.01708; Task Arithmetic arXiv:2212.04089; Modular Deep Learning
survey arXiv:2302.11529.
**Library learning / solidification:** DreamCoder arXiv:2006.08381; Stitch arXiv:2211.16605; LILO
arXiv:2310.19791; Write/Execute/Assess arXiv:1906.04604; RNN->DFA extraction arXiv:1711.09576; Distilling
Step-by-Step arXiv:2305.02301; ALCHEmist arXiv:2407.11004.
**Functorial lift / rank polymorphism:** Gibbons, APLicative Programming with Naperian Functors (ESOP
2017); Remora arXiv:1912.13451 (ESOP 2014); McBride & Paterson, Applicative Programming with Effects (JFP
2008); Meijer et al., Bananas/Lenses/Envelopes (FPCA 1991); Wadler, Theorems for Free! (FPCA 1989); J
rank; jax vmap docs.
**Typed boundary / verification:** Outlines arXiv:2307.09702; GCD arXiv:2305.13971; PICARD
arXiv:2109.05093; Findler & Felleisen, Contracts for Higher-Order Functions (ICFP 2002); Wadler & Findler,
Well-Typed Programs Can't Be Blamed (ESOP 2009); Selective Classification arXiv:1705.08500; Metamorphic
Testing review (ACM CSUR 2018); Let Me Speak Freely arXiv:2408.02442.
**Anchor paper:** Program-as-Weights (PAW) arXiv:2607.02512.
