"""Configuration for the runtime semiformal system."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Terminal/Jupyter stream UI (fixed; not exposed on SemiConfig).
STREAM_PEEK_LINES = 4
STREAM_TIMELINE = True
STREAM_SHOW_ELAPSED = False


def effective_stream_display_mode(*, verbose: bool) -> str:
    """Return ``peek`` (rolling model tail + timeline) or ``none`` when ``verbose`` is off."""
    return "peek" if verbose else "none"


@dataclass
class SemiConfig:
    """Global configuration for semi() and the generation agent."""
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_model: str = "gpt-5.5"
    #: Per-role model overrides for the multi-role orchestration pipeline. ``None`` falls
    #: back to ``openai_model`` via ``model_for_role``, so behavior is unchanged until a
    #: role is explicitly retargeted (e.g. a cheaper explorer, a stronger verifier).
    orchestrator_model: Optional[str] = None
    coder_model: Optional[str] = None
    verifier_model: Optional[str] = None
    version_checker_model: Optional[str] = None
    explorer_model: Optional[str] = None
    surfacer_model: Optional[str] = None
    #: Number of LLM alignment-judge samples the verifier draws per verdict (majority vote).
    #: Correctness-first: >1 trades tokens for a more reliable verdict. 1 = single judgment.
    verifier_vote_samples: int = 3
    #: Hard per-call timeout (seconds) for an LLM judge (alignment verifier / reuse judge).
    #: A stalled call would otherwise block the shared loop's fut.result() indefinitely;
    #: on timeout the judge yields no verdict and the aggregator abstains (reuse/pass).
    judge_timeout: int = 60
    e2b_api_key: Optional[str] = field(default_factory=lambda: os.getenv("E2B_API_KEY"))
    gist_timeout: int = 30
    cache_dir: Path = field(default_factory=lambda: Path(".semiformal"))
    max_retries: int = 3
    verbose: bool = True
    cocoindex_enabled: bool = False
    cocoindex_db_url: str = ""
    session_source: Optional[str] = None
    semantic_verify: bool = True
    #: Number of reuse/adapt judge samples for the version-checker's evidence-grounded
    #: reuse decision (majority vote, ties -> ADAPT to bias toward verification).
    #: Default 1 = single judgment (unchanged behavior/cost); raise for correctness.
    reuse_vote_samples: int = 1
    #: Cap on semantic-recheck-driven ADAPTs per slot. An inherently-semantic slot
    #: (e.g. triage, preference judgment, summarization) compiles to a *static*
    #: function the intent judge can keep rejecting on every fresh free-text input,
    #: which would regenerate on essentially every call -- unbounded cost/latency at
    #: scale. After this many semantic-driven adapts the slot stops re-checking and
    #: trusts the current implementation, so it CONVERGES. Raise for more refinement,
    #: set 0 to disable the cap (regenerate whenever the judge says so).
    semantic_verify_max_adapts: int = 2
    #: After GENERATE/ADAPT, extract NL-to-code bindings and update ``sketch_library.json``.
    sketch_library_learning: bool = True
    #: If False, persist sketches before ``execute_slot`` returns (needed for INSTANTIATE in the same process).
    #: If True, run extraction in a background thread (lower latency; same-run INSTANTIATE may not see new sketches).
    sketch_library_learning_async: bool = False
    #: Minimum model-reported confidence for a sketch binding to be *considered* at all.
    #: This is a cheap proposal-side prefilter (an LLM self-report, never the oracle-gated
    #: authority on reuse): when the spec/code alignment is unclear, the binding is skipped
    #: rather than memorized as a pattern. Raise to be stricter; lower to be more permissive.
    sketch_library_min_confidence: float = 0.6
    #: Minimum number of independently generated occurrences of the same structural
    #: pattern before a sketch is licensed for cross-slot matching (kernel.operators
    #: .license_sketch). A pattern seen once proves nothing; this replaces the old
    #: single-shot confidence-only promotion gate.
    sketch_library_min_recurrence: int = 2
    #: When True, record per-call outcomes, run the intent-fit judge on real data,
    #: and emit context-change traces. Default off for backward compatibility.
    adaptive_mode: bool = False

    # --- Behavioral contract subsystem (records WHY/EFFECT of changes) ---
    #: Master switch for the contract subsystem (deterministic invariant seeding,
    #: change records). Cheap; on by default.
    contract_enabled: bool = True
    #: Executable acceptance gate: a reused/regenerated impl must satisfy the slot's
    #: carried-forward behavioral cases. Off until proven on a project; flip on to enforce.
    contract_gate: bool = False
    #: Max regeneration retries to satisfy violated cases before quarantining them (latency cap).
    contract_gate_max_retries: int = 1
    #: When True, an unintended effect-diff (a change that alters a previously-passing
    #: input pattern) fails the gate and triggers a regeneration retry.
    contract_block_regressions: bool = True
    #: Cap on active cases executed per gate (latency / portal size).
    contract_max_cases: int = 25
    #: Selectivity cap: max new golden-master example cases the maintainer pins per commit.
    contract_max_new_examples: int = 3
    #: Run the LLM maintainer pass (proposes examples/metamorphic relations, supersedes
    #: outdated cases). Deterministic invariant seeding runs whenever contract_enabled.
    contract_maintainer: bool = False
    #: If True, run the maintainer in a background thread (lower latency; cases may lag a call).
    contract_maintainer_async: bool = False

    # --- Effects subsystem (reified real-world effects: DB/file/data/API) ---
    #: Master switch. When on, effectful slots (whose generated function declares an
    #: ``fx`` parameter) emit a reified EffectScript via ``fx`` and the generation
    #: prompt teaches that capability. Default OFF: a pure project sees no change to
    #: generation or runtime until a researcher opts in.
    effects_enabled: bool = False
    #: Open shadows and run effect verification before accepting/applying. (Stage 1)
    effect_staging: bool = False
    #: Enforce effect-invariant cases + block blast-radius regressions (acceptance gate). (Stage 1)
    effect_gate: bool = False
    #: Max regeneration retries to satisfy violated effect cases before quarantining.
    effect_gate_max_retries: int = 1
    #: When True, an unintended artifact-state diff vs the parent fails the gate. (Stage 2)
    effect_block_regressions: bool = True
    #: Prove blast-radius bounds for-all-inputs via schema superkey + AST-structural
    #: checks (dependency-free; no Z3/CrossHair). Else static + shadow checks only. (Stage 3)
    effect_smt: bool = False
    #: Commit the verified shadow to the REAL artifact. REQUIRES effect_gate on + passed. (Stage 4)
    effect_auto_apply: bool = False
    #: Externalized/irreversible targets (APIs, email) require approval before commit. (Stage 5)
    effect_require_approval_external: bool = True
    #: Approval hook for externalized effects: ``callable(EffectScript) -> bool``. Receives the
    #: planned (un-sent) effects so the caller can show "what I will do" and decide. ``None``
    #: (default) means external effects are never auto-performed -- they stay planned (dry-run).
    #: Runtime-only (not persisted).
    effect_approval_callback: Optional[object] = None
    #: Default per-effect blast-radius bound when none is declared.
    effect_default_blast_radius: int = 1

    # --- Decisions subsystem (surface the model's silent forks) ---
    #: Master switch. When on, an underspecified slot draws multiple candidates,
    #: clusters them by observed divergence, and surfaces each silent choice as a
    #: navigable ``#?`` fork. Default OFF: a slot whose candidates would agree, and
    #: every existing project, sees no change to generation or runtime until opt-in.
    decisions_enabled: bool = False
    #: Adaptive draw: initial candidate count; escalates to the max only on divergence.
    decision_initial_candidates: int = 3
    #: Adaptive draw: maximum candidates drawn for a divergent slot.
    decision_max_candidates: int = 5
    #: Per-role model override for the decision classifier (labels forks in user
    #: language). ``None`` falls back to ``openai_model`` via ``model_for_role``.
    decision_classifier_model: Optional[str] = None
    #: Wall-clock budget (seconds) for observing one slot's candidate divergence,
    #: bounding cost on expensive/nondeterministic slots (U11).
    decision_cost_budget_s: int = 20

    def model_for_role(self, role: Optional[str] = None) -> str:
        """Return the model id for an orchestration role, falling back to ``openai_model``.

        ``role`` names a pipeline role (``coder``, ``verifier``, ``explorer``,
        ``surfacer``, ``orchestrator``). When the matching ``<role>_model`` field is
        unset (the default), the global ``openai_model`` is used, so the pipeline runs
        on one model until a role is deliberately retargeted.
        """
        if role:
            override = getattr(self, f"{role}_model", None)
            if override:
                return override
        return self.openai_model


_config: Optional[SemiConfig] = None


def get_config() -> SemiConfig:
    """Return the global SemiConfig singleton, creating it with defaults if needed."""
    global _config
    if _config is None:
        _config = SemiConfig()
    return _config


def configure(**kwargs: object) -> None:
    """Update global config. Unknown keys are ignored so older scripts keep running."""
    cfg = get_config()
    allowed = {f.name for f in fields(SemiConfig)}
    for key, val in kwargs.items():
        if key not in allowed or val is None:
            continue
        if key == "cache_dir":
            setattr(cfg, key, Path(val))  # type: ignore[arg-type]
        else:
            setattr(cfg, key, val)
