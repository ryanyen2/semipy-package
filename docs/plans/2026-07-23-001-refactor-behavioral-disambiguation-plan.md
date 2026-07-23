# Behavioral disambiguation: sound-but-incomplete divergence under input drift

Date: 2026-07-23. Design notes + pipeline-modification plan for the `#?`
decision subsystem (`semipy/decisions/`). Supersedes the probability-weighting
approach shipped in `231e7da`.

Register: this is a **change plan**, not a research pitch. The honest headline is
that most of the machinery this proposes already exists in the repo. The work is
(1) deleting a weighting signal that measures the wrong thing, (2) fixing one
under-sampling bug that is the mechanical cause of wrong branch merges, and (3) a
bounded extension to the splitter search plus honest completeness accounting.
Nothing here is a novel method in the literature sense (see "Prior work" below).

---

## 1. TL;DR

Three concrete defects, each with a file anchor and a fix:

| # | Defect | Location | Fix |
|---|--------|----------|-----|
| D1 | `#?` branch **weights** come from a softmax over per-candidate mean log-prob, which averages over *every* token in the response and has **zero attribution** to the decision point. | `cluster.py:119-135`, `generator.py:341-444`, `config.py:177` | Delete the logprob path. Weight by cluster mass (vote count), which is what the fallback already does. |
| D2 | The pure decision path observes divergence over **exactly one input** (the current runtime invocation), never over `input_observation_samples`. Candidates that agree on that one input but differ elsewhere get **merged into one branch**. | `slot_resolver.py:1571-1575` | Feed the recorded observation population (decoupled across free variables) into `observe_pure`. |
| D3 | The discriminating-input search only mutates the **primary** variable with structural germs (null/empty/tie/boundary). It cannot find forks that require **decoupling correlated free variables** (e.g. `lower` vs `text`) or matching against string literals the candidates test. | `discriminate.py:38-82,148-165` | Add a decoupling pass over non-primary free variables; optionally a literal-vocabulary probe. Report the residual completeness gap as a number. |

D2 is the highest-value fix and the smallest diff. D1 removes a misleading
signal. D3 is the genuinely hard, open-ended part and is scoped as best-effort
with an explicit gap number, never a "complete" claim.

---

## 2. What the weighting actually measures (D1)

`generator._extract_mean_logprob` (`generator.py:341`) averages logprobs over all
tokens in the final response:

```python
values = [float(lp["logprob"]) for lp in logprobs]
return sum(values) / len(values)
```

`cluster_signatures` (`cluster.py:119-135`) then turns those per-candidate scalars
into branch weights via a subtract-max softmax when every candidate is scored.

The problem is attribution. The number the user reads as "this branch is 40%
likely" is a length-normalized sequence probability dominated by boilerplate
tokens (imports, signature, docstring, control-flow scaffolding). The tokens that
actually *choose the behavior* — the label string, the conditional that selects
it — are a vanishing fraction of the average. Empirically the apache candidates
scored in a band of -0.006 to -0.013: the label token's signal is drowned. The
softmax over those numbers is precise noise.

The object that *would* carry decision attribution is the martingale
quadratic-variation increment `r_t` at the fork tokens — the per-position
resolution profile from the program-pattern ERP work
(`program-pattern/docs/plan-fork-microstructure.md`). But `r_t` requires
open-weight, per-token prefix-tree resampling (Qwen/vLLM). semipy generates
through the OpenAI API, which does not expose it. There is no API-reachable fix
that makes mean-logprob mean what the `#?` weight claims it means.

**Decision: delete the logprob weighting.** Branch weight becomes cluster mass
(the `len(members) / total` fallback that already exists). Cluster mass is honest:
it is the fraction of *drawn candidates* that landed in this behavior. It is not a
calibrated probability, and the surface should not imply it is (see §5.4).

---

## 3. Why one input is not enough (D2) — the worked example

The pure path builds `sample_rows` as a single dict:

```python
# slot_resolver.py:1571
sample_rows = (
    None
    if effectful_runtime_values is not None
    else [{v: runtime_values.get(v) for v in free_vars}]
)
```

`observe_pure` clusters candidates by their output-value vector over those rows
(`divergence.py:107`, `cluster.py:signature_for_run`). Over one row, the vector
has length one, so **any two candidates that produce the same output on that
single input are declared behaviorally identical** and merged into one branch.

Worked example — the cached apache slot (`examples/.semiformal/855628c46486f6f5.portal.json`,
slot `a85d013a2ddaa861`, 5 candidates c0–c4, 19 recorded observations). The first
observed body is `'jk2_init() Found child 6725 in scoreboard slot 10'`. On that
one input:

- c0 → `jk2_found_child_scoreboard`
- c1 → `mod_jk_scoreboard_child_found`
- c2, c3 → `jk2_scoreboard_child_found`   ← merged
- c4 → `mod_jk_scoreboard_child`

Four distinct labels → four branches, with **c2 and c3 pooled at ~40%**. But c2
and c3 do *not* agree in general; they only agree on this one body. The 40% branch
is an artifact of the sample size, not a real behavior.

The refinement, measured against the real cached data, as the input evidence grows:

| Evidence fed to the observer | Behavioral classes found |
|------------------------------|--------------------------|
| 1 runtime row (today's path) | 4 (c2+c3 wrongly merged) |
| 19 recorded observations (`input_observation_samples`) | 5 (c2, c3 separate) |
| 19 + structural germ probes | 5 |
| 19 + literal-subset probes (797 inputs) | 4 → **but** this *under*-counts unless `lower` is decoupled from `text` |
| 19 + literal subsets + **decoupled `lower`** (2407 inputs) | **5, residual gap 0** |

Two lessons, both load-bearing for the plan:

1. **Just feeding more observed inputs (D2) already fixes the visible wrong merge**
   (4 → 5, separating c2/c3). This is the cheap, high-value change.
2. **Completeness is the hard part, and it is adversarial.** The observed inputs
   are correlated: `lower == text.lower()` in every recorded sample. Some
   candidates rely on that correlation; others don't. A search that does not
   actively *decouple* correlated inputs silently under-reports — my own first
   literal-subset search returned 4 and I wrongly believed it complete until an
   input with `lower` disagreeing from `text.lower()` split c1 from c4. That is
   D3.

---

## 4. The honest object, and prior work

The correct thing to report for a slot is never a bare branch count. It is:

> **(behavioral classes found, the tested-input set, upper bound = #candidates,
> residual gap)** — plus a re-open trigger keyed to input drift.

- **Soundness** is easy and already holds: every split is a real, executed output
  difference. The searcher can only ever produce a **sound lower bound** on the
  number of distinct behaviors.
- **Completeness** is the ballgame and is unbounded in general. Gap-0 closure
  (all candidates proven pairwise distinct) only happened for apache because the
  input space was tiny and enumerable. A structured-dict slot would leave a
  non-zero residual that only SMT could close, and we will not claim otherwise.
- **Re-open**: an "equivalent over N tested inputs" verdict is an ∃-sample claim
  masquerading as an ∀-input claim (the exact F1 caveat in
  `program-pattern/docs/probe-round1-reflection.md`). It must carry a re-open
  condition: when `input_observation_samples` grows a genuinely new input pattern
  and it splits a merged branch, the decision re-opens.

**This is not novel, and the plan should not pretend it is.** Clustering
candidates by execution output is Functional Clustering (2025), Semantic Voting
(2026), Symbolic Equivalence Partitioning (2026), CodeT (2022)
(`program-pattern/allinone.md`, Group A). Synthesizing distinguishing inputs and
asking the user is *Choose, Don't Label* (POPL 2026) and the Mica differential-
testing line (Groups B/C). semipy already implements the Group A + Group B loop
(`divergence.py` + `discriminate.py`). The only under-owned pieces here are
operational, not conceptual: (a) honest completeness-gap accounting surfaced to
the user, and (b) re-opening a committed decision under input-distribution drift.
Treat those as engineering, not a paper.

---

## 5. Pipeline modification plan

Phased so each phase is independently shippable and testable. Phases 1–2 are the
correctness core; 3–5 are the honesty/completeness layer.

### Phase 1 — Remove the logprob weighting (D1)

Smallest correct diff first: stop threading scores; the vote-count path already
exists as the fallback.

- `agents/config.py:177` — remove `decision_probability_weighting` (or set the
  default to `False` and deprecate; removal is cleaner since nothing else reads a
  meaningful value from it).
- `slot_resolver.py:1510,1521,1528-1541,1588` — remove the `probability_weighting`
  branch, the `candidate_scores` dict, and the `generate_scored` call. Candidates
  are always drawn via `SemiAgent().generate`.
- `cluster.py:96-135` — delete the `scores`/softmax branch of `cluster_signatures`;
  weight is unconditionally `len(members) / total`. Drop the `scores` parameter.
- `divergence.py:78,114,165,228` — drop the `scores` parameter and the
  `logprob` field on `CandidateRun` (`divergence.py:42`).
- `draw.py:97,112,156,171,199,216,259` — drop `candidate_scores` threading.
- `generator.py:341-444` — delete `_extract_mean_logprob`; revert `generate_scored`
  to plain `generate` (or delete it if it now has no callers).

Verification: `git grep -n "candidate_scores\|logprob\|_extract_mean_logprob\|
generate_scored\|decision_probability_weighting"` returns nothing. Existing
decision tests still pass (weights change from softmax to vote-share on any test
that had scoring on — update fixtures).

### Phase 2 — Observe over the recorded population, not one row (D2) — DONE

This is the highest-value change and touches one call site.

- `slot_resolver.py:1571-1584` — replaced the single-row construction with a call
  to the **existing** `slot_observations._reuse_verify_sample_inputs`, mapping its
  sample-input dicts to the `{free_var: value}` row shape `observe_pure` wants.
  That helper builds the current invocation row **plus** one row per recorded
  observation per free variable, each varied **independently** (one variable
  substituted, others held fixed) — so correlated variables (`lower`/`text`) are
  exercised apart. It is already capped (`_REUSE_VERIFY_MAX_SAMPLES = 50`,
  `_REUSE_VERIFY_MAX_PER_KEY = 40`) and falls back to the single row when the slot
  has no observations or `slot` is `None`.
- No change needed in `observe_pure`/`cluster_signatures` — they already cluster
  over an arbitrary-length row list.

Note: because the reused helper decouples one variable at a time rather than
zipping observations positionally, it already exercises the correlation-coupling
axis that pure structural germ injection misses. On the apache slot this alone
separates all five candidates (including the `lower`-vs-`text` split that D3 was
scoped to catch). That is slot-specific; the general completeness gap in §4 still
stands, and D3 remains the place to strengthen it for slots the one-var-at-a-time
substitution doesn't fully decouple.

Cost note: this multiplies candidate executions by up to ~50×. That is bounded
(candidates are gists over in-memory rows, `timeout=15s` total) and gated by
`decisions_enabled` (off by default).

Verification (done): `tests/test_decision_observed_population.py` loads the apache
portal and asserts the single-row path merges c2/c3 into 4 branches while the
recorded-population path separates them into 5.

### Phase 3 — Decouple correlated free variables in the splitter (D3) — DONE

`search_discriminating_inputs` mutated only the *primary* variable (first
non-`self`), and `_germ_variants` list-wrapped any string/scalar into `[None]`/
`[0]` — so a string variable got no meaningful germ and a fork hinging on a
non-primary variable was unreachable.

- `discriminate.py` — the search now iterates **every** free variable, mutating
  one at a time with the others held at their base values, and `_germ_variants`
  gains a `str` case (`""` empty, `None` null). Because each variable is mutated
  in isolation, this **decouples correlated inputs for free**: mutating a `lower`
  argument that always equals `text.lower()` in observed data, while holding
  `text` fixed, breaks the equality and splits a candidate that trusts the derived
  input from one that recomputes it — no correlation-detection heuristic and no
  new germ taxonomy required. `DiscriminationResult` gains `variable` (which input
  exposed the split), feeding Phase 4's provenance.
- `draw.py` `_observe_and_search` — a germ-found fork is now **appended** to the
  observed population (`sample_rows + [disc_row]`) instead of replacing it with a
  single row. Adding a row can only refine a clustering, so the search can add
  branches but never drop ones the population (Phase 2) already separated. This
  closes a downgrade that Phase 2 exposed (population-diverged slot vs. a
  single-row germ observation).

Still best-effort: it closes the correlation-coupling gap germ injection missed;
it does not make the search complete. The residual gap in §4 stands.

Verification (done): `tests/test_decision_discriminate.py`
::`test_search_decouples_correlated_nonprimary_variables` — an inert primary with
a correlated non-primary pair; the search finds the fork the primary-only search
could not. Kernel merge/freeze separation-search callers
(`kernel/operators.py`) get a strictly more thorough (more conservative) search;
their tests are unchanged.

### Phase 4 — Report the completeness gap (honesty layer) — DONE

- `divergence.py` / `DivergenceResult` — `tested_inputs` field (rows clustered
  over) and a `residual_gap` **property** = `n_candidates − n_clusters`
  (co-clustered candidates not proven distinct). Zero means the behavioral
  partition is fully resolved for the drawn candidates; nonzero marks candidates a
  wider input set might still split.
- `model.py` `Branch` — `equivalent_over_n: Optional[int]`, persisted. Set to
  `tested_inputs` when a branch pools >1 candidate (its members are only
  *equivalent over N tested inputs*, the ∃-sample-as-∀ caveat that licenses
  re-open); `None` for a singleton, which is already distinguished and whose
  `example_in` is its witness. I dropped the plan's alternative `distinguished_by`
  field as redundant — `Branch.example_in` already carries the separating input.
- `decision_classifier.py` `_build_branches` — the one construction site — sets
  `equivalent_over_n` from `divergence.tested_inputs`.

Verification (done): `tests/test_decision_completeness.py` — residual gap counts
candidates not proven distinct (3 candidates, 2 classes → gap 1; all-distinct →
gap 0); a pooled branch carries `equivalent_over_n == tested_inputs` and a
singleton carries `None`; round-trips through `to_dict`.

### Phase 5 — Surface the evidence + re-open hook

- `surface.py:format_decision_line` (`surface.py:21-33`) currently renders only
  `fate_label` + `weight`. Add the discriminating input (or the "agree over N
  tested inputs" caveat) so a reviewer can see *why* two options differ:
  `#? <axis>: A [in="…"→out=X] (60%) | B [in="…"→out=Y] (40%)`. Keep it one line;
  truncate examples.
- Re-open hook: `slot_observations.py:300` already computes
  `_obs_content_fingerprint` — a pattern-level fingerprint that changes only when
  a genuinely new input *pattern* appears. Register a check on slot execution: if
  the fingerprint changed since the DecisionSet was committed, re-run Phase-2
  observation over the enlarged population; if a previously-merged branch now
  splits, re-open the decision (mark it open again and re-render `#?`).

---

## 6. What we are explicitly NOT doing

- **No SMT / symbolic equivalence.** The residual gap on structured-input slots
  stays open and is reported as a number, not closed.
- **No calibrated probabilities.** Weights are vote-share, labeled as such.
- **No token-level resolution profiling** (`r_t`) — not API-reachable; it lives in
  the program-pattern open-weight track, not here.
- **No new dependencies.** Everything reuses existing modules (`divergence`,
  `discriminate`, `slot_observations`, `germs`).

## 7. Test plan

1. Phase 1: grep-clean of the removed symbols; decision tests green with
   vote-share weights.
2. Phase 2 (the key test): apache portal → 5 clusters, c2 ≠ c3. Add a second
   fixture where the observed inputs are correlated to lock in that Phase 2 alone
   does *not* yet close the correlation gap (documents the D3 boundary).
3. Phase 3: the correlated-input fixture now separates the coupled candidates;
   assert the residual gap drops.
4. Phase 4/5: assert `Branch.distinguished_by` is populated for split branches and
   the re-open hook fires when a fingerprint-changing input is appended to
   `input_observation_samples`.

---

## 8. Order of work / risk

Ship Phase 1 and Phase 2 first — together they are a small diff that removes a
misleading signal and fixes a real wrong-merge, with one decisive regression test.
Phases 3–5 are additive and can land incrementally. The one behavior change users
will see is that some slots surface *more* branches than before (the previously-
merged ones) and weights shift from softmax to vote-share; call this out in the
changelog since `decision_probability_weighting` shipped in `231e7da`.
