"""
Sandbox execution for gist validation via E2B code interpreter.

Captures stdout, stderr, and result via __GIST_RESULT__ marker.
E2B is the only supported execution substrate. Set E2B_API_KEY in the
environment or via configure(e2b_api_key=...) before generating any slot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ExecutionResult:
    """Result of running a gist in the E2B sandbox."""

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


class GistExecutor:
    """Execute gist source in the E2B sandbox.

    One sandbox is reused for the whole agent run (all tool calls).
    close_async() must be called when the agent run is done (e.g. from
    generate_async finally block).
    """

    def __init__(
        self,
        timeout: int = 30,
        e2b_api_key: Optional[str] = None,
    ) -> None:
        from semipy.types import SemiGenerationError

        if not e2b_api_key:
            raise SemiGenerationError(
                "E2B_API_KEY is required. Set it in your environment or via "
                "configure(e2b_api_key=...) before generating any slot."
            )
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

    async def execute_async(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute gist asynchronously in E2B."""
        return await self._execute_e2b(gist_source, user_source_path=user_source_path)

    async def execute_action_program_async(self, composed_code: str) -> str:
        """Run a composed action program (preamble + model code) in E2B.

        The program must print a JSON-serializable dict as its last stdout line.
        Returns a JSON string with keys matching ObservationBundle, or a JSON string
        with action_errors populated if no valid JSON output was produced.
        """
        import json as _json

        result = await self._execute_e2b(composed_code)
        if not result.success and not result.stdout:
            return _json.dumps({"action_errors": [result.error or "E2B execution failed"]})

        raw = (result.stdout or "").strip()
        # Try parsing the last non-empty line as JSON
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
        errors.append("Action program produced no JSON output. Ensure the program ends with print(json.dumps(result_dict)).")
        return _json.dumps({"action_errors": errors, "stdout_preview": raw[:500]})

    def execute_sync(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute gist synchronously (blocks via asyncio.run)."""
        return asyncio.run(self._execute_e2b(gist_source, user_source_path=user_source_path))

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
                return await self._execute_e2b(
                    gist_source, user_source_path=user_source_path, _retrying=True
                )
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result_repr=None,
                error=err_msg,
            )
