"""
Document loading for internal use: agent tools, slot input materialization, tests.

Not part of the public ``semipy`` package surface (see ``__init__.py``). PDFs use
liteparse and/or LlamaCloud; plain text paths are read as UTF-8.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Literal

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
        upload_file=path,
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


def _normalize_document_backend(name: str) -> Backend:
    b = (name or "auto").strip().casefold()
    if b == "liteparse":
        return "liteparse"
    if b in ("llama_cloud", "llama-cloud"):
        return "llama_cloud"
    return "auto"


def materialize_value_if_document_path(
    val: Any,
    *,
    backend: Backend | None = None,
    layout_heavy: bool | None = None,
) -> Any:
    """
    If *val* is a ``Path`` or a string path to an existing ``.pdf`` file, return
    extracted text via ``load_document_text``; otherwise return *val* unchanged.
    """
    from semipy.agents.config import get_config

    cfg = get_config()
    bk: Backend = backend if backend is not None else _normalize_document_backend(
        str(getattr(cfg, "document_pdf_backend", "auto"))
    )
    lh = (
        layout_heavy
        if layout_heavy is not None
        else bool(getattr(cfg, "document_layout_heavy", False))
    )

    p: Path | None = None
    if isinstance(val, Path):
        try:
            p = val.expanduser().resolve()
        except OSError:
            return val
    elif isinstance(val, str):
        s = val.strip()
        if not s:
            return val
        try:
            cand = Path(s).expanduser().resolve()
        except OSError:
            return val
        if cand.suffix.casefold() == ".pdf" and cand.exists():
            p = cand
    if p is None:
        return val
    if p.suffix.casefold() != ".pdf" or not p.exists():
        return val
    return load_document_text(p, backend=bk, layout_heavy=lh)


def _materialize_pdf_attributes_on_object(
    obj: Any,
    seen: set[int],
    *,
    backend: Backend,
    layout_heavy: bool,
) -> None:
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            try:
                v = getattr(obj, f.name)
            except Exception:
                continue
            nv = materialize_value_if_document_path(
                v, backend=backend, layout_heavy=layout_heavy
            )
            if nv is not v:
                setattr(obj, f.name, nv)
        return

    od = getattr(obj, "__dict__", None)
    if isinstance(od, dict):
        for k in list(od.keys()):
            v = od[k]
            nv = materialize_value_if_document_path(
                v, backend=backend, layout_heavy=layout_heavy
            )
            if nv is not v:
                setattr(obj, k, nv)


def materialize_runtime_document_inputs(runtime_values: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve PDF paths to text before slot resolution and generated-function calls.

    Top-level slot kwargs that are PDF paths become document strings. For the
    ``self`` argument, instance attributes holding PDF paths are replaced in place
    with extracted text so generated slots see agreement text without calling
    ``load_document_text`` in user code.
    """
    from semipy.agents.config import get_config

    cfg = get_config()
    bk = _normalize_document_backend(str(getattr(cfg, "document_pdf_backend", "auto")))
    lh = bool(getattr(cfg, "document_layout_heavy", False))
    seen: set[int] = set()
    out = dict(runtime_values)
    if out.get("self") is not None:
        _materialize_pdf_attributes_on_object(out["self"], seen, backend=bk, layout_heavy=lh)
    for k, v in list(out.items()):
        if k == "self":
            continue
        out[k] = materialize_value_if_document_path(v, backend=bk, layout_heavy=lh)
    return out
