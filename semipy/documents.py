"""
Load document text for semiformal pipelines and tooling.

PDFs are handled here (not in user examples): local extraction via liteparse when
appropriate, and LlamaCloud parsing when layout-heavy or when local extraction is
thin or unavailable. Plain text paths are read as UTF-8.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

Backend = Literal["auto", "liteparse", "llama_cloud"]
LlamaTier = Literal["fast", "cost_effective", "agentic", "agentic_plus"]


def _pdf_via_liteparse(path: Path) -> str:
    from liteparse import LiteParse

    pr = LiteParse().parse(path)
    return (pr.text or "").strip()


def _pdf_via_llama_cloud(path: Path, *, tier: LlamaTier, timeout: float) -> str:
    from llama_cloud import LlamaCloud
    from llama_cloud._polling import PollingError, PollingTimeoutError, poll_until_complete

    api_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLAMA_CLOUD_API_KEY is not set; cannot use LlamaCloud document parsing."
        )

    client = LlamaCloud(api_key=api_key)
    job = client.parsing.create(
        tier=tier,
        version="latest",
        upload_file=p,
    )
    job_id = job.id

    def fetch() -> object:
        return client.parsing.get(
            job_id,
            expand=["markdown_full", "text_full"],
        )

    try:
        result = poll_until_complete(
            get_status_fn=fetch,
            is_complete_fn=lambda r: r.job.status == "COMPLETED",
            is_error_fn=lambda r: r.job.status == "FAILED",
            get_error_message_fn=lambda r: (r.job.error_message or "parse failed"),
            timeout=timeout,
            polling_interval=1.5,
            max_interval=8.0,
        )
    except (PollingError, PollingTimeoutError) as e:
        raise RuntimeError(str(e)) from e

    text = (getattr(result, "text_full", None) or "").strip()
    if not text:
        text = (getattr(result, "markdown_full", None) or "").strip()
    return text


def load_document_text(
    path: str | Path,
    *,
    backend: Backend = "auto",
    layout_heavy: bool = False,
    llama_tier: LlamaTier | None = None,
    min_chars_before_cloud_fallback: int = 80,
    llama_timeout: float = 600.0,
) -> str:
    """
    Return full document text for *path*.

    Non-PDF files are always read as UTF-8 (with replacement).

    PDF strategy:

    - ``backend="liteparse"``: local liteparse only.
    - ``backend="llama_cloud"``: LlamaCloud only (tier from *llama_tier* or
      ``agentic`` when *layout_heavy* else ``cost_effective``).
    - ``backend="auto"``: if *layout_heavy* and LlamaCloud API key is set, use
      LlamaCloud with the tier above; else try liteparse first, then fall back
      to LlamaCloud when text is shorter than *min_chars_before_cloud_fallback*
      or liteparse fails, if the API key is set; otherwise return whatever
      liteparse produced or re-raise.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.casefold() != ".pdf":
        return p.read_text(encoding="utf-8", errors="replace")

    tier: LlamaTier = llama_tier or (
        "agentic" if layout_heavy else "cost_effective"
    )

    if backend == "llama_cloud":
        return _pdf_via_llama_cloud(p, tier=tier, timeout=llama_timeout)

    if backend == "liteparse":
        return _pdf_via_liteparse(p)

    if layout_heavy and os.getenv("LLAMA_CLOUD_API_KEY"):
        return _pdf_via_llama_cloud(p, tier=tier, timeout=llama_timeout)

    local = ""
    lite_failed: Exception | None = None
    try:
        local = _pdf_via_liteparse(p)
    except Exception as e:
        lite_failed = e

    if (
        len(local.strip()) >= min_chars_before_cloud_fallback
        and lite_failed is None
    ):
        return local

    if os.getenv("LLAMA_CLOUD_API_KEY"):
        try:
            return _pdf_via_llama_cloud(
                p,
                tier="cost_effective" if not layout_heavy else tier,
                timeout=llama_timeout,
            )
        except Exception:
            if local.strip():
                return local
            raise

    if lite_failed is not None:
        raise lite_failed
    return local
