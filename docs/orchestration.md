# Orchestration pipeline

The generation pipeline is organized as an explicit set of **named roles** driven
by a code-driven orchestrator, rather than one procedural function. This document
describes the role topology, the determinism boundary, the parallelism model, and
the opt-in configuration. Implementation lives under `semipy/orchestration/`.

> Design origin:
> [`docs/plans/2026-06-06-001-feat-multi-agent-orchestration-pipeline-plan.md`](plans/2026-06-06-001-feat-multi-agent-orchestration-pipeline-plan.md).

## Why not langroid

langroid was evaluated as the multi-agent substrate and **dropped after a
dependency audit**: it pulls ~36 additional transitive packages (onnxruntime,
grpcio, qdrant-client, redis, pandas, nltk, plus several cloud-provider SDKs) and
talks to the OpenAI **Chat Completions** API, not the **Responses** API the
generator depends on. For a distributed library that cost is not justified. The
orchestrator is therefore built in-house as a thin, code-driven layer over the
existing `pydantic_ai` + OpenAI Responses stack — no new heavy dependency, and
generation keeps its tuned behavior and reasoning-id continuity.

## Roles

| Role | Module | LLM? | Artifact returned |
|------|--------|------|-------------------|
| Orchestrator | `orchestration/orchestrator.py` | No (thin router) | drives the stages |
| Code-explorer | `orchestration/roles/explorer.py` | No | `ExplorationResult` |
| Version-checker | `orchestration/roles/version_checker.py` | routing 0; reuse judge ≤N | `VersionContext` / `ReuseVerdict` |
| Coder | `orchestration/roles/coder.py` | Yes (`SemiAgent`) | `GenerationResult` |
| Executor | `orchestration/roles/executor_role.py` | **No** | `ExecutionEvidence` |
| Verifier | `orchestration/roles/verifier.py` | rules 0; alignment N | `VerificationVerdict` |
| Surfacer | `orchestration/roles/surfacer.py` | ≤1 (changed keys) | `SurfacePlan` |

Roles exchange the JSON-safe typed artifacts in `orchestration/artifacts.py`;
live objects (the `Slot`, the compiled function, the portal) stay with the
orchestrator.

## Determinism boundary

Deterministic (no LLM): routing (`routing.py`/`resolver.py`), the structural
validator (`validator.py`), the executor, the skeleton write (`skeleton_writer`),
and `verified`/`yields` derivation. LLM-backed: the coder, the alignment layer of
the verifier, the reuse judge, and changed-key steering synthesis.

**Every LLM-backed role degrades to a deterministic default when no API key is
configured** (`make_responses_model` returns `(None, None)`), so the offline unit
suite runs without a key:

- verifier alignment → abstain (`passed=True`, `alignment_verdict=None`); the
  deterministic guards remain the gate.
- reuse judge → REUSE (trust the cached implementation).
- surfacer → heuristic steering block.
- coder → raises (a slot that routed to GENERATE has nothing to fall back to).

## Correctness model

Optimized for correctness first (latency second). The two payoff mechanisms:

- **Alignment verifier** (`verifier.verify_alignment`): deterministic guards
  first, then a binary, evidence-grounded LLM judge of observed `{input, output}`
  behavior vs the spec. Draws `verifier_vote_samples` independent judgments
  concurrently and combines them by **strict majority** (ties fail). Uses the
  `verifier` role model, distinct from the coder, to blunt self-enhancement bias.
- **Evidence-grounded reuse** (`decision.aggregate_semantic_votes`): the reuse
  judge can draw `reuse_vote_samples` votes; ties resolve to **ADAPT** (bias
  toward verification — under-verification is the modal multi-agent failure).

Both abstain rather than hallucinate when there is no executed evidence.

## Parallelism (read-only only)

`orchestration/parallel.gather_readonly` runs independent **read-only** roles
(code-explorer, version-checker evidence-gathering) and the verifier's vote
fan-out concurrently on the shared background event loop (`embed_run` →
`asyncio.gather`, with `asyncio.to_thread` for sync roles). State-mutating work
(coder dispatch write, surfacer skeleton edit, portal read-modify-write) stays
**serial** behind the existing per-portal and per-slot locks. A failing read-only
thunk degrades to `None` without aborting the batch.

## Configuration (all default to unchanged behavior)

| Flag | Default | Effect |
|------|---------|--------|
| `verifier_vote_samples` | 3 | alignment-judge samples per verdict (majority vote) |
| `reuse_vote_samples` | 1 | reuse-judge samples (1 = single judgment, unchanged) |
| `<role>_model` | `None` | per-role model override; falls back to `openai_model` |

## Status

Shipped and verified: the role modules, typed artifacts, the routing seam, the
alignment verifier (live-verified), evidence-grounded reuse voting, the surfacer,
the explorer, the parallel gather, and the concurrent-role lane model. The full
end-to-end orchestrator integration (driving every role through the spine, the
coder↔verifier evaluator-optimizer retry loop, and the KTD7 lock-narrowing)
builds on these pieces; see the plan for the remaining integration steps.
