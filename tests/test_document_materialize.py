from __future__ import annotations

from pathlib import Path

import pytest


def test_materialize_top_level_pdf_path_str(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from semipy.documents import materialize_runtime_document_inputs

    def _fake_load(path: Path, **kwargs: object) -> str:
        assert path.suffix.casefold() == ".pdf"
        return "FAKE_TEXT"

    monkeypatch.setattr("semipy.documents.load_document_text", _fake_load)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.1\n")

    out = materialize_runtime_document_inputs({"doc": str(pdf)})
    assert out["doc"] == "FAKE_TEXT"


def test_materialize_self_attribute_pdf_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from semipy.documents import materialize_runtime_document_inputs

    monkeypatch.setattr(
        "semipy.documents.load_document_text",
        lambda path, **kwargs: "INNARDS",
    )
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.1\n")

    class Holder:
        def __init__(self) -> None:
            self.agreement_path = pdf

    h = Holder()
    out = materialize_runtime_document_inputs({"self": h})
    assert out["self"] is h
    assert h.agreement_path == "INNARDS"
