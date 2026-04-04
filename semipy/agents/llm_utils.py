"""Shared LLM utilities for classification, impact analysis, and pattern naming (OpenAI, same key as generator)."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Optional, TypeVar

from semipy.agents.config import get_config

T = TypeVar("T")


def _chat_completion_content(
    *,
    url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    max_tokens: int,
    extra_headers: dict[str, str] | None,
) -> Optional[str]:
    import urllib.request

    payload: dict[str, object] = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    choices = data.get("choices", [])
    if not choices:
        return None
    return (choices[0].get("message", {}) or {}).get("content", "")


async def classify_with_llm(
    prompt: str,
    parse_fn: Callable[[str], T],
    default: T,
    timeout: float = 15.0,
    *,
    max_tokens: int = 512,
) -> T:
    """
    Call OpenAI chat completions when OPENAI_API_KEY is set; otherwise OpenRouter
    when OPENROUTER_API_KEY is set. Parse with parse_fn; return default on error/timeout.
    """
    config = get_config()

    def _sync_openai() -> Optional[str]:
        model_id = getattr(config, "openai_model", "gpt-5.4")
        api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return _chat_completion_content(
            url="https://api.openai.com/v1/chat/completions",
            api_key=api_key,
            model_id=model_id,
            prompt=prompt,
            max_tokens=max_tokens,
            extra_headers=None,
        )

    def _sync_openrouter() -> Optional[str]:
        api_key = config.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None
        model_id = getattr(config, "openrouter_model", "anthropic/claude-sonnet-4-6")
        return _chat_completion_content(
            url="https://openrouter.ai/api/v1/chat/completions",
            api_key=api_key,
            model_id=model_id,
            prompt=prompt,
            max_tokens=max_tokens,
            extra_headers={
                "HTTP-Referer": "https://github.com/semipy",
                "X-Title": "semipy-classify",
            },
        )

    def _sync_call() -> Optional[str]:
        raw = _sync_openai()
        if raw is not None:
            return raw
        return _sync_openrouter()

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=timeout)
        if raw:
            return parse_fn(raw.strip())
    except (asyncio.TimeoutError, Exception):
        pass
    return default
