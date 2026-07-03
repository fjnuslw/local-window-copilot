from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


class ModelRuntimeError(RuntimeError):
    pass


class ModelRuntimeManager:
    def __init__(
        self,
        *,
        server_exe: Path,
        model_path: Path,
        mmproj_path: Path,
        host: str,
        port: int,
        ctx_size: int,
        endpoint_path: str,
        startup_timeout_seconds: float,
    ) -> None:
        self.server_exe = server_exe
        self.model_path = model_path
        self.mmproj_path = mmproj_path
        self.host = host
        self.port = port
        self.ctx_size = ctx_size
        self.endpoint_path = endpoint_path
        self.startup_timeout_seconds = startup_timeout_seconds
        self._process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def chat_completions_endpoint(self) -> str:
        return f"{self.base_url}{self.endpoint_path}"

    def ensure_server_ready(self) -> None:
        self.verify_files()
        if self.is_server_ready():
            return
        self.start_server()
        self.wait_until_ready()

    def verify_files(self) -> None:
        missing = [
            path
            for path in (self.server_exe, self.model_path, self.mmproj_path)
            if not path.exists()
        ]
        if missing:
            joined = ", ".join(str(path) for path in missing)
            raise ModelRuntimeError(f"Missing model runtime files: {joined}")

    def is_server_ready(self) -> bool:
        for path in ("/health", "/v1/models"):
            try:
                request = urllib.request.Request(f"{self.base_url}{path}", method="GET")
                with urllib.request.urlopen(request, timeout=1.0) as response:
                    if 200 <= response.status < 500:
                        return True
            except (OSError, urllib.error.URLError):
                continue
        return False

    def start_server(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        command = [
            str(self.server_exe),
            "-m",
            str(self.model_path),
            "--mmproj",
            str(self.mmproj_path),
            "-c",
            str(self.ctx_size),
            "--gpu-layers",
            "all",
            "--reasoning",
            "off",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--no-webui",
        ]
        self._process = subprocess.Popen(
            command,
            cwd=str(self.server_exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.is_server_ready():
                return
            if self._process is not None and self._process.poll() is not None:
                raise ModelRuntimeError("llama-server exited before becoming ready.")
            time.sleep(0.5)
        raise ModelRuntimeError("llama-server did not become ready in time.")


@lru_cache
def get_model_runtime_manager() -> ModelRuntimeManager:
    settings = get_settings()
    return ModelRuntimeManager(
        server_exe=settings.llama_server_path,
        model_path=settings.minicpm_model_path,
        mmproj_path=settings.minicpm_mmproj_path,
        host=settings.llama_server_host,
        port=settings.llama_server_port,
        ctx_size=settings.minicpm_ctx_size,
        endpoint_path=settings.llama_chat_completions_path,
        startup_timeout_seconds=settings.llama_startup_timeout_seconds,
    )
