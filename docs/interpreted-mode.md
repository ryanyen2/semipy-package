# Interpreted Mode and the Promote-to-Code Decision

This document specifies **interpret-until-shape-stable** slots — the `semipy`
execution mode in which a slot keeps the LLM in the hot path *per call* until it
can compile itself into ordinary cached code. It is the bridge between the
default `semipy` behavior (residualize a spec to code on the *first* call) and
per-row semantic systems such as DocETL / Palimpzest (call the model on *every*
row, forever).

The mode lives in `semipy/interpreted.py` and is driven from
`semipy/slot_resolver.py` (`_execute_interpreted_slot`, `_promote_interpreted_commit`).
All LLM work flows through `semipy/agents/llm_utils.py:_openai_responses_text`
(the **OpenAI Responses API**, `openai_model`, default `gpt-5.5`). Validation runs
through the same sandbox as generation, `semipy/agents/executor.py:GistExecutor`.

It is **fully opt-in**: a slot only enters this mode when marked `interpreted=True`.


## 1. Motivation: the irreducible-operator boundary

A normal `semipy` slot residualizes its meaning to a deterministic Python function
on first use. This is exactly right when the slot's correct output is a function of
the input's *shape* (parsing, extraction, normalization, classification): the
compiled function generalizes, and later calls are LLM-free.

It is exactly *wrong* for an operator whose correct output depends on each input's
open-ended *content* — "summarize this passage", "which of these two answers is
better", "is this clause unusually risky". There is no stable function to freeze;
forcing one produces a non-semantic stub (a truncator, a keyword counter) that
type-checks but does not do the task. The default REUSE machinery already half-says
this: its semantic re-check is capped (`semantic_verify_max_adapts`) precisely
because "an inherently-semantic slot compiles to a static function the judge can
reject on every new free-text input."

Interpreted mode resolves the tension by *not committing to code until it can prove
the commitment generalizes*:

- **shape-stable** operators accumulate a few examples, compile a residual that
  reproduces **held-out** examples, and promote — after which they are LLM-free;
- **irreducibly-semantic** operators never reproduce held-out outputs, so they stay
  interpreted and the model is paid on every (non-memoized) call — correctly.

The same surface serves both; the held-out validation gate decides which regime a
slot is actually in, per slot, from its own behavior.


## 2. Opting in

```python
from typing import Literal
from semipy import semi, semiformal

# standalone / inline expression
summary = semi(f"one-sentence summary of: {passage}", interpreted=True)

# every #> slot in a function
@semiformal(interpreted=True)
def parse(line: str) -> dict:
    #> extract the bracketed timestamp into ts and the level into level
    ts = ...
    level = ...
    return {"ts": ts, "level": level}
```

`interpreted=True` sets `SlotSpec.interpreted` (`semipy/types.py`). For `semi()` the
flag is threaded in `semipy/semi_fn.py`; for the decorator it is stamped onto every
lowered `SlotSpec` in `semipy/decorator.py:_wrap_function`. The flag is persisted in
the slot snapshot (`_slot_spec_snapshot`), and the resolver branches into interpreted
execution *before* `resolve()` whenever the slot is interpreted and not yet promoted.


## 3. The execution loop

`_execute_interpreted_slot` runs on each call of an interpreted, not-yet-promoted
slot:

1. **Memo check.** Compute `compute_runtime_input_fingerprint(runtime_values)`. If it
   is in the slot's interpreted memo, return the stored output (no LLM).
2. **Interpret.** Otherwise call the model on this input via
   `interpreted.interpret_call`. The output shape depends on the slot:
   - **multi-output `#>`** (`len(output_names) > 1`): a JSON object keyed by the
     output names — the dict-shaped payload the statement-block scaffold unpacks.
   - **constrained classification** (`expected_type` is a `Literal[...]` / `Enum`,
     detected by `extract_label_set`): exactly one label, snapped to the set by
     `_snap_to_labels`.
   - **otherwise**: a string (or, for a concrete non-`str` `expected_type`, the JSON
     answer coerced via `type_adapter_for`).
3. **Record.** Append a JSON-safe `(args, output)` example to
   `slot.advisor_state["interpreted_examples"]` and persist the portal.
4. **Maybe promote.** Once `>= promote_after` (default 6) examples have accumulated
   (and again every `promote_after` after a failed attempt), call `attempt_promotion`.

State on `slot.advisor_state` (persisted, so the loop survives process restarts):
`interpreted_examples`, `interpreted_memo`, `interpreted_promoted`,
`interpreted_attempts`, `interpreted_next_attempt`, `interpreted_holdout_match`.


## 4. Promotion: held-out validation in the sandbox

`attempt_promotion(instruction, free_variables, examples, ...)` is the gate:

1. **Split.** `split_holdout` deterministically holds out every *k*-th example
   (~34%); the rest are the training set the codegen sees.
2. **Synthesize.** `synthesize_residual_source` asks the model to compile the
   operation into `def solve(<free_vars>): ...` — stdlib-only, imports inside the
   body, **no lookup table of the examples**, and (for multi-output) returning a dict
   keyed by the output names, or (for classification) returning one of the labels.
3. **Validate in the sandbox.** `validate_residual` composes a standalone program
   (`_compose_validation_gist`) that defines `solve`, runs it on every **held-out**
   input, and prints a JSON verdict after `__GIST_RESULT__`. It runs through
   `GistExecutor.execute_sync` (local subprocess, or E2B when `e2b_api_key` is set) —
   the same sandbox the generation pipeline uses, never in-process `exec`. The
   residual must reproduce **every** held-out output (`_match`: per-key for dicts,
   whitespace-normalized otherwise).
4. **Resample.** Because codegen is non-deterministic, each attempt draws up to two
   `solve` candidates and takes the first that passes; otherwise it reports the best
   held-out fraction seen and the slot stays interpreted.

Holding out examples the codegen never saw is what rejects two failure modes at once:
a **lookup-table** residual (fails on held-out inputs) and an **irreducibly-semantic**
operator (held-out outputs are unpredictable, so no `solve` ever passes).

### Promotion produces a normal commit

On success, `_promote_interpreted_commit` mints an ordinary `Commit` (decision
string `"PROMOTE"`) holding the residual source via `create_commit` /
`add_commit_to_slot`, writes the dispatch module, and persists the portal. From then
on `interpreted_promoted` is set, the interpreted branch is skipped, and the slot is
served by the **standard REUSE path** — including its verify-on-REUSE and (capped)
semantic re-check. The promoted implementation is a first-class citizen of the
Portal⊃Slot⊃Commit DAG: it appears in the version tree, can be rolled back, and
carries provenance like any other commit.


## 5. Worked trace (timestamp extraction)

A `semi(f"extract the bracketed timestamp ...: {line}", interpreted=True)` over
Apache log lines:

```
[ 0]   ~2 s   (LLM)   Sun Dec 04 04:47:44 2005      # interpret + record example
 ...
[ 5]  ~16 s   (LLM)   Sun Dec 04 04:51:14 2005      # 6th example -> attempt_promotion -> PROMOTED (held-out 1.00)
[ 7]   ~3 ms  (cache) Sun Dec 04 04:51:14 2005      # standard REUSE of the promoted residual, no LLM
 ...
[13]   ~3 ms  (cache) Sun Dec 04 04:51:38 2005
```

The model is paid ~6 times, then never again; a fresh process REUSEs the persisted
commit immediately. An interpreted "summarize this passage" slot over the same number
of calls reports `interpreted_holdout_match = 0.00`, never promotes, and returns a
real LLM summary every (non-memoized) call.

The cost model mirrors the sketch library's: once promoted, per-call cost collapses
from one LLM round-trip to a native function call.

$$
\text{cost}(\text{promoted call}) \;=\; O(\text{run residual}) \;\ll\; \text{cost}(\text{interpret call}) \;=\; \text{one LLM round-trip}
$$


## 6. Relationship to the rest of the system

- **vs. default residualize-on-first-call.** Default mode commits to code immediately
  and adapts reactively when verification fails. Interpreted mode *defers* the
  commitment until a residual provably generalizes, then hands the result to the same
  REUSE/ADAPT machinery.
- **vs. the sketch library.** INSTANTIATE substitutes literals into a learned template
  with no LLM. Promotion synthesizes a *new* residual from observed behavior and
  validates it on held-out examples. Both end in a normal cached commit.
- **vs. per-row semantic systems (DocETL / Palimpzest).** Those keep the model in the
  hot path by design and optimize the per-row plan. Interpreted mode keeps the model
  in the hot path *only until it can compile it away* — and for genuinely irreducible
  operators it behaves like them (interpret every row), without ever freezing a wrong
  residual.


## 7. What it does and does not handle

Handled, and verified end to end:

- single-value extraction / parsing / normalization → promotes to cached code;
- multi-output `#>` blocks (`{ts, level}`) → promotes to a dict-returning residual;
- classification with a constrained `Literal[...]` / `Enum` `expected_type` →
  promotes (free-form labels never stabilize, so an unconstrained classifier stays
  interpreted by design);
- summarize / open judgment → never promotes; interprets every row (real output);
- validation always in the sandboxed `GistExecutor`; promotion via a normal commit.

Current limits:

- An `Enum` `expected_type` contributes its members as the label set, but interpreted
  output is the label **string**, not the `Enum` member (round-trip parity is a TODO).
- Multi-output slots return one model-produced dict per call; there is no per-output
  sub-promotion.
- Interpreted-mode outputs do not carry reactivity `DataFlow` until the slot promotes
  (the promoted commit then participates in the normal flow/edge machinery).
- One model only — there is no cheap/expensive routing for the interpret phase.


## 8. Standalone operator

`semipy.interpreted.InterpretedOp` (and the `interpreted(...)` factory) exposes the
same interpret→promote loop as a self-contained callable over a single string input,
without the portal. It is handy for experiments and tests:

```python
from semipy import interpreted

op = interpreted("classify into a snake_case family", param="body",
                 labels=["scoreboard_child", "worker_env_error", "other"])
op("jk2_init() Found child 6725 in scoreboard slot 10")   # interprets, then promotes
print(op.stats.summary())                                  # state, llm_calls, residual_hits, ...
```

It uses the same `attempt_promotion` (sandboxed held-out validation); the only
difference from the wired path is that it serves the promoted residual in-process
rather than through the dispatch module.
