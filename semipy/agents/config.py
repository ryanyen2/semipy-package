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
    propagation_mode: str = "eager"
    llm_impact_analysis: bool = True
    max_eager_cascade_depth: int = 5
    abstraction_discovery: bool = True
    sleep_phase_trigger_count: int = 10
    min_pattern_frequency: int = 3
    cocoindex_enabled: bool = False
    cocoindex_db_url: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    resolution_async_verify: bool = False
    session_source: Optional[str] = None
    # Console UX: normal = tools + peek rolling tail; debug = same stream UI + verbose tool lines; quiet = summary only
    console_verbosity: str = "normal"
    console_peek_lines: int = 4
    console_show_elapsed: bool = False
    console_timeline: bool = True
    # Internal document materialization for PDF paths passed into slots (see semipy.documents)
    document_pdf_backend: str = "auto"
    document_layout_heavy: bool = False

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
        propagation_mode: Optional[str] = None,
        llm_impact_analysis: Optional[bool] = None,
        max_eager_cascade_depth: Optional[int] = None,
        abstraction_discovery: Optional[bool] = None,
        sleep_phase_trigger_count: Optional[int] = None,
        min_pattern_frequency: Optional[int] = None,
        cocoindex_enabled: Optional[bool] = None,
        cocoindex_db_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
        resolution_async_verify: Optional[bool] = None,
        session_source: Optional[str] = None,
        console_verbosity: Optional[str] = None,
        console_peek_lines: Optional[int] = None,
        console_show_elapsed: Optional[bool] = None,
        console_timeline: Optional[bool] = None,
        document_pdf_backend: Optional[str] = None,
        document_layout_heavy: Optional[bool] = None,
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
        if propagation_mode is not None:
            self.propagation_mode = propagation_mode
        if llm_impact_analysis is not None:
            self.llm_impact_analysis = llm_impact_analysis
        if max_eager_cascade_depth is not None:
            self.max_eager_cascade_depth = max_eager_cascade_depth
        if abstraction_discovery is not None:
            self.abstraction_discovery = abstraction_discovery
        if sleep_phase_trigger_count is not None:
            self.sleep_phase_trigger_count = sleep_phase_trigger_count
        if min_pattern_frequency is not None:
            self.min_pattern_frequency = min_pattern_frequency
        if cocoindex_enabled is not None:
            self.cocoindex_enabled = cocoindex_enabled
        if cocoindex_db_url is not None:
            self.cocoindex_db_url = cocoindex_db_url
        if embedding_model is not None:
            self.embedding_model = embedding_model
        if resolution_async_verify is not None:
            self.resolution_async_verify = resolution_async_verify
        if session_source is not None:
            self.session_source = session_source
        if console_verbosity is not None:
            self.console_verbosity = console_verbosity
        if console_peek_lines is not None:
            self.console_peek_lines = console_peek_lines
        if console_show_elapsed is not None:
            self.console_show_elapsed = console_show_elapsed
        if console_timeline is not None:
            self.console_timeline = console_timeline
        if document_pdf_backend is not None:
            self.document_pdf_backend = document_pdf_backend
        if document_layout_heavy is not None:
            self.document_layout_heavy = document_layout_heavy


def effective_stream_display_mode(config: SemiConfig) -> str:
    """
    How model streaming is shown: none | peek | full.

    ``debug`` verbosity uses **peek** (rolling tail + timeline), not ``full`` panels, so
    reasoning/thinking deltas stream during generation. Mapping ``debug`` to ``full`` left
    no peek sink and only flushed reasoning at part boundaries, which looked hung on long
    thinking streams. Tool-line verbosity still comes from ``console_verbosity == "debug"``.
    """
    if not config.stream:
        return "none"
    v = (config.console_verbosity or "normal").lower()
    if v == "debug":
        return "peek"
    if v == "quiet":
        return "none"
    return "peek"


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
