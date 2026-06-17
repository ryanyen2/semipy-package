# Decisions: surfacing the model's silent forks

When a slot is genuinely underspecified, the model must guess (skip nulls vs
count them as zero; omit a never-surveyed site vs emit NaN). The `decisions`
subsystem (`semipy/decisions/`) makes that guess **visible and correctable while
you write the code** instead of discovering it later as a bug. It draws several
candidate implementations, runs them to find where their behavior diverges,
labels each divergence in user language, and surfaces it as a navigable `#?`
fork the user resolves by picking a branch or asserting a property.

Fully opt-in: `decisions_enabled` defaults **off**. A slot whose candidates would
agree, and every existing project, sees no change until a user opts in.

> Vocabulary: this subsystem owns **decision / fork / branch / germ**. It does
> not reuse **effect**, which `semipy/effects/` reserves for real-world mutations.

## Pipeline

```
slot needs generation
  -> draw K initial candidates                         [decisions/draw.py]
  -> execute + cluster by observed divergence           [divergence.py, cluster.py]
       one cluster  -> commit single head (= today)
       diverged OR a discriminating input splits them:
         -> escalate draw to N
         -> discriminating-input search (germ-seeded)   [discriminate.py]
         -> classify: label forks, prune noise, rank    [roles/decision_classifier.py]
         -> persist DecisionSet (+ all candidate sources) [persistence.py, store.py]
         -> write #? lines, commit a default head        [surface.py]
  -> user resolves: pick a branch | assert a property    [resolve.py]
```

The grounding is **execution, not static analysis** (KTD1): clustering is
deterministic and domain-agnostic, so it survives the pandas/numpy code semipy
targets, where taint and symbolic execution lose precision. The classifier only
*names* forks that execution demonstrated (KTD2) -- it can never introduce a
decision the candidates did not exhibit. With no API key it abstains to a
deterministic, unlabeled output-cluster view.

## The decision node

A `Decision` is a feature-fate node indexed by an **ambiguity germ** in the input
(KTD3), not by a code statement. The germ taxonomy (`germs.py`) is small and
reusable -- `null`, `empty`, `tie`, `boundary`, `ordering`, `coercion`,
`precision`, `unit`, `grouping-key` -- which is what makes the surface legible
("null reading") rather than a raw diff. Detection is purely structural and
data-agnostic; germs *seed* the discriminating search, and execution decides
which correspond to a real fork.

## Cross-domain execution modes

Divergence is observed differently per domain, but always on real behavior:

- **Pure / deterministic** (parsing, in-memory transforms) -- return-value
  capture via the contract batch-gist primitive (`observe_pure`). Signatures are
  noise-insensitive: float jitter and dict key ordering collapse into one branch.
- **Effectful** (DB, server/client, webscraping) -- the candidate runs through
  `effects.shadow.run_effectful_source` and is clustered by its reified
  `EffectScript` (`observe_effectful`). Divergence is observed on *intended
  effects* with **no real mutation**.
- **Nondeterministic / expensive** (model training, scraping, visualization) --
  `runmodes.py`. RNG is **seeded** for reproducibility; a **cost guard** bounds
  wall-clock so an expensive candidate cannot hang resolution; clustering uses
  **decision structure** (`cluster_by_decision_structure`), which keeps
  categorical choices (which feature/split/chart-type) and collapses the volatile
  numeric artifact (trained weights, pixels). When a slot's output is
  non-reproducible even when seeded (an object repr with a memory address),
  `assess_comparability` reports **"no comparable signal"** rather than surfacing
  noise as a decision -- the honest limit, not a fabricated fork.

## Steering

A `#?` fork is resolved two ways (`resolve.py`), both LLM-free at the pick site:

- **Pick a branch** (U9) -- the chosen fate's stored candidate becomes the
  committed head (a new commit minted from the *persisted* candidate source; no
  regeneration), and the fate is returned as a spec clause to promote into the
  `#<`/`#>` surface. The fork closes.
- **Assert a property** (U10) -- when no branch fits, a natural-language property
  is recorded as a contract case; candidates are filtered by a metamorphic check;
  if none satisfy it, a targeted regeneration is signalled.

Because the `DecisionSet` persists **every** candidate source (including losers),
a later pick swaps the head without regenerating.

## The `DecisionSet` render contract

The portal `Slot` carries a serialized `decision_set` dict (mirroring how it
carries `contract` and `ledger`); empty for unambiguous and legacy slots. This
schema is the contract the VS Code extension renders; keep
`semipy-vscode/src/data/types.ts` (`DecisionSetJson` / `DecisionJson` /
`DecisionBranchJson`) in sync with `semipy/decisions/model.py`.

```
DecisionSet
  slot_id: str
  decisions: [
    Decision {
      decision_id: str                 # content-addressed (germ + branch signatures)
      germ: str                        # taxonomy id
      axis_label: str                  # user-language; == germ when unlabeled
      branches: [
        Branch { fate_label, candidate_ids[], weight,
                 signature[], example_in, example_out }
      ]
      guard: str | null                # best-effort predicate
      consequence: float               # rank score
      consequence_kind: str            # structural | categorical | numeric
      status: "open" | "resolved"
      resolution: null
                | {via:"pick",   branch, candidate_id}
                | {via:"assert", property, contract_case_id, ...}
      labeled: bool                    # LLM-named vs deterministic view
    }
  ]
  candidates: { candidate_id -> source }   # includes losing candidates
```

`#?` lines are stripped before lowering (`lowering_ast.strip_skeleton_lines`, the
same path `#<` uses), so adding, editing, or resolving a fork never perturbs
`slot_id`, slot ordinals, or line numbers (KTD8).

## Configuration

On `SemiConfig` (via `configure`): `decisions_enabled` (master switch, default
off), `decision_initial_candidates` (3), `decision_max_candidates` (5),
`decision_classifier_model` (per-role override), `decision_cost_budget_s` (20).
