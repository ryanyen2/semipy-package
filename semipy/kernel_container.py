import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import requests
from websocket import create_connection


def _normalize_mount_path(path: str | Path) -> Path:
    """Resolve an extra mount; files mount via their parent directory."""
    resolved = Path(path).expanduser().resolve()
    return resolved.parent if resolved.is_file() else resolved


def _default_build_context() -> Path:
    """Find the Dockerfile (instead of using docker build . ) 
    used to build the kernel gateway image."""
    module_path = Path(__file__).resolve()
    candidates = [
        module_path.parents[1] / "docker" / "kernel-gateway",
        module_path.parents[1],
        Path.cwd(),
    ]
    for candidate in candidates:
        if (candidate / "Dockerfile").exists():
            return candidate
    return Path.cwd()


class ContainerKernelExecutor:
    """
    Small standalone executor for:
    - starting a Docker container that runs Jupyter Kernel Gateway
    - creating a kernel inside that container
    - sending code to the kernel over WebSocket
    - returning (result, stdout, error) tuples to callers like glm.py
    """

    def __init__(
        self,
        container_name="glm-kernel-container",
        image_name="kernel-gateway-demo",
        host="127.0.0.1",
        port=None,
        required_packages=None,
        build_context=None,
        workspace_dir=None,
        extra_mounts=None,
        reuse_container=None,
    ):
        self.container_name = container_name
        self.image_name = image_name
        self.host = host
        self.port = int(port if port is not None else os.getenv("SEMIPY_KERNEL_PORT", "8888"))
        self.base_url = f"http://{host}:{self.port}"
        self.required_packages = required_packages or []
        self.build_context = Path(build_context).expanduser().resolve() if build_context else _default_build_context()
        self.workspace_dir = Path(workspace_dir or Path.cwd()).expanduser().resolve()
        self.extra_mounts = [
            _normalize_mount_path(path)
            for path in (extra_mounts or [])
        ]
        reuse_env = os.getenv("SEMIPY_KERNEL_REUSE_CONTAINER", "")
        self.reuse_container = (
            bool(reuse_container)
            if reuse_container is not None
            else reuse_env.lower() in {"1", "true", "yes", "on"}
        )
        self.kernel_id = None
        self.ws = None

    def container_exists(self):
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{self.container_name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return self.container_name in result.stdout.splitlines()

    def container_running(self):
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name=^{self.container_name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return self.container_name in result.stdout.splitlines()

    def _docker_volume_args(self):
        args = [
            "-v",
            f"{self.workspace_dir}:/workspace",
        ]
        for mount in self.extra_mounts:
            if mount == self.workspace_dir or self.workspace_dir in mount.parents:
                continue
            args.extend(["-v", f"{mount}:{mount}:ro"])
        return args

    def start_container(self):
        if self.reuse_container and self.container_exists():
            if not self.container_running():
                subprocess.run(["docker", "start", self.container_name], check=True)
            return

        command = [
            "docker",
            "run",
        ]
        if not self.reuse_container:
            command.append("--rm")
        command.extend(
            [
                "-d",
                "-p",
                f"{self.port}:8888",
                *self._docker_volume_args(),
                "-w",
                "/workspace",
                "-e",
                "PYTHONPATH=/workspace",
                "--name",
                self.container_name,
                self.image_name,
            ]
        )
        subprocess.run(
            command,
            check=True,
        )

    def wait_for_gateway(self, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                response = requests.get(f"{self.base_url}/api/kernelspecs", timeout=1)
                if response.ok:
                    return
            except requests.RequestException:
                pass
            time.sleep(1)
        raise RuntimeError("Kernel Gateway did not become ready in time")

    def create_kernel(self):
        response = requests.post(f"{self.base_url}/api/kernels", timeout=5)
        response.raise_for_status()
        self.kernel_id = response.json()["id"]

    def connect_channels(self):
        if self.kernel_id is None:
            raise RuntimeError("Kernel must be created before connecting channels")
        ws_url = f"ws://{self.host}:{self.port}/api/kernels/{self.kernel_id}/channels"
        self.ws = create_connection(ws_url)

    def ensure_packages(self):
        if not self.required_packages:
            return

        for package in self.required_packages:
            check_code = f"""
import importlib.util
import subprocess
import sys

package = {package!r}
module_name = package.split("==")[0].split(">=")[0].split("[")[0]

if importlib.util.find_spec(module_name) is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
"""
            _, _, error = self.execute(check_code)
            if error:
                raise RuntimeError(f"Failed ensuring package {package}: {error}")

    def execute(self, code):
        '''
        1. Check that WebSocket exists
        2. wraps the code 
        3. creates Jupyter executer_request message and sends it over WebSocket 
        4. Loops and receives kernel messages 
        5. Capture printed output/results/tracebacks 
        6. joins stdout, returns (result, stdout, error)
        '''

        if self.ws is None:
            raise RuntimeError("WebSocket is not connected")

        marker = "__PIPS_RESULT__"
        wrapped_code = f"""
_locs = {{}}
exec({code!r}, _locs, _locs)
__out = _locs.get("answer", _locs.get("result"))
print({marker!r}, repr(__out), flush=True)
"""

        msg_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        request = {
            "header": {
                "msg_id": msg_id,
                "username": "user",
                "session": session_id,
                "msg_type": "execute_request",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": wrapped_code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
            "channel": "shell",
            "buffers": [],
        }

        self.ws.send(json.dumps(request))

        stdout_parts = []
        result = None
        error = None

        while True:
            message = json.loads(self.ws.recv())

            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue

            msg_type = message.get("msg_type")
            content = message.get("content", {})

            if msg_type == "stream":
                stdout_parts.append(content.get("text", ""))
            elif msg_type == "execute_result":
                result = content.get("data", {}).get("text/plain")
            elif msg_type == "error":
                error = "\n".join(content.get("traceback", []))
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break

        stdout = "".join(stdout_parts)

        if marker in stdout:
            left, _, right = stdout.partition(marker)
            stdout = left.strip()
            line = right.strip().splitlines()[0] if right.strip() else ""
            if line and line != "None":
                try:
                    result = eval(line, {"__builtins__": {}}, {})
                except Exception:
                    result = line
            elif line == "None":
                result = None

        return result, stdout, error

    def stop(self):
        if self.ws is not None:
            self.ws.close()
            self.ws = None

        if self.kernel_id is not None:
            try:
                requests.delete(f"{self.base_url}/api/kernels/{self.kernel_id}", timeout=3)
            except requests.RequestException:
                pass
            self.kernel_id = None

        if not self.reuse_container:
            subprocess.run(["docker", "stop", self.container_name], check=False)

    def docker_available(self):
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Docker is not running. Please start Docker Desktop and try again."
            )

    def image_exists(self):
        result = subprocess.run(
            ["docker", "image", "inspect", self.image_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def build_image_if_needed(self):
        if self.image_exists():
            return
        subprocess.run(
            ["docker", "build", "-t", self.image_name, str(self.build_context)],
            check=True,
        )

    def start(self):
        self.docker_available() #runs docker info; if Docker not running raises error
        self.build_image_if_needed() #check if image exists, else builds 
        self.start_container() #starts container, maps container port to host port (8888)
        self.wait_for_gateway() #polls http://127.0.0.1:8888/api/kernelspecs until gateway is ready
        self.create_kernel() #sends requests.post(f"{self.base_url}/api/kernels"), gateway returns kernel ID
        self.connect_channels() #opens WebSocket 
        self.ensure_packages() 
