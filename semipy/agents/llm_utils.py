"""Shared LLM utilities for classification, impact analysis, and pattern naming (validator model)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional, TypeVar

from semipy.agents.config import get_config

T = TypeVar("T")


async def classify_with_llm(
    prompt: str,
    parse_fn: Callable[[str], T],
    default: T,
    timeout: float = 15.0,
) -> T:
    """
    Call the validator model with the given prompt; parse response with parse_fn; return default on error/timeout.
    Uses config.validator_model. No hardcoded patterns; the prompt drives the task.
    """
    config = get_config()
    model_id = getattr(config, "validator_model", "anthropic/claude-haiku-4-5-20251001")
    api_key = getattr(config, "openrouter_api_key", None) or __import__("os").environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return default
    def _sync_call() -> Optional[str]:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 512,
        }
        import urllib.request
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=int(timeout)) as resp:
            data = json.loads(resp.read().decode())
        choices = data.get("choices", [])
        if not choices:
            return None
        return (choices[0].get("message", {}) or {}).get("content", "")
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=timeout)
        if raw:
            return parse_fn(raw.strip())
    except (asyncio.TimeoutError, Exception):
        pass
    return default
