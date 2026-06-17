# Decisions subsystem -- end-to-end user evaluation findings

Acting as a user with the real LLM (`.env` `OPENAI_API_KEY`, default `gpt-5.5`),
driving the genuine pipeline (`resolve_with_decisions` -> divergence -> classify
-> surface -> steer) via a candidate generator that sends the **bare spec +
signature** with no case-specific hints (mirrors `agents/generator.py`
`SYSTEM_PROMPT` function-requirements, including its generic "prefer safe
defaults" line). No examples added to make things pass; no fallbacks/patches.

## F0 (blocking, integration): `decisions_enabled` is wired nowhere in the live path

`decisions_enabled` exists on `SemiConfig` but is referenced by **no** module
outside `semipy/decisions/` and the classifier role. A user writing
`@semiformal(decisions_enabled=True)` gets **no** fork surfaced today -- the
subsystem is only reachable through the `decisions/` API. All findings below were
obtained by driving that API directly with a real LLM candidate generator. The
plan documented live wiring as a follow-up; this confirms the feature is not yet
user-reachable through the decorator.

## Case 1 -- pure aggregation, runtime-determined null handling

- Spec: `average coral cover per site across years; some covers are null`
- Sig: `def coral_report(rows: list[dict]) -> dict[str, float]`
- Input: sites A (one null of three), B (all null), C (no null).

### Result: `diverged=False`, nothing surfaced. But a silent decision WAS made.

6 independent draws -> 6 **textually distinct** sources (distinct hashes/lengths)
-> **one** behavioral cluster: `{"A": 40.0, "C": 15.0}`. Every candidate (a) skips
null readings and (b) **drops site B entirely** because it is all-null.

The developer's mental model of "average per site" plausibly expects site B to
appear (as NaN/None, or flagged) -- its silent omission is precisely a
"discovered later as a bug" decision. The subsystem does not surface it.

### Root cause (NOT a context/prompt issue -- a method limitation)

This is a structural **false negative** of divergence-based detection:
`diverged()` is `len(clusters) > 1`, so a decision that **every** candidate makes
**identically** produces no divergence and is invisible. It is not sampling
collapse (sources genuinely differ) and not fixable by the discriminating-input
search (R7) -- that search splits *existing candidates* on *input* variation, but
all candidates handle an all-null group the same way, so no input splits them.

Detection is bounded by candidate **disagreement**, which is a strict subset of
the silent **decisions** made. A unanimously-opinionated model hides its choice.

### Corroboration (divergence hunt, 5 realistic specs x 5 draws)

Four of five realistic specs were behaviorally **unanimous** despite textually
distinct sources -- each hiding a major silent decision:

| Spec | Unanimous behavior | Silent decision hidden |
|------|--------------------|-------------------------|
| round to nearest integer | `[0,2,2,4,0]` | banker's rounding (not half-up) |
| count visits per city | no normalization | `"Paris"!="paris"!=" Lyon"` (no case-fold/trim) |
| median of 4 values | `2.5` | even-count = mean of middle two |
| rank players, ties | stable input order | tie-break convention |
| split full/last name | **3 clusters** | the one case that diverged |

The model is *more* consistent on well-known patterns (rounding, median), which
is exactly where a wrong-but-confident convention is most dangerous and least
visible to this detector.

## Case 2' -- extraction, runtime-determined surname span (a GENUINE fork)

- Spec: `split the full name into first and last name`; input `"Maria de la Cruz"`.

### The faithful, working path

When it diverges, the surfaced line is genuinely legible and aligned with a
developer's mental model -- no gibberish:

```
#? multi-part last name: keep all remaining (80%) | last word only (20%)
   guard: name has more than two words
```

axis, guard, and both fates are correct. `consequence_kind` toggles
`categorical`/`structural` by draw. **Steering verified end-to-end**: picking
"keep all remaining" mints an LLM-free commit from the stored candidate, returns
spec clause `unless name has more than two words -> keep all remaining`, sets the
decision `resolved`, and a re-check confirms the committed head reproduces
`{'first_name':'Maria','last_name':'de la Cruz'}`. Labels vary across runs
("keep rest"/"keep all remaining") but `decision_id` is content-addressed on
signatures, so identity is stable -- good.

### F1 (robustness): escalation gate misses rare forks

The adaptive policy draws `initial_candidates=3`; it only escalates to
`max_candidates` *after* the initial draw already diverges (or a discriminating
input splits it). A minority fate must appear in the first 3 draws to trigger
escalation -- the discriminating-input search cannot manufacture a candidate that
was never drawn. Measured hit-rate for this ~30% fork: **4/5** surfaced (one miss
was initial-3 agreement). For a genuinely rare fork (5-10%) the miss rate is high:
`P(miss) ~ (1-p)^3`. The escalation is keyed on observed disagreement, not on
consensus *confidence*, so low-probability decisions are detected by luck of the
initial draw.

### F2 (semantic duplication / axis conflation)

On a 3-cluster draw the surface was:

```
#? multi-part name output: last word only (40%) | remaining words (40%) | short keys (20%)
```

The `short keys` branch differs on a DIFFERENT axis -- dict key naming
(`first`/`last` vs `first_name`/`last_name`) -- conflated into the same fork as
the semantic surname-span decision. Root cause: `signature_for_run` keys on the
**entire** output value, so an orthogonal structural difference (key naming, which
is arguably a schema/contract issue, not a semantic guess) creates a sibling
branch, and `classify_divergence` emits a single `axis_label` spanning two
independent decisions. The user sees a 3-way fork where one branch is
off-topic. The pipeline has no notion of *which feature of the output* a branch
varies on, so it cannot factor a multi-axis divergence into separate decisions.

## Case 3 -- effectful, runtime-determined write mode (save a user)

- Spec: `save the user to the database`; `fx` capability; input has an `id`.

### Result: model unanimously UPSERTs; the real decision is unobservable

All candidates: `read by id -> update if exists else create`. The surfaced fork:

```
#? ID miss fallback: create immediately (80%) | check email (20%)
   guard: id and email present; lookup by id finds no existing user
```

The classifier label is **faithful to what was observed** -- but what was observed
is an impoverished slice.

### F3 (root cause: stateless shadow world)

`observe_effectful` runs each candidate against a fresh shadow recorder whose
`fx.read(...)` returns `[]` unconditionally (`semipy/effects/shadow.py`). So every
candidate's "if existing: update" branch is dead in observation -- the world has
no existing user -- and all collapse to the `create` path. The genuinely
consequential effectful decision (**update existing row vs create a duplicate**)
is structurally **invisible**. The system surfaces a second-order quirk (whether
to also check email after an id-miss) while the first-order decision collapses
away.

This is the effectful analogue of F0/Case-1: pure mode needs discriminating
*inputs* (and `discriminate.py` searches for them); effectful mode needs
discriminating *world state*, and there is no equivalent. Note the seam already
exists: `run_effectful_source(..., world=ShadowWorld)` accepts a seed world and
`ShadowWorld.read` returns per-target state (`shadow.py:114, :30`) -- but
`observe_effectful` calls it with **no world**, so it always runs against an
empty `ShadowWorld()`. Without a pre-seeded "user id=7 already exists" world the
create-vs-update fork cannot appear. The labeling is not the problem and the fix
is not a hack (thread a seed world / a discriminating-state search through
`observe_effectful`); the observation context is simply impoverished today.

## Summary of diagnoses (no patches applied)

| ID | Finding | Layer | Kind |
|----|---------|-------|------|
| F0 | `decisions_enabled` wired nowhere in live path | integration | not user-reachable via decorator |
| F1 | escalation only fires after initial-3 divergence; rare forks missed | pipeline | robustness |
| F2 | full-output clustering conflates orthogonal axes into one fork | pipeline | semantic duplication |
| F3 | stateless shadow world hides update-vs-create; no discriminating-state search | pipeline/context | coverage |
| -- | unanimous-but-silent decisions invisible (banker's round, no case-fold, drop all-null group) | method | false negative |

What works well: when a genuine fork is sampled, the **labeling is faithful and
legible** (axis/guard/fates a developer can reason through), and **steering
(pick -> LLM-free head swap -> behavior change) is verified end-to-end**. The
weaknesses are all in *detection coverage*, not in the surface or the steer.

---

# Fixes applied (F1, F2, F3) + re-validation

Addressed the three detection-coverage gaps as principled pipeline changes (no
example injection, no fallbacks). All offline tests
(`tests/test_decision_coverage_fixes.py`, 6 new) pass; full suite 296 passed;
lint clean. Re-validated with the real LLM.

## F1 fix -- escalate the draw on agreement, not only on divergence

`resolve_with_decisions` (`draw.py`) previously concluded "no fork" from the
initial 3 draws and only escalated to `max_candidates` *after* divergence was
already visible. Now an agreeing initial draw escalates to `max_candidates` and
re-observes (re-running the discriminating-input search) before concluding
no-fork; divergence still escalates to stabilize weights. Extracted
`_observe_and_search` so both passes share one code path. Cost now scales with
`max_candidates` on genuinely-unanimous slots -- the documented price of catching
a rare minority fate that three draws miss ~half the time.

Re-validation (name-split, ~30% minority fate): **5/5 surfaced** (was 4/5).

## F2 fix -- factor a multi-axis dict divergence (`factor.py`)

Whole-output clustering conflated an output-schema difference (`first`/`last` vs
`first_name`/`last_name`) with a semantic value difference (surname span) into one
3-way fork. `factor_decisions` now separates, for all-dict outputs, the
**output-shape axis** (which keys are present) from the **value axis** within the
dominant schema, emitting one `Decision` each.
`classify_divergence._classify_factored` builds them (shape axis labeled
deterministically + legibly, value axis LLM-labeled over only its sub-clusters).
Conservative: factors only when key sets actually differ, else keeps the single
decision (a pure value fork is already one clean axis). Aligning value axes
*across* differing key sets would need semantic key alignment execution cannot
supply -- separating the shape axis removes the conflation without inventing it.

Re-validation: offline test proves factoring (shape + value) on a constructed
3-axis divergence; real-LLM runs (28 fresh draws) confirm **no false-positive
factoring** -- the model now near-always emits `first_name`/`last_name`, so the
schema fork is rare and clean 2-way value forks stay a single decision.

## F3 fix -- two-pass world state in `observe_effectful`

The stateless shadow world hid update-vs-create. Added `SeededShadowWorld`
(`shadow.py`) whose reads report the input records as already present, and
`observe_effectful` now runs each candidate against both an **absent** and an
**exists** world, clustering on the combined signature (`seed_existing=True`
default; the seed is the runtime input, data-agnostic). A lookup-before-write
candidate that updates when the row exists now diverges from one that always
creates -- invisible under the single empty-world pass.

Re-validation (save-user): offline test confirms the fork surfaces with the
seeded pass and is hidden without it. Real-LLM run now reports **agreement** (all
candidates genuinely upsert) -- the faithful answer -- instead of the earlier
**spurious empty-world "check email" quirk**. The two-pass removes the misleading
artifact and exposes the real axis when candidates actually differ on it.

## Still open

- **F0 (live wiring)** -- `decisions_enabled` is still referenced only by the
  `decisions/` API, not by `execute_slot`/`SemiAgent.generate`. The subsystem is
  exercised and validated, but not yet reachable through the `@semiformal`
  decorator. This is integration plumbing into the hot path, tracked as the
  plan's documented follow-up.
- **Method-level false negative** (unanimous-but-silent decisions: banker's
  rounding, no case-fold, dropping an all-null group) -- **fundamental** to
  divergence-based detection, not patchable. Surfacing a decision *every*
  candidate makes identically needs a different detector (e.g. consensus-boundary
  probing or a reference-expectation check), out of scope for these fixes.
