"""Configuration for the runtime semiformal system."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from dotenv import load_dotenv
load_dotenv()


@dataclass
class SemiConfig:
    """Global configuration for semi() and the generation agent."""

    model: str = "gpt-5-mini"
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    cache_dir: Path = field(default_factory=lambda: Path(".semiformal"))
    max_retries: int = 3
    enable_execution_test: bool = True
    verbose: bool = True
    stream: bool = False
    confirm_on_failure: bool = False
    confirm_on_external_tools: bool = False
    confirm_callback: Optional[Callable[[str], str]] = None

    def configure(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        max_retries: Optional[int] = None,
        enable_execution_test: Optional[bool] = None,
        verbose: Optional[bool] = None,
        stream: Optional[bool] = None,
        confirm_on_failure: Optional[bool] = None,
        confirm_on_external_tools: Optional[bool] = None,
        confirm_callback: Optional[Callable[[str], str]] = None,
    ) -> None:
        """Update config attributes from the given keyword arguments (only non-None values)."""
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
        if verbose is not None:
            self.verbose = verbose
        if stream is not None:
            self.stream = stream
        if confirm_on_failure is not None:
            self.confirm_on_failure = confirm_on_failure
        if confirm_on_external_tools is not None:
            self.confirm_on_external_tools = confirm_on_external_tools
        if confirm_callback is not None:
            self.confirm_callback = confirm_callback


_config: Optional[SemiConfig] = None


def get_config() -> SemiConfig:
    """Return the global SemiConfig singleton, creating it with defaults if needed."""
    global _config
    if _config is None:
        _config = SemiConfig()
    return _config


def configure(**kwargs: object) -> None:
    """Update global config with the given options (model, api_key, cache_dir, etc.)."""
    get_config().configure(**kwargs)
