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
    e2b_api_key: Optional[str] = field(default_factory=lambda: os.getenv("E2B_API_KEY"))
    gist_timeout: int = 30
    cache_dir: Path = field(default_factory=lambda: Path(".semiformal"))
    max_retries: int = 3
    verbose: bool = True
    cocoindex_enabled: bool = False
    cocoindex_db_url: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    session_source: Optional[str] = None
    semantic_verify: bool = True
    semantic_verify_threshold: int = 10
    #: After GENERATE/ADAPT, extract NL-to-code bindings and update ``sketch_library.json``.
    sketch_library_learning: bool = True
    #: If False, persist sketches before ``execute_slot`` returns (needed for INSTANTIATE in the same process).
    #: If True, run extraction in a background thread (lower latency; same-run INSTANTIATE may not see new sketches).
    sketch_library_learning_async: bool = False
    #: Minimum model-reported confidence for a sketch binding to be added to the library.
    #: When the spec/code alignment is unclear, the binding is skipped rather than memorized
    #: as a pattern. Raise to be stricter; lower to be more permissive.
    sketch_library_min_confidence: float = 0.6
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
