"""Thin orchestration runtime: shared-loop embedding + centralized model factory.

Two responsibilities, deliberately small:

1. ``embed_run`` -- run a coroutine on semipy's single shared background event loop
   (owned by ``semipy.agents.agent``), so every role and every fan-out
   (``asyncio.gather``) shares one loop instead of spinning up per-call loops.

2. ``make_responses_model`` -- the one place that constructs an OpenAI Responses
   model + settings for a pipeline role. Centralizing this removes the three
   duplicated ``OpenAIResponsesModel(config.openai_model)`` constructions in
   ``generator.py`` / ``decision.py`` / ``steering.py`` and makes per-role model
   selection (``config.model_for_role``) and reasoning settings a single switch.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from semipy.agents.config import get_config


def embed_run(coro: Any) -> Any:
    """Run ``coro`` on the shared background event loop and block for its result.

    Reuses ``semipy.agents.agent._run_async`` (the long-lived daemon loop) rather
    than creating a fresh loop per call, so concurrent roles composed with
    ``asyncio.gather`` run on one loop. Imported lazily to avoid an import cycle
    (``agent`` imports much of the agents package at module load).
    """
    from semipy.agents.agent import _run_async

    return _run_async(coro)


def make_responses_model(
    role: Optional[str] = None,
    *,
    reasoning: bool = False,
) -> tuple[Any, Any] | tuple[None, None]:
    """Build an OpenAI Responses model + settings for a pipeline ``role``.

    Returns ``(model, settings)`` when an API key is configured, or ``(None, None)``
    when it is absent -- callers that require generation (the coder) raise on
    ``None``; best-effort roles (verifier judge, surfacer) fall back to their
    deterministic default. The model id is resolved via ``config.model_for_role``,
    so unset role overrides transparently use ``openai_model``.

    ``reasoning=True`` applies the generator's reasoning settings (medium effort,
    auto summary, reasoning-id continuity); the default is plain settings, matching
    the previous behavior of the decision and steering call sites.
    """
    config = get_config()
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    try:
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )

        model = OpenAIResponsesModel(config.model_for_role(role))
        if reasoning:
            settings = OpenAIResponsesModelSettings(
                openai_reasoning_effort="medium",
                openai_reasoning_summary="auto",
                openai_send_reasoning_ids=True,
            )
        else:
            settings = OpenAIResponsesModelSettings()
        return model, settings
    except Exception:
        return None, None


def make_scoring_model() -> tuple[Any, Any] | tuple[None, None]:
    """Build the OpenAI Responses model + settings for decision-mode candidate scoring.

    Deliberately does NOT go through ``config.model_for_role`` -- that resolves to
    ``openai_model`` (default ``gpt-5.5``) whenever no override is set, and pydantic_ai's
    OpenAI profile always runs ``gpt-5.5`` in reasoning mode, which unconditionally strips
    ``openai_logprobs``/``openai_top_logprobs`` before the request is sent. ``config.
    decision_scoring_model`` names a model (``gpt-5.1``/``gpt-5.2``) that supports
    ``openai_reasoning_effort="none"``, the one bucket that keeps sampling params, so
    logprobs actually survive on the wire. Returns ``(None, None)`` on any failure
    (missing key, import error); the caller treats that as "no score for this candidate."
    """
    config = get_config()
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    try:
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )

        model = OpenAIResponsesModel(config.decision_scoring_model)
        settings = OpenAIResponsesModelSettings(
            openai_reasoning_effort="none",
            openai_logprobs=True,
            openai_top_logprobs=1,
        )
        return model, settings
    except Exception:
        return None, None
