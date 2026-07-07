from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.analyze import CandidateQuestion, WindowAnalysis, WindowAnalysisResult
from app.schemas.window import ForegroundWindowInfo, RawWindowCapture, WindowBounds
from app.services.window_watcher import WindowWatcherService


class FakeCaptureService:
    def __init__(self, capture: RawWindowCapture) -> None:
        self.capture = capture
        self.info = ForegroundWindowInfo(
            window_handle=100,
            app_name=capture.app_name,
            process_id=capture.process_id,
            window_title=capture.window_title,
            window_bounds=capture.window_bounds,
        )
        self.foreground_capture_calls = 0
        self.window_info_capture_calls = 0

    def get_foreground_window_info(self) -> ForegroundWindowInfo:
        return self.info

    def capture_foreground_window(self) -> RawWindowCapture:
        self.foreground_capture_calls += 1
        return self.capture

    def capture_window_info(self, info: ForegroundWindowInfo) -> RawWindowCapture:
        self.window_info_capture_calls += 1
        return self.capture


class SequenceCaptureService:
    def __init__(self, captures: list[RawWindowCapture]) -> None:
        self.captures = captures
        self.index = 0
        first = captures[0]
        self.info = ForegroundWindowInfo(
            window_handle=100,
            app_name=first.app_name,
            process_id=first.process_id,
            window_title=first.window_title,
            window_bounds=first.window_bounds,
        )

    def get_foreground_window_info(self) -> ForegroundWindowInfo:
        return self.info

    def capture_foreground_window(self) -> RawWindowCapture:
        return self._next_capture()

    def capture_window_info(self, info: ForegroundWindowInfo) -> RawWindowCapture:
        return self._next_capture()

    def _next_capture(self) -> RawWindowCapture:
        capture = self.captures[min(self.index, len(self.captures) - 1)]
        self.index += 1
        return capture


class FakeAnalysisService:
    def __init__(self) -> None:
        self.captures: list[RawWindowCapture] = []

    def analyze_capture(self, capture: RawWindowCapture) -> WindowAnalysisResult:
        self.captures.append(capture)
        return WindowAnalysisResult(
            capture=capture,
            analysis=WindowAnalysis(
                window_type="ide",
                summary="当前窗口是代码编辑器。",
                key_points=["窗口发生变化"],
                candidate_questions=[
                    CandidateQuestion(
                        question="这是什么窗口？",
                        category="summary",
                        reason="自动观察检测到了新窗口。",
                        priority=0.8,
                    )
                ],
                caution=None,
            ),
            latency_ms=120,
            model_endpoint="http://127.0.0.1:18181/v1/chat/completions",
        )


class FakeStateService:
    def __init__(self) -> None:
        self.states: list[tuple[str, str | None]] = []

    async def set_state(self, state: str, *, reason: str | None = None) -> None:
        self.states.append((state, reason))


def make_capture(tmp_path: Path, screenshot_hash: str = "hash-1") -> RawWindowCapture:
    image_path = tmp_path / f"{screenshot_hash}.png"
    image_path.write_bytes(b"fake-image")
    return RawWindowCapture(
        app_name="Code.exe",
        process_id=1234,
        window_title="README.md - Visual Studio Code",
        window_bounds=WindowBounds(left=10, top=20, right=810, bottom=620),
        screenshot_path=image_path,
        screenshot_hash=screenshot_hash,
        captured_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_watcher_tick_analyzes_new_capture_hash(tmp_path) -> None:
    capture = make_capture(tmp_path)
    analysis_service = FakeAnalysisService()
    state_service = FakeStateService()
    watcher = WindowWatcherService(
        capture_service=FakeCaptureService(capture),
        analysis_service=analysis_service,
        state_service=state_service,
        interval_seconds=1.0,
        capture_min_interval_seconds=0.0,
        analysis_min_interval_seconds=0.0,
    )

    await watcher.tick()

    assert analysis_service.captures == [capture]
    assert watcher.status().analyses_count == 1
    assert watcher.status().last_analysis is not None
    assert state_service.states == [
        ("observing", "window-watch-capture-started"),
        ("analyzing", "window-watch-analysis-started"),
        ("idle", "window-watch-analysis-finished"),
    ]

    await watcher.tick()

    assert analysis_service.captures == [capture]
    assert watcher.status().analyses_count == 1


@pytest.mark.anyio
async def test_watcher_analyzes_same_window_when_screenshot_changes(tmp_path) -> None:
    first = make_capture(tmp_path, screenshot_hash="hash-1")
    second = make_capture(tmp_path, screenshot_hash="hash-2")
    analysis_service = FakeAnalysisService()
    watcher = WindowWatcherService(
        capture_service=SequenceCaptureService([first, second]),
        analysis_service=analysis_service,
        state_service=FakeStateService(),
        interval_seconds=1.0,
        capture_min_interval_seconds=0.0,
        analysis_min_interval_seconds=0.0,
    )

    await watcher.tick()
    await watcher.tick()

    assert analysis_service.captures == [first, second]
    assert watcher.status().analyses_count == 2


@pytest.mark.anyio
async def test_watcher_manual_observe_once_without_starting_auto_loop(tmp_path) -> None:
    capture = make_capture(tmp_path)
    analysis_service = FakeAnalysisService()
    state_service = FakeStateService()
    watcher = WindowWatcherService(
        capture_service=FakeCaptureService(capture),
        analysis_service=analysis_service,
        state_service=state_service,
        interval_seconds=1.0,
        capture_min_interval_seconds=10.0,
        analysis_min_interval_seconds=10.0,
    )

    await watcher.observe_once_now()

    assert watcher.status().running is False
    assert watcher.status().captures_count == 1
    assert watcher.status().analyses_count == 1
    assert analysis_service.captures == [capture]
    assert state_service.states == [
        ("observing", "window-watch-manual-capture-started"),
        ("analyzing", "window-watch-manual-analysis-started"),
        ("idle", "window-watch-manual-analysis-finished"),
    ]


@pytest.mark.anyio
async def test_watcher_manual_observe_uses_fresh_foreground_capture(tmp_path) -> None:
    current_capture = make_capture(tmp_path, screenshot_hash="current")
    capture_service = FakeCaptureService(current_capture)
    analysis_service = FakeAnalysisService()
    watcher = WindowWatcherService(
        capture_service=capture_service,
        analysis_service=analysis_service,
        state_service=FakeStateService(),
        interval_seconds=1.0,
        capture_min_interval_seconds=10.0,
        analysis_min_interval_seconds=10.0,
    )

    await watcher.observe_once_now()

    assert analysis_service.captures == [current_capture]
    assert capture_service.foreground_capture_calls == 1
    assert capture_service.window_info_capture_calls == 0


@pytest.mark.anyio
async def test_watcher_manual_observe_once_can_resume_auto_loop(tmp_path) -> None:
    capture = make_capture(tmp_path)
    watcher = WindowWatcherService(
        capture_service=FakeCaptureService(capture),
        analysis_service=FakeAnalysisService(),
        state_service=FakeStateService(),
        interval_seconds=1.0,
        capture_min_interval_seconds=10.0,
        analysis_min_interval_seconds=10.0,
    )

    await watcher.observe_once_now(resume_after=True)

    assert watcher.status().running is True
    await watcher.stop()
