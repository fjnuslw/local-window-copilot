from __future__ import annotations

import json
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
        reasoning: str,
        reasoning_format: str,
        reasoning_budget: int,
    ) -> None:
        self.server_exe = server_exe
        self.model_path = model_path
        self.mmproj_path = mmproj_path
        self.host = host
        self.port = port
        self.ctx_size = ctx_size
        self.endpoint_path = endpoint_path
        self.startup_timeout_seconds = startup_timeout_seconds
        self.reasoning = reasoning
        self.reasoning_format = reasoning_format
        self.reasoning_budget = reasoning_budget
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
            mismatch = self._server_mismatch_reason()
            if not mismatch:
                return
            if self._process is not None and self._process.poll() is None:
                self.stop_server()
            else:
                raise ModelRuntimeError(mismatch)
        self.start_server()
        self.wait_until_ready()
        mismatch = self._server_mismatch_reason()
        if mismatch:
            raise ModelRuntimeError(mismatch)

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
            self.reasoning,
            "--reasoning-format",
            self.reasoning_format,
            "--reasoning-budget",
            str(self.reasoning_budget),
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

    def stop_server(self) -> None:
        if self._process is None or self._process.poll() is not None:
            self._process = None
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)
        finally:
            self._process = None

    def wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.is_server_ready():
                return
            if self._process is not None and self._process.poll() is not None:
                raise ModelRuntimeError("llama-server exited before becoming ready.")
            time.sleep(0.5)
        raise ModelRuntimeError("llama-server did not become ready in time.")

    def server_context_size(self) -> int | None:
        props = self._get_json("/props")
        if isinstance(props, dict):
            default_settings = props.get("default_generation_settings")
            if isinstance(default_settings, dict):
                params = default_settings.get("params")
                if isinstance(params, dict) and isinstance(params.get("n_ctx"), int):
                    return params["n_ctx"]
        models = self._get_json("/v1/models")
        if isinstance(models, dict):
            data = models.get("data")
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    meta = first.get("meta")
                    if isinstance(meta, dict) and isinstance(meta.get("n_ctx"), int):
                        return meta["n_ctx"]
        return None

    def _server_mismatch_reason(self) -> str | None:
        actual_ctx = self.server_context_size()
        if actual_ctx is not None and actual_ctx < self.ctx_size:
            return (
                "Existing llama-server has a smaller context window than configured: "
                f"actual n_ctx={actual_ctx}, expected n_ctx>={self.ctx_size}. "
                "Stop the stale llama-server on this port so the backend can restart it "
                "with the configured high-context settings."
            )
        return None

    def _get_json(self, path: str) -> dict[str, object] | None:
        try:
            request = urllib.request.Request(f"{self.base_url}{path}", method="GET")
            with urllib.request.urlopen(request, timeout=1.0) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None


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
        reasoning=settings.minicpm_reasoning,
        reasoning_format=settings.minicpm_reasoning_format,
        reasoning_budget=settings.minicpm_reasoning_budget,
    )