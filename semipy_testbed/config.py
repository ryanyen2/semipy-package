"""
Minimal configuration for testbed inference.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SemiConfig:
    """Configuration for simplified semiformal inference."""

    # LLM and API
    openrouter_api_key: str = ""
    model: str = "anthropic/claude-sonnet-4-6"  # OpenRouter model ID
    openai_api_key: str = ""  # Optional fallback for OpenAI

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
    # Load from env if not set
    if not _global_config.openrouter_api_key:
        _global_config.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not _global_config.openai_api_key:
        _global_config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    return _global_config
