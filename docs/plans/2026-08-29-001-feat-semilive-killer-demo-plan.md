# semilive: the live-coding probe, planned end to end around one killer demo

Date: 2026-08-29. Build plan for the paper's creative-domain demonstrator.
Follows `docs/brainstorms/2026-08-29-creative-domain-demonstrators.md`.

Register: this is a **scoped build plan**, not a research pitch. The goal is one
3.5-minute demo that carries the thesis, buildable in ~14 working days on top of
the existing semipy core, with explicit cut lines. Everything not needed for the
demo is a stated non-goal.

---

## 1. TL;DR

Build `semilive/`: a ~700-line pure pattern kernel + sampler + clock in this
repo, with one new slot form — `.semi("...")` on a pattern — whose generated
implementations are ordinary **`Pattern → Pattern` transformer functions**,
resolved, versioned, contracted, and surfaced by the existing semipy core.

The killer demo is one word carried through its whole life: **"skittery."**

1. The performer attaches `.semi("skittery")` to a hi-hat line mid-set. The
   pattern keeps playing unchanged while an implementation is generated; at the
   next cycle boundary the hats change; `#<` lines appear in the source.
2. They attach the *same word* to the bass. The equivalence key matches, so the
   system **REUSEs the hats' transformer verbatim — no model call — and it is
   audibly wrong** (dropout that reads as texture on dense hats kills the groove
   on a sparse bass line). The performer says `nah("bass")`; the system ADAPTs
   against the bass pattern's own queried events, forks a branch with a learned
   regime guard (`has_note_field`), and the corrected bass drops in at the next
   boundary. One spec, two implementations, one discovered distinction.
3. The performer flips the surfaced `#< by:` line to `#>` on the bass site. The
   discovered distinction is now formal, in the source, with its history intact.

The demo's central moment (step 2) is **mechanically guaranteed, not
model-dependent**: REUSE serves the donor implementation without invoking the
LLM, so the wrongness is a deterministic property of the mechanism. This is what
makes it a *reliable* killer demo.

---

## 2. What the demo must prove (thesis → demo beats)

The thesis sentence, decomposed into the four things a viewer must see:

| Thesis clause | Demo beat |
|---|---|
| a specification already meaningful but not sufficient to determine one implementation | "skittery" runs before it is decided; the bass moment proves it was genuinely underdetermined |
| later evidence progressively makes it executable | first audition (queried pattern events as context) → performer's `keep`/`nah` → site-scoped ADAPT |
| without losing what the programmer wrote | the spec text persists unchanged on both sites through the whole arc |
| …what the system inferred… | `#<` lines written into the source; the branch + guard in the version tree |
| …and what the program later does | the dispatch module holds readable transformers; the portal timeline replays the set's decisions |
| (intro P1) inputs that expose an unknown way of dividing the problem | the hats/bass fork: the performer discovers the structure of their own word through use |

Success criterion for the whole build: a viewer can answer *what did the
performer write, what did the system infer, what does the program do now, and
why* — entirely from artifacts visible on screen (source file, log, timeline).

One framing line opens the demo, spoken or captioned: *"I know what I want the
hats to feel like. I don't yet know its parameters for this track."* That is
"meaningful but insufficient" in one breath, and it distinguishes the probe
from prompting.

---

## 3. The demo script, beat by beat (~3.5 min recorded)

Everything starts fully formal — this is programming, not prompting:

```python
# set.py
cps(0.6)                                    # 4-beat cycles ≈ 144 BPM
drums = stack(s("bd ~ bd ~"), s("~ sd ~ sd"))
bass  = note("a1 ~ ~ a1 ~ ~ c2 ~").sound("bass").gain(0.9)
hats  = s("hh*8").gain(0.7)
play(drums, bass, hats)
```

**Beat 1 — the spec enters and runs (~70s).**
Edit + save:

```python
hats = s("hh*8").gain(0.7).semi("skittery")
```

For ~10s the hats play **unchanged** (identity placeholder; log shows
`GENERATE · cooking`) — the spec is part of the running program before its
interpretation exists. At the next cycle boundary the interpretation lands:
ratchet-and-dropout texture. The source file gains:

```python
#< by: sometimesBy 0.35 (fast 2) + degradeBy 0.25; because a dense closed-hat
#<     line reads skittery as dropout plus ratchet
#< unless: never silences a full cycle
hats = s("hh*8").gain(0.7).semi("skittery")
#< verified: cycles 12-15, 5-7 events/cycle, deterministic per cycle
```

Performer: `keep("hats")`.

**Beat 2 — the killer moment (~90s).**
Edit + save:

```python
bass = note("a1 ~ ~ a1 ~ ~ c2 ~").sound("bass").gain(0.9).semi("skittery")
```

Log: `REUSE ← donor hats@a1b2c3 (no LLM)`. At the boundary the bass loses half
its notes — the groove audibly collapses. Performer: `nah("bass")`. The system
ADAPTs *for this site's population* (generation context = the bass pattern's own
queried events: sparse, pitched, `note`-valued; failure reason = "rejected by
performer on this material"; parent = the donor commit). New transformer at the
next boundary: off-beat octave-ghost notes, **every original onset preserved**.
Performer: `keep("bass")`. The TUI shows one spec, two branches, guard
`has_note_field` — the distinction the performer did not know they had, now
program state.

**Beat 3 — promotion (~45s).**
On the bass site the performer flips the surfaced line:

```python
#> skittery: by off-beat octave ghosts; keep every original onset
```

Spec text changed → a formalized descendant slot. The source file itself now
records the discovered distinction; the hats keep the still-open reading.
`hush()`. Close on the portal timeline: the set as a readable decision record.

---

## 4. Design decisions (flagged, with rationale)

**D1 — Own pure pattern kernel, not Vortex/Sardine/Strudel.**
The demo needs (a) patterns queryable as pure functions of time (the whole
symbolic-oracle story), (b) semipy's Python machinery underneath, (c) full
control under deadline. Vortex is pure but experimental; Sardine is
imperative/async where we need purity; Strudel is JS. A Tidal-core kernel with
~12 combinators is ~400 lines and entirely ours to validate against.
*Reconsider only if* integration with a real environment becomes a paper claim —
it is not one; the probe claims the programming model, not a production
instrument.

**D2 — The unit of interpretation is a `Pattern → Pattern` transformer.**
Generated code is an ordinary pure Python function using only kernel
combinators. Consequences: validation = querying cycles (no audio, no taste);
the dispatch module stays a readable Python file; every existing history/store
mechanism applies unchanged; the LLM is *structurally* never in the audio path.

**D3 — Explicit `keep`/`nah`, with retention as the quiet default.**
The performer is the runtime verifier: `nah` is a human-issued guard failure
(maps onto the existing verify-fail → ADAPT path), `keep` marks the commit
verified for this site's population. Implicit retention (N=16 unedited cycles →
auto-keep) runs underneath and is mentioned, but the demo uses explicit commands
for legibility. Crucially the correction is **not** a spec edit — editing the
spec would mint a new slot and break the "same words, forked meaning" story.

**D4 — Regime guards from a fixed predicate vocabulary.**
When a fork happens, the guard is chosen as the predicate (from a closed
alphabet, mirroring the contract-design ethos) that separates the two site
populations' queried events: `has_note_field`, `event_density > x`,
`percussive_bank`. If none separates, the guard falls back to site identity —
honest, and stated as such.

**D5 — In-repo subpackage `semilive/`, excluded from the wheel.**
Imports semipy directly, no packaging friction, one repo for the paper artifact.

---

## 5. Architecture

```
set.py (performer's file)
  └─ file-watcher session (semilive/live.py): re-eval on save; keep/nah/mute/hush
       └─ layer graph: named Patterns, some wrapped as SemiPattern proxies
            └─ SemiPattern.query(span):
                 resolve current commit (portal) → transformer → transformed events
                 no commit yet → identity over inner pattern; trigger background resolve
  clock (semilive/clock.py): query 1 cycle ahead → events
  audio (semilive/audio.py): render events → numpy sampler → sounddevice
                              (optional OSC → SuperDirt for the recording)
  resolver thread: GENERATE/ADAPT via semipy agents → symbolic validation
                   → commit → dispatch module → takes effect at next queried cycle
  #< writeback: skeleton-writer pass over set.py after commit changes
  TUI (Rich): per-slot spec, decision, commit id, branch/guard, cycle timeline
```

### 5.1 Kernel (`semilive/pattern.py`, ~400 lines)

`Pattern` wraps `query(span) -> list[Event]`; `Event = (whole, part, value:
dict)`; time as `Fraction`. Constructors: `s()`, `note()`, `silence` with a
mini-notation subset (sequence, `~` rest, `*n` repeat, `<a b>` alternation —
~100 lines; anything fancier is out). Combinators: `fast slow rev stack cat
every off gain speed sound degradeBy sometimesBy euclid range sine`.
Determinism rule: all stochastic combinators draw from an RNG seeded by
`(cycle_number, commit_id)` — same cycle, same events, forever. This is what
"deterministic during the phrase" means operationally, and it is also what
makes validation-by-query sound.

### 5.2 Slot plumbing (`semilive/slot.py`)

`.semi(text)` lowers to a `SlotSpec` with a new category `PATTERN_TRANSFORM`:
spec template = the text (no interpolation in v1), expected type = `Pattern`,
site = `(file, assignment-target name)`. Hats and bass are therefore *distinct
slots sharing one `spec_equivalence_key`* — exactly semipy's donor-reuse model,
which is what makes Beat 2 fire mechanically.

Generated implementation contract:

```python
def transform(pat: Pattern, ctx: Ctx) -> Pattern:
    # pure; only semilive.pattern combinators; randomness only via ctx.rng
```

`Ctx` carries the queried sample cycles of the target pattern (the generation
evidence) and, later, named siblings (the coupling stretch goal).

### 5.3 The oracle: symbolic validation before audibility

Before any commit is scheduled, in order, all cheap and audio-free:

1. **AST gate**: imports/names restricted to the kernel allowlist (also the
   security boundary for generated code).
2. **Query k=4 unseen cycles**, check the transposed contract invariants:
   `non_silent` (≥1 event/cycle when the source pattern had events — the
   `non_empty` transpose), `non_identity` (it changes something),
   `deterministic` (query twice, byte-equal), `ambit` (touches only allowed
   value fields — gain/speed/degrade/note offsets; never the sample bank, never
   tempo — the Bound story), `bounded` (gain ≤ 1.2).
3. Commit → dispatch module → effective for cycles queried after the swap
   point (= next cycle boundary; the audition IS the staging discipline —
   conceptually the effects subsystem's shadow world, no effects code needed).

What the symbolic oracle cannot judge — whether it *is* skittery — is exactly
what `keep`/`nah` judges. The division of labor between the two oracles is
paper content (§7 of the outline: which parts must be fixed, which stay open).

### 5.4 Lifecycle mechanics

- **GENERATE**: first query with no commit → background resolve; identity
  placeholder meanwhile. Commit-during-use, unchanged in spirit.
- **REUSE**: equivalence-key donor lookup across slots — existing semipy
  concept, reimplemented in a deliberately simple routing policy (the full
  `RoutingPolicy` is tuned to data-parsing signals; we replace, not bend).
- **ADAPT on `nah`**: scoped to the rejecting site's population; parent = the
  rejected commit; generation prompt carries the site's queried events and the
  rejection. Branch + guard recorded per D4.
- **`#<` writeback**: reuse the skeleton-writer approach with music keys
  (`by`, `unless`, `verified`). Byte-stable across no-op runs, as in semipy.
- **Promotion**: flip `#<`→`#>` — existing semantics: spec text change mints
  the formalized descendant. Nothing new to build beyond honoring it.
- **Interpreted mode is deliberately unused**: the real-time constraint forbids
  an LLM in the per-cycle path, so the domain itself forces
  residualize-before-audibility. State this in the paper — the timing
  discipline *selects* the commitment mode.

### 5.5 Reuse map (this table is itself paper content)

| semipy subsystem | in semilive |
|---|---|
| `history/` Portal/Slot/Commit/Branch | unchanged |
| `store` dispatch module | unchanged |
| `agents/` generator + llm_utils | reused; new prompt profile (combinator vocabulary, purity rules) |
| contract vocabulary | transposed: `non_empty→non_silent`, `non_identity` kept, `ambit`/`bounded` new |
| `#<` surface + promotion | reused with music keys |
| equivalence-key donor REUSE | concept reused; simple new routing policy |
| runtime fingerprints / observations | replaced by queried-event population fingerprints |
| interpreted mode | deliberately unused (real-time forbids it) — a finding |
| effects / shadow world | conceptual reuse only (audition-as-staging); no code in v1 |
| decisions `#?`, sketch library, reactivity | out of scope; named as future work |

---

## 6. Milestones (14 working days) and cut lines

| M | Days | Exit criterion |
|---|---|---|
| M0 kernel+sound | 1–2 | `python -m semilive demo0` loops the scripted beat; determinism test green |
| M1 slot end-to-end | 3–5 | `.semi("skittery")` on hats: GENERATE in background, symbolic validation, boundary swap, portal commit, `#<` written |
| M2 the killer beat | 6–8 | REUSE across sites; `nah` → site-scoped ADAPT + branch + guard; `keep`/retention; full Beat 1→2 runs unrehearsed |
| M3 surfaces | 9–10 | promotion flow honored; Rich TUI timeline; all five invariants enforced |
| M4 the recording | 11–14 | sound design, 3 full rehearsals with different Beat-1 generations, record 3.5-min video, generate the timeline figure from portal JSON, draft paper subsection |

Rehearsal rule for M4: run Beat 1 three times with fresh generations and
confirm Beat 2 lands identically each time — it must, since REUSE is
mechanical; only Beat 1's flavor varies. That variance is worth *showing* (a
footnote: cold starts differ, the lifecycle doesn't).

Cut lines, in order, if slipping: TUI → plain structured log; live promotion →
shown post-hoc on the file; SuperDirt → builtin sampler only; mini-notation →
constructors take explicit lists.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Generated transformer misuses the API | AST allowlist gate + tight prompt + existing `max_retries`; the combinator vocabulary is small and fully documented in the prompt |
| Beat 2 not audibly wrong | Guaranteed by mechanism: REUSE serves donor code verbatim, and `degradeBy 0.25 + sometimesBy(fast 2)` on a 3-onset bass line deterministically breaks the groove. Fixed seed; rehearsed |
| Generation latency (5–20 s) breaks flow | Identity placeholder + `cooking` state; the wait is honest narrative ("the spec runs before its interpretation exists") |
| Python audio glitches | Render one full cycle ahead into buffers; the audio thread only plays buffers; kernel query is O(events) |
| Probe drifts into "AI music tool" | Non-goals below; the demo opens with a fully formal set and the spec never replaces code |

## 8. Non-goals (v1)

No general live-coding environment; no audio-feature analysis; no coupling
slots ("brighter when drums busy" — the designed stretch goal, only if M4
finishes early); no sketch library / INSTANTIATE; no `#?` forks; no MDL or
counterexample search; no editor plugin beyond `#<` writeback into the file.

## 9. Paper deliverables

1. **The figure**: annotated cycle timeline generated from the portal JSON —
   x = cycles; lanes = hats/bass; markers = GENERATE, REUSE, `nah`, ADAPT,
   `keep`, promotion; the `#<` text at each transition; waveform strip beneath.
2. **The recorded demo** (3.5 min) and the ~40-line `set.py` it was played from.
3. **The subsection** (Discussion or a short secondary-case section, per the
   SMX sizing logic): evidence = performer's acts of use; oracle = symbolic
   invariants + human verifier; timing = boundary-staged commitment; the
   reuse-map table as the argument that the *model* transferred and only the
   evidence/oracle bindings changed.
4. **The claims it licenses**: P1's "way of dividing the problem" made audible;
   P4's *keeps* clause under a persisting two-branch spec; P6's
   interpretation-as-program-state (the set is a readable decision record);
   epistemic vs constitutive openness (from the 08-29 brainstorm) as a
   Discussion distinction.
