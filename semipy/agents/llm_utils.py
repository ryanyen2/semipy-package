"""Shared LLM utilities: classification and JSON-oriented calls (OpenAI Responses first, OpenRouter fallback)."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Optional, TypeVar

from semipy.agents.config import get_config

T = TypeVar("T")


def _http_chat_completion(
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


def _openai_responses_text(
    *,
    api_key: str,
    model_id: str,
    prompt: str,
    max_output_tokens: int,
) -> Optional[str]:
    """One-shot text via the Responses API (same as the main agent when using OpenAI)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    resp = client.responses.create(
        model=model_id,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )
    out = getattr(resp, "output_text", None)
    if isinstance(out, str) and out.strip():
        return out
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            t = getattr(block, "text", None)
            if isinstance(t, str) and t:
                parts.append(t)
    joined = "".join(parts).strip()
    return joined or None


def _openrouter_chat_text(
    *,
    api_key: str,
    model_id: str,
    prompt: str,
    max_tokens: int,
) -> Optional[str]:
    return _http_chat_completion(
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


async def classify_with_llm(
    prompt: str,
    parse_fn: Callable[[str], T],
    default: T,
    timeout: float = 15.0,
    *,
    max_tokens: int = 512,
) -> T:
    """
    Try OpenAI Responses API with ``configure(openai_model=...)`` (e.g. gpt-5.4), then OpenRouter
    chat completions with ``validator_model`` when OpenAI is unavailable or returns empty text.

    Parse with ``parse_fn``; return ``default`` on error, empty response, or timeout.
    """
    config = get_config()

    def _try_openai() -> Optional[str]:
        api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        model_id = getattr(config, "openai_model", "gpt-5.4")
        return _openai_responses_text(
            api_key=api_key,
            model_id=model_id,
            prompt=prompt,
            max_output_tokens=max_tokens,
        )

    def _try_openrouter() -> Optional[str]:
        api_key = config.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None
        model_id = getattr(config, "validator_model", None) or getattr(
            config, "openrouter_model", "anthropic/claude-sonnet-4-6"
        )
        return _openrouter_chat_text(
            api_key=api_key,
            model_id=model_id,
            prompt=prompt,
            max_tokens=max_tokens,
        )

    def _sync_call() -> Optional[str]:
        for fn in (_try_openai, _try_openrouter):
            try:
                raw = fn()
                if raw:
                    return raw
            except Exception:
                continue
        return None

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=timeout)
        if raw:
            return parse_fn(raw.strip())
    except (asyncio.TimeoutError, Exception):
        pass
    return default
