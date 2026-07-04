# Stream C — Code-Generation Self-Improvement Driven by Execution Feedback

Research date: 2026-07-04. Focus axis: *when is adjusting the PROGRAM (localized repair,
candidate selection, AST-level edit) better than adjusting the PROMPT / re-generating?*
All arXiv IDs, dates, and venues below were verified against arXiv abstract pages during this
research. IDs I could not verify are explicitly flagged and not cited.

---

## 1. Verdict — adjust the program vs. adjust the prompt

Execution converts the vague problem "make the answer better" into a *checkable* one, and the
evidence says the highest-ROI lever in the code setting is **execution-based SELECTION among
sampled candidates, not re-prompting**. Candidate selection (CodeT, MBR-exec, LEVER, μCode)
reliably beats a single generation because it sidesteps the one bottleneck that limits every
repair method: the model's ability to critique its own output. Localized **repair** does beat
regeneration, but only under a narrow condition — when you have a *grounded, discriminating*
signal (a real traceback, a runtime-state divergence, or a single failing typed/contract
assertion) **and** the fix is small. When the signal is weak (self-generated critique, few or
auto-generated tests), repair is frequently *not cost-effective* and can even *degrade* output
(Olausson et al. 2024; Huang et al. 2024). So the user's intuition is correct with a sharp
caveat: "adjust the program" wins primarily through candidate **selection** and grounded
**minimal** repair — not through open-ended repair loops or naive re-prompting, both of which
the skeptical literature shows can waste budget or regress.

---

## 2. Mechanism catalog

Tag legend: **TAKE** (directly adoptable), **ADAPT** (mechanism is right, needs reshaping for a
cached/versioned slot), **LEAVE** (informative but not applicable — usually because it requires
model training, which semipy does not do).

### Execution-based candidate selection (the strongest, training-free family)

**CodeT — Code Generation with Generated Tests** — `arXiv:2207.10397` (Jul 2022, ICLR 2023).
Same LLM generates both candidate programs *and* test cases, then "dual execution agreement"
scores each candidate by (a) how many generated tests it passes and (b) how many other
candidates agree with its outputs; the top consensus cluster wins. Raised HumanEval pass@1 to
65.8%. **TAKE** — semipy's decisions subsystem *already* draws multiple candidates and clusters
by observed output divergence; CodeT is the missing scoring rule to pick the head commit.

**MBR-exec — Natural Language to Code Translation with Execution** — `arXiv:2204.11454`
(Apr 2022, EMNLP 2022). Selects a program by minimum-Bayes-risk over *execution results*: run
each candidate on a few inputs, group candidates that behave identically, pick from the largest
semantic-equivalence cluster. "Consistently improves over all execution-unaware selection."
**TAKE** — this is the formal justification for choosing a slot head by *behavioral* consensus
rather than by generation log-prob; maps 1:1 onto clustering-by-output-divergence.

**LEVER — Learning to Verify Language-to-Code with Execution** — `arXiv:2302.08468`
(Feb 2023, ICML 2023). Trains a lightweight verifier on (NL prompt, program, execution output)
to predict correctness, then reranks by blending verifier score with generation probability and
marginalizing over programs with identical execution results. +4.6–10.9% over base LLMs.
**ADAPT** — semipy has no trainable verifier, but the *feature set* (prompt + program + typed
execution result + return-type match) is exactly what a slot's contract cases already encode; a
non-trained scoring heuristic over those features is the practical port.

**μCode — Multi-Turn Code Generation Through Single-Step Rewards** — `arXiv:2502.20380`
(Feb 2025). Frames multi-turn codegen as a "one-step recoverable MDP": a learned verifier scores
each freshly generated candidate, and best-of-N selection per turn using execution feedback
matches heavier hierarchical-RL approaches. **ADAPT** — validates the "sample K, verify, keep
best" loop as competitive with far more complex training; the verifier can be a scoring function,
not necessarily a trained net.

### In-context repair from execution (helps, but bottlenecked)

**Self-Debugging — Teaching LLMs to Self-Debug** — `arXiv:2304.05128` (Apr 2023, v2 Oct 2023).
Few-shot prompts the model to explain its own code ("rubber-duck") and inspect execution results,
then revise — matching baselines that sample >10x more candidates when unit-test feedback exists.
**ADAPT** — the "explain then fix from execution output" trace is a good ADAPT-path prompt, but
its gains lean on the availability of real test feedback, which for semipy means the contract
cases must carry a discriminating input.

**LDB — Debug like a Human (LLM Debugger, runtime state step-by-step)** — `arXiv:2402.16906`
(Feb 2024). Splits the program into basic blocks and feeds *intermediate variable values* after
each block back to the model, so it localizes the failing block instead of re-reasoning over the
whole program; up to +9.8% on HumanEval/MBPP/TransCoder. **ADAPT** — the highest-signal repair
input is runtime *state*, not just pass/fail; if semipy captures intermediate values from the
GistExecutor run of a failing contract case, repair becomes far more targeted than re-prompting.

**Reflexion — Language Agents with Verbal Reinforcement Learning** — `arXiv:2303.11366`
(Mar 2023, NeurIPS 2023). After a failed attempt the agent writes a natural-language reflection
and stores it in an episodic memory buffer that conditions the next attempt; 91% pass@1 on
HumanEval. **ADAPT** — semipy's behavioral contract *is* a durable, content-addressed memory of
"why we regenerated and what broke"; feeding prior change-records into the next GENERATE prompt
is Reflexion specialized to a versioned slot (and unlike Reflexion's transient buffer, it persists
across sessions).

**AlphaCodium — From Prompt Engineering to Flow Engineering** — `arXiv:2401.08500` (Jan 2024).
A structured multi-stage flow (reflect on problem → generate public + AI tests → iterate code
against tests) lifted GPT-4 CodeContests pass@5 from 19% to 44%. **ADAPT** — the value is
*flow* over a single mega-prompt; semipy's orchestration roles already mirror this, and the
"generate additional tests, then iterate against them" stage is the concrete hook for turning a
GENERATE into an execution-guided loop.

### Learning reusable structure from solved programs

**LILO — Learning Interpretable Libraries by Compressing and Documenting Code** —
`arXiv:2310.19791` (Oct 2023, ICLR 2024). Iterates synthesize → compress (Stitch mines optimal
shared lambda abstractions from *solved* programs) → auto-document, so recurring patterns become
reusable library functions. **ADAPT** — pure learning-from-execution angle: after a slot's
candidate passes its contract, the winning program is exactly the kind of solved artifact from
which semipy's sketch library can mine a parametric NL→code pattern for later INSTANTIATE.

### RL-from-tests family (informative; requires training → not applicable)

**CodeRL** — `arXiv:2207.01780` (Jul 2022, NeurIPS 2022): actor-critic where a critic predicts
functional correctness from unit tests to give dense reward. **RLTF** — `arXiv:2307.04349`
(Jul 2023, TMLR): online RL using *multi-granularity* unit-test feedback (error location/type),
not just pass/fail. **AceCoder** — `arXiv:2502.01718` (Feb 2025, ACL 2025): automated large-scale
test-case synthesis to build a verifiable reward for RL, +25% HumanEval-plus in 80 steps.
**SWE-RL** — `arXiv:2502.18449` (Feb 2025, NeurIPS 2025): RL on open-source software evolution
using a *rule-based similarity* reward (notably NOT execution) to hit 41% on SWE-bench Verified.
**LEAVE** (all four) — semipy never updates model weights; the transferable takeaway is only the
*reward design* — fine-grained, execution-derived, location-aware signals beat scalar pass/fail,
which argues for contract cases that pin the exact divergence rather than a boolean.

### Skeptical / negative results (read these before betting on repair)

**Is Self-Repair a Silver Bullet for Code Generation?** — `arXiv:2306.09896` (Jun 2023,
ICLR 2024). On HumanEval/APPS with Code Llama, GPT-3.5, GPT-4: once the *cost* of repair is
counted, gains are "often modest, vary a lot between subsets, and are sometimes not present at
all." Self-repair is "bottlenecked by the model's ability to provide feedback on its own code" —
a stronger critic (or a human) yields substantially larger gains. **Implication for semipy:**
spending the same token budget on more candidates + execution selection often dominates a repair
loop; only repair when the feedback is grounded and discriminating, not self-generated.

**Large Language Models Cannot Self-Correct Reasoning Yet** — `arXiv:2310.01798` (Oct 2023,
ICLR 2024). *Intrinsic* self-correction (no external feedback) does not help and can *degrade*
outputs; external feedback is the differentiator. **Implication:** re-prompting a slot to "try
again, do better" with no execution signal is expected to be neutral-to-harmful; the loop must be
closed on real execution/contract signal or not run at all.

**Do LLMs generate test oracles that capture the actual or the expected program behaviour?** —
`arXiv:2410.21136` (Oct 2024). LLM-generated oracles are "prone on generating oracles that
capture the actual program behaviour rather than the expected one" — i.e., they rubber-stamp what
the code *does*, including its bugs, not what it *should* do. **Implication:** auto-generated
tests must never be the sole acceptance gate; the human `#>` spec and stored contract cases are
the only trustworthy ground truth, and generated tests may serve only to *discriminate* between
candidates.

*(Unverified but on-topic: a relevance search surfaced 2025–2026 IDs — μCode's neighbors, a
"who tests the tests" AUC-consistency paper, differential-fuzzing equivalence checks — whose IDs
I could not confirm from an abstract page and therefore do not cite.)*

---

## 3. How this maps to semipy

semipy is unusually well-positioned because it is already `model(x) → code → execution → typed
value` with a versioned commit DAG, a held-out-example promotion path (interpreted mode), and a
multi-candidate draw that clusters by output divergence (decisions subsystem). The missing pieces
are a *selection rule* and a *disciplined repair-vs-regenerate policy*.

**Where each signal plugs in:**

- **GENERATE / ADAPT → candidate selection.** Turn the existing multi-candidate draw into a
  CodeT/MBR-exec selector: run each candidate in `GistExecutor` against the slot's contract cases
  + held-out examples + typed (`isinstance`/`TypeAdapter`) checks, cluster by behavioral
  equivalence, and commit the largest-consensus, contract-passing candidate as the branch head.
  This is training-free and the best-supported win in the literature.
- **Learned verifier → scoring heuristic over contract features.** LEVER/μCode say a verifier over
  (prompt, program, execution output) reranks well. semipy can't train one, but it can *score*
  candidates with a deterministic function over features it already has: fraction of contract
  cases passed, return-type match, held-out reproduction, and behavioral-cluster size.
- **Repair loop (bounded) → ADAPT path only.** Reserve in-context repair for the ADAPT decision,
  feed it *grounded* signal (LDB-style runtime state / the failing contract case's traceback,
  Self-Debugging-style explanation), cap at 1–2 turns, and gate acceptance on re-running the full
  contract suite with no regression.
- **Contract as Reflexion memory.** Pass prior `ChangeRecord`s ("we regenerated because X broke")
  into the regeneration prompt so the pipeline does not re-make a known-bad choice.
- **Post-success library mining (LILO).** After a candidate passes, hand the solved program to the
  sketch library so a same-shaped future slot resolves via INSTANTIATE with no LLM.

**Decision rule — repair vs. regenerate (for the ADAPT/GENERATE fork):**

1. If a *single* contract case / held-out example fails, the current head passes most other cases,
   and there is a discriminating execution signal (specific traceback line, runtime-state
   divergence, one failing typed assertion) → **bounded localized repair** (≤2 turns), accept only
   if it fixes the failure and regresses nothing (monotone gate against the contract suite).
2. If the failure is broad (many cases fail, wrong output shape/type, empty-string or
   identity-passthrough guard trips), OR repair makes no net progress after 2 turns, OR the only
   available signal is self-generated critique → **regenerate**: draw K candidates and **select by
   execution** over contract + held-out cases, commit the winner as a new branch head.
3. Ground truth is always the human `#>` spec + stored contract cases; auto-generated tests only
   break ties or drive discriminating-input search — never gate acceptance alone.

---

## 4. Open risks

1. **Weak / auto-generated tests give false confidence.** `2410.21136` shows LLM oracles capture
   buggy *actual* behavior, and CodeT's own tests can be wrong. A slot can pass a self-generated
   suite and still be wrong. *Mitigation:* human-anchored contract cases as the sole gate;
   generated tests confined to tie-breaking and discriminating-input search.
2. **Overfitting the program to the contract's stored examples.** With few cases, execution
   selection/repair can reward-hack — a candidate memorizes the examples and fails in general.
   *Mitigation:* keep a held-out split (interpreted mode already does this) and prefer
   property/invariant assertions over raw I/O pairs.
3. **Repair-loop thrash / non-convergence.** Repair oscillates, or fixing one case regresses
   another. *Mitigation:* hard turn cap, strict no-regression acceptance gate, fall back to
   regenerate+select — and record the abandoned attempt in the contract so it isn't retried.
4. **Sandbox + LLM cost.** Every candidate draw and every `GistExecutor`/E2B run costs; the
   Self-Repair result warns repair is often not cost-effective versus simply sampling more.
   *Mitigation:* run cheap execution *selection* before any expensive repair; respect the existing
   decisions cost budget; cache execution results per candidate.
5. **Non-discriminating execution signal misleads.** For nondeterministic, effectful, or expensive
   slots the output-divergence signal may be noise — semipy's decisions subsystem already flags
   "no comparable signal." Repairing or selecting on a noisy signal chases ghosts. *Mitigation:*
   when the signal is flagged non-discriminating, do not repair on it; fall back to spec-level
   regeneration or surface a `#?` fork for human steering.

---

## Sources (all IDs verified against arXiv abstract pages)

- CodeT: Code Generation with Generated Tests — https://arxiv.org/abs/2207.10397 (Jul 2022, ICLR 2023)
- MBR-exec / Natural Language to Code Translation with Execution — https://arxiv.org/abs/2204.11454 (Apr 2022, EMNLP 2022)
- LEVER: Learning to Verify Language-to-Code with Execution — https://arxiv.org/abs/2302.08468 (Feb 2023, ICML 2023)
- Multi-Turn Code Generation Through Single-Step Rewards (μCode) — https://arxiv.org/abs/2502.20380 (Feb 2025)
- Teaching Large Language Models to Self-Debug — https://arxiv.org/abs/2304.05128 (Apr 2023)
- Debug like a Human (LDB) — https://arxiv.org/abs/2402.16906 (Feb 2024)
- Reflexion: Language Agents with Verbal Reinforcement Learning — https://arxiv.org/abs/2303.11366 (Mar 2023, NeurIPS 2023)
- AlphaCodium: From Prompt Engineering to Flow Engineering — https://arxiv.org/abs/2401.08500 (Jan 2024)
- LILO: Learning Interpretable Libraries by Compressing and Documenting Code — https://arxiv.org/abs/2310.19791 (Oct 2023, ICLR 2024)
- CodeRL — https://arxiv.org/abs/2207.01780 (Jul 2022, NeurIPS 2022)
- RLTF: Reinforcement Learning from Unit Test Feedback — https://arxiv.org/abs/2307.04349 (Jul 2023, TMLR)
- AceCoder: Acing Coder RL via Automated Test-Case Synthesis — https://arxiv.org/abs/2502.01718 (Feb 2025, ACL 2025)
- SWE-RL — https://arxiv.org/abs/2502.18449 (Feb 2025, NeurIPS 2025)
- Is Self-Repair a Silver Bullet for Code Generation? — https://arxiv.org/abs/2306.09896 (Jun 2023, ICLR 2024) [skeptical]
- Large Language Models Cannot Self-Correct Reasoning Yet — https://arxiv.org/abs/2310.01798 (Oct 2023, ICLR 2024) [skeptical]
- Do LLMs generate test oracles that capture actual vs expected behaviour? — https://arxiv.org/abs/2410.21136 (Oct 2024) [skeptical]
