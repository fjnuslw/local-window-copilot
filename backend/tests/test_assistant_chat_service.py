from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.analyze import WindowAnalysis, WindowAnalysisResult
from app.schemas.window import RawWindowCapture, WindowBounds
from app.services import assistant_chat as chat_module
from app.services.assistant_chat import AssistantChatService


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.deleted: list[str] = []
        self.events: list[tuple[str, object]] = []

    def set_json(self, name: str, payload: object, *, ttl_seconds: int | None = None) -> None:
        self.data[name] = payload

    def get_json(self, name: str) -> object | None:
        return self.data.get(name)

    def record_event(self, name: str, payload: object) -> bool:
        self.events.append((name, payload))
        return True

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.data.pop(name, None)


class FakeWatcher:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def stop(self):
        self.stopped += 1

    def start(self):
        self.started += 1


class FakeStateService:
    def __init__(self) -> None:
        self.states: list[tuple[str, str | None]] = []

    async def set_state(self, state: str, *, reason: str | None = None):
        self.states.append((state, reason))


class FakeAnalysisService:
    def get_latest(self) -> WindowAnalysisResult:
        capture = RawWindowCapture(
            app_name="Code.exe",
            process_id=123,
            window_title="README.md - VS Code",
            window_bounds=WindowBounds(left=0, top=0, right=800, bottom=600),
            screenshot_path=Path("capture.png"),
            screenshot_hash="hash",
            captured_at=datetime.now(UTC),
        )
        return WindowAnalysisResult(
            capture=capture,
            analysis=WindowAnalysis(
                window_type="ide",
                summary="Current window is an IDE.",
                key_points=["README", "backend"],
                candidate_questions=[],
            ),
            latency_ms=1,
            model_endpoint="http://127.0.0.1:18181/v1/chat/completions",
            analyzed_at=datetime.now(UTC),
        )


class FakeVisionClient:
    def stream_chat(self, *, messages, image_path=None):
        yield "第一段"
        yield "，第二段。"


@pytest.mark.anyio
async def test_chat_question_pauses_streams_and_resume_restarts(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = AssistantChatService(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
    )

    session = await service.ask("这个窗口在做什么？")
    await asyncio.sleep(0.1)

    assert watcher.stopped == 1
    assert session.question == "这个窗口在做什么？"
    current = service.current()
    assert current is not None
    assert current.answer == "第一段，第二段。"
    assert current.status == "done"
    history = service.history(limit=5)
    assert len(history) == 1
    assert history[0].session_id == current.session_id
    assert history[0].question == session.question
    assert history[0].answer == current.answer

    await service.resume_auto_watch()

    assert watcher.started == 1
    assert service.current() is None
    assert runtime_store.deleted == ["assistant:chat:current"]
