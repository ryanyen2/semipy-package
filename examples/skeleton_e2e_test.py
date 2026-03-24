"""
End-to-end check for `#<` skeleton surfacing after GENERATE/ADAPT.

Requires OPENAI_API_KEY (or OpenRouter) in the environment; uses the same pydantic_ai
backend selection as ``semipy/agents/generator.py``.

Usage::

    cd /path/to/semipy-package
    uv run python examples/skeleton_e2e_test.py

Phase 2 (edit first `#<` to `#>`, clear cache, re-invoke in a fresh interpreter)::

    uv run python examples/skeleton_e2e_test.py phase2
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from semipy import configure, semiformal

REPO = Path(__file__).resolve().parents[1]
_CACHE = REPO / ".semiformal-skeleton-e2e"
_SESSION = str((REPO / "examples").resolve())
_THIS = Path(__file__).resolve()

if __name__ == "__main__" and not sys.argv[1:]:
    shutil.rmtree(_CACHE, ignore_errors=True)

configure(
    cache_dir=str(_CACHE),
    session_source=_SESSION,
    verbose=True,
)


@semiformal
def skeleton_probe(date_str: str) -> str:
    #< [Task] Infer a workable input date pattern and format accepted dates as "%b %Y".
    #< [Given] The implementation tries common date layouts seen in session inputs.
    input_pattern = ...  #> infer strptime pattern from observed formats in this session.
    #< [Then] The selected pattern feeds a stable month-year output representation.
    output_pattern = "%b %Y"
    #< [When] A parsed date has year greater than 2026, still return formatted output.
    if ...:  #> year after 2026
        #< [Then] The result is the parsed date rendered with the output pattern.
        return ...  #> formatted string
    else:
        #< [But] If the year is not after 2026, reparse with the inferred pattern and format.
        #< [Verify] Parsing failures or empty inputs resolve outside this skeleton before formatting.
        return datetime.strptime(str(date_str), input_pattern).strftime(output_pattern)


def _skeleton_lines_in_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("#<")]


def _wait_for_skeleton(*, max_wait_s: float = 120.0, poll_s: float = 2.0) -> list[str]:
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        lines = _skeleton_lines_in_file(_THIS)
        if lines:
            return lines
        time.sleep(poll_s)
    return _skeleton_lines_in_file(_THIS)


def _phase1() -> None:
    _ = skeleton_probe("03/15/2025")
    lines = _wait_for_skeleton()
    print("skeleton_line_count", len(lines))
    for ln in lines[:16]:
        print(ln)
    if not lines:
        raise SystemExit("expected at least one #< line in skeleton_e2e_test.py")
    print("phase1_ok")


def _phase2_edit_and_reinvoke() -> None:
    text = _THIS.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    changed = False
    for line in lines:
        s = line.lstrip()
        if not changed and s.startswith("#<"):
            indent = len(line) - len(s)
            rest = s[2:].lstrip()
            out.append(line[:indent] + "#> " + rest)
            if not out[-1].endswith("\n"):
                out[-1] += "\n"
            changed = True
        else:
            out.append(line)
    if not changed:
        raise SystemExit("phase2: no #< line found to convert to #>")
    _THIS.write_text("".join(out), encoding="utf-8")
    shutil.rmtree(_CACHE, ignore_errors=True)

    subprocess.run(
        [sys.executable, str(_THIS), "invoke"],
        cwd=str(REPO),
        check=True,
    )
    after = _wait_for_skeleton()
    print("skeleton_line_count_after_regen", len(after))
    for ln in after[:16]:
        print(ln)
    print("phase2_ok")


def _invoke_only() -> None:
    _ = skeleton_probe("03/16/2025")
    _wait_for_skeleton()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "invoke":
        _invoke_only()
    elif len(sys.argv) > 1 and sys.argv[1] == "phase2":
        _phase2_edit_and_reinvoke()
    else:
        _phase1()
