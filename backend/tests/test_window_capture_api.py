from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import window as window_routes
from app.core.config import get_settings
from app.main import app
from app.services.assistant_state import get_assistant_state_service
from app.services.local_copilot_identity import is_local_runtime_title
from app.services.window_capture import is_local_copilot_window
from app.schemas.window import RawWindowCapture, WindowBounds, WindowWatchStatus


def reset_singletons() -> None:
    get_settings.cache_clear()
    get_assistant_state_service.cache_clear()


class FakeAssistantStateService:
    def __init__(self) -> None:
        self.states: list[tuple[str, str | None]] = []

    async def set_state(self, state: str, *, reason: str | None = None) -> None:
        self.states.append((state, reason))


class FakeCaptureService:
    def __init__(self, capture: RawWindowCapture) -> None:
        self.capture = capture
        self.calls = 0

    def capture_foreground_window(self) -> RawWindowCapture:
        self.calls += 1
        return self.capture


class FakeWatcherService:
    def __init__(self) -> None:
        self.running = False

    def start(self) -> WindowWatchStatus:
        self.running = True
        return self.status()

    async def stop(self) -> WindowWatchStatus:
        self.running = False
        return self.status()

    def status(self) -> WindowWatchStatus:
        return WindowWatchStatus(
            running=self.running,
            interval_seconds=1.0,
            capture_min_interval_seconds=3.0,
            analysis_min_interval_seconds=15.0,
            last_capture=None,
            last_analysis=None,
            last_error=None,
            captures_count=0,
            analyses_count=0,
        )


def test_capture_endpoint_updates_state_and_returns_window_capture(monkeypatch) -> None:
    reset_singletons()

    capture = RawWindowCapture(
        app_name="Code.exe",
        process_id=1234,
        window_title="README.md - Visual Studio Code",
        window_bounds=WindowBounds(left=10, top=20, right=810, bottom=620),
        screenshot_path=Path("backend/data/captures/test.png"),
        screenshot_hash="abc123",
        captured_at=datetime.now(UTC),
    )
    fake_state = FakeAssistantStateService()
    fake_capture = FakeCaptureService(capture)

    monkeypatch.setattr(window_routes, "get_assistant_state_service", lambda: fake_state)
    monkeypatch.setattr(window_routes, "get_window_capture_service", lambda: fake_capture)

    client = TestClient(app)
    response = client.post("/api/window/capture")

    assert response.status_code == 200
    body = response.json()
    assert body["app_name"] == "Code.exe"
    assert body["window_title"] == "README.md - Visual Studio Code"
    assert body["screenshot_hash"] == "abc123"
    assert fake_capture.calls == 1
    assert fake_state.states == [
        ("observing", "window-capture-started"),
        ("idle", "window-capture-finished"),
    ]


def test_window_watch_endpoints_start_report_and_stop(monkeypatch) -> None:
    reset_singletons()

    fake_watcher = FakeWatcherService()
    monkeypatch.setattr(window_routes, "get_window_watcher_service", lambda: fake_watcher)

    client = TestClient(app)

    start = client.post("/api/window/watch/start")
    assert start.status_code == 200
    assert start.json()["running"] is True

    status = client.get("/api/window/watch/status")
    assert status.status_code == 200
    assert status.json()["running"] is True

    stop = client.post("/api/window/watch/stop")
    assert stop.status_code == 200
    assert stop.json()["running"] is False


def test_local_copilot_windows_are_excluded_from_capture_targets() -> None:
    assert is_local_copilot_window(
        class_name="LocalWindowAwareFloatingAssistant",
        title="任意标题",
    )
    assert is_local_copilot_window(
        class_name="OtherClass",
        title="Floating Assistant",
    )
    assert is_local_copilot_window(
        class_name="OtherClass",
        title="Floating Chat",
    )
    assert is_local_copilot_window(
        class_name="OtherClass",
        title="AlertWindow",
    )
    assert not is_local_copilot_window(
        class_name="ConsoleWindowClass",
        title="cmd.exe - uv run uvicorn app.main:app --host 127.0.0.1 --port 18081",
    )
    assert is_local_runtime_title(
        "cmd.exe - uv run uvicorn app.main:app --host 127.0.0.1 --port 18081"
    )
    assert not is_local_copilot_window(
        class_name="Chrome_WidgetWin_1",
        title="Local Window Copilot · 控制台 - Microsoft Edge",
    )
    assert is_local_runtime_title("Local Window Copilot · 控制台 - Microsoft Edge")
