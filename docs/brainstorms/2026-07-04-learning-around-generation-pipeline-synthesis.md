---
date: 2026-07-04
topic: learning-around-the-generation-pipeline
status: research synthesis (reflection, pre-requirements)
inputs:
  - docs/brainstorms/research/2026-07-04-stream-a-reflective-prompt-optimization.md
  - docs/brainstorms/research/2026-07-04-stream-b-continual-memory-curation.md
  - docs/brainstorms/research/2026-07-04-stream-c-code-execution-selfimprove.md
  - docs/brainstorms/research/2026-07-04-stream-d-adaptive-triggering-routing.md
related:
  - docs/brainstorms/2026-07-03-programmable-neural-programs-requirements.md
  - docs/brainstorms/2026-07-03-programmable-neural-programs-design-inspiration.md
---

# Learning Around the Generation Pipeline

A reflection on how semipy should get *better over time* — robust yet flexible, triggering the
flexible path only when warranted, and learning from its own priors — synthesized from four parallel
literature streams (reflective prompt optimization, continual/experiential memory, code
self-improvement, adaptive triggering) mapped onto semipy's actual hook points.

---

## 0. Where this sits

There are **two orthogonal axes** for improving semipy, and they compose rather than compete.

- **Axis 1 — runtime program structure** (the 2026-07-03 *Programmable Neural Programs* brainstorm):
  make the neural part a typed, composable hole executed by a neurosymbolic interpreter —
  shape-invariance, a solidification lattice, per-leaf blame. This changes *what a semiformal program
  is at runtime*.
- **Axis 2 — the learning loop around generation** (this doc): keep the current architecture and make
  the *pipeline that produces and maintains slots* improve from its own accumulated experience. This
  changes *how the generator, judge, verifier, and library get better and when they fire*.

Your question lives on Axis 2. The good news from the research: Axis 2 is largely **additive** to what
semipy already has, needs **no model weights**, and semipy is structurally *better positioned* for it
than the systems these methods were designed for. The two axes even reinforce each other — the
solidification lattice (Axis 1) is a special case of the curation loop (Axis 2, §3.B), and blame
localization (Axis 1) is the credit-assignment signal a prompt optimizer (Axis 2, §3.A) needs.

---

## 1. The one insight that reframes everything

> **semipy owns a cheap, deterministic, executable oracle — run the code, replay the contract cases,
> check the types — and that oracle is the pivot that changes every method we surveyed.**

Every stream, independently, ran into the same wall in the `model(x) → y` literature and the same
escape in semipy's `model(x) → code → execution → typed value` setting:

- The recurring wall in text-land is **untrustworthy self-judgment.** Prompt optimizers grade against a
  fuzzy text metric; memory systems decide what to keep via an LLM's "poignancy" score; self-repair
  asks the model to critique itself; routers gate on verbalized confidence. Stream D's sharpest finding
  is that verbalized LLM confidence is systematically overconfident, often *epistemically vacuous*
  (near-constant regardless of correctness: 0.856–0.937 whether accuracy is 49% or 75%,
  arXiv:2606.19509), and specifically untracking of pass/fail for **code** (arXiv:2402.02047). Stream C
  and D both cite *LLMs Cannot Self-Correct Reasoning Yet* (arXiv:2310.01798): without an **external**
  signal, self-judgment degrades output.

- semipy's escape is that it replaces self-judgment with **execution.** The reward for a prompt
  optimizer is a contract/execution **pass-rate** (A). The gate on a memory merge is **re-running the
  cases** (B). The candidate selector ranks by **executed-output divergence and dual-agreement** (C).
  The regeneration trigger is a **deterministic contract replay** (D). The oracle is the external
  feedback all four streams say is the thing that actually works.

**The corollary is the single most important design consequence in this whole document:** the oracle
is only as good as what the contract cases exercise. Every downstream benefit — an honest optimizer
reward, a safe merge, a correct candidate pick, a calibrated trigger — is **bottlenecked by contract-case
coverage.** So the highest-leverage investment is not any one borrowed method; it is **coverage of
discriminating inputs**, which semipy already has a seed for (the decisions subsystem's germ-seeded
discriminating-input search). Fund that first; it is the substrate the rest stands on.

---

## 2. Your four questions, answered

**Q1 — "When do we trigger the flexible (LLM) path vs reuse?"** (Stream D)
Invert the usual cascade. Text routers escalate on model confidence because they have nothing better;
semipy should escalate on a **cheapest-trustworthy-first ladder**: (1) runtime-input fingerprint match →
REUSE with no check; (2) contract-case + invariant replay and the data-agnostic guards → REUSE or force
ADAPT; (3) reactive upstream-commit staleness → invalidate; and only when the oracle is *silent* on a
case (4) consult sampled model signals — reuse-judge / alignment-verifier voting and candidate-output
divergence. Never let an LLM self-estimate gate the transition when a cheaper deterministic check
exists. A single confidence threshold provably fails exactly under spec/distribution drift
(arXiv:2307.02764) — which is precisely when reuse is unsafe, so a threshold is worst where it matters
most. Any residual scalar that must gate a transition is **calibrated** (isotonic/Platt on held-out
contract cases) with the threshold chosen by cost minimization or a conformal risk-coverage bound
(UCCI, arXiv:2605.18796).

**Q2 — "How does it learn from priors — which matter, which combine?"** (Stream B)
Reframe "continuous learning" as a **curation problem over the code-prior store you already have**
(portal DAG + contracts + sketches), not as adding a text memory of trajectories. The decisive
asymmetry: because the priors are executable, **every curation action can be verified by re-running
cases** instead of trusting an LLM. Concretely: score importance by **objective signals** (reuse count,
contract pass-rate, recency decay, downstream fan-out — a code-ified Generative-Agents
importance×recency×relevance, arXiv:2304.03442, not LLM poignancy); **abstract a sketch when its shape
recurs across N commits** (frequency-driven, AWM arXiv:2409.07429) rather than on today's single-shot
0.6-confidence gate; **merge two priors only behind a discriminating-input gate** (see the false-merge
risk, §5); **forget by demote-not-delete** (quarantine, keep the deopt path). "Combining priors" for
executable code means composition (a sketch calls an earlier sketch, Voyager arXiv:2305.16291) and
verified parametric merge — *not* weight arithmetic (the neurosymbolic brainstorm's TIES-Merging
lesson).

**Q3 — "GEPA-style, optimize the prompt not the weights?"** (Stream A)
Yes — and semipy is *better* positioned than GEPA's usual target, not worse, because the reward is a
discrete verifiable pass-rate and semipy already stores the two things these optimizers bootstrap from
scratch: a rollout log (portal commit DAG) and a graded example set (contract cases + regen
provenance). GEPA (arXiv:2507.19457) is the closest structural match — reflective mutation from
natural-language traces plus **Pareto-frontier** candidate selection, sample-efficient, beats RL/GRPO
and MIPROv2, already demonstrated on code. Trace/OptoPrime (arXiv:2406.16218) is *near-isomorphic to a
semipy slot*: its parameter space natively includes generated code and its feedback is an execution
trace. DSPy/MIPROv2 (arXiv:2406.11695) is the scaffolding for optimizing a multi-module pipeline. Do
NOT weight-train — the RL-from-tests family (CodeRL/RLTF/AceCoder/SWE-RL) is a LEAVE; only its reward-
design lesson transfers (fine-grained, location-aware execution signals beat scalar pass/fail).

**Q4 — "`model(x)→y` methods vs our `model(x)→code→execution`?"** (all streams, esp. C)
The code-execution layer is not a complication; it is the **advantage** (§1). Two adaptations recur:
(a) prefer **selection over repair** — execution-based selection among sampled candidates (CodeT
arXiv:2207.10397, MBR-exec arXiv:2204.11454, LEVER arXiv:2302.08468) reliably beats iterative
re-prompting, which is capped by the model's ability to critique itself; (b) localized **program
repair** wins over full regeneration only under a narrow condition — a *grounded, discriminating* signal
(a real traceback, LDB-style runtime-state divergence arXiv:2402.16906, or one failing typed/contract
assertion) **and** a small fix. semipy's decisions subsystem *already draws candidates and clusters them
by executed output divergence* — it is missing only the **scoring rule to pick the head commit**, which
is the cheapest high-value change on this whole list.

---

## 3. Cross-stream convergences (the load-bearing part)

Ideas that ≥2 independent streams produced are the ones to trust. Five converged:

**C1 — Execution oracle > LLM self-confidence.** (A, B, C, D) Every stream ends up anchoring its signal
on running the code, not on the model's opinion of the code. This is the §1 insight; it is unanimous.

**C2 — Selection / Pareto over a candidate set beats iterative self-repair.** (A's GEPA Pareto frontier,
C's CodeT/MBR-exec/LEVER selection, D's candidate-output-divergence semantic entropy) The winning shape
is "sample a diverse set, then let execution choose," not "generate once, then loop on self-critique."
semipy's decisions subsystem is exactly a candidate-set generator; wire selection into it.

**C3 — Incremental delta curation, not wholesale rewrite.** (B's ACE, arXiv:2510.04618: monolithic
prompt/context rewriting causes "context collapse" and brevity bias; fix with delta updates +
grow-and-refine dedup. A's ProTeGi textual-gradient-from-misclassified-examples is the same move at the
prompt level.) When semipy updates a prompt, a sketch, or a contract, it should **amend with a localized
delta**, not regenerate the whole artifact. This maps onto amend-contract-vs-regenerate-slot.

**C4 — Coverage of discriminating inputs is the shared bottleneck and the shared fix.** (B's false-merge
risk, C's overfit-to-cases risk, A's reward-hacking risk, D's deferral-fails-under-drift) All four
failure modes are the same failure: the oracle only checks what the cases cover, so a thin case set lets
a bad merge / a trivially-passing program / a churny prompt / a mis-triggered reuse slip through
"verified." The shared fix is the decisions subsystem's **germ-seeded discriminating-input search** —
run it *before* accepting a merge, *as* the held-out set for the optimizer, and *to* expand the
calibration set for the trigger.

**C5 — Optimize / curate for held-out generalization + REUSE stability, not case satisfaction.** (A's
reward-hacking risk, D's over-triggering-churn risk) The objective must reward doing well on *held-out*
slots and must treat **REUSE stability as a first-class term**, or the system silently buys accuracy by
regenerating constantly (burning cost, C5≡D) or by emitting programs that overfit the exact stored
cases (A, C).

---

## 4. A staged plan, cheapest-and-safest first

Ordered by (value / risk), each anchored to a concrete hook point from the codebase map. Nothing here
requires touching model weights or the Axis-1 interpreter.

| # | Move | Why now | Hook point |
|---|------|---------|------------|
| **P0** | **Persist per-case pass/fail** on every contract-case replay | *Prerequisite for all of A/B/C/D.* Today `ContractCase` stores no outcome — it is computed live in `ChangeRecord` and discarded. Without a persisted outcome there is no rollout dataset (A), no importance signal (B), no verifier trainset (C), no calibration set (D). | `contract/models.py:84 ContractCase` (+ outcome field); write at `contract/runner.py run_contract` and `slot_resolver.py:730 _run_reuse_contract_gate` |
| **P1** | **Execution-based candidate selection** — add a scoring rule (CodeT/LEVER-style: type validity + contract-pass fraction + output-cluster agreement) to pick the head commit | Highest value / lowest risk. The candidates already exist and are already clustered by output divergence; only the pick is missing. Pure function, no new LLM calls. | decisions draw + `slot_resolver.py:1610`; select before committing head; ranks over `(spec, program, typed result, contract-pass fraction)` |
| **P2** | **Recurrence-gated abstraction + verified merge** for the sketch library | Replaces the single-shot 0.6-confidence gate (weak signal) with AWM frequency + ACE grow-and-refine dedup, each merge gated by discriminating-input replay (C4). | `slot_resolver.py:589 _run_sketch_binding_extraction`; `sketch.py:330 merge_sketch_into_library`; add N-recurrence + germ-seeded gate |
| **P3** | **Calibrated, cheapest-first trigger ladder** for REUSE/ADAPT/GENERATE | Makes "when to be flexible" principled instead of a fixed cascade; isotonic-calibrate the one residual scalar on P0's data; add a REUSE-stability/cost term (C5). | `routing.py:124 RoutingPolicy.decide` (insertion at `:275`); calibrate the `aggregate_semantic_votes` tie rule at `decision.py:424` |
| **P4** | **ProTeGi/GEPA on the *non-code* prompts first** — verifier (`_ALIGNMENT_SYSTEM`) and reuse judge (`_DECISION_SYSTEM`) | They emit *gradeable decisions*, not code, so there is **no reward-hacking-through-code surface** and labels already exist (P0 + regen provenance). Improving the judge directly improves P1/P3. | `roles/verifier.py:44`, `agents/decision.py` `_DECISION_SYSTEM`; prompts are module-level string literals — swap-in-place |
| **P5** | **GEPA / Trace on the coder prompt** (`generator.py:284 SYSTEM_PROMPT`) with a held-out reward | Highest value, highest risk — this is where reward-hacking bites (C5). Do it last, only after P0 gives a real dataset and P4 proves the loop out on a safe surface. Reward = held-out contract pass-rate − regeneration-churn penalty. | `generator.py:284`; needs a new persisted **prompt-version / optimizer-state store** (portal JSON or a sibling of `library/sketch_store.py` — none exists today) |

Two structural gaps the map surfaced that P0–P5 depend on: (i) **no persisted per-case outcome**
(fixed by P0), and (ii) **no persistence slot for prompt-version / optimizer state** (needed by P5).
Also note the `roles/verifier.py` voting layer is **built but unwired** into `slot_resolver` — P1/P3/P4
are a natural occasion to wire it.

---

## 5. Load-bearing risks and the shared mitigation

- **Reward hacking through the code layer** (A). A prompt tuned only to raise contract pass-rate learns
  to emit trivially-passing programs (constant returns, overfit-to-case branches) on a small,
  usage-biased case set. → Reward **held-out** generalization (C5); expand the held-out set with
  germ-seeded discriminating inputs (C4).
- **False abstraction / merge** (B, sharpest). Two slots share a spec shape but diverge on an edge case
  no stored case exercises; a "verified" merge that only replays existing cases silently unifies them.
  → **Never merge on shape congruence alone**; seed discriminating inputs first (C4); two-stage
  retrieval (embedding recall → executable-type + `spec_equivalence_key` + contract-replay gate).
- **Self-repair thrash** (C). Open-ended repair loops are not cost-effective (arXiv:2306.09896, ICLR
  2024) and can degrade output (arXiv:2310.01798). → Prefer selection (P1); allow repair only with a
  grounded discriminating signal + a small-fix bound; cap iterations.
- **Over-trusting confidence** (D). → Gate on the oracle; calibrate any residual scalar; treat an LLM
  self-estimate as the *last* resort, used only where the oracle is silent.
- **Oracle coverage gap** (C4, the meta-risk). Everything above is only as safe as case coverage. →
  Coverage is the funded substrate, not an afterthought.

The mitigations are not five separate patches — they are one discipline: **make the executable oracle
the arbiter, and make the oracle's coverage the thing you invest in.**

---

## 6. What NOT to do (LEAVEs)

- **Weight training** (RL-from-tests: CodeRL/RLTF/AceCoder/SWE-RL). semipy never trains weights; keep it
  that way. Take only the reward-design lesson.
- **Gate on verbalized LLM confidence** — uncalibrated, vacuous for code (D).
- **Monolithic prompt/context rewrites** — cause context collapse; use delta updates (B/ACE).
- **Open-ended self-repair loops** as a default improvement mechanism (C).
- **EvoPrompt / OPRO / PromptBreeder / APE** as the optimizer — subsumed by GEPA's reflection + Pareto
  (A). Reflexion as a memory — subsumed by the contract subsystem (B).
- **Merging priors by weight arithmetic / co-training composable holes** — carried over from the Axis-1
  brainstorm's risk section; sequence and compose, don't merge weights.

---

## 7. Open questions for you

1. **Scope of the learning loop:** offline (a `python -m semipy optimize` pass over a portal, GEPA-style
   batch) vs online (curation nudges on every generation)? P0–P3 fit online; P4–P5 are naturally offline
   batch jobs.
2. **Where does optimizer/prompt-version state live** — extend the portal JSON, or a new sibling store
   next to `library/sketch_store.py`? (P5 blocker.)
3. **Is prompt optimization even in scope for a research contribution**, or is it "engineering that makes
   the Axis-1 story land"? GEPA-on-a-code-generating-self-curating-pipeline with an execution oracle is
   arguably novel enough to be its own leg — but it competes with the neurosymbolic brainstorm for focus.
4. **Coverage budget:** how aggressively to run the germ-seeded discriminating-input search (C4) — it is
   the substrate but it costs LLM calls; where is the cost ceiling?

---

## 8. Pointers

- Stream digests (full mechanism catalogs, verified arXiv IDs, per-method TAKE/ADAPT/LEAVE):
  `docs/brainstorms/research/2026-07-04-stream-{a,b,c,d}-*.md`.
- Companion axis: `docs/brainstorms/2026-07-03-programmable-neural-programs-{requirements,design-inspiration}.md`.
- Anchor citations by stream: GEPA arXiv:2507.19457; Trace/OptoPrime arXiv:2406.16218; DSPy/MIPROv2
  arXiv:2406.11695; ProTeGi arXiv:2305.03495 (A). ACE arXiv:2510.04618; AWM arXiv:2409.07429; Generative
  Agents arXiv:2304.03442; Voyager arXiv:2305.16291 (B). CodeT arXiv:2207.10397; MBR-exec
  arXiv:2204.11454; LEVER arXiv:2302.08468; LDB arXiv:2402.16906; *Is Self-Repair a Silver Bullet?*
  arXiv:2306.09896 (C). UCCI arXiv:2605.18796; deferral arXiv:2307.02764; Adaptive-RAG arXiv:2403.14403;
  code-confidence arXiv:2402.02047; *LLMs Cannot Self-Correct Reasoning Yet* arXiv:2310.01798 (C/D).
- Recent IDs (2024-2026) were verified against the arXiv API by the research agents; pre-2024 classics
  (Voyager, Reflexion, Generative Agents, MemGPT) are established knowledge, not re-verified.
</content>
