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
    openrouter_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY"))
    openrouter_model: str = "anthropic/claude-sonnet-4-6"
    validator_model: str = "anthropic/claude-haiku-4-5-20251001"
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_model: str = "gpt-5.4"
    e2b_api_key: Optional[str] = field(default_factory=lambda: os.getenv("E2B_API_KEY"))
    use_e2b: bool = True
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
