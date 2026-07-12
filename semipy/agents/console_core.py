"""
Shared Rich console infrastructure for the semiformal pipeline.

Provides the module-level Console singletons and Jupyter detection used by
all other console modules.
"""
from __future__ import annotations

import atexit
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from semipy.agents.console_messages import format_annealing_header, format_annealing_row

_console: Optional[Console] = None
_jupyter_output_console: Optional[Console] = None


def _is_jupyter() -> bool:
    """True if running inside Jupyter (notebook or IPython)."""
    try:
        if "ipykernel" in sys.modules:
            return True
        ipy = sys.modules.get("IPython", None)
        if ipy is not None and getattr(ipy, "get_ipython", None):
            return ipy.get_ipython() is not None
    except Exception:
        pass
    return False


def get_console() -> Console:
    """Return the shared Rich Console (works in terminal, REPL, and Jupyter).
    When in Jupyter and inside jupyter_capture_console(), returns a Console that
    writes to the ipywidgets Output so all output appends in one cell."""
    global _console, _jupyter_output_console
    if _jupyter_output_console is not None:
        return _jupyter_output_console
    if _console is None:
        _console = Console()
    return _console


@contextmanager
def jupyter_capture_console():
    """In Jupyter, send all generation output to a single ipywidgets.Output so it
    appends in one scrollable area instead of creating many output cells.
    No-op when not in Jupyter or when ipywidgets/IPython.display unavailable."""
    global _jupyter_output_console
    if not _is_jupyter():
        yield
        return
    try:
        import ipywidgets as ipw
        from IPython.display import display
    except ImportError:
        yield
        return
    out = ipw.Output()
    display(out)
    prev = _jupyter_output_console
    with out:
        _jupyter_output_console = Console()
        try:
            yield
        finally:
            _jupyter_output_console = prev


# ---------------------------------------------------------------------------
# End-of-run annealing report (R7): one compact table of per-slot ledger
# activity, printed once at process exit. There is no explicit "run finished"
# hook (a user script just calls semi() a few times and exits), so this reads
# every portal in the configured cache_dir at atexit and reports whatever
# ledger entries carry a timestamp at or after ``_RUN_START_TS`` -- no new
# persistence, no change to the execution path.
# ---------------------------------------------------------------------------

_RUN_START_TS = time.time()


def _iter_portal_files(cache_dir: Path) -> list[Path]:
    if not cache_dir.exists():
        return []
    return sorted(cache_dir.glob("*.portal.json"))


def _slot_activity_row(slot_id: str, slot_dict: dict[str, Any], since_ts: float) -> Optional[dict[str, Any]]:
    """One slot's ledger delta since ``since_ts``, or None if it saw no activity."""
    commits = slot_dict.get("commits") or {}
    recent_commits = [c for c in commits.values() if float(c.get("timestamp", 0.0) or 0.0) >= since_ts]
    decision = ""
    if recent_commits:
        latest = max(recent_commits, key=lambda c: float(c.get("timestamp", 0.0) or 0.0))
        decision = str(latest.get("decision", "") or "")

    contract = slot_dict.get("contract") or {}
    cases = contract.get("cases") or {}
    cases_added = sum(1 for c in cases.values() if float(c.get("created_ts", 0.0) or 0.0) >= since_ts)
    quarantines = sum(
        1 for c in cases.values()
        if c.get("status") == "quarantined" and float(c.get("updated_ts", 0.0) or 0.0) >= since_ts
    )

    advisor = slot_dict.get("advisor_state") or {}
    deopts = sum(
        1 for e in (advisor.get("scope_deopts") or [])
        if float(e.get("timestamp", 0.0) or 0.0) >= since_ts
    )

    # Disputes (contract["asserted_properties"], from `semipy assert-decision`)
    # carry no timestamp -- a real gap (out of U4's scope; see decisions/resolve.py's
    # assert_property) -- so this column cannot be windowed by this run yet and is
    # reported as 0 rather than a misleading all-time total.
    disputes = 0

    if not (recent_commits or cases_added or quarantines or deopts or disputes):
        return None
    return {
        "slot_id": slot_id,
        "decision": decision or "-",
        "cases_added": cases_added,
        "deopts": deopts,
        "disputes": disputes,
        "quarantines": quarantines,
    }


def annealing_report_rows(cache_dir: Path, since_ts: float) -> list[dict[str, Any]]:
    """Per-slot ledger deltas since ``since_ts`` (R7): decision, cases added,
    deopts, disputes, quarantines. Only slots with activity in the window are
    included. Reads portal JSON directly off disk -- no live Slot objects needed."""
    rows: list[dict[str, Any]] = []
    for path in _iter_portal_files(cache_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for slot_id, slot_dict in (data.get("slots") or {}).items():
            row = _slot_activity_row(slot_id, slot_dict, since_ts)
            if row is not None:
                rows.append(row)
    return rows


def render_annealing_report(rows: list[dict[str, Any]], *, console: Optional[Console] = None) -> None:
    if not rows:
        return
    c = console or get_console()
    c.print("[dim][semipy][/] [bold]annealing report[/] (since run start)")
    c.print(format_annealing_header())
    for row in rows:
        c.print(format_annealing_row(row))


def print_annealing_report() -> None:
    """atexit entry point: print the annealing report unless disabled via
    ``SemiConfig.annealing_report`` (checked independently of ``verbose`` --
    this is the run's only end-of-run summary, not the per-call stream)."""
    from semipy.agents.config import get_config

    cfg = get_config()
    if not getattr(cfg, "annealing_report", True):
        return
    rows = annealing_report_rows(Path(cfg.cache_dir), _RUN_START_TS)
    render_annealing_report(rows)


atexit.register(print_annealing_report)
