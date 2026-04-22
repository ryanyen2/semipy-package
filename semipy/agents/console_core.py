"""
Shared Rich console infrastructure for the semiformal pipeline.

Provides the module-level Console singletons and Jupyter detection used by
all other console modules.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Optional

from rich.console import Console

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
