"""Stable portal identity when the interpreter reports an ephemeral Jupyter kernel path."""
from __future__ import annotations

import os
from pathlib import Path

from semipy.agents.config import get_config


def _path_contains_ipykernel(path: str) -> bool:
    return "ipykernel" in path.replace("\\", "/").lower()


def resolve_portal_anchor(actual_filename: str) -> str:
    """
    Return the path string used for portal session_id, module_name, and persistence.

    Jupyter executes user code from a temporary file such as
    .../ipykernel_*/4052506816.py; its basename changes whenever the kernel restarts, which
    would otherwise create a new empty portal every time. For those paths we anchor to
    ``os.getcwd()`` so all runs in the same working directory share one portal (and
    cross-slot reuse can see prior slots).

    Override with ``configure(session_source=...)`` or environment variable
    ``SEMIPY_SESSION_SOURCE`` (absolute path to a notebook or any stable string path).
    """
    cfg = get_config()
    ss = getattr(cfg, "session_source", None)
    if ss is not None and str(ss).strip():
        return str(Path(str(ss)).expanduser().resolve())
    env = (os.environ.get("SEMIPY_SESSION_SOURCE") or "").strip()
    if env:
        return str(Path(env).expanduser().resolve())

    if not actual_filename or actual_filename == "<unknown>":
        return actual_filename or "<unknown>"

    if _path_contains_ipykernel(actual_filename):
        try:
            return str(Path.cwd().resolve())
        except Exception:
            return actual_filename

    try:
        return str(Path(actual_filename).resolve())
    except Exception:
        return actual_filename
