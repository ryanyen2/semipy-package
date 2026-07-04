# Stream D — Adaptive Triggering, Routing, Abstention & Calibration

Research digest for semipy (`semiformal-py`). Question served: **WHEN to spend the
flexible/expensive LLM path (GENERATE/ADAPT) vs reuse the cheap deterministic path
(REUSE/INSTANTIATE), and how trustworthy the gating signals are.**

Date: 2026-07-04. Sources: arXiv (IDs verified against the arXiv API; canonical IDs
confirmed by direct `id_list` lookup, two mis-guessed IDs caught and corrected).

**Research value: high** — the routing/cascade/calibration literature is directly on
point, and there is a strong, convergent finding that self-reported LLM confidence is
untrustworthy (especially for code), which flips the standard design in semipy's favor.

---

## 1. Verdict

semipy should **not** gate REUSE-vs-ADAPT-vs-GENERATE on any LLM self-estimate of
confidence. The single most robust empirical result across this literature is that
verbalized/self-reported confidence is systematically overconfident, often
*epistemically vacuous* (near-constant regardless of correctness), and specifically
unreliable for **code** — where a program can be confidently well-typed and wrong.
semipy is in the fortunate position that pure-text routers are not: it owns a **cheap,
trustworthy execution/contract oracle**. So the correct design is to **invert the usual
cascade**: gate primarily on a *cheapest-trustworthy-first* ladder of **deterministic,
execution-grounded** checks — (1) runtime input-fingerprint match, (2) contract-case /
invariant replay + data-agnostic guards via `verify_runtime_execution`, (3) reactive
upstream-commit staleness — and consult *sampled* model signals (reuse-judge / alignment
verifier voting, candidate-output divergence) only when the oracle is silent or
ambiguous. Any residual scalar that gates a transition must be **calibrated** (isotonic /
Platt on held-out contract cases) with the threshold chosen by cost-constrained
optimization or a conformal risk-coverage bound — never a raw or verbalized number. This
is FrugalGPT/cascade-deferral logic with the deferral rule anchored on an *execution
verifier* (LEVER/CodeT) instead of a confidence score, which the code literature says is
the more reliable signal.

---

## 2. Mechanism catalog

Tags: **TAKE** (adopt the idea), **ADAPT** (useful but must be reshaped for the
execution-oracle setting), **LEAVE** (mechanism doesn't transfer; note the lesson).

### LLM cascades & confidence deferral

- **FrugalGPT** — *ADAPT*. Cascade cheap→expensive models; a learned scorer accepts the
  cheap answer or escalates. The whole cost-saving structure ("only escalate hard
  queries") is exactly semipy's REUSE-else-LLM. *Oracle change:* semipy's cheap path is
  deterministic cached **code**, not a small model, so the accept/escalate decision can
  be grounded in *execution success*, not a learned text scorer. `arXiv:2305.05176`
  (May 2023).
- **When Does Confidence-Based Cascade Deferral Suffice?** — *TAKE (lesson)*. Proves the
  optimal deferral rule and shows a single max-softmax confidence threshold *fails*
  exactly under distribution shift, label noise, and downstream specialists; post-hoc
  deferral rules beat it there. *Oracle change:* spec drift = distribution shift, which
  is precisely when a confidence threshold would betray you — so gate on the oracle.
  `arXiv:2307.02764` (Jul 2023, rev. Jan 2024; NeurIPS 2023).
- **UCCI: Calibrated Uncertainty for Cost-Optimal LLM Cascade Routing** — *TAKE*. Maps
  token-margin uncertainty → per-query error probability via **isotonic regression**,
  then picks the escalation threshold by **constrained cost minimization**; cuts cost 31%
  at F1=0.91, drops ECE 0.12→0.03, beats a FrugalGPT-style learned threshold, entropy
  thresholding, and split-conformal routing. *Oracle change:* the calibration-first
  recipe is directly portable to any residual signal that gates ADAPT; but semipy's
  contract-pass is a *categorical* oracle strictly better than a calibrated scalar.
  `arXiv:2605.18796` (May 2026).

### Learned routers

- **RouteLLM** — *ADAPT*. Learn a weak/strong router from preference data (up-front, not
  post-hoc). *Oracle change:* semipy's "which path is better" label is *free* — it comes
  from contract-case outcomes and execution, supervision a text router must buy.
  `arXiv:2406.18665` (Jun 2024, rev. Feb 2025).
- **Hybrid LLM: Cost-Efficient & Quality-Aware Query Routing** — *ADAPT*. A BERT router
  predicts query difficulty and routes small-vs-large with a **tunable quality knob** at
  test time (up to 40% fewer large-model calls, no quality drop). *Oracle change:* the
  knob maps onto semipy's cost budget; the difficulty predictor is augmented by whether a
  cached impl exists and its contract history. `arXiv:2404.14618` (Apr 2024).
- **The Routing Plateau** — *TAKE (caveat)*. 21 routers × 5 benchmarks converge to a
  narrow accuracy band far below the oracle because they learn *global average*
  model-performance trends, not instance-specific signal, and collectively fail on hard
  queries. *Oracle change:* warns against bolting a learned router onto slot features —
  the per-slot execution oracle *is* the instance-specific signal routers lack.
  `arXiv:2606.07587` (May 2026). (Also MetaRouter `arXiv:2606.06178`, HyDRA
  `arXiv:2605.17106`, ReCal `arXiv:2606.12479` — preference/meta/RL routing variants.)

### Calibration & "do LLMs know when they're right?"

- **Language Models (Mostly) Know What They Know** — *ADAPT/caveat*. Base models are
  fairly calibrated on MC/TF via P(True)/P(IK) self-evaluation — but this is *base*
  models, and P(IK) calibration degrades on new tasks. Useful only as a tiebreaker.
  `arXiv:2207.05221` (Jul 2022). 
- **Just Ask for Calibration** — *ADAPT*. For RLHF models, *verbalized* confidence is
  better-calibrated than the model's conditional token probabilities (~50% ECE
  reduction). If you must elicit a scalar, ask verbally rather than read logits.
  `arXiv:2305.14975` (May 2023; EMNLP 2023).
- **Can LLMs Express Their Uncertainty?** — *TAKE (caveat)*. Systematic finding: LLMs are
  consistently **overconfident** when verbalizing; sampling+consistency aggregation beats
  a single verbalized number. `arXiv:2306.13063` (Jun 2023; ICLR 2024).
- **LLM Doesn't Know What It Doesn't Know** — *TAKE (sharpest caveat)*. On structured
  data, LLM verbalized confidence is *epistemically vacuous*: near-constant 0.856–0.937
  whether accuracy is 49% or 75%, tracking prompt format not correctness. Strongest recent
  evidence that self-confidence must not gate REUSE. `arXiv:2606.19509` (Jun 2026).
- **Calibration and Correctness of Language Models for Code** — *TAKE (the code caveat)*.
  Code LLMs are poorly calibrated/overconfident; intrinsic token probabilities and
  reflective (verbalized) confidence do not reliably track pass/fail; post-hoc rescaling
  (Platt/temperature) helps only partially. *This is the citation that self-confidence for
  CODE is untrustworthy — tests/execution are the reliable signal.* `arXiv:2402.02047`
  (Feb 2024).

### Uncertainty via sampling / semantic entropy

- **Semantic Uncertainty / Semantic Entropy** — *ADAPT*. Sample N generations, cluster by
  *meaning* (bidirectional entailment), entropy over meaning-clusters predicts errors far
  better than token entropy; the Nature 2024 follow-up detects "confabulations", and
  **Semantic Entropy Probes** (`arXiv:2406.15927`, Jun 2024) approximate it from a single
  generation's hidden states at ~zero cost. *Oracle change:* semipy's decisions subsystem
  already clusters candidate implementations **by observed output divergence**
  (return-value capture / EffectScript diff) — that is semantic entropy computed over
  *executed behavior*, strictly more reliable than over text. `arXiv:2302.09664` (Feb 2023;
  ICLR 2023 Spotlight).
- **Self-Consistency** — *ADAPT*. Sample multiple paths, majority-vote; agreement tracks
  correctness. RISC (`arXiv:2606.05054`, Jun 2026) shows plain majority vote drops
  correct-but-minority answers — learn to rank instead. *Oracle change:* map "agreement"
  to agreement across candidate programs' **execution outputs**, not text votes.
  `arXiv:2203.11171` (Mar 2022; ICLR 2023). (Adaptive semantic-entropy sampling budget:
  `arXiv:2603.22812`, Mar 2026.)

### Selective prediction / abstention with risk-coverage

- **ASPIRE (Adaptation with Self-Evaluation for Selective Prediction)** — *ADAPT*.
  Fine-tune a self-evaluation selection score; abstain below a threshold, giving a
  risk-coverage curve. *Oracle change:* abstain == "don't REUSE, escalate"; the
  risk-coverage framing lets you pick a threshold that *bounds the rate of wrongly-reused
  slots*. `arXiv:2310.11689` (Oct 2023; EMNLP 2023 Findings).
- **Adaptive Conformal Prediction for Factuality** — *ADAPT*. Prompt-adaptive conformal
  prediction gives distribution-free (marginal) coverage guarantees for selective
  filtering, with input-dependent thresholds. *Oracle change:* conformal calibration on
  held-out contract cases could give a **provable bound on the false-REUSE rate** — a
  principled threshold, not a magic number. `arXiv:2604.13991` (Apr 2026).

### Verifier-gated escalation (where the execution oracle lives)

- **LEVER** — *TAKE*. Train a verifier over (NL input, program, **execution result**);
  rerank by verifier×gen-prob and marginalize over programs with identical execution
  results. Execution features (data type, value range) indicate correctness better than
  heuristics. *This is the execution-oracle thesis;* semipy's `verify_runtime_execution` +
  contract cases are exactly this signal. `arXiv:2302.08468` (Feb 2023; ICML 2023).
- **CodeT** — *TAKE*. Model self-generates tests, executes candidates, and uses **dual
  execution agreement** (behavioral consensus + test pass) to select. *Oracle change:*
  analog for choosing among candidate implementations and for a self-generating
  contract-case oracle to gate promotion (cf. germ-seeded discriminating-input search).
  `arXiv:2207.10397` (Jul 2022).
- **Large Language Models Cannot Self-Correct Reasoning Yet** — *TAKE (caveat)*. Without
  *external* feedback, intrinsic self-correction doesn't help and can degrade output.
  *Oracle change:* the execution/contract check *is* that external feedback — it is what
  makes regeneration safe; don't let the model decide it's wrong on its own.
  `arXiv:2310.01798` (Oct 2023).
- **Let's Verify Step by Step** / **Generative Verifiers (GenRM)** — *ADAPT*. A dedicated
  verifier (process supervision; or a next-token-prediction verifier with CoT + vote) is
  more reliable than the generator's own confidence and improves Best-of-N. *Oracle
  change:* semipy's alignment verifier (already majority-voting) can adopt GenRM-style CoT
  verification for ambiguous forks, but keep execution as ground truth above any LLM
  judge. `arXiv:2305.20050` (May 2023) / `arXiv:2408.15240` (Aug 2024).
- **Trust but Verify: Prover-Verifier Deliberation** — *ADAPT (optional)*. Inference-time
  prover/verifier dialogue emits answer + Accept/Challenge/Reject verdict for selective
  prediction. A richer surfacer for `#?` forks, but heavier than the oracle.
  `arXiv:2605.25133` (May 2026).

### Adaptive computation & adaptive retrieval (the "when to spend" analogs)

- **CALM (Confident Adaptive Language Modeling)** — *LEAVE mechanism / TAKE framing*.
  Per-token early exit when a *calibrated* local confidence clears a threshold, with a
  global statistical consistency guarantee via Learn-Then-Test calibration. Token exit is
  irrelevant, but "calibrated threshold + statistical consistency guarantee" is the right
  *shape* for semipy's escalation threshold. `arXiv:2207.07061` (Jul 2022; NeurIPS 2022).
- **Adaptive-RAG** — *TAKE (analogy)*. A lightweight **query-complexity classifier** picks
  no-retrieval / single-step / multi-step — cheapest sufficient path per query. Closest
  analog to semipy's layered trigger; semipy's "classifier" is the deterministic
  fingerprint/contract cascade. `arXiv:2403.14403` (Mar 2024; NAACL 2024).
- **Self-RAG** — *ADAPT*. Model emits reflection tokens deciding *when to retrieve* and
  whether output is supported. *Oracle change:* good framing for "when to regenerate", but
  don't trust a learned reflection token — the trigger is the contract/fingerprint.
  `arXiv:2310.11511` (Oct 2023; ICLR 2024).

---

## 3. How this maps to semipy — a layered, cheapest-trustworthy-first trigger

**REUSE gate (evaluate top-down; first pass wins):**
1. **Runtime input-fingerprint match** → REUSE, skip verification. Free and exact; this is
   an HTTP `ETag`/`If-None-Match` 304 or a Bazel content-hash cache hit.
2. **Fingerprint miss** → run `verify_runtime_execution` + data-agnostic guards + **replay
   stored contract cases / invariants**. This is the LEVER/CodeT execution oracle — the
   trustworthy signal. Pass → REUSE.
3. **Reactive staleness** → if a consumed upstream slot's commit changed
   (`stale_against_inputs` vs `record_consumed`), force re-resolve regardless of
   fingerprint. Free, graph-based; build-system dependency invalidation.

**ADAPT-vs-GENERATE gate (only reached when the oracle fails or cannot be evaluated):**
4. Contract cases partially fail, or `spec_equivalence_key` changed (spec drift) → **ADAPT**
   (cheaper, preserves history). Bias ties toward ADAPT (already the design) — consistent
   with cascade-deferral's guidance to escalate conservatively.
5. No viable cached impl / ADAPT can't satisfy the oracle → **GENERATE**.

**Where model-based uncertainty is allowed in (last, and only as consistency):**
6. The evidence-grounded reuse judge + alignment verifier (majority voting) are the *only*
   place to consult model signal, and should be treated as **semantic-entropy-style
   consistency** (agreement across sampled candidates), *not* verbalized confidence. Use
   the decisions subsystem's **output-divergence clustering** as the semantic-entropy
   analog — reliable because it is grounded in execution.
7. If any residual scalar gates a transition, **calibrate it** (isotonic/Platt on held-out
   contract cases, UCCI-style) and set the threshold by **cost-constrained optimization**
   (FrugalGPT/UCCI) or a **conformal risk-coverage bound** (ASPIRE/adaptive conformal) that
   caps the false-REUSE rate. Never gate on a raw or verbalized number.

**Cost budget:** treat LLM spend as the constrained resource (UCCI/FrugalGPT). Escalate
only when the *expected reduction in false-REUSE risk* exceeds the LLM cost (Jitkrittum's
optimal-deferral framing); the multi-candidate decision draw's existing cost guard is the
natural hook.

**Reactive invalidation:** keep pull-based staleness as the free tier, but recognize its
blind spot (below).

---

## 4. Open risks

1. **Over-trusting self-reported confidence.** Verbalized confidence is overconfident and
   often near-constant (`2606.19509`, `2306.13063`) and specifically unreliable for code
   (`2402.02047`). *Mitigation:* gate on execution/contract; use model signal only as a
   calibrated consistency tiebreaker.
2. **Under-triggering → stale/wrong slot cached.** A fingerprint match skips verification;
   if the fingerprint is too *coarse* it masks a semantically changed input and reuses a
   wrong impl. Reactive staleness only fires on commit-id change, so an upstream whose
   *behavior* changed without a new commit is missed. *Mitigation:* periodic verification
   sampling + a conformal coverage bound on the false-REUSE rate.
3. **Over-triggering → burning LLM cost.** An over-sensitive guard or too-*fine* fingerprint
   forces needless ADAPT/GENERATE. The Routing Plateau (`2606.07587`) shows learned routers
   add little over cheap signals — don't add an expensive learned trigger that mostly
   re-derives the oracle. *Mitigation:* cost-constrained threshold (UCCI); tie-bias to
   ADAPT, not GENERATE.
4. **The verifier is itself an LLM.** The alignment verifier / reuse judge can be
   miscalibrated or gamed; "cannot self-correct" (`2310.01798`) warns intrinsic judgment is
   weak. *Mitigation:* keep the deterministic execution oracle as ground truth *above* any
   LLM judge; majority-vote the judge (GenRM/self-consistency) only for genuinely ambiguous
   forks.
5. **Oracle coverage gap.** An execution/contract oracle only catches divergence it actually
   exercises; contract cases may not cover the input region that diverges (germ-seeded
   discriminating-input search partially addresses this). *Mitigation:* CodeT-style
   self-generated discriminating inputs to widen oracle coverage *before* trusting REUSE.

---

## Sources

- FrugalGPT — `arXiv:2305.05176` (May 2023). LLM cascade, cost-quality tradeoff.
- When Does Confidence-Based Cascade Deferral Suffice? — `arXiv:2307.02764` (Jul 2023). When a confidence threshold fails; post-hoc deferral.
- UCCI: Calibrated Uncertainty for Cost-Optimal LLM Cascade Routing — `arXiv:2605.18796` (May 2026). Isotonic-calibrated, cost-optimal escalation threshold.
- RouteLLM — `arXiv:2406.18665` (Jun 2024). Learned weak/strong router from preference data.
- Hybrid LLM: Cost-Efficient & Quality-Aware Query Routing — `arXiv:2404.14618` (Apr 2024). BERT difficulty router with tunable quality knob.
- The Routing Plateau — `arXiv:2606.07587` (May 2026). Routers converge below oracle; miss instance-specific signal.
- Language Models (Mostly) Know What They Know — `arXiv:2207.05221` (Jul 2022). P(True)/P(IK) self-evaluation calibration.
- Just Ask for Calibration — `arXiv:2305.14975` (May 2023, EMNLP 2023). Verbalized > logit confidence for RLHF models.
- Can LLMs Express Their Uncertainty? — `arXiv:2306.13063` (Jun 2023, ICLR 2024). LLMs overconfident; consistency beats verbalized.
- LLM Doesn't Know What It Doesn't Know — `arXiv:2606.19509` (Jun 2026). Verbalized confidence epistemically vacuous / near-constant.
- Calibration and Correctness of Language Models for Code — `arXiv:2402.02047` (Feb 2024). Code LLMs overconfident; confidence weak signal for pass/fail.
- Semantic Uncertainty / Semantic Entropy — `arXiv:2302.09664` (Feb 2023, ICLR 2023 Spotlight); Semantic Entropy Probes `arXiv:2406.15927` (Jun 2024). Meaning-cluster entropy predicts errors.
- Self-Consistency — `arXiv:2203.11171` (Mar 2022, ICLR 2023); RISC `arXiv:2606.05054` (Jun 2026). Sample agreement as correctness signal.
- ASPIRE (Selective Prediction via Self-Evaluation) — `arXiv:2310.11689` (Oct 2023, EMNLP 2023 Findings). Abstain below a risk-coverage threshold.
- Adaptive Conformal Prediction for Factuality — `arXiv:2604.13991` (Apr 2026). Prompt-adaptive conformal coverage guarantee for selective filtering.
- LEVER — `arXiv:2302.08468` (Feb 2023, ICML 2023). Learned verifier over execution results; rerank + marginalize.
- CodeT — `arXiv:2207.10397` (Jul 2022). Self-generated tests + dual execution agreement.
- Large Language Models Cannot Self-Correct Reasoning Yet — `arXiv:2310.01798` (Oct 2023). Needs external feedback.
- Let's Verify Step by Step — `arXiv:2305.20050` (May 2023). Process > outcome supervision for verifiers.
- Generative Verifiers (GenRM) — `arXiv:2408.15240` (Aug 2024). Next-token-prediction verifier with CoT + vote.
- Trust but Verify: Prover-Verifier Deliberation — `arXiv:2605.25133` (May 2026). Inference-time prover/verifier selective prediction.
- Confident Adaptive Language Modeling (CALM) — `arXiv:2207.07061` (Jul 2022, NeurIPS 2022). Calibrated early-exit threshold with consistency guarantee.
- Adaptive-RAG — `arXiv:2403.14403` (Mar 2024, NAACL 2024). Query-complexity classifier picks cheapest sufficient path.
- Self-RAG — `arXiv:2310.11511` (Oct 2023, ICLR 2024). Reflection tokens decide when to retrieve.
- (Cross-domain analogs, not LLM papers: CPU speculative execution + rollback; HTTP ETag/`stale-while-revalidate`; Bazel/Make content-hash build caches; GPU selective surrogate `arXiv:2605.31464`, LM forecasts kernel runtime but defers to real GPU measurement when unsure.)
