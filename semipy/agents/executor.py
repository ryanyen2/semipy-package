"""
Gist execution for validation during code generation.

Runs generated code in a configured backend:
- Docker/Jupyter kernel when requested
- E2B sandbox when available and configured
- local subprocess fallback
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
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


def _find_project_root(start: Path) -> Path:
    """Find a likely project root by walking upward from a file or directory."""
    start = start.expanduser().resolve()
    current = start if start.is_dir() else start.parent
    markers = (
        ".git",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "uv.lock",
    )
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return current


def _split_config_paths(*values: str) -> list[str]:
    """Split comma-separated path config values while preserving order."""
    paths: list[str] = []
    for value in values:
        for path in value.split(","):
            path = path.strip()
            if path and path not in paths:
                paths.append(path)
    return paths


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
        backend: str = "auto",
        kernel_image_name: str = "kernel-gateway-demo",
        kernel_container_name: str = "semipy-kernel-container",
        kernel_host: str = "127.0.0.1",
        kernel_port: int = 8888,
        kernel_required_packages: Optional[list[str]] = None,
        kernel_extra_mounts: Optional[list[str]] = None,
        kernel_reuse_container: bool = False,
    ) -> None:
        self.timeout = timeout
        self.backend = (backend or "auto").lower()
        self._e2b_api_key = e2b_api_key
        self._e2b_sandbox: Any = None
        self._use_e2b = self.backend in {"auto", "e2b"} and bool(e2b_api_key) and _e2b_available()
        self._kernel_image_name = kernel_image_name
        self._kernel_container_name = kernel_container_name
        self._kernel_host = kernel_host
        self._kernel_port = kernel_port
        self._kernel_required_packages = kernel_required_packages or []
        if kernel_extra_mounts is None:
            try:
                from semipy.agents.config import get_config

                config = get_config()
                kernel_extra_mounts = _split_config_paths(
                    config.kernel_extra_mounts,
                    config.kernel_allowed_folders,
                )
            except Exception:
                kernel_extra_mounts = []
        self._kernel_extra_mounts = kernel_extra_mounts
        self._kernel_reuse_container = kernel_reuse_container
        self._kernel_executor: Any = None
        self._kernel_workspace_dir: Optional[Path] = None
        self._kernel_mounts: tuple[Path, ...] = ()

    async def close_async(self) -> None:
        """Close any stateful execution backend resources."""
        if self._kernel_executor is not None:
            try:
                self._kernel_executor.stop()
            except Exception:
                pass
            self._kernel_executor = None

        if self._e2b_sandbox is not None:
            try:
                await self._e2b_sandbox.kill()
            except Exception:
                pass
            self._e2b_sandbox = None

    def close_sync(self) -> None:
        """Close stateful execution resources from synchronous callers."""
        asyncio.run(self.close_async())

    async def execute_async(
        self,
        gist_source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        if self.backend == "kernel":
            return await self._execute_kernel(gist_source, cwd=cwd, user_source_path=user_source_path)
        if self.backend == "subprocess":
            return await self._execute_subprocess(gist_source, cwd=cwd)
        if self._use_e2b:
            return await self._execute_e2b(gist_source, user_source_path=user_source_path)
        return await self._execute_subprocess(gist_source, cwd=cwd)

    async def execute_action_program_async(self, composed_code: str) -> str:
        """Run a composed action program; returns a JSON string with ObservationBundle keys."""
        import json as _json

        result = await self.execute_async(composed_code)

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

    # ── Docker/Jupyter kernel backend ───────────────────────────────────────

    def _resolve_kernel_workspace(
        self,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> Path:
        """Resolve the host project folder that should be mounted into Docker."""
        if cwd:
            return _find_project_root(Path(cwd))
        if user_source_path:
            return _find_project_root(Path(user_source_path))
        return _find_project_root(Path.cwd())

    def _resolve_kernel_mounts(self) -> tuple[Path, ...]:
        """Resolve additional host paths to mount at the same absolute paths."""
        mounts: list[Path] = []
        for raw_path in self._kernel_extra_mounts:
            path = Path(raw_path).expanduser().resolve()
            mount = path.parent if path.is_file() else path
            if mount not in mounts:
                mounts.append(mount)
        return tuple(mounts)

    def _get_kernel_executor(
        self,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> Any:
        """Start and reuse the Docker/Jupyter kernel executor."""
        workspace_dir = self._resolve_kernel_workspace(
            cwd=cwd,
            user_source_path=user_source_path,
        )
        extra_mounts = self._resolve_kernel_mounts()
        if (
            self._kernel_executor is not None
            and (
                self._kernel_workspace_dir != workspace_dir
                or self._kernel_mounts != extra_mounts
            )
        ):
            try:
                self._kernel_executor.stop()
            except Exception:
                pass
            self._kernel_executor = None

        if self._kernel_executor is None:
            from semipy.kernel_container import ContainerKernelExecutor

            self._kernel_executor = ContainerKernelExecutor(
                container_name=self._kernel_container_name,
                image_name=self._kernel_image_name,
                host=self._kernel_host,
                port=self._kernel_port,
                required_packages=self._kernel_required_packages,
                workspace_dir=workspace_dir,
                extra_mounts=list(extra_mounts),
                reuse_container=self._kernel_reuse_container,
            )
            self._kernel_executor.start()
            self._kernel_workspace_dir = workspace_dir
            self._kernel_mounts = extra_mounts
        return self._kernel_executor

    async def _execute_kernel(
        self,
        source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        """Run source in a persistent Docker-hosted Jupyter kernel."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._execute_kernel_sync,
            source,
            cwd,
            user_source_path,
        )

    def _execute_kernel_sync(
        self,
        source: str,
        cwd: Optional[str] = None,
        user_source_path: Optional[str] = None,
    ) -> ExecutionResult:
        try:
            result, stdout, error = self._get_kernel_executor(
                cwd=cwd,
                user_source_path=user_source_path,
            ).execute(source)
            stdout_clean, result_repr = _parse_gist_result_stdout(stdout or "")
            if result_repr is None and result is not None:
                result_repr = repr(result)
            if error:
                return ExecutionResult(
                    success=False,
                    stdout=stdout_clean,
                    stderr="",
                    result_repr=result_repr,
                    error=error[:2000],
                )
            return ExecutionResult(
                success=True,
                stdout=stdout_clean,
                stderr="",
                result_repr=result_repr,
            )
        except Exception as exc:
            return ExecutionResult(success=False, error=str(exc))

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
