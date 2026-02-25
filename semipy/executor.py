"""
Sandbox execution for gist validation: E2B code interpreter or subprocess fallback.

Captures stdout, stderr, and result via __GIST_RESULT__ marker.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    """Result of running a gist in the sandbox."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    result_repr: Optional[str] = None
    error: Optional[str] = None


def _patch_blocking_calls(code: str) -> str:
    """Patch common blocking calls so code can run headless (e.g. plt.show(), input())."""
    if "plt.show()" in code:
        code = code.replace("plt.show()", "plt.close()")
    if "input(" in code:
        code = code.replace("input(", "(lambda _='': _)(")
    return code


def _run_python_subprocess(
    script_path: str,
    script_args: list[str],
    timeout: int,
    cwd: Optional[str] = None,
) -> tuple[str, str, int]:
    """Run a Python script. Returns (stdout, stderr, returncode)."""
    cmd = [sys.executable, script_path] + list(script_args)
    run_kwargs: dict = dict(capture_output=True, text=True, timeout=timeout)
    if cwd:
        cwd_abs = os.path.abspath(os.path.expanduser(cwd))
        if os.path.isdir(cwd_abs):
            run_kwargs["cwd"] = cwd_abs
    try:
        result = subprocess.run(cmd, **run_kwargs)
        return (result.stdout or "", result.stderr or "", result.returncode)
    except subprocess.TimeoutExpired:
        return ("", "Execution timed out", -1)
    except Exception as e:
        return ("", str(e), -1)


def _exec_gist_subprocess(
    gist_source: str,
    timeout: int = 30,
    cwd: Optional[str] = None,
) -> tuple[Optional[object], str, Optional[str]]:
    """
    Execute gist in subprocess. Returns (result_value, stdout, error_or_None).
    Result is read from __GIST_RESULT__ printed by the gist.
    """
    gist_source = _patch_blocking_calls(gist_source)
    marker = "__GIST_RESULT__"
    cleanup: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(gist_source)
            code_path = f.name
            cleanup.append(code_path)
        runner = f"""_locs = {{}}
with open({repr(code_path)}) as _f:
    exec(_f.read(), _locs, _locs)
__out = _locs.get("__GIST_RESULT__")
print({repr(marker)}, repr(__out), flush=True)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(runner)
            path = f.name
            cleanup.append(path)
        stdout, stderr, returncode = _run_python_subprocess(path, [code_path], timeout, cwd=cwd)
        err = None
        if returncode != 0:
            err = stderr or f"Process exited with code {returncode}"
        out = None
        if marker in stdout:
            left, _, right = stdout.partition(marker)
            stdout = left.strip()
            try:
                line = right.strip().split("\n")[0].strip()
                if line.startswith("Error:"):
                    err = line
                else:
                    out = eval(line)
            except Exception:
                pass
        return (out, stdout, err)
    except Exception as e:
        return (None, "", str(e))
    finally:
        for p in cleanup:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


class GistExecutor:
    """Execute gist source in E2B sandbox or subprocess fallback."""

    def __init__(
        self,
        use_e2b: bool = False,
        timeout: int = 30,
        e2b_api_key: Optional[str] = None,
    ) -> None:
        self.use_e2b = use_e2b and bool(e2b_api_key)
        self.timeout = timeout
        self._e2b_api_key = e2b_api_key

    async def execute_async(self, gist_source: str, cwd: Optional[str] = None) -> ExecutionResult:
        """Execute gist asynchronously. Uses E2B if configured, else subprocess in executor."""
        if self.use_e2b and self._e2b_api_key:
            return await self._execute_e2b(gist_source, cwd)
        return self._execute_subprocess_sync(gist_source, cwd)

    def execute_sync(self, gist_source: str, cwd: Optional[str] = None) -> ExecutionResult:
        """Execute gist synchronously (subprocess or E2B via asyncio.run)."""
        if self.use_e2b and self._e2b_api_key:
            import asyncio
            return asyncio.run(self._execute_e2b(gist_source, cwd))
        return self._execute_subprocess_sync(gist_source, cwd)

    def _execute_subprocess_sync(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
    ) -> ExecutionResult:
        value, stdout, err = _exec_gist_subprocess(gist_source, timeout=self.timeout, cwd=cwd)
        return ExecutionResult(
            success=err is None,
            stdout=stdout,
            stderr=err or "",
            result_repr=repr(value) if value is not None else None,
            error=err,
        )

    async def _execute_e2b(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute via E2B AsyncSandbox if available."""
        try:
            from e2b_code_interpreter import AsyncSandbox
        except ImportError:
            return self._execute_subprocess_sync(gist_source, cwd)

        gist_source = _patch_blocking_calls(gist_source)
        try:
            async with AsyncSandbox(api_key=self._e2b_api_key) as sandbox:
                exec_result = await sandbox.run_code(gist_source, timeout=self.timeout)
                stdout = exec_result.logs.stdout or ""
                stderr = exec_result.logs.stderr or ""
                success = not exec_result.error
                result_repr = None
                if exec_result.result and hasattr(exec_result.result, "value"):
                    result_repr = repr(exec_result.result.value)
                return ExecutionResult(
                    success=success,
                    stdout=stdout,
                    stderr=stderr,
                    result_repr=result_repr,
                    error=exec_result.error if exec_result.error else None,
                )
        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result_repr=None,
                error=str(e),
            )
