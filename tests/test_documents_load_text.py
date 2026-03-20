from __future__ import annotations

from pathlib import Path

from semipy.documents import load_document_text


def test_load_non_pdf_utf8(tmp_path: Path) -> None:
    p = tmp_path / "note.txt"
    p.write_text("alpha\nbeta\n", encoding="utf-8")
    assert load_document_text(p) == "alpha\nbeta\n"
