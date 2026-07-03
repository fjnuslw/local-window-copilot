from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from app.api.routes import assistant as assistant_routes
from app.core.config import get_settings
from app.main import app
from app.schemas.chat import ChatSession
from app.services import assistant_state as assistant_state_module
from app.services.assistant_state import AssistantStateService, get_assistant_state_service
from app.services.runtime_store import get_runtime_store
from datetime import UTC, datetime


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.events: list[tuple[str, object]] = []

    def require_ready(self) -> None:
        return None

    def status(self) -> dict[str, object]:
        return {"available": True}

    def set_json(
        self,
        name: str,
        payload: object,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self.data[name] = payload

    def get_json(self, name: str) -> object | None:
        return self.data.get(name)

    def record_event(self, name: str, payload: object) -> bool:
        self.events.append((name, payload))
        return True


def reset_singletons() -> None:
    get_settings.cache_clear()
    get_assistant_state_service.cache_clear()
    get_runtime_store.cache_clear()


def install_fake_runtime_store(monkeypatch) -> FakeRuntimeStore:
    fake = FakeRuntimeStore()
    monkeypatch.setattr(assistant_state_module, "get_runtime_store", lambda: fake)
    monkeypatch.setattr(main_module, "get_runtime_store", lambda: fake)
    return fake


def test_health_and_state_update_write_runtime_store(monkeypatch) -> None:
    fake_store = install_fake_runtime_store(monkeypatch)
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

    stored_state = fake_store.data["assistant:state"]
    assert isinstance(stored_state, dict)
    assert stored_state["state"] == "analyzing"
    assert stored_state["source"] == "fastapi"
    assert fake_store.events[-1][0] == "assistant:state"

    state = client.get("/api/assistant/state")
    assert state.status_code == 200
    assert state.json()["state"] == "analyzing"


def test_invalid_state_returns_422(monkeypatch) -> None:
    install_fake_runtime_store(monkeypatch)
    reset_singletons()

    client = TestClient(app)

    response = client.post("/api/assistant/state", json={"state": "busy"})
    assert response.status_code == 422


def test_conversation_history_endpoint(monkeypatch) -> None:
    now = datetime.now(UTC)

    class FakeChatService:
        def history(self, *, limit: int = 20):
            return [
                ChatSession(
                    session_id="session-1",
                    question="这个窗口在做什么？",
                    answer="它正在显示项目代码。",
                    status="done",
                    created_at=now,
                    updated_at=now,
                )
            ]

    monkeypatch.setattr(assistant_routes, "get_assistant_chat_service", lambda: FakeChatService())
    client = TestClient(app)

    response = client.get("/api/assistant/conversations")

    assert response.status_code == 200
    assert response.json()["items"][0]["session_id"] == "session-1"
    assert response.json()["items"][0]["answer"] == "它正在显示项目代码。"


def test_assistant_state_service_loads_existing_runtime_state() -> None:
    fake_store = FakeRuntimeStore()
    fake_store.data["assistant:state"] = {
        "state": "privacy",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "source": "fastapi",
        "reason": "existing",
    }

    service = AssistantStateService(runtime_store=fake_store)

    assert service.get_state().state == "privacy"
