"""Portal anchor: Jupyter kernel paths map to cwd so session_id stays stable."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from semipy.session_anchor import resolve_portal_anchor


def test_ipykernel_path_anchors_to_cwd() -> None:
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            a = resolve_portal_anchor("/var/folders/x/ipykernel_48303/4052506816.py")
            assert Path(a).resolve() == Path(tmp).resolve()
        finally:
            os.chdir(old)


def test_normal_script_not_rewritten_to_cwd() -> None:
    p = "/some/project/analysis.py"
    a = resolve_portal_anchor(p)
    assert "ipykernel" not in a.lower()
    assert a.endswith("analysis.py")
