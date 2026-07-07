from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app import main as main_module
from app.api.routes import assistant as assistant_routes
from app.api.routes import webui as webui_routes
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

    def list_events(self, *, names: list[str] | None = None, limit: int = 100):
        selected = [
            {"id": index + 1, "name": name, "payload": payload, "created_at": "2026-07-04T00:00:00+00:00"}
            for index, (name, payload) in enumerate(self.events)
            if names is None or name in names
        ]
        return list(reversed(selected[-limit:]))


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


def test_context_status_endpoint(monkeypatch) -> None:
    class FakeChatService:
        def context_status(self):
            return {
                "model_name": "minicpm-v4.6-f16",
                "ctx_size": 8192,
                "estimated_tokens": 2048,
                "usage_percent": 25.0,
                "remaining_percent": 75.0,
                "registered_tool_count": 1,
            }

    monkeypatch.setattr(assistant_routes, "get_assistant_chat_service", lambda: FakeChatService())
    client = TestClient(app)

    response = client.get("/api/assistant/context-status")

    assert response.status_code == 200
    data = response.json()
    assert data["model_name"] == "minicpm-v4.6-f16"
    assert data["usage_percent"] == 25.0
    assert data["remaining_percent"] == 75.0
    assert data["registered_tool_count"] == 1


def test_pause_and_observe_endpoints_delegate_to_watcher(monkeypatch) -> None:
    class FakeStatus:
        def model_dump(self, mode: str = "json"):
            return {"running": False, "mode": mode}

    class FakeWatcher:
        def __init__(self) -> None:
            self.pause_reasons: list[str | None] = []
            self.observe_requests = 0

        async def pause(self, *, reason: str | None = None):
            self.pause_reasons.append(reason)

        async def observe_once_now(self, *, resume_after: bool = True):
            self.observe_requests += 1
            self.resume_after = resume_after
            return None

        def status(self):
            return FakeStatus()

    watcher = FakeWatcher()
    monkeypatch.setattr(assistant_routes, "get_window_watcher_service", lambda: watcher)
    client = TestClient(app)

    pause = client.post("/api/assistant/pause")
    observe = client.post("/api/assistant/observe")

    assert pause.status_code == 200
    assert pause.json() == {"running": False}
    assert watcher.pause_reasons == ["chat-workbench-opened"]
    assert observe.status_code == 200
    data = observe.json()
    assert data["ok"] is True
    assert data["started"] is False
    assert data["resume_after"] is True
    assert data["record"] is None
    assert watcher.observe_requests == 1
    assert watcher.resume_after is True


def test_interaction_traces_endpoint_filters_assistant_trace(monkeypatch) -> None:
    fake = FakeRuntimeStore()
    fake.record_event("assistant:interaction_trace", {
        "session_id": "s1",
        "stage": "answer_messages",
        "payload": {"messages": []},
    })
    fake.record_event("assistant:state", {"state": "idle"})
    monkeypatch.setattr(webui_routes, "get_runtime_store", lambda: fake)
    client = TestClient(app)

    response = client.get("/api/webui/interaction-traces")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["items"][0]["payload"]["stage"] == "answer_messages"


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


def test_compact_endpoint_delegates_to_chat_service(monkeypatch) -> None:
    class FakeChatService:
        def compact_history(self):
            return {"status": "ok", "compacted": True}

    monkeypatch.setattr(assistant_routes, "get_assistant_chat_service", lambda: FakeChatService())
    client = TestClient(app)

    response = client.post("/api/assistant/compact")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "compacted": True}

def test_compact_status_endpoint_delegates_to_chat_service(monkeypatch) -> None:
    class FakeChatService:
        def compact_status(self):
            return {"enabled": True, "summary": {"present": False}}

    monkeypatch.setattr(assistant_routes, "get_assistant_chat_service", lambda: FakeChatService())
    client = TestClient(app)

    response = client.get("/api/assistant/compact-status")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "summary": {"present": False}}


def test_webui_contains_compact_panel_static_hooks() -> None:
    html_path = Path(__file__).parents[1] / "app" / "webui" / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    assert "compactStatusPanel" in html
    assert "refreshCompactBtn" in html
    assert "runCompactBtn" in html
    assert "async function loadCompactStatus()" in html
    assert "async function runCompactNow()" in html
    assert "escapeHTML(summary.text" in html