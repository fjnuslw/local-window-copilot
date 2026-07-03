from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas.analyze import WindowAnalysis
from app.schemas.observation import ObservationCard
from app.schemas.window import WindowBounds
from app.services.memory import MEMORY_ITEMS_KEY, WORKING_OBSERVATION_KEY, MemoryService


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.events: list[tuple[str, object]] = []
        self.deleted: list[str] = []

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


def make_observation(
    tmp_path: Path,
    title: str = "README.md - Visual Studio Code",
) -> ObservationCard:
    image_path = tmp_path / "capture.png"
    image_path.write_bytes(b"fake-image")
    return ObservationCard(
        app_name="Code.exe",
        process_id=1234,
        window_title=title,
        window_kind_hint="coding",
        window_bounds=WindowBounds(left=0, top=0, right=800, bottom=600),
        screenshot_path=image_path,
        screenshot_hash="hash",
        source_signals=["window_title", "process", "screenshot"],
        captured_at=datetime.now(UTC),
    )


def test_memory_service_saves_working_observation_and_analysis(tmp_path) -> None:
    runtime_store = FakeRuntimeStore()
    service = MemoryService(runtime_store=runtime_store)
    observation = make_observation(tmp_path)
    analysis = WindowAnalysis(
        window_type="ide",
        summary="当前窗口是 VS Code 项目。",
        key_points=["README 打开", "正在做 Agent 架构"],
        candidate_questions=[],
    )

    service.save_observation(observation)
    item = service.remember_analysis(
        observation=observation,
        analysis=analysis,
        latency_ms=42,
    )

    assert WORKING_OBSERVATION_KEY in runtime_store.data
    assert item is not None
    assert item.kind == "analysis_summary"
    assert MEMORY_ITEMS_KEY in runtime_store.data
    relevant = service.retrieve_for_observation(
        observation,
        question="Agent 架构怎么推进？",
    )
    assert relevant
    assert relevant[0].text.startswith("当前窗口是 VS Code 项目。")


def test_memory_service_does_not_store_privacy_observation(tmp_path) -> None:
    runtime_store = FakeRuntimeStore()
    service = MemoryService(runtime_store=runtime_store)
    observation = make_observation(tmp_path, title="Payment password")
    observation.privacy_state = "privacy"
    observation.privacy_reasons = ["password keyword"]

    service.save_observation(observation)
    item = service.remember_analysis(
        observation=observation,
        analysis=WindowAnalysis(
            window_type="unknown",
            summary="should not persist",
            key_points=[],
            candidate_questions=[],
        ),
    )

    assert item is None
    assert runtime_store.deleted == [WORKING_OBSERVATION_KEY]
    assert MEMORY_ITEMS_KEY not in runtime_store.data
