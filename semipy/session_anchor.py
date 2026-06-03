"""Stable portal identity when the interpreter reports an ephemeral Jupyter kernel path."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from semipy.agents.config import get_config

_DEFAULT_CACHE_DIR_NAME = ".semiformal"


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


def _nearest_semiformal_dir(start: Path) -> Optional[Path]:
    """Walk up from ``start`` to the nearest existing ``.semiformal/`` directory."""
    try:
        cur = start.resolve()
    except Exception:
        return None
    for d in [cur, *cur.parents]:
        candidate = d / _DEFAULT_CACHE_DIR_NAME
        if candidate.is_dir():
            return candidate
    return None


def resolve_project(source_file: str, cache_dir: Path) -> tuple[Path, Path]:
    """Resolve ``(cache_dir, project_root)`` for a source file.

    A *project* is the folder tree rooted at the nearest ancestor ``.semiformal/``
    directory (git-style discovery). Every source file under that root resolves to
    the same project, so they share one portal and one dispatch module.

    - If ``cache_dir`` is explicitly set (absolute, or anything other than the bare
      default ``.semiformal``), it is honored verbatim; the project root is its parent.
    - Otherwise the project anchor is ``resolve_portal_anchor(source_file)`` (which
      already applies the ``session_source`` / ``SEMIPY_SESSION_SOURCE`` /
      ipykernel->cwd rules). We walk up from there to the nearest existing
      ``.semiformal/``. If none exists, the project root is the current working
      directory and the cache dir is ``<cwd>/.semiformal`` (created on first save).
    """
    cache_dir = Path(cache_dir)
    is_default = (
        not cache_dir.is_absolute()
        and cache_dir.name == _DEFAULT_CACHE_DIR_NAME
        and len(cache_dir.parts) == 1
    )
    if not is_default:
        resolved = cache_dir.expanduser()
        try:
            project_root = resolved.resolve().parent
        except Exception:
            project_root = resolved.parent
        return resolved, project_root

    anchor = resolve_portal_anchor(source_file)
    anchor_path = Path(anchor)
    start = anchor_path if anchor_path.is_dir() else anchor_path.parent
    found = _nearest_semiformal_dir(start)
    if found is not None:
        return found, found.parent

    try:
        project_root = Path.cwd().resolve()
    except Exception:
        project_root = Path.cwd()
    return project_root / _DEFAULT_CACHE_DIR_NAME, project_root
