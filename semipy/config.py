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

    openrouter_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY"))
    openrouter_model: str = "anthropic/claude-sonnet-4-6"
    validator_model: str = "anthropic/claude-haiku-4-5-20251001"
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_model: str = "gpt-5.2"
    e2b_api_key: Optional[str] = field(default_factory=lambda: os.getenv("E2B_API_KEY"))
    use_e2b: bool = True
    gist_timeout: int = 30
    cache_dir: Path = field(default_factory=lambda: Path(".semiformal"))
    max_retries: int = 3
    enable_execution_test: bool = True
    verbose: bool = True
    stream: bool = True
    confirm_on_failure: bool = False
    confirm_on_external_tools: bool = False
    confirm_callback: Optional[Callable[[str], str]] = None
    reactive: bool = True
    analyze_scripts: bool = True

    def configure(
        self,
        openrouter_api_key: Optional[str] = None,
        openrouter_model: Optional[str] = None,
        validator_model: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
        e2b_api_key: Optional[str] = None,
        use_e2b: Optional[bool] = None,
        gist_timeout: Optional[int] = None,
        cache_dir: Optional[Path] = None,
        max_retries: Optional[int] = None,
        enable_execution_test: Optional[bool] = None,
        verbose: Optional[bool] = None,
        stream: Optional[bool] = None,
        confirm_on_failure: Optional[bool] = None,
        confirm_on_external_tools: Optional[bool] = None,
        confirm_callback: Optional[Callable[[str], str]] = None,
        reactive: Optional[bool] = None,
        analyze_scripts: Optional[bool] = None,
    ) -> None:
        """Update config attributes from the given keyword arguments (only non-None values)."""
        if openrouter_api_key is not None:
            self.openrouter_api_key = openrouter_api_key
        if openrouter_model is not None:
            self.openrouter_model = openrouter_model
        if validator_model is not None:
            self.validator_model = validator_model
        if openai_api_key is not None:
            self.openai_api_key = openai_api_key
        if openai_model is not None:
            self.openai_model = openai_model
        if e2b_api_key is not None:
            self.e2b_api_key = e2b_api_key
        if use_e2b is not None:
            self.use_e2b = use_e2b
        if gist_timeout is not None:
            self.gist_timeout = gist_timeout
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
        if reactive is not None:
            self.reactive = reactive
        if analyze_scripts is not None:
            self.analyze_scripts = analyze_scripts


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
