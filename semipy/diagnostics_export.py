"""Append structured diagnostics for editor consumption (.semiformal/diagnostics.json)."""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from semipy.types import SemiCallError, SlotSpec


def _diagnostics_path(cache_dir: Path) -> Path:
    return cache_dir / "diagnostics.json"


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("entries")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_diagnostics(cache_dir: Path, slot_id: str) -> None:
    path = _diagnostics_path(cache_dir)
    entries = _read_entries(path)
    kept = [e for e in entries if str(e.get("slot_id", "")) != slot_id]
    if len(kept) != len(entries):
        _write_entries(path, kept)


def export_diagnostic(
    cache_dir: Path,
    slot_id: str,
    *,
    source_file: str,
    source_line_start: int,
    source_line_end: int,
    severity: str,
    message: str,
    generated_path: str,
    generated_line_range: tuple[int, int],
    code: str = "semi-call-error",
) -> None:
    path = _diagnostics_path(cache_dir)
    entries = _read_entries(path)
    entries = [e for e in entries if str(e.get("slot_id", "")) != slot_id]
    entries.append(
        {
            "slot_id": slot_id,
            "source_file": source_file,
            "source_line_start": int(source_line_start),
            "source_line_end": int(source_line_end),
            "severity": severity,
            "message": message,
            "generated_path": generated_path,
            "generated_line_range": [int(generated_line_range[0]), int(generated_line_range[1])],
            "code": code,
        }
    )
    _write_entries(path, entries)


def export_from_semi_call_error(
    cache_dir: Path,
    slot_spec: SlotSpec,
    err: SemiCallError,
) -> None:
    fn, ln, _ = slot_spec.source_span
    msg = str(err)
    cause = err.__cause__
    if cause is not None:
        msg = "".join(traceback.format_exception_only(type(cause), cause)).strip()
    gp = err.generated_path or ""
    lr = err.line_range if err.line_range and err.line_range[1] else (0, 0)
    export_diagnostic(
        cache_dir,
        slot_spec.slot_id,
        source_file=fn,
        source_line_start=int(ln),
        source_line_end=int(ln),
        severity="error",
        message=msg,
        generated_path=gp,
        generated_line_range=(int(lr[0]), int(lr[1])),
        code="semi-call-error",
    )
