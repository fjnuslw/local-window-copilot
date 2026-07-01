from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.assistant_state import get_assistant_state_service


def reset_singletons() -> None:
    get_settings.cache_clear()
    get_assistant_state_service.cache_clear()


def test_health_and_state_update_write_bridge(tmp_path, monkeypatch) -> None:
    bridge_path = tmp_path / "state_bridge.json"
    monkeypatch.setenv("LWC_ASSISTANT_STATE_BRIDGE_PATH", str(bridge_path))
    reset_singletons()

    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["assistant_state"] == "idle"

    response = client.post(
        "/api/assistant/state",
        json={"state": "analyzing", "reason": "unit-test"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "analyzing"
    assert response.json()["reason"] == "unit-test"

    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    assert bridge["state"] == "analyzing"
    assert bridge["source"] == "fastapi"

    state = client.get("/api/assistant/state")
    assert state.status_code == 200
    assert state.json()["state"] == "analyzing"


def test_invalid_state_returns_422(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LWC_ASSISTANT_STATE_BRIDGE_PATH", str(tmp_path / "state_bridge.json"))
    reset_singletons()

    client = TestClient(app)

    response = client.post("/api/assistant/state", json={"state": "busy"})
    assert response.status_code == 422
