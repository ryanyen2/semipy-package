"""Shared LLM utilities: classification via OpenAI Responses API."""
from __future__ import annotations

import asyncio
from typing import Callable, Optional, TypeVar

from semipy.agents.config import get_config

T = TypeVar("T")


def _openai_responses_text(
    *,
    api_key: str,
    model_id: str,
    prompt: str,
    max_output_tokens: int,
) -> Optional[str]:
    """One-shot text via the Responses API."""
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


async def classify_with_llm(
    prompt: str,
    parse_fn: Callable[[str], T],
    default: T,
    timeout: float = 15.0,
    *,
    max_tokens: int = 512,
) -> T:
    """
    Call OpenAI Responses API with ``openai_model`` and parse the result.

    Returns ``default`` on error, empty response, or timeout.
    Raises ``SemiGenerationError`` if no OpenAI API key is configured.
    """
    from semipy.types import SemiGenerationError

    config = get_config()
    api_key = config.openai_api_key
    if not api_key:
        raise SemiGenerationError(
            "OPENAI_API_KEY is required; set it in your environment or via configure(openai_api_key=...)."
        )
    model_id = config.openai_model

    def _sync_call() -> Optional[str]:
        return _openai_responses_text(
            api_key=api_key,
            model_id=model_id,
            prompt=prompt,
            max_output_tokens=max_tokens,
        )

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=timeout)
        if raw:
            return parse_fn(raw.strip())
    except (asyncio.TimeoutError, Exception):
        pass
    return default
