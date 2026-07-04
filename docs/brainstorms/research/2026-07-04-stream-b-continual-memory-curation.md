# Stream B — Continual / Experiential Learning & Agentic Memory Curation

Prior-art mechanism catalog for semipy, focused on **how LLM systems accumulate
reusable knowledge across tasks and CURATE it** — deciding what is important,
what to merge/abstract into skills, and what to forget — without degrading over
time. Date: 2026-07-04.

Scope reminder: semipy's "memories" are not text trajectories/reflections. They
are **executable code priors** (generated functions, parametric sketches,
behavioral contracts) carried in a versioned commit DAG, each with **real
execution outcomes**. Every method below is read through that lens: what changes
when a memory can be *executed, verified, and merged at the AST/parametric level*
rather than merely retrieved as text.

---

## 1. Verdict — how "continuous learning" should be framed for semipy

semipy already owns the substrate that most agentic-memory papers bolt on after
the fact: a **versioned, executable, outcome-carrying store** (Portal DAG +
content-addressed contracts + parametric sketches). So "continual learning" for
semipy should **not** be reframed as adding a text memory of trajectories or
reflections — it should be reframed as a **curation problem over an existing
code-prior store**. Four policies, in priority order: (a) **importance / utility
scoring** on commits and sketches (reuse count, verification pass-rate, recency,
spec-centrality) so retrieval and retention are ranked, not FIFO; (b)
**abstraction** of a sketch from N structurally-congruent commits, gated on
*recurrence and compression benefit*, not just today's 0.6 confidence threshold;
(c) **merging** near-duplicate sketches at the AST/parametric level, with an
**executable equivalence check** (re-run both parents' contract cases) as the
safety gate that pure-text memory systems structurally cannot have; and (d)
**forgetting** by *demotion/archival* — not deletion — of low-utility,
superseded, or regression-prone priors, keeping the DAG as an audit trail while
shrinking the working set. Throughout, resist ACE's "context collapse" /
brevity-bias failure mode by preferring **incremental delta edits** to the
durable record over wholesale LLM rewrites. The decisive asymmetry versus every
text-memory method: because semipy memories are executable, **every curation
action can be gated by re-running contract cases** instead of trusting an LLM's
self-assessment — turning risky memory heuristics (merge? forget? abstract?)
into checkable code operations.

---

## 2. Mechanism catalog

Tag key: **TAKE** = adopt the mechanism largely as-is; **ADAPT** = the idea is
right but the code layer changes it materially; **LEAVE** = already covered by
semipy or a poor fit.

### ACE — Agentic Context Engineering  ·  **TAKE**
The single most directly relevant paper. Splits context maintenance into three
roles — **Generator** (produces trajectories/candidate content), **Reflector**
(distills what worked/failed into lessons), **Curator** (merges lessons into the
context) — and treats context as an **evolving "playbook"** of itemized
strategies. Its two curation inventions are the takeaways: (1) **incremental
delta updates** — append/edit small structured items rather than rewrite the
whole context, which prevents **"context collapse"** (iterative full-rewrites
that erode detail) and **brevity bias** (useful domain knowledge lost to
summaries); (2) **grow-and-refine** — periodically de-duplicate and prune the
accumulated items so the playbook grows monotonically in coverage but stays
compact. Adapts from *natural execution feedback*, no labeled supervision.
*Code layer:* semipy's "playbook items" are commits, contract cases, and
sketches — so the Curator's merge/prune step can be **verified** (re-run cases)
rather than judged; delta-vs-rewrite maps exactly to "amend the contract /
sketch metadata" vs "regenerate the whole slot."
Cite: arXiv **2510.04618** (Zhang et al., submitted 6 Oct 2025; ICLR 2026).

### Generative Agents — importance × recency × relevance + reflection  ·  **TAKE**
Introduces the canonical **retrieval scoring formula**: each memory is scored by
a weighted sum of **recency** (exponential time decay), **importance** (an LLM
"poignancy" rating 1–10 assigned at write time), and **relevance** (embedding
cosine to the query). **Reflection** is the abstraction mechanism: when summed
importance since the last reflection crosses a threshold, the agent synthesizes
higher-level insights from recent memories and writes them *back* into the
stream, forming a tree of increasingly abstract memories.
*Code layer:* gives semipy a ready importance-scoring template for commits/
sketches — but replace subjective LLM poignancy with **objective code signals**
(reuse count, contract pass-rate, downstream-slot fan-out). "Reflection when
importance crosses a threshold" becomes "**abstract a sketch when N congruent
commits accumulate**."
Cite: arXiv **2304.03442** (Park et al., submitted 7 Apr 2023; UIST 2023).

### Voyager — skill library + self-verification + automatic curriculum  ·  **TAKE**
The closest analog to semipy's sketch library. Each learned **skill is
executable code**; a skill is **admitted to the library only after
self-verification confirms it completed the task**; skills are **indexed by an
embedding of their (LLM-generated) natural-language description** and retrieved
by similarity; new skills **compose** by calling previously stored skills.
Curation rule: never store an unverified skill; retrieval is semantic, not
literal.
*Code layer:* semipy already has the "verify before admit" gate (validation +
contract) — Voyager confirms it should be **mandatory before any sketch is
promoted to INSTANTIATE-eligible**. Two deltas to steal: **compositional
skills** (let a sketch invoke an earlier sketch) and **embedding-keyed
retrieval** of the *spec's durable meaning* (semipy currently uses deterministic
token-alignment — see risks).
Cite: arXiv **2305.16291** (Wang et al., submitted 24 May 2023).

### AWM — Agent Workflow Memory  ·  **TAKE**
Induces **commonly-reused routines ("workflows")** from past trajectories and
selectively injects the relevant ones into future generations. Works **offline**
(mine workflows from a training set once) and **online** (induce from test
queries on the fly). The curation principle is **frequency-driven abstraction**:
a routine becomes a first-class reusable unit *because it recurs*, and only
*selectively relevant* workflows are surfaced (not the whole library).
*Code layer:* this is the missing gate on semipy's sketch abstraction — abstract
a sketch **when the same shape recurs across N commits**, not merely when a
single generation scores ≥ 0.6 confidence. "Selective provision" maps to
ranked, budgeted retrieval of priors into the generation prompt.
Cite: arXiv **2409.07429** (Wang, Mao, Fried, Neubig; submitted 11 Sep 2024).

### ExpeL — Experiential Learning of LLM Agents  ·  **ADAPT**
Autonomously **gathers experiences across training tasks and extracts natural-
language "insights,"** plus keeps a pool of successful trajectories to recall at
inference. Its distinctive curation move is comparing **successful vs. failed
attempts** on the same task to distill a generalizable insight, and applying
**edit operations** (add/upvote/downvote/remove) to the insight set so it
refines rather than only grows.
*Code layer:* semipy's equivalent of "successful vs failed trajectory" is
**passing vs regressing commits of the same slot** — and the "insight" should be
crystallized not as NL but as an **executable contract case** (the durable,
verifiable form). ADAPT ExpeL's add/upvote/downvote/remove as the **lifecycle
operations on contract cases and sketches** (semipy has no downvote/decay today).
Cite: arXiv **2308.10144** (Zhao et al., submitted 20 Aug 2023; AAAI-24).

### A-MEM — Agentic Memory (Zettelkasten)  ·  **ADAPT**
Stores memories as **atomic notes** with structured attributes (contextual
description, keywords, tags); on each insert it **generates links** to related
prior notes where meaningful similarity exists, and — critically — **evolves
existing notes** (updates their attributes/context) as new memories arrive, so
the network refines itself rather than freezing at write time.
*Code layer:* two ideas port. (1) **Link generation** across slots/sketches
gives semipy a dependency-aware library (complements the existing reactivity
graph). (2) **Memory evolution** — updating a *prior* sketch's metadata when a
new congruent commit arrives — is exactly the "grow-and-refine" refresh, and in
semipy it can be **verified** (does the evolved sketch still satisfy the old
commits' cases?) rather than trusted. LEAVE the free-text-note substrate; keep
the linking + evolution policy.
Cite: arXiv **2502.12110** (Xu, Liang, Mei, Gao, Tan, Zhang; submitted 17 Feb
2025; NeurIPS 2025).

### MemGPT — memory hierarchy / virtual context  ·  **ADAPT**
OS-inspired **two-tier memory**: a small in-context "main context" and a large
out-of-context "external context," with the LLM issuing **self-directed function
calls to page data in/out** under memory-pressure signals and eviction. Solves
*working-set management*, not abstraction/merging.
*Code layer:* relevant to **which priors get loaded into the generation prompt**
under a token budget — page in the top-k ranked commits/sketches, evict the
rest to the DAG (which is already the durable "external context"). LEAVE the
self-paging autonomy; semipy's retrieval should be a ranked, deterministic
working-set fill, not LLM-driven paging.
Cite: arXiv **2310.08560** (Packer et al., submitted 12 Oct 2023; now "Letta").

### Reflexion — verbal reinforcement / self-reflection  ·  **LEAVE** (thin ADAPT)
Maintains an **episodic buffer of self-reflections** written after a failed
attempt and re-injected on the next try; the buffer is a **small bounded sliding
window**, per-task, with **no abstraction, merging, or cross-task transfer**.
*Code layer:* semipy's behavioral contract already records *why* a regeneration
happened and guards regression — a superset of Reflexion's per-task reflection,
and durable rather than windowed. The one crumb worth ADAPTing: **reflect on the
failing execution before regenerating** (feed the failure trace into the next
GENERATE), which semipy can do more rigorously because the failure is an actual
execution result, not a narrated one.
Cite: arXiv **2303.11366** (Shinn et al., submitted 20 Mar 2023; NeurIPS 2023).

### Surveys — framing / vocabulary  ·  reference
- **Self-Evolving AI Agents (comprehensive survey).** Unifies self-evolution as
  a feedback loop over four parts: **System Inputs · Agent System · Environment
  · Optimisers**, and organizes methods by *which part they evolve*. Useful lens
  for positioning semipy's curation as "evolving the Agent System's stored
  priors via execution-feedback optimisers."
  Cite: arXiv **2508.07407** (Fang et al., submitted 10 Aug 2025).
- **Lifelong Learning of LLM-based Agents: A Roadmap.** Organizes lifelong
  agents into **Perception / Memory / Action** modules and centers
  **catastrophic forgetting** and continual adaptation. Grounds the "avoid
  degradation over time" requirement.
  Cite: arXiv **2501.07278** (Zheng et al., submitted 13 Jan 2025; IEEE TPAMI).

---

## 3. How this maps to semipy — concrete curation policies

### 3.1 Importance / utility scoring (adopt Generative Agents' formula, code-ified)
Score every commit and sketch with objective, executable signals instead of LLM
poignancy:

```
utility(prior) = w_r · reuse_count_norm          # how often INSTANTIATE/REUSE hit it
               + w_p · contract_pass_rate         # verified quality, computed live
               + w_t · recency_decay(last_used)   # exponential decay
               + w_c · spec_centrality             # fan-out to downstream slots (reactivity graph)
```

Use `utility` to (a) **rank retrieval** of priors into the generation prompt
(MemGPT working-set fill under a token budget) and (b) **rank retention** when
deciding what to demote. Note the CLAUDE.md caveat: per-case pass/fail is *not
persisted today* (computed live) — so either persist a rolling pass-rate or
recompute at curation time.

### 3.2 When to ABSTRACT a sketch from N commits (AWM + Voyager)
Today abstraction is **confidence-gated at 0.6** on a single generation. Change
it to **recurrence + compression gated**:
- Trigger when **≥ N structurally-congruent commits** (same
  `spec_equivalence_key` shape / same AST skeleton, differing only in literals)
  exist across the portal — AWM's frequency signal.
- Require a **compression benefit** (the abstracted sketch, parametrized over the
  varying literals, reproduces all N commits) — Voyager's "verify before admit"
  applied to abstraction, checkable because these are executable.
- Only then promote to INSTANTIATE-eligible. This makes abstraction *earned by
  reuse*, not asserted by a one-shot confidence score.

### 3.3 When to MERGE two sketches (the code-layer superpower)
Text systems merge memories by LLM judgment and hope. semipy can merge with a
**gate**:
1. Detect candidates via near-duplicate **durable-meaning keys** (see risks —
   move beyond token-alignment toward spec-embedding, but keep a structural
   AST check to avoid false merges).
2. Construct the merged parametric sketch at the **AST/parametric level**.
3. **Re-run both parents' contract cases against the merged sketch.** Merge only
   if *all* cases still pass. This is ACE's grow-and-refine dedup, but *verified*
   rather than *trusted*.

### 3.4 When to RETIRE / FORGET a prior (ExpeL edit-ops + lifelong forgetting)
Prefer **demotion/archival over deletion** (the DAG stays an audit trail):
- **Superseded**: a slot's `spec_changed` retires old contract cases already —
  extend to demote the now-orphaned sketch if nothing else references it.
- **Regression-prone**: a prior whose reuse repeatedly triggers ADAPT/guard
  failures gets **downvoted** (ExpeL's downvote/remove) below the retrieval
  cutoff.
- **Cold**: `recency_decay` alone drops long-unused, low-fan-out sketches out of
  the working set — but they remain in the DAG, recoverable.
- Bound the *active* library size; let the DAG be unbounded cold storage.

### 3.5 What "combining priors" means: code vs text
For text-memory systems, "combining" = concatenate/summarize and pray. For
semipy's **executable** priors it is a spectrum of *verifiable* operations:
- **Parametric merge** — unify literals into parameters (sketch level), gated by
  contract-case replay.
- **AST composition** — one sketch calls another (Voyager compositionality),
  building higher-order skills.
- **Contract union** — merged prior must satisfy the *union* of both parents'
  cases; the union is the acceptance test.
- **Delta amendment** — ACE-style: append a discriminating contract case or edit
  sketch metadata instead of regenerating (avoids context collapse).

---

## 4. Open risks

1. **Merging subtly-different sketches (false abstraction).** Two slots can share
   a spec shape yet diverge on an edge case that no stored contract case
   exercises; a verified merge that only replays *existing* cases will silently
   unify them. Mitigation: seed **discriminating inputs** (semipy's decisions
   subsystem already does germ-seeded discriminating-input search) *before*
   accepting a merge — don't merge on shape congruence alone.

2. **Unbounded library / DAG growth.** Recurrence-gated abstraction and
   never-delete archival both trade memory for auditability; without an active
   working-set cap and a cold-storage tier, retrieval ranking degrades and
   generation prompts bloat. Mitigation: hard cap the *active* set by `utility`;
   spill the rest to the DAG.

3. **Stale priors surfacing after the world changed.** A high-`reuse_count`
   sketch can be stale if its upstream data-flow or an external contract shifted;
   recency decay is a weak proxy. Mitigation: lean on the reactivity graph's
   *pull-based* staleness (`stale_against_inputs`) as a first-class term in
   `utility`, not just time decay.

4. **Spec-embedding retrieval surfacing a wrong prior.** Moving from
   deterministic token-alignment to embedding-of-durable-meaning improves recall
   but risks confident false positives (semantically-near specs with
   incompatible types/outputs). Mitigation: **two-stage retrieval** — embedding
   for candidate recall, then the *executable* gate (type check +
   `spec_equivalence_key` structural match + contract-case replay) for
   precision. The code layer converts a soft-similarity risk into a hard,
   checkable filter.

5. **Context collapse in the durable record.** If curation ever rewrites
   contracts/sketch metadata wholesale via an LLM (rather than delta-editing),
   ACE's exact failure mode — erosion of accumulated detail and brevity bias —
   reappears in semipy's ledger. Mitigation: make every curation write an
   **incremental, content-addressed delta**, never a full-record regeneration;
   the portal DAG's append-only commits already point this way.

---

## Citation index (verified 2026-07-04)

| Method | arXiv / venue | Date |
|---|---|---|
| ACE — Agentic Context Engineering | 2510.04618 · ICLR 2026 | 6 Oct 2025 |
| A-MEM — Agentic Memory | 2502.12110 · NeurIPS 2025 | 17 Feb 2025 |
| AWM — Agent Workflow Memory | 2409.07429 | 11 Sep 2024 |
| ExpeL — Experiential Learning | 2308.10144 · AAAI-24 | 20 Aug 2023 |
| Voyager — skill library | 2305.16291 | 24 May 2023 |
| Reflexion | 2303.11366 · NeurIPS 2023 | 20 Mar 2023 |
| Generative Agents | 2304.03442 · UIST 2023 | 7 Apr 2023 |
| MemGPT / Letta | 2310.08560 | 12 Oct 2023 |
| Survey: Self-Evolving AI Agents | 2508.07407 | 10 Aug 2025 |
| Survey: Lifelong LLM Agents Roadmap | 2501.07278 · IEEE TPAMI | 13 Jan 2025 |

Uncertainty notes: ACE/A-MEM/AWM/ExpeL and both surveys were fetched and
confirmed live from arXiv abstract pages this session (title, authors, date,
venue). Voyager, Reflexion, Generative Agents, and MemGPT IDs/venues are stated
from established prior knowledge and were not re-fetched this session; the IDs
are well-established but treat the venue/date fields as high-confidence-not-
re-verified. No IDs were fabricated; where a mechanism detail was not present on
the fetched abstract (e.g., ExpeL's exact edit-operation names, ACE's precise
grow-and-refine algorithm), it is stated from prior knowledge and flagged as
such rather than attributed to the fetched text.
