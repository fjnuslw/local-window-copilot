from __future__ import annotations

import pytest

from app.services.model_runtime import ModelRuntimeError, ModelRuntimeManager


def _manager(tmp_path, *, ctx_size: int = 256000) -> ModelRuntimeManager:
    server = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    for path in (server, model, mmproj):
        path.write_text("stub", encoding="utf-8")
    return ModelRuntimeManager(
        server_exe=server,
        model_path=model,
        mmproj_path=mmproj,
        host="127.0.0.1",
        port=18181,
        ctx_size=ctx_size,
        endpoint_path="/v1/chat/completions",
        startup_timeout_seconds=1,
        reasoning="off",
        reasoning_format="deepseek",
        reasoning_budget=0,
    )


def test_ready_server_with_smaller_context_is_rejected(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "is_server_ready", lambda: True)
    monkeypatch.setattr(manager, "server_context_size", lambda: 8192)

    with pytest.raises(ModelRuntimeError, match="actual n_ctx=8192"):
        manager.ensure_server_ready()


def test_ready_server_with_expected_context_is_reused(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "is_server_ready", lambda: True)
    monkeypatch.setattr(manager, "server_context_size", lambda: 256000)

    manager.ensure_server_ready()