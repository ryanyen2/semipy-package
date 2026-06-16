---
date: 2026-06-16
topic: surface-silent-decisions
---

# Surface the Model's Silent Decisions as Navigable Forks

## Summary

When a semiformal slot is genuinely underspecified, semipy will draw several
candidate implementations, run them to find where their behavior diverges, and
surface each silent choice the model made as a labeled, navigable fork the user
can resolve *while writing the code* — instead of discovering it later as a bug.
Open forks appear inline as `#?` lines; resolving one (pick a branch, or assert
a property when no branch fits) flows into the existing spec and contract
machinery.

## Problem Frame

Underspecification is the normal case when a person describes intent in a
sentence. "Average coral cover per site across years; some covers are null"
leaves real decisions unstated: does a never-surveyed site appear in the output
at all? Does a null reading count as zero or get skipped? The model *must* guess
to produce code — that is not a defect — but today it guesses silently and the
guess is discovered later, usually as a bug.

semipy already lowers this kind of intent into a slot and generates an
implementation, but two existing surfaces leave the gap open. The generator
produces exactly **one** candidate per slot, so the alternatives the model
considered and rejected are never visible. And the `#<` steering surface renders
only the *one* mechanism the chosen implementation used (`by`, `unless`) *after*
validation — it presents a settled decision, never the fork. The user can edit
the spec to correct a guess, but only once they already know the guess was
wrong. The cost shows up downstream, when the wrong branch silently shaped real
output.

The opportunity is to make the guess visible and correctable at authoring time:
capture the candidate space automatically, find where candidates actually
diverge, and show each divergence as a decision the user can steer — in their
own language, not as a code diff.

## Key Decisions

- **Branches are grounded in observed output divergence, not static analysis.**
  Candidates are run; a decision point is wherever their observed behavior
  differs. This survives real pandas/numpy/dataframe code, exactly where the
  precise static techniques (taint, symbolic execution) lose precision. The
  cost — divergence is only seen where some input exercises it — is mitigated by
  actively searching for discriminating inputs (R7).

- **Execution finds and weights the forks; the LLM only names them.** A
  deterministic pass clusters candidates by behavior and assigns weights (cluster
  sizes). A grounded LLM classifier then labels each real fork in user language.
  The classifier can only label forks that execution demonstrated, so the tree
  cannot contain an invented decision. This mirrors semipy's existing
  generate-then-ground discipline.

- **The node abstraction is a "decision" indexed by an ambiguity source in the
  input, not by a code statement.** A decision is `(ambiguity germ, set of fates,
  optional guard, distribution)` — e.g. *null reading → {skip 3/5, count-as-zero
  2/5}*. The reusable, general-purpose part is a small taxonomy of **ambiguity
  germs** (null/missing, empty collection, duplicate/tie, boundary
  inclusive-vs-exclusive, ordering/stability, type coercion, numeric
  precision/overflow, unit/timezone/encoding, grouping-key absence). Decisions
  cluster around these germs, which is what makes the surface legible instead of
  a raw statement diff.

- **Naming: "decision" / "fork" / "germ" — never "effect."** semipy's existing
  `effects/` subsystem already owns "effect" for real-world mutations (DB/file/
  API writes). The fate of an ambiguity germ is a different concept and must use
  distinct vocabulary or the two will collide.

- **Steering resolves a fork two ways: pick a branch (primary) or assert a
  property (fallback).** Picking writes the chosen fate into the spec surface
  (a resolved/promoted `#<`/`#>` clause) and commits the matching candidate.
  Asserting an NL property (when no branch fits) becomes a contract case enforced
  on every future regeneration; if no current candidate satisfies it, that
  triggers a targeted regeneration.

- **Open decisions live as inline `#?` lines in source.** Parallel to `#>` (spec)
  and `#<` (reasoning), a `#?` line marks an unresolved fork, so the guesses are
  visible in any editor, in git diff, and in PR review — not only inside the VS
  Code extension. The extension upgrades `#?` lines into a rich pick UI.

- **Multi-candidate generation fires adaptively.** Draw a few candidates; if they
  agree, behave exactly like today (one committed implementation, no `#?` lines,
  no extra cost). If they diverge, draw more, cluster, and surface the forks.
  Cost scales with real ambiguity, not slot count.

## Requirements

### Candidate space and divergence

- R1. When a slot is resolved by generation, the system draws multiple candidate
  implementations rather than one, under an adaptive policy: a small initial draw,
  escalating to more candidates only when the initial draw diverges.
- R2. Candidates are executed and clustered by **observed output divergence**.
  Each cluster is a branch; the cluster's share of candidates is its weight.
- R3. When all candidates agree (no material divergence), the slot resolves
  exactly as today — single committed implementation, no forks surfaced, no added
  persistence — so unambiguous slots are unaffected.
- R4. Divergence that produces no observable behavioral difference (e.g. float
  jitter, dict key ordering, an untaken tie-break) is pruned and never surfaced
  as a decision.

### Decision model and labeling

- R5. Each surfaced decision is represented as `(ambiguity germ, set of fates,
  optional guard, distribution)`, indexed by an ambiguity source in the input.
- R6. A grounded LLM classifier names each real fork in user language, drawing
  only on forks that execution demonstrated. With no API key, the system falls
  back to a deterministic, unlabeled output-cluster view rather than failing.
- R7. The system actively searches for discriminating inputs (seeded by the
  germ taxonomy) to expose forks that the available inputs did not exercise, so a
  null-handling decision is surfaced even when no sample input contained a null.
- R8. Decisions are ranked by consequence (output spread — e.g. a fork that
  changes the set of output keys outranks one that shifts a value within
  tolerance), so high-stakes forks surface first.

### Cross-domain divergence observation

- R9. Divergence observation supports pure/deterministic slots by capturing rich
  return-value signatures (type, structured value, shape, emptiness).
- R10. Divergence observation supports effectful slots (DB, server/client,
  webscraping) by capturing each candidate's reified `EffectScript` and diffing
  candidates by *intended effects*, without performing real mutations.
- R11. For nondeterministic or expensive slots (model training, scraping,
  visualization), divergence is observed on the decision-bearing structure under
  controlled determinism (seeding) and within an execution-cost guard; where a
  domain cannot be reduced to a comparable signal, the system says so rather than
  surfacing noise.

### Surface and steering

- R12. Open decisions are written as inline `#?` lines in the source skeleton,
  parallel to `#>`/`#<`, and are stripped before lowering so they never perturb
  `slot_id`, slot ordinals, or absolute line numbers.
- R13. A user can resolve a fork by **picking a branch**; the chosen fate is
  written into the spec surface, the matching candidate becomes the committed
  head, and the fork closes — without regenerating.
- R14. A user can resolve a fork by **asserting a property** in natural language
  when no branch fits; the property becomes a contract case enforced on future
  regenerations, and triggers a targeted regeneration when no current candidate
  satisfies it.
- R15. A user can interrogate the neighborhood counterfactually ("what if a site
  had zero readings?") and see which branch each candidate takes, including for
  decisions no real input exercised.
- R16. The decision set is queryable through a small DSL / NL surface (e.g.
  "which choices make site s3 disappear?") so the user can navigate forks rather
  than scroll a tree.

### Persistence and rendering contract

- R17. Each resolution persists a `DecisionSet` in the portal (content-addressed,
  like contract cases), referencing the actual candidate sources — including the
  losing candidates — so a later branch-pick can swap the committed head without
  regenerating.
- R18. The `DecisionSet` schema is the contract the VS Code extension renders; its
  shape is documented and kept in sync with the extension's type definitions.

## Key Flows

- F1. Adaptive resolve with divergence
  - **Trigger:** A slot needs generation (no cached reuse).
  - **Steps:** Draw initial candidates; execute and cluster; if a single cluster,
    commit and finish (as today); if multiple, escalate the draw, search for
    discriminating inputs, cluster, classify forks, prune noise, persist the
    `DecisionSet`, write `#?` lines, and commit a default head.
  - **Covers:** R1, R2, R3, R4, R6, R7, R8, R17.

- F2. Pick a branch
  - **Trigger:** User selects a fate on a `#?` fork (in-editor or via extension).
  - **Steps:** Write the fate into the spec surface; swap the committed head to a
    candidate in the chosen cluster; close the fork; leave other forks open.
  - **Covers:** R12, R13, R17.

- F3. Assert a property
  - **Trigger:** No surfaced branch matches what the user wants.
  - **Steps:** User states an NL invariant; candidates are filtered by a
    metamorphic check; the property persists as a contract case; if none satisfy
    it, a targeted regeneration runs against the new constraint.
  - **Covers:** R14.

## Acceptance Examples

- AE1. Null-handling fork surfaced. **Given** `#> average cover per site; some
  covers are null` over rows where at least one cover is null, **when** candidates
  diverge between dropping nulls and treating them as zero, **then** a `#?` line
  reads approximately `#? null reading: skip (3/5) | count as 0 (2/5)` and hovering
  shows a minimal discriminating input with both outputs.
- AE2. Never-surveyed site fork. **Given** the same spec where some site has no
  readings, **when** candidates diverge between omitting the site key and emitting
  NaN, **then** a separate `#?` decision is surfaced and ranked above a
  within-tolerance numeric fork.
- AE3. Unambiguous slot unaffected. **Given** a slot where all candidates produce
  identical observed behavior, **then** no `#?` lines appear, no `DecisionSet`
  persistence occurs, and resolution matches today's single-candidate path.
- AE4. Effectful divergence without mutation. **Given** an effectful slot ("upsert
  each row into table T"), **when** candidates diverge on insert-vs-upsert, **then**
  the divergence is detected by diffing reified `EffectScript`s and no real
  database write occurs during analysis.
- AE5. Hidden fork exposed by discriminating input. **Given** a sample input with
  no nulls, **when** the discriminating-input search injects a null germ, **then**
  the null-handling fork is still surfaced.
- AE6. No-API-key fallback. **Given** no API key, **then** divergence is shown as a
  deterministic output-cluster view (key set / NaN presence / numeric band)
  without LLM labels, rather than an error.

## Scope Boundaries

### Deferred for later

- The query/NL DSL surface (R16) and counterfactual interrogation (R15) are
  navigation richness layered on the core capture-and-surface loop; valuable but
  not required to prove the bet.
- Full divergence support for visualization and model-training outputs beyond the
  decision-bearing-structure strategy (R11).

### Outside this product's identity

- Symbolic execution and taint tracking as the primary divergence mechanism. They
  remain optional, best-effort *guard* enrichment (deriving an `unless` predicate
  where cheap), never the load-bearing path — they break on the real
  numeric/dataframe code semipy targets.
- Building the VS Code extension UI itself. This work defines the `DecisionSet`
  data/rendering contract the extension consumes (R18); the UI is downstream.
- Replacing or re-architecting the existing `effects/`, `contract/`, or version
  DAG subsystems. This feature composes on top of them.

## Dependencies / Assumptions

- Reuses `GistExecutor` and the `contract/runner.py` batch-gist output-capture
  pattern for pure-slot divergence, `effects/shadow.py:run_effectful_source` and
  `effects/diff.py` for effectful divergence, and `interpreted.py` memoization
  for nondeterministic slots.
- Assumes seeding/determinism control and a per-resolution execution-cost guard
  are **new** machinery (absent today) needed for R11.
- Assumes `#?` lines can adopt the same `strip_skeleton_lines` treatment `#<`
  receives so slot identity is preserved (R12).
- The N-way clustering and discriminating-input search are new; the pairwise
  output-comparison logic in `contract/change.py` is the reusable seed.

## Outstanding Questions

### Resolve before planning

- None blocking — the core shape (grounding, node model, steering, surface,
  trigger) is decided.

### Deferred to planning

- The initial candidate count and escalation thresholds for the adaptive policy
  (R1) — a tuning decision best made against real execution cost.
- Exactly how a picked fate is written into the spec surface (a synthesized `#<`
  clause vs a promoted `#>` line) while preserving `slot_id` (R12, R13).
- The consequence-ranking metric's precise definition (R8).
