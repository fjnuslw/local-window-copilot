from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas.analyze import CandidateQuestion, WindowAnalysis
from app.schemas.window import RawWindowCapture, WindowBounds
from app.services.observation_builder import ObservationBuilder
from app.services.vision_model_client import extract_json_object, parse_window_analysis
from app.services.window_analysis import WindowAnalysisService


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.ensure_calls = 0

    def ensure_server_ready(self) -> None:
        self.ensure_calls += 1


class FakeVisionModelClient:
    def __init__(self, analysis: WindowAnalysis) -> None:
        self.analysis = analysis
        self.endpoint = "http://127.0.0.1:18181/v1/chat/completions"
        self.image_paths: list[Path] = []

    def analyze_image(self, image_path: Path) -> WindowAnalysis:
        self.image_paths.append(image_path)
        return self.analysis


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.events: list[tuple[str, object]] = []
        self.ttls: dict[str, int | None] = {}

    def set_json(
        self,
        name: str,
        payload: object,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self.data[name] = payload
        self.ttls[name] = ttl_seconds

    def get_json(self, name: str) -> object | None:
        return self.data.get(name)

    def record_event(self, name: str, payload: object) -> bool:
        self.events.append((name, payload))
        return True


def make_capture(image_path: Path, screenshot_hash: str = "abc123") -> RawWindowCapture:
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


def test_parse_window_analysis_extracts_json_after_think_tags() -> None:
    raw = """
<think>internal reasoning that should be ignored</think>
{
  "window_type": "ide",
  "summary": "Current window is an IDE.",
  "key_points": ["editor", "project files", "tests"],
  "candidate_questions": [
    {
      "question": "How should I verify this change?",
      "category": "testing",
      "reason": "The window includes code and test context.",
      "priority": 0.8
    }
  ],
  "caution": null
}
"""

    json_text = extract_json_object(raw)
    parsed = parse_window_analysis(raw)

    assert json_text.startswith("{")
    assert parsed.window_type == "ide"
    assert parsed.candidate_questions[0].question == "How should I verify this change?"


def test_window_analysis_service_stores_latest_summary_in_runtime_store(tmp_path) -> None:
    capture = make_capture(tmp_path / "capture.png")
    analysis = WindowAnalysis(
        window_type="ide",
        summary="Current window is VS Code with the project README open.",
        key_points=["README is visible", "backend work is in progress", "model integration exists"],
        candidate_questions=[
            CandidateQuestion(
                question="What should be implemented next?",
                category="development",
                reason="The current context is a development workspace.",
                priority=0.9,
            )
        ],
        caution=None,
    )
    runtime = FakeRuntimeManager()
    client = FakeVisionModelClient(analysis)
    runtime_store = FakeRuntimeStore()
    service = WindowAnalysisService(
        runtime_manager=runtime,
        vision_model_client=client,
        runtime_store=runtime_store,
        latest_analysis_ttl_seconds=60,
    )

    result = service.analyze_capture(capture)

    assert runtime.ensure_calls == 1
    assert client.image_paths == [capture.screenshot_path]
    assert result.capture.screenshot_hash == "abc123"
    assert result.analysis.summary == "Current window is VS Code with the project README open."
    assert "window:latest_analysis" in runtime_store.data
    assert runtime_store.ttls["window:latest_analysis"] == 60
    assert runtime_store.events[-1][0] == "window:analysis"
    assert service.get_latest().analysis.summary == "Current window is VS Code with the project README open."


def test_window_analysis_service_pauses_privacy_without_model_call(tmp_path) -> None:
    capture = make_capture(tmp_path / "capture.png")
    capture.window_title = "Payment password"
    runtime = FakeRuntimeManager()
    client = FakeVisionModelClient(
        WindowAnalysis(
            window_type="ide",
            summary="Should not be called.",
            key_points=[],
            candidate_questions=[],
        )
    )
    runtime_store = FakeRuntimeStore()
    service = WindowAnalysisService(
        runtime_manager=runtime,
        vision_model_client=client,
        runtime_store=runtime_store,
        observation_builder=ObservationBuilder(),
    )

    result = service.analyze_capture(capture)

    assert runtime.ensure_calls == 0
    assert client.image_paths == []
    assert result.observation is not None
    assert result.observation.privacy_state == "privacy"
    assert result.analysis.summary == "当前窗口可能包含敏感信息，已暂停自动分析。"
    assert runtime_store.data["window:latest_analysis"]["observation"]["privacy_state"] == "privacy"
