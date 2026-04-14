"""TraceStore — structured JSONL operational log for each slot resolution run.

Replaces SEMIPY_PIPELINE_TRACE env-variable dump with a queryable, structured file.
Each entry records the slot_id, decision, attempt number, elapsed time, validation
failure kind, and a serialized CommitmentRecord when available.

Storage: {cache_dir}/trace.jsonl (append-only JSONL).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from semipy.models import CommitmentRecord
    from semipy.types import Decision, ValidationResult


class TraceStore:
    """Append-only structured log for slot resolution runs."""

    def __init__(self, cache_dir: Path) -> None:
        self._path = cache_dir / "trace.jsonl"

    def record_run(
        self,
        *,
        slot_id: str,
        decision: "Decision",
        attempt: int,
        commitment_record: Optional["CommitmentRecord"] = None,
        validation_result: Optional["ValidationResult"] = None,
        elapsed_s: float = 0.0,
        source_file: str = "",
    ) -> None:
        """Append one run record to trace.jsonl.

        Non-blocking: silently discards on write failure to never interrupt the main flow.
        """
        try:
            entry: dict = {
                "ts": time.time(),
                "slot_id": slot_id,
                "decision": decision.value if hasattr(decision, "value") else str(decision),
                "attempt": attempt,
                "elapsed_s": round(elapsed_s, 3),
                "source_file": source_file,
            }
            if validation_result is not None:
                entry["validation"] = {
                    "passed": validation_result.passed,
                    "failure_kind": getattr(validation_result, "failure_kind", None),
                    "error": validation_result.error_message[:200] if validation_result.error_message else None,
                }
            if commitment_record is not None:
                entry["commitment"] = {
                    "goal": commitment_record.goal,
                    "givens": commitment_record.givens[:3],
                    "decision_points": commitment_record.decision_points[:3],
                    "checks_performed": commitment_record.checks_performed[:2],
                }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def recent_runs(self, slot_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Read recent trace entries, optionally filtered by slot_id."""
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            entries = []
            for line in reversed(lines[-200:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if slot_id is None or entry.get("slot_id") == slot_id:
                        entries.append(entry)
                        if len(entries) >= limit:
                            break
                except json.JSONDecodeError:
                    continue
            return entries
        except Exception:
            return []
