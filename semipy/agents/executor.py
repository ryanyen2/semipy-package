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
from typing import Any, Optional


@dataclass
class ExecutionResult:
    """Result of running a gist in the sandbox."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    result_repr: Optional[str] = None
    error: Optional[str] = None


def _parse_gist_result_stdout(stdout: str) -> tuple[str, Optional[str]]:
    """Extract __GIST_RESULT__ line from stdout; return (stdout_before_marker, result_repr)."""
    marker = "__GIST_RESULT__"
    if marker not in stdout:
        return stdout, None
    left, _, right = stdout.partition(marker)
    stdout_clean = left.strip()
    line = right.strip().split("\n")[0].strip() if right.strip() else ""
    if line.startswith("Error:"):
        return stdout_clean, None
    return stdout_clean, line if line else None


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
    """Execute gist source in E2B sandbox or subprocess fallback.

    When using E2B, one sandbox is reused for the whole agent run (all tool calls).
    Closing it after each gist run was causing 'Event loop is closed' on the next
    call. close_async() must be called when the agent run is done (e.g. from
    generate_async finally block).
    """

    def __init__(
        self,
        use_e2b: bool = False,
        timeout: int = 30,
        e2b_api_key: Optional[str] = None,
    ) -> None:
        self.use_e2b = use_e2b and bool(e2b_api_key)
        self.timeout = timeout
        self._e2b_api_key = e2b_api_key
        self._e2b_sandbox: Any = None

    async def close_async(self) -> None:
        """Kill the E2B sandbox if one was created. Call when the agent run is done."""
        if self._e2b_sandbox is None:
            return
        try:
            await self._e2b_sandbox.kill()
        except Exception:
            pass
        self._e2b_sandbox = None

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
        _retrying: bool = False,
    ) -> ExecutionResult:
        """Execute via E2B AsyncSandbox. Reuses one sandbox per executor; on 'Event loop is closed' retries with a fresh sandbox."""
        try:
            from e2b_code_interpreter import AsyncSandbox
        except ImportError:
            return self._execute_subprocess_sync(gist_source, cwd)

        gist_source = _patch_blocking_calls(gist_source)
        try:
            if self._e2b_sandbox is None:
                self._e2b_sandbox = await AsyncSandbox.create(api_key=self._e2b_api_key)
            exec_result = await self._e2b_sandbox.run_code(gist_source, timeout=self.timeout)
            logs = getattr(exec_result, "logs", None)
            stdout_parts = getattr(logs, "stdout", None) if logs else None
            stderr_parts = getattr(logs, "stderr", None) if logs else None
            if stdout_parts is None and hasattr(exec_result, "text"):
                stdout_parts = [getattr(exec_result, "text", "")] or []
            if stdout_parts is None:
                stdout_parts = []
            if stderr_parts is None:
                stderr_parts = []
            stdout = "\n".join(stdout_parts) if isinstance(stdout_parts, list) else (stdout_parts or "")
            stderr = "\n".join(stderr_parts) if isinstance(stderr_parts, list) else (stderr_parts or "")
            stdout, result_repr = _parse_gist_result_stdout(stdout)
            if result_repr is None:
                res = getattr(exec_result, "result", None)
                if res is not None and hasattr(res, "value"):
                    result_repr = repr(res.value)
            err = getattr(exec_result, "error", None)
            if err is not None:
                err_name = getattr(err, "name", "Error")
                err_value = getattr(err, "value", str(err))
                err_tb = getattr(err, "traceback", None)
                error_msg = f"{err_name}: {err_value}"
                if err_tb:
                    error_msg += "\n" + (err_tb if isinstance(err_tb, str) else "\n".join(err_tb))
                return ExecutionResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    result_repr=result_repr,
                    error=error_msg[:2000],
                )
            return ExecutionResult(
                success=True,
                stdout=stdout,
                stderr=stderr,
                result_repr=result_repr,
                error=None,
            )
        except Exception as e:
            err_msg = str(e)
            if not _retrying and "Event loop is closed" in err_msg:
                self._e2b_sandbox = None
                try:
                    return await self._execute_e2b(gist_source, cwd=cwd, _retrying=True)
                except Exception as e2:
                    err_msg = str(e2)
            if "Event loop is closed" in err_msg:
                return self._execute_subprocess_sync(gist_source, cwd)
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result_repr=None,
                error=err_msg,
            )
