"""
Minimal configuration for testbed inference.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class SemiConfig:
    """Configuration for simplified semiformal inference."""

    # LLM and API
    openai_api_key: str = ""
    model: str = "gpt-5.4-mini"  # OpenAI model ID
    base_url: str = ""  # Optional OpenAI-compatible base URL override

    # Execution
    timeout: int = 30  # Gist execution timeout (seconds)
    use_docker: bool = False  # Use Docker for gist execution
    docker_image: str = "python:3.11-slim"  # Base Docker image

    # Generation
    temperature: float = 0.7
    max_tokens: int = 4096

    # Logging
    verbose: bool = False
    debug: bool = False


_global_config: SemiConfig = SemiConfig()


def configure(**kwargs: dict) -> None:
    """Update global configuration."""
    global _global_config
    for key, value in kwargs.items():
        if hasattr(_global_config, key):
            setattr(_global_config, key, value)


def get_config() -> SemiConfig:
    """Get current global configuration."""
    global _global_config
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    load_dotenv(override=False)

    if not _global_config.openai_api_key:
        _global_config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    if not _global_config.base_url:
        _global_config.base_url = os.environ.get("OPENAI_BASE_URL", "")
    return _global_config
