"""Shared LLM utilities for classification, impact analysis, and pattern naming (OpenAI, same key as generator)."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Optional, TypeVar

from semipy.agents.config import get_config

T = TypeVar("T")


async def classify_with_llm(
    prompt: str,
    parse_fn: Callable[[str], T],
    default: T,
    timeout: float = 15.0,
) -> T:
    """
    Call the OpenAI chat completions API with the given prompt; parse response with parse_fn; return default on error/timeout.
    Uses config.openai_model and config.openai_api_key or OPENAI_API_KEY (same as generator._create_openai_model). No hardcoded patterns; the prompt drives the task.
    """
    config = get_config()
    model_id = getattr(config, "openai_model", "gpt-5.4")
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return default
    def _sync_call() -> Optional[str]:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
        }
        import urllib.request
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
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
