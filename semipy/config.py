"""Configuration for the runtime semiformal system."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
load_dotenv()


@dataclass
class SemiConfig:
    """Global configuration for semi() and the generation agent."""

    model: str = "gpt-5-mini"
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    cache_dir: Path = field(default_factory=lambda: Path(".semiformal/runtime"))
    max_retries: int = 3
    enable_execution_test: bool = True

    def configure(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        max_retries: Optional[int] = None,
        enable_execution_test: Optional[bool] = None,
    ) -> None:
        if model is not None:
            self.model = model
        if api_key is not None:
            self.api_key = api_key
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir)
        if max_retries is not None:
            self.max_retries = max_retries
        if enable_execution_test is not None:
            self.enable_execution_test = enable_execution_test


_config: Optional[SemiConfig] = None


def get_config() -> SemiConfig:
    global _config
    if _config is None:
        _config = SemiConfig()
    return _config


def configure(**kwargs: object) -> None:
    get_config().configure(**kwargs)
