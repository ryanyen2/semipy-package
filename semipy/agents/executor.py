"""
Gist execution for validation during code generation.

Runs generated code either in an E2B sandbox (when e2b-code-interpreter is
installed and E2B_API_KEY is set) or in a local subprocess (fallback).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Optional


def subprocess_env_with_user_path() -> dict[str, str]:
    """Environment for a gist subprocess that mirrors the parent's import path.

    A subprocess running a temp file only gets the temp file's directory on
    ``sys.path`` -- so a user's sibling project modules (e.g. a ``domain.py`` that
    the ``@semiformal`` code imports for its types) are not importable, even though
    they import fine in the user's own process. Propagating the parent interpreter's
    ``sys.path`` via ``PYTHONPATH`` makes ``from my_module import T`` resolve in the
    gist exactly as it does where the user ran their program. This is the general
    fix for multi-file / package projects; it does not special-case any module.
    """
    env = dict(os.environ)
    paths: list[str] = []
    for p in sys.path:
        p = p or os.getcwd()  # '' means cwd
        if os.path.isdir(p) and p not in paths:
            paths.append(p)
    existing = env.get("PYTHONPATH", "")
    if existing:
        paths.append(existing)
    if paths:
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    return env


@dataclass
class ExecutionResult:
    """Result of running a gist."""

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


def _e2b_available() -> bool:
    try:
        import importlib
        importlib.import_module("e2b_code_interpreter")
        return True
    except ImportError:
        return False


class GistExecutor:
    """Execute gist source for validation.

    Uses E2B sandbox when `e2b_api_key` is provided and `e2b-code-interpreter`
    is installed. Falls back to a local subprocess automatically.
    """

    def __init__(
        self,
        timeout: int = 30,
        e2b_api_key: Optional[str] = None,
    ) -> None:
        self.timeout = timeout
        self._e2b_api_key = e2b_api_key
        self._e2b_sandbox: Any = None
        self._use_e2b = bool(e2b_api_key) and _e2b_available()

    async def close_async(self) -> None:
        """Kill the E2B sandbox if one was created."""
        if self._e2b_sandbox is None:
            return
        try:
            await self._e2b_sandbox.kill()
        except Exception:
            pass
        self._e2b_sandbox = None

    async def execute_async(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        if self._use_e2b:
            return await self._execute_e2b(gist_source, user_source_path=user_source_path)
        return await self._execute_subprocess(gist_source, cwd=cwd)

    async def execute_action_program_async(self, composed_code: str) -> str:
        """Run a composed action program; returns a JSON string of observation keys."""
        import json as _json

        if self._use_e2b:
            result = await self._execute_e2b(composed_code)
        else:
            result = await self._execute_subprocess(composed_code)

        if not result.success and not result.stdout:
            return _json.dumps({"action_errors": [result.error or "execution failed"]})

        raw = (result.stdout or "").strip()
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = _json.loads(line)
                if isinstance(parsed, dict):
                    if result.stderr:
                        parsed.setdefault("action_errors", [])
                        if result.stderr not in parsed["action_errors"]:
                            parsed["action_errors"].append(result.stderr[:500])
                    return _json.dumps(parsed)
            except (_json.JSONDecodeError, ValueError):
                continue

        errors: list[str] = []
        if result.error:
            errors.append(result.error[:1000])
        if result.stderr:
            errors.append(result.stderr[:500])
        errors.append("Action program produced no JSON output.")
        return _json.dumps({"action_errors": errors, "stdout_preview": raw[:500]})

    def execute_sync(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute synchronously (blocks)."""
        return asyncio.run(self.execute_async(gist_source, cwd=cwd, user_source_path=user_source_path))

    # ── subprocess backend ──────────────────────────────────────────────────

    async def _execute_subprocess(
        self,
        source: str,
        cwd: Optional[str] = None,
    ) -> ExecutionResult:
        """Run source in a local subprocess via a temp file."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._execute_subprocess_sync, source, cwd)

    def _execute_subprocess_sync(
        self,
        source: str,
        cwd: Optional[str] = None,
    ) -> ExecutionResult:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
            f.write(source)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=cwd,
                env=subprocess_env_with_user_path(),
            )
            stdout, result_repr = _parse_gist_result_stdout(proc.stdout or "")
            if proc.returncode != 0:
                return ExecutionResult(
                    success=False,
                    stdout=stdout,
                    stderr=proc.stderr or "",
                    result_repr=result_repr,
                    error=(proc.stderr or "").strip()[:2000] or f"exit code {proc.returncode}",
                )
            return ExecutionResult(
                success=True,
                stdout=stdout,
                stderr=proc.stderr or "",
                result_repr=result_repr,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(success=False, error=f"execution timed out after {self.timeout}s")
        except Exception as exc:
            return ExecutionResult(success=False, error=str(exc))
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── E2B backend ─────────────────────────────────────────────────────────

    async def _execute_e2b(
        self,
        gist_source: str,
        user_source_path: Optional[str] = None,
        _retrying: bool = False,
    ) -> ExecutionResult:
        """Execute via E2B AsyncSandbox. Reuses one sandbox per executor."""
        from e2b_code_interpreter import AsyncSandbox

        try:
            if self._e2b_sandbox is None:
                self._e2b_sandbox = await AsyncSandbox.create(api_key=self._e2b_api_key)
            exec_result = await self._e2b_sandbox.run_code(gist_source, timeout=self.timeout)
            logs = getattr(exec_result, "logs", None)
            stdout_parts = getattr(logs, "stdout", None) if logs else None
            stderr_parts = getattr(logs, "stderr", None) if logs else None
            if stdout_parts is None and hasattr(exec_result, "text"):
                stdout_parts = [getattr(exec_result, "text", "")] or []
            stdout_parts = stdout_parts or []
            stderr_parts = stderr_parts or []
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
            return ExecutionResult(success=True, stdout=stdout, stderr=stderr, result_repr=result_repr)
        except Exception as e:
            err_msg = str(e)
            if not _retrying and "Event loop is closed" in err_msg:
                self._e2b_sandbox = None
                return await self._execute_e2b(gist_source, user_source_path=user_source_path, _retrying=True)
            return ExecutionResult(success=False, stdout="", stderr="", error=err_msg)
