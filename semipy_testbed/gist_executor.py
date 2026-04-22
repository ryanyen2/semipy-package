"""
Simple gist executor: run generated code in subprocess or Docker.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from semipy_testbed.types import GistExecutorResult
from semipy_testbed.config import get_config


def _parse_gist_result_stdout(stdout: str) -> tuple[str, Optional[str]]:
    """
    Extract __GIST_RESULT__ marker from stdout.
    Returns (stdout_before_marker, result_repr).
    """
    marker = "__GIST_RESULT__"
    if marker not in stdout:
        return stdout, None
    idx = stdout.find(marker)
    before = stdout[:idx].rstrip()
    after = stdout[idx + len(marker):].strip()
    # Extract repr string from after marker
    if after.startswith("'") or after.startswith('"'):
        # Simple case: quoted string follows marker
        for end_idx in range(1, len(after)):
            if after[end_idx] == after[0] and after[end_idx - 1] != "\\":
                return before, after[1:end_idx]
    return before, after.split("\n")[0] if after else None


class SimpleGistExecutor:
    """Execute generated gist code in subprocess or Docker."""

    def __init__(self, use_docker: bool = False, timeout: int = 30):
        self.use_docker = use_docker
        self.timeout = timeout

    def execute(
        self,
        gist_source: str,
        env_vars: Optional[dict[str, str]] = None,
        user_source_path: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> GistExecutorResult:
        """Execute gist synchronously."""
        if self.use_docker:
            return self._execute_docker(gist_source, env_vars, user_source_path, cwd)
        return self._execute_subprocess(gist_source, env_vars, user_source_path, cwd)

    def _execute_subprocess(
        self,
        gist_source: str,
        env_vars: Optional[dict[str, str]] = None,
        user_source_path: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> GistExecutorResult:
        """Execute in subprocess (default)."""
        try:
            # Write gist to temp file
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(gist_source)
                temp_path = f.name

            try:
                # Prepare environment
                exec_env = os.environ.copy()
                if env_vars:
                    exec_env.update(env_vars)
                if user_source_path:
                    exec_env["SEMIPY_GIST_USER_SOURCE"] = user_source_path

                # Run subprocess
                result = subprocess.run(
                    ["python", temp_path],
                    capture_output=True,
                    timeout=self.timeout,
                    text=True,
                    cwd=cwd or os.getcwd(),
                    env=exec_env,
                )

                stdout_before, result_repr = _parse_gist_result_stdout(
                    result.stdout)

                return GistExecutorResult(
                    success=result.returncode == 0,
                    stdout=stdout_before,
                    stderr=result.stderr,
                    result_repr=result_repr,
                    error=None if result.returncode == 0 else f"Exit code: {result.returncode}",
                )

            finally:
                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        except subprocess.TimeoutExpired:
            return GistExecutorResult(
                success=False,
                error=f"Gist execution timeout ({self.timeout}s)",
            )
        except Exception as e:
            return GistExecutorResult(
                success=False,
                error=f"Execution error: {e}",
            )

    def _execute_docker(
        self,
        gist_source: str,
        env_vars: Optional[dict[str, str]] = None,
        user_source_path: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> GistExecutorResult:
        """Execute in Docker container."""
        try:
            import docker

            client = docker.from_env()
            config = get_config()

            # Prepare environment
            exec_env = {} if not env_vars else dict(env_vars)
            if user_source_path:
                exec_env["SEMIPY_GIST_USER_SOURCE"] = user_source_path

            # Convert env dict to list of "KEY=VALUE" strings
            environment = [f"{k}={v}" for k, v in exec_env.items()]

            try:
                # Run container
                result = client.containers.run(
                    config.docker_image,
                    command=["python", "-c", gist_source],
                    environment=environment,
                    volumes={cwd or os.getcwd(): {"bind": "/data",
                                                  "mode": "ro"}},
                    working_dir="/data",
                    timeout=self.timeout,
                    remove=True,
                )

                result_str = result.decode(
                    "utf-8") if isinstance(result, bytes) else result
                stdout_before, result_repr = _parse_gist_result_stdout(
                    result_str)

                return GistExecutorResult(
                    success=True,
                    stdout=stdout_before,
                    result_repr=result_repr,
                )

            except docker.errors.ContainerError as e:
                stdout_str = e.stdout.decode(
                    "utf-8") if isinstance(e.stdout, bytes) else str(e.stdout)
                stderr_str = e.stderr.decode(
                    "utf-8") if isinstance(e.stderr, bytes) else str(e.stderr)
                stdout_before, result_repr = _parse_gist_result_stdout(
                    stdout_str)
                return GistExecutorResult(
                    success=False,
                    stdout=stdout_before,
                    stderr=stderr_str,
                    result_repr=result_repr,
                    error=str(e),
                )

        except ImportError:
            return GistExecutorResult(
                success=False,
                error="Docker client not available. Install 'docker' package.",
            )
        except Exception as e:
            return GistExecutorResult(
                success=False,
                error=f"Docker execution error: {e}",
            )
