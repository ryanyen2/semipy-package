# Stream A — Reflective Prompt & Pipeline Optimization (the GEPA lineage)

External grounding digest for semipy. Question: can GEPA-style prompt-space
optimization (no weight updates) evolve semipy's static per-role prompts, using
the portal DAG + contract cases as the feedback dataset?

Research value: **high** — a mature, converging lineage exists (APE -> OPRO/EvoPrompt
-> ProTeGi/MIPRO -> GEPA/Trace), several members explicitly optimize *multi-module
pipelines*, several use *execution/trace* feedback, and the newest (GEPA, Trace) are
already applied to code. All arXiv IDs below were verified against the arXiv API.

---

## 1. Verdict

Yes — reflective prompt/pipeline-space optimization is the right frame, and semipy is
*better* positioned than the typical target, not worse. The canonical target is
`model(x) -> text`, optimized against a fuzzy text metric. semipy is
`model(x) -> code -> execution -> typed value`, so the reward can be an
execution/test/contract **pass-rate** — a discrete, verifiable, far-less-hackable
signal than BLEU/EM. semipy also already owns the two things these methods have to
bootstrap from scratch: a **rollout log** (portal commit DAG per slot) and a
**graded example set** (content-addressed contract cases + invariants + regen
provenance). The lineage member that fits closest is GEPA (reflective mutation from
NL traces + Pareto selection, sample-efficient, already run on code), with
DSPy/MIPROv2 as the pipeline-optimization scaffolding and Trace/OptoPrime as the one
framework that natively optimizes *heterogeneous* parameters (prompts **and** code)
over execution traces. The twist to respect throughout: the thing being optimized is
a *code-generating* pipeline, so reward flows through a compiler/executor, which
makes the signal cleaner but introduces reward-hacking and per-role credit-assignment
problems that pure-text optimizers never face.

---

## 2. Mechanism catalog

### GEPA — Genetic-Pareto reflective prompt evolution  **[TAKE]**
Samples system trajectories, **reflects in natural language** on the trace to diagnose
what went wrong, proposes/tests prompt edits, and — crucially — keeps a **Pareto
frontier** of candidates across tasks rather than a single best, then combines
complementary lessons. Reports beating GRPO (RL) by ~6% avg with far fewer rollouts and
MIPROv2 by >10%. Explicitly demonstrated as an inference-time search for **code
optimization** (classified cs.SE; DSPy-integrated).
*Code layer:* the reflection reads real execution/contract traces and the fitness is a
pass-rate, so the "richer-than-scalar" signal GEPA wants is exactly what semipy's
contract already emits. arXiv **2507.19457** (v1 2025-07-25, v2 2026-02-14).

### DSPy + MIPROv2 / BootstrapFewShot  **[TAKE / ADAPT]**
DSPy treats a multi-module LM program as the optimization target; MIPROv2 jointly
optimizes **instructions and few-shot demonstrations** for each module via Bayesian
search over a minibatch metric; BootstrapFewShot self-generates demos by running the
pipeline and keeping traces that pass the metric. This is the direct precedent for
"optimize a compound pipeline, not one prompt."
*Code layer:* the DSPy "metric" becomes semipy's contract/execution pass-rate; the
"trainset" becomes accumulated contract cases; BootstrapFewShot's "keep passing traces"
is literally what the portal DAG already stores. MIPRO: arXiv **2406.11695** (2024-06-17,
EMNLP 2024); DSPy: arXiv **2310.03714** (Khattab et al., ICLR 2024).

### Trace / OptoPrime — optimizing computational workflows  **[TAKE / ADAPT]**
Frames workflow optimization as **OPTO** (Optimization with Trace Oracle): the optimizer
receives the workflow's **execution trace** plus feedback (console output, errors),
treating the trace like a back-propagated gradient, and an LLM updates **heterogeneous
parameters — prompts *and* code — that may be non-differentiable**. Demonstrated on
prompt optimization, hyperparameter tuning, and **code debugging**.
*Code layer:* this is the only listed method whose parameter space already *includes*
generated code and whose feedback is already an execution trace — a near-isomorphic match
to semipy's slot (spec+code+trace). arXiv **2406.16218** (2024-06-23, NeurIPS 2024;
microsoft.github.io/Trace).

### TextGrad — textual gradients  **[ADAPT]**
Backpropagates natural-language "gradients" (LLM critiques) through a computation graph of
LM calls to update each node's text. Powerful for chained modules; needs a well-formed
graph and per-node feedback flow.
*Code layer:* semipy's roles form exactly such a graph, and execution/contract failures are
the loss; but TextGrad's per-node critique is heavier than GEPA's whole-trace reflection and
duplicates what Trace does more directly. arXiv **2406.07496** (2024-06-11); journal version
published in *Nature* (2025) as "Optimizing generative AI by backpropagating language model
feedback" (confirm exact citation before use).

### ProTeGi — APO with "gradient descent" + beam search  **[ADAPT]**
Builds textual "gradients" from **misclassified examples**, edits the prompt in the opposite
direction, and beam-searches candidates. Error-example-driven and cheap.
*Code layer:* semipy's **regression contract cases** are ready-made "misclassified examples"
— the failing (spec, input, expected) triples that should drive the next prompt edit.
arXiv **2305.03495** (2023-05-04, EMNLP 2023).

### SAMMO — symbolic prompt *program* search  **[ADAPT]**
Represents a prompt as a **structured program** (a tree of operators/sections) and does
**compile-time** structure-aware search with mutation operators over that program.
*Code layer:* semipy's role prompts are structured (system role + task + schema + few-shot),
and semipy's "generate-once-then-cache" is itself a compile-time model — SAMMO's compile-time
framing and structural mutation operators map cleanly. arXiv **2404.02319** (2024-04-02, title
"Symbolic Prompt Program Search…", introduces SAMMO; EMNLP 2024; github.com/microsoft/sammo).

### OPRO — Optimization by PROmpting  **[LEAVE / borrow idea]**
LLM proposes new prompts conditioned on a **trajectory of (prompt, score) pairs**, hill-climbing
on the meta-prompt. Foundational but scalar-score only, no reflection, no Pareto.
*Code layer:* subsumed by GEPA, which adds NL reflection over traces and Pareto selection.
arXiv **2309.03409** (2023-09-07, ICLR 2024).

### EvoPrompt — LLM + evolutionary algorithms  **[LEAVE]**
Population-based GA/DE where the LLM performs crossover/mutation on prompts, selected by a
scalar dev-set metric. No trace reflection; GEPA is the reflective, Pareto-aware successor of
this exact idea. arXiv **2309.08532** (2023-09-15, ICLR 2024).

### PromptBreeder — self-referential evolution  **[LEAVE]**
Evolves both task-prompts and the **mutation-prompts** that evolve them. Elegant but
sample-hungry and ungrounded in execution; the self-referential trick is interesting future work,
not a first move. arXiv **2309.16797** (2023-09-28).

### APE — Automatic Prompt Engineer  **[LEAVE]**
Generate-then-select over instruction candidates for a **single** prompt. Historically important
origin point; single-module and superseded by everything above. arXiv **2211.01910** (2022-11-03,
ICLR 2023).

### 2025-2026 successors / surveys (orientation)
- **Systematic survey of APO techniques** — 5-part unifying framework; best map of the space.
  arXiv **2502.16923** (2025-02-24, EMNLP 2025).
- **Survey of automatic prompt engineering (optimization lens)** — explicitly flags
  *agent-oriented / pipeline prompt design* as the open frontier. arXiv **2502.11560** (2025-02-17).
- **APO with instruction-focused heuristic search (survey)** — arXiv **2502.18746** (ACL 2025).
- **AutoPDL: Automatic Prompt Optimization for LLM Agents** — APO targeted at *agent* pipelines.
  arXiv **2504.04365** (2025-04-06, IBM).
- **Generalizable Self-Evolving Memory for APO** — an accumulating memory of past optimization
  episodes as the feedback substrate; directly analogous to using the contract store as a growing
  training signal. arXiv **2603.21520** (2026-03-23).
- **Submodular Evaluation Subset Selection in APO** — how to pick *which* eval examples to score
  each round (minibatch selection); relevant to choosing which contract cases to run per rollout to
  control cost. arXiv **2601.03493** (2026-01-07).
- **GEPA-trained programmatic prompting framework** (applied) — GEPA-via-DSPy building an
  inspectable, code-based multi-domain pipeline; concrete evidence GEPA works on
  code-generating pipelines. arXiv **2512.01452** (2025-12-01).
- **Program-as-Weights** (adjacent) — compiles NL specs into reusable neural artifacts, reframing the
  model "from a per-input solver into a tool builder"; structural cousin of semipy's NL->code->cache.
  arXiv **2607.02512** (2026-07-02).

---

## 3. How this maps to semipy

**Rollout / feedback dataset (already accrued).** Each slot's commit DAG plus its contract is a
GEPA/Trace-shaped rollout log: `(spec_text + free vars, generated code, execution/typed-value
outcome, contract pass/fail, change-provenance "why we regenerated")`. BootstrapFewShot's move
("run the pipeline, keep the traces that pass the metric") is what the portal already does — every
accepted commit is a passing trace; every regen with a recorded regression is a labeled negative.

**What gets optimized.** The static per-role prompts: explorer, version-checker, coder, executor,
verifier (alignment judge), reuse judge, surfacer. Treat the *set* of role prompts as one candidate
(DSPy/Trace view of a compound program), mutate one role at a time via GEPA-style reflection over the
failing trace, and keep a **Pareto frontier across slots** — different slots stress different roles, so
a single scalar would collapse them; Pareto selection is what stops the coder prompt from being tuned
for one slot shape at the expense of others.

**Objective function.** Maximize a vector of: (a) contract/invariant pass-rate, (b) first-try
execution + type-validation success (fewer coder retries), (c) REUSE stability (fewer downstream
regens / less churn), and (d) correct reuse-judge and verifier decisions vs. the recorded ground truth;
minus (e) cost (LLM tokens x rollouts, sandbox executions). Credit assignment uses the trace: attribute
a failure to the role whose artifact first diverged (explorer picked wrong prior vs. coder mis-synthesized
vs. verifier mis-graded), à la Trace/GEPA, and only mutate that role's prompt.

**Cleanest lever first.** ProTeGi/GEPA on the **verifier and reuse-judge** prompts, scored against
recorded human `pick-decision`/`assert-decision` resolutions and contract outcomes, is the lowest-risk
starting point — those roles output a decision (gradeable directly), not code, so there is no
reward-hacking-through-code surface, and the labels already exist in the portal.

---

## 4. Open risks (specific to optimizing a code-generating pipeline)

1. **Reward hacking through the code layer.** A prompt tuned only to raise contract pass-rate can learn
   to emit trivially-passing programs (constant returns, identity passthrough, overfit-to-cases branches).
   semipy's data-agnostic guards (empty-string / `return s`) blunt the obvious ones, but the optimizer will
   probe for weak contracts — the objective must reward *generalization*, not case satisfaction.
2. **Overfitting prompts to a small, biased contract set.** Contract cases accrue from *actual usage*, so
   they over-represent seen slot shapes and data; a prompt improved on them may regress on novel slots. Needs
   held-out slots (semipy's interpreted-mode held-out validation is a precedent) and Pareto spread across slot
   families, not a single averaged metric.
3. **Interaction with caching / instruction drift.** REUSE skips the LLM, so evolving prompts only touches
   future GENERATE/ADAPT — but a "better" prompt can destabilize slots that were fine, trading measured
   accuracy for regeneration churn. REUSE stability has to be a first-class objective term, or the optimizer
   silently buys quality with churn.
4. **Cross-role credit assignment is genuinely hard.** A failed typed value could originate in explorer,
   coder, or verifier; naive per-role mutation optimizes the wrong role and can oscillate. Requires
   trace-grounded attribution (Trace/OPTO) before mutation, and a stationary enough trace format to attribute against.
5. **Cost and non-stationarity.** Each rollout is a full multi-role pipeline plus sandboxed execution;
   LLM sampling makes the objective noisy (semipy already votes: `verifier_vote_samples`, `reuse_vote_samples`),
   so honest evaluation needs seeded/multi-sample scoring — multiplying cost. And the contract itself evolves
   (maintainer + invariant seeding), so the objective is a moving target; freeze the eval contract per
   optimization run.

---

## Sources (verified via arXiv API, 2026-07-04)
- GEPA — arXiv 2507.19457 (v2 2026-02): reflective Pareto prompt evolution; the closest fit, run on code.
- MIPROv2 / DSPy optimizers — arXiv 2406.11695 (EMNLP 2024): joint instruction+demo optimization for multi-module LM programs.
- DSPy — arXiv 2310.03714 (ICLR 2024): declarative self-improving LM pipelines (BootstrapFewShot).
- Trace / OptoPrime — arXiv 2406.16218 (NeurIPS 2024): OPTO; optimizes prompts AND code over execution traces.
- TextGrad — arXiv 2406.07496 (2024); Nature 2025 journal version: textual-gradient backprop through LM graphs.
- ProTeGi — arXiv 2305.03495 (EMNLP 2023): textual gradients from error examples + beam search.
- SAMMO — arXiv 2404.02319 (EMNLP 2024): structure-aware compile-time prompt-program search.
- OPRO — arXiv 2309.03409 (ICLR 2024): LLM-as-optimizer over (prompt, score) trajectory.
- EvoPrompt — arXiv 2309.08532 (ICLR 2024): LLM-driven evolutionary prompt search.
- PromptBreeder — arXiv 2309.16797 (2023): self-referential prompt evolution.
- APE — arXiv 2211.01910 (ICLR 2023): generate-and-select instruction search (origin point).
- Surveys: 2502.16923 (EMNLP 2025), 2502.11560, 2502.18746 (ACL 2025).
- Successors: AutoPDL 2504.04365; Self-Evolving Memory for APO 2603.21520; Submodular eval-subset selection 2601.03493; GEPA-applied code pipeline 2512.01452; Program-as-Weights 2607.02512.
