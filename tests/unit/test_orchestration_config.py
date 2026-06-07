"""U1: per-role model config + thin orchestration runtime. Runs fully offline."""
from __future__ import annotations

import asyncio

import pytest

from semipy.agents.config import SemiConfig, get_config
from semipy.orchestration.runtime import embed_run, make_responses_model


@pytest.fixture
def fresh_config(monkeypatch):
    """Install a fresh SemiConfig singleton with no API key, restored after the test."""
    import semipy.agents.config as cfg_mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    original = cfg_mod._config
    new = SemiConfig()
    new.openai_api_key = None
    cfg_mod._config = new
    try:
        yield new
    finally:
        cfg_mod._config = original


# --- model_for_role -------------------------------------------------------

def test_model_for_role_defaults_to_openai_model():
    cfg = SemiConfig()
    cfg.openai_model = "gpt-test"
    assert cfg.model_for_role(None) == "gpt-test"
    for role in ("coder", "verifier", "explorer", "surfacer", "orchestrator"):
        assert cfg.model_for_role(role) == "gpt-test"


def test_model_for_role_uses_override_when_set():
    cfg = SemiConfig()
    cfg.openai_model = "gpt-base"
    cfg.verifier_model = "gpt-strong"
    assert cfg.model_for_role("verifier") == "gpt-strong"
    # An unset role still falls back to the base model.
    assert cfg.model_for_role("coder") == "gpt-base"


def test_configure_overrides_role_models():
    from semipy.agents.config import configure

    original_model = get_config().verifier_model
    original_samples = get_config().verifier_vote_samples
    try:
        configure(verifier_model="gpt-judge", verifier_vote_samples=5)
        assert get_config().model_for_role("verifier") == "gpt-judge"
        assert get_config().verifier_vote_samples == 5
    finally:
        # Restore BOTH mutated fields, not just the model (the leak T1 fix).
        get_config().verifier_model = original_model
        get_config().verifier_vote_samples = original_samples


def test_verifier_vote_samples_default():
    assert SemiConfig().verifier_vote_samples == 3


def test_judge_timeout_default():
    # Hard per-call judge timeout so a stalled call can't block the shared loop.
    assert SemiConfig().judge_timeout == 60


def test_version_checker_model_is_its_own_knob():
    cfg = SemiConfig()
    cfg.openai_model = "gpt-base"
    # Falls back to the global model until set.
    assert cfg.model_for_role("version_checker") == "gpt-base"
    cfg.version_checker_model = "gpt-router"
    assert cfg.model_for_role("version_checker") == "gpt-router"
    # Independent of the verifier (alignment) model.
    assert cfg.model_for_role("verifier") == "gpt-base"


# --- make_responses_model -------------------------------------------------

def test_make_responses_model_returns_none_without_key(fresh_config):
    model, settings = make_responses_model("coder", reasoning=True)
    assert model is None and settings is None


def test_make_responses_model_builds_model_with_key(fresh_config, monkeypatch):
    # pydantic_ai's OpenAIResponsesModel reads OPENAI_API_KEY from the env at
    # construction; a dummy env key is enough to build the object (no network until
    # .run()). This mirrors how the real call sites construct the model.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    fresh_config.openai_api_key = "sk-test-dummy"
    fresh_config.openai_model = "gpt-5.5"
    model, settings = make_responses_model("verifier")
    assert model is not None and settings is not None


# --- embed_run ------------------------------------------------------------

def test_embed_run_returns_result():
    async def _coro():
        return 41 + 1

    assert embed_run(_coro()) == 42


def test_embed_run_reuses_one_shared_loop():
    """Two embed_run calls execute on the same background loop (no per-call loop)."""

    async def _which_loop():
        return id(asyncio.get_running_loop())

    first = embed_run(_which_loop())
    second = embed_run(_which_loop())
    assert first == second


# --- public API unchanged -------------------------------------------------

def test_public_api_surface_unchanged():
    import semipy

    for name in ("semiformal", "semi", "configure", "get_config", "Decision"):
        assert hasattr(semipy, name), name
