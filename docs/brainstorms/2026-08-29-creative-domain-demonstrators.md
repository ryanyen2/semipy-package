---
date: 2026-08-29
topic: creative-domain-demonstrators
status: brainstorm (scenario selection for the paper's exploratory/creative programming demonstrators)
related:
  - docs/semi-formal-programming.md
  - docs/brainstorms/2026-07-04-incremental-formalization-thesis.md
  - docs/interpreted-mode.md
  - docs/effects.md
---

# Creative-Domain Demonstrators for Semi-Formal Programming

The paper's closing move (P8) claims that if partial intent can remain
computationally active before its semantics are fixed, programming extends into
situations where understanding develops through the act of using the software —
naming live media, exploratory analysis, and malleable applications. This
brainstorm asks: which of those claims should be *built*, what exactly each
demonstrator would demonstrate, and what building them forces the theory to add.

Candidate domains from the discussion: live music (Strudel/Tidal-style live
coding), 3D scene work (Blender), exploratory data analysis (marimo).

---

## 1. What a creative demonstrator must add beyond SemiPy

SemiPy already demonstrates the full lifecycle for one configuration of the
paradigm: the evidence is **input data shape** (log lines that arrive), the
oracle is **executable** (the parse either reproduces held-out outputs or it
doesn't), and commitment can happen **whenever** (batch time). SMX, per the
paper plan, varies one dimension: evidence can be user action rather than
runtime data.

A creative demonstrator is only worth building if it varies the dimensions that
creative work actually stresses. Three axes:

**Axis 1 — what kind of evidence resolves openness.**

| evidence kind | example | density | label strength | who supplies it |
|---|---|---|---|---|
| input-shape (SemiPy) | new log format appears | dense | objective | the world |
| user-action/config (SMX) | a setting chosen at use time | sparse | explicit | the user, deliberately |
| artifact correction (Blender) | artist deletes 5 rocks the rule placed | sparse | labeled counterexample, high trust | the user, in the output |
| behavioral preference (live coding) | performer keeps an interpretation playing 32 cycles; or immediately re-edits | dense | weak, implicit | the user, through use |
| derived perceptual/symbolic (music) | onset density per cycle, queried from the pattern | dense | objective | computed from the artifact |

**Axis 2 — how strong an oracle judges stabilization.**

- *Exact executable oracle* (SemiPy): held-out reproduction, type checks.
- *Partial formal oracle* (Blender): geometric predicates are checkable —
  collision with the walkway mesh, distance to a spline, count in range.
- *Relational/symbolic oracle* (music, see §3): the *relation* stated in an
  aesthetic intent is often checkable even when its absolute values are not.
- *Human-as-sampled-oracle* (taste): the only judge of what cannot be
  symbolized, consulted implicitly (retention) or explicitly (keep/promote).

**Axis 3 — when commitment is allowed to happen.**

Batch (SemiPy: whenever the call arrives) → reactive (marimo: on cell re-run)
→ real-time (music: only at a cycle/phrase boundary; a glitch is worse than a
stale interpretation).

SemiPy occupies the easiest corner of all three axes. The creative domains
occupy the hard corners, which is exactly why they make good demonstrators: if
Preserve/Anchor/Stage/Stabilize/Reify/Bound survive there, they are design
operations, not a description of one system.

## 2. Epistemic vs. constitutive openness

There is a conceptual distinction hiding here that the paper can own.

In the log parser, the openness is **epistemic**: the missing distinctions
exist in the world (the formats really are in the file); the programmer just
has not seen them yet. Evidence is *discovered*.

In creative work, the openness is often **constitutive**: the missing
distinctions do not exist anywhere yet. "Skittery" has no ground truth to be
found; its operational meaning comes into existence through the performer's
acceptance and correction. Evidence is *created by use*.

This upgrades "use is the best oracle" from an efficiency claim to a necessity
claim: for constitutive openness, use is the *only* place the answer can come
from, because the answer is produced, not retrieved. It also connects directly
to the intro's P1 — "inputs that expose a previously unknown way of dividing
the problem": in the music scenario below, the performer discovers mid-set that
"skittery" means different things for hats than for bass. The system's
REUSE-vs-ADAPT decision is what *surfaces* that latent distinction to its own
author. Semi-formal programming becomes an instrument for discovering the
structure of one's own intent.

Naur supports this reading precisely (theory building through the activity),
and it is a claim none of the delayed-commitment ancestors make.

---

## 3. Domain A — live music (recommended primary build)

### Why this domain is the strongest test

Live coding is the setting where "cannot specify fully formally upfront while
in the flow" is not a scenario but the entire practice. The performer can
state a musical intent in one breath — *make the hats more skittery as the
energy rises* — and the pattern language demands exact terms
(`.sometimesBy(0.35, x => x.fast(2))`) that they cannot produce at tempo, and
whose right values they do not know until they hear them.

Crucially, the formal surround is unusually rich: the timing model (cycles),
the pattern types, the other named layers, the synth parameter space. A slot in
a pattern program is *heavily* anchored — this is not prompt-to-music.

### The load-bearing technical insight: patterns are pure

Tidal-family patterns are pure functions of time: the events of any cycle can
be **queried without rendering audio**. This means:

1. **Contracts survive into the aesthetic domain in symbolic form.** "Brighter
   when the drums are busy" contains a checkable relation: *when onset count
   of the drum layer increases across cycles, cutoff must not decrease*. That
   is a metamorphic/monotone contract over queried event streams — no audio
   analysis, no taste required. The contract vocabulary extends from
   `non_identity` / `idempotent` to monotone couplings between layers.
2. **Held-out validation has an exact analogue**: check the interpretation's
   event stream on *unseen cycles* for the stated relation before it is ever
   audible.
3. **Taste is only consulted for what cannot be symbolized** — the absolute
   mapping (cutoff 800→2400 vs 400→4000), the flavor of "skittery". The
   relational skeleton of an aesthetic intent formalizes; its calibration
   stays open. This is the cleanest possible instance of the paper's central
   question: *which parts need to be fixed for the computation we want now,
   and which parts can acquire precision from evidence.*

### Scenario walkthrough (the demo is a 15-minute set)

1. Performer writes an ordinary formal skeleton: drums, bass, pads.
2. Adds a slot on a layer:
   `pads $> drift slowly between two chords, brighter when the drums are busy`.
   The surround fixes: the pattern type, the cycle structure, what "drums"
   denotes (a named sibling layer — cross-slot dataflow, the reactivity layer
   verbatim). Open: what "brighter" maps to, what "busy" measures, the curve.
3. **Staged audition.** The first interpretation is *cued, not dropped*: it
   auditions at the next phrase boundary (or on a pre-listen/shadow channel —
   the effects subsystem's shadow world, transposed to sound). Interpretation
   changes are themselves scheduled events in musical time.
4. **Stabilization by retention.** The performer plays on; N unedited cycles
   (plus the symbolic relation holding on queried cycles) stabilizes the
   interpretation into a concrete pattern transform — deterministic per cycle,
   LLM out of the loop. Determinism *during* the phrase is non-negotiable in
   performance; this is interpreted-mode promotion with a behavioral gate
   replacing part of the held-out gate.
5. `$> darker` on the same layer later → ADAPT from the prior commit. The
   version DAG becomes a **timeline of the performance**: every aesthetic
   decision is a commit with the musical moment it happened. After the set,
   the performance is a program you can read.
6. Next session, "brighter when busy" lands on a different instrument →
   INSTANTIATE from the sketch library. Over sessions the performer's informal
   vocabulary — skittery, darker, let it breathe — accretes learned, versioned
   meanings: **the sketch library as an idiolect**. Eventually they promote:
   `#< by: cutoff 800→2400 tracked to onset count` flips to `#>`, and
   "brighterWhenBusy" exits as a named formal combinator in their personal
   library. Gradual formalization, but the thing being formalized is a
   *personal musical language grown from performance* rather than designed.

### What it stresses in the design operations

- **Stage** gains a temporal dimension: not just *which* evidence may resolve
  openness, but *when* resolution is allowed to act (phrase boundaries;
  stale-but-stable beats fresh-but-glitchy).
- **Stabilize** splits into within-piece stability (must be deterministic now)
  and across-piece revisability (should re-open cheaply next session).
- **Bound** becomes vivid: tempo, output gain, the layers *not* marked with a
  slot are never up for interpretation. The cost of a bad interpretation is
  immediate and sensory, which makes the negative space legible to readers.

### Implementation path

Build on a Python Tidal: **Vortex** (TidalCycles' Python port) or **Sardine**,
so the semipy core (portal, routing, contracts, interpreted mode, sketches,
reactivity) is reused nearly verbatim. Needed new parts:

- a slot surface inside pattern definitions (`$>` chained onto a pattern, or
  `#>` above it) lowering to a `SlotSpec` whose free variables include sibling
  layers;
- an evidence recorder hooking the REPL/editor: keep-duration, re-edit,
  mute/solo events → weak preference labels; corrections → strong labels;
- symbolic pattern-query checks as a contract backend (query k unseen cycles,
  check the monotone relation);
- boundary-aligned swap-in of new interpretations (cue/crossfade).

Risks: LLM latency in a live loop (mitigated: interpretation is authored
off-beat, applied on-boundary; the hot path is always compiled patterns);
degeneration into text-to-music (mitigated: slots decorate an authored formal
program, never replace it; the demo must show the program staying primary and
legible).

---

## 4. Domain B — Blender / 3D scenes (scoped probe, not a full system)

### The real phenomenon

An environment artist can state: *scatter rocks along the ridge, denser near
the path, but never blocking the walkway*. The open distinctions are geometric
predicates over **this** scene — what counts as "ridge", "near", "blocking" —
determinable only from the actual scene data plus the artist's corrections.

The deep attraction: this is the procedural-vs-direct-manipulation gap
(Sketch-n-Sketch's territory). Procedural is re-runnable but you can't just
grab a rock; direct manipulation is immediate but unrepeatable. Semi-formal
programming offers a principled middle: **a direct manipulation is evidence
that refines the interpretation of a named open predicate in a persistent
spec** — it does not patch the output, and it does not vanish on re-run.

### Why to scope it hard

General inverse synthesis (arbitrary output edits → program changes) is its
own research program. The tractable, still-novel probe:

- slots emit **reified placement effects** into a shadow collection (the
  effects subsystem, almost verbatim: preview = shadow world, apply = ledger
  event, revert = compensation replay; Blender's undo is transient, the
  ledger is durable and keyed to commits);
- artist corrections (delete/move/scale of rule-placed objects) are captured
  as labeled counterexamples **against named open predicates only** ("near",
  "blocking") — threshold/region fitting gated by the counterexample search,
  not open-ended synthesis;
- the system surfaces `#< unless: within 0.5m of path spline → skip`; the
  artist promotes it;
- the demo moment: click any rock → provenance chain *object → effect →
  ledger event → commit → spec line → the corrections that shaped it*. "Why
  is this rock here" has an answer. No other scatter tool can answer it.

Geometric invariants give this domain a **partial formal oracle** (collision,
distance, counts) — the middle rung of the oracle spectrum, between logs and
taste. That is its role in the paper's argument: the ladder needs a middle.

---

## 5. Domain C — marimo / exploratory data analysis (cheap secondary win)

Closest to what semipy already does (messy-data normalization), so it adds the
least conceptual novelty — but it is the lowest-effort, most legible build,
and it is the only one that makes two existing subsystems *visible*:

- **Reactivity.** marimo's cell DAG and semipy's `DependencyGraph` are the
  same shape. When a cleaning slot ADAPTs because the feed drifted, downstream
  cells go stale *in the notebook UI*. Staleness propagation of interpretation
  changes, in an interface analysts already trust.
- **Branch-as-regime** (the incremental-formalization thesis, §3). The demo:
  a weekly report notebook over a drifting CSV feed. Week 7's data changes
  format; the cleaning slot forks; both regimes coexist with a guard; and —
  the reflection-section payoff — the contract can answer *did last week's
  published numbers change meaning under this week's implementation?* The
  reproducibility question, made concrete.

Build: mostly existing semipy + a marimo plugin surfacing the portal (slot
health, version tree, regime guards inline). Low risk, high legibility.

---

## 6. What the creative domains force the theory to add

These are contributions, not costs — each is a section-6/7 upgrade:

1. **An evidence typology** under Stage: discovered vs created; dense vs
   sparse; weak-implicit (retention) vs strong-labeled (correction) vs
   objective-derived (queried event streams). Trust and rate become
   parameters of the operation.
2. **An oracle spectrum** under Stabilize: exact → partial-formal →
   relational-symbolic → human-sampled. Stabilization policy must be
   parameterized by oracle strength: the weaker the oracle, the more
   conservative the freeze, the cheaper the re-melt, and the more load the
   visible surface (`#<`) must carry.
3. **Commitment timing as a first-class discipline**: batch → reactive →
   real-time. In real-time media, *when interpretation may change* is part of
   the program's correctness, and interpretation changes are scheduled events.
4. **Bound made sensory**: each domain has non-negotiables (gain/tempo; scene
   integrity — never touch user-authored objects; determinism of a published
   report run). Creative domains make the negative space legible because
   violations are immediately perceptible.
5. **Vocabulary accretion / idiolect**: only longitudinal creative use shows
   the sketch library becoming a personal semi-formal language — a strong HCI
   claim: semi-formal programming is how a personal DSL is *grown* rather
   than designed.
6. **Provenance as creative record**: the commit timeline of a set/scene is
   not debugging metadata; it is a readable record of the work (and in music,
   replayable). Regimes read as movements.

## 7. Recommendation

- **Build the live-music probe as the primary creative demonstrator.**
  Highest conceptual payoff (constitutive openness, taste-as-sampled-oracle,
  symbolic-relational contracts via pattern purity, real-time staging,
  idiolect), most memorable demo, and a Python substrate (Vortex/Sardine)
  that reuses the semipy core.
- **Do the marimo integration as the cheap secondary** — it demonstrates
  reactivity and branch-as-regime, which currently have no visible demo, and
  it anchors the reproducibility discussion.
- **Scope Blender to the provenance/effects probe** (or position it as the
  worked future-work case): corrections refine named predicates only; the
  click-a-rock provenance walk is the demo. Do not attempt general inverse
  synthesis.
- **Do not build**: a prompt-to-music layer (indistinguishable from
  generative-AI tooling; forfeits the persistent-spec identity), three
  symmetric systems (page pressure; the SMX reasoning in the outline applies
  here too — one deep case plus scoped probes).

## 8. Where each lands in the paper

| artifact | paper role |
|---|---|
| SemiPy | deep case (§5 Operationalizing), full lifecycle, exact oracle |
| SMX | secondary case: evidence = user action |
| music probe | the P8 live-media claim, operationalized; sources for §2 (which parts must be fixed — the relational skeleton formalizes, calibration stays open) and §6 (oracle spectrum, timing discipline) |
| marimo probe | the P8 EDA claim; makes reactivity + regimes + reproducibility visible for the Critical Reflection section |
| Blender probe | Discussion / future-work case with one built moment (provenance walk); the middle rung of the oracle spectrum |

The one-sentence version to keep in view while designing any of these: the
demonstrator must show a specification that **persists** while its
interpretations become program state underneath it — the moment a probe stops
being readable as "the spec stayed and the evidence made it precise", it has
drifted into being an AI feature, and it stops serving the paper.
