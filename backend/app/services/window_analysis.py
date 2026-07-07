from __future__ import annotations

import time
from datetime import UTC, datetime
from functools import lru_cache

from app.core.config import get_settings
from app.schemas.analyze import WindowAnalysisResult
from app.schemas.observation import ObservationCard
from app.schemas.window import RawWindowCapture
from app.services.memory import MemoryService, get_memory_service
from app.services.model_runtime import ModelRuntimeManager, get_model_runtime_manager
from app.services.observation_builder import ObservationBuilder, get_observation_builder
from app.services.runtime_log import get_runtime_log_service
from app.services.runtime_store import RuntimeStore, get_runtime_store
from app.services.vision_model_client import VisionModelClient, get_vision_model_client
from app.services.window_summary_store import (
    WindowSummaryStore,
    get_window_summary_store,
)


class ObservationAgent:
    """观察 agent：截图去重 → VLM 详细窗口观察 → 写 WindowSummaryStore + 记忆。

    职责边界（见 kv_cache_profile_and_agent_split_spec_zh.md §4.1）：
    - 输入：截图 + ObservationCard
    - 输出：WindowSummaryRecord
    - 不生成候选问题，不参与用户多轮对话
    """

    def __init__(
        self,
        *,
        runtime_manager: ModelRuntimeManager,
        vision_model_client: VisionModelClient,
        runtime_store: RuntimeStore,
        observation_builder: ObservationBuilder | None = None,
        memory_service: MemoryService | None = None,
        window_summary_store: WindowSummaryStore | None = None,
        latest_analysis_ttl_seconds: int | None = None,
    ) -> None:
        self.runtime_manager = runtime_manager
        self.vision_model_client = vision_model_client
        self.runtime_store = runtime_store
        self.observation_builder = observation_builder
        self.memory_service = memory_service
        self.window_summary_store = window_summary_store
        self.latest_analysis_ttl_seconds = latest_analysis_ttl_seconds
        self._latest: WindowAnalysisResult | None = None
        self._load_latest()

    def analyze_capture(self, capture: RawWindowCapture) -> WindowAnalysisResult:
        log = get_runtime_log_service()
        fields = self._capture_fields(capture)
        log.info(
            "window_analysis",
            "start",
            "Observation analysis started.",
            **fields,
            endpoint=self.vision_model_client.endpoint,
        )
        try:
            observation = self._build_observation(capture)

            self.runtime_manager.ensure_server_ready()
            log.info(
                "window_analysis",
                "model_ready",
                "Model runtime is ready.",
                **fields,
                endpoint=self.vision_model_client.endpoint,
            )
            started = time.perf_counter()
            analysis, vision_input = self.vision_model_client.analyze_image(capture.screenshot_path)
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = WindowAnalysisResult(
                capture=capture,
                observation=observation,
                analysis=analysis,
                latency_ms=latency_ms,
                model_endpoint=self.vision_model_client.endpoint,
                analyzed_at=datetime.now(UTC),
                vision_input=vision_input,
            )
            self._latest = result
            self._write_latest(result)
            log.info(
                "window_analysis",
                "latest_write",
                "Latest window analysis was written.",
                **fields,
                latency_ms=latency_ms,
                window_type=analysis.window_type,
                summary=analysis.summary,
            )
            if self.window_summary_store is not None:
                self.window_summary_store.record(
                    observation=observation,
                    window_type=analysis.window_type,
                    summary=analysis.summary,
                    key_points=analysis.key_points,
                    analysis=analysis,
                    capture=capture,
                    vision_input=vision_input,
                )
                log.info(
                    "window_analysis",
                    "summary_write",
                    "Window summary record was written.",
                    **fields,
                )
            if observation is not None and self.memory_service is not None:
                self.memory_service.save_observation(observation)
            return result
        except Exception as exc:
            log.exception(
                "window_analysis",
                "failure",
                "Observation analysis failed.",
                exc,
                **fields,
                endpoint=self.vision_model_client.endpoint,
            )
            raise

    def get_latest(self) -> WindowAnalysisResult | None:
        if self._latest is None:
            self._load_latest()
        return self._latest

    def _write_latest(self, result: WindowAnalysisResult) -> None:
        payload = result.model_dump(mode="json")
        self.runtime_store.set_json(
            "window:latest_analysis",
            payload,
            ttl_seconds=self.latest_analysis_ttl_seconds,
        )
        self.runtime_store.record_event("window:analysis", payload)

    def _load_latest(self) -> None:
        data = self.runtime_store.get_json("window:latest_analysis")
        if not isinstance(data, dict):
            return
        self._latest = WindowAnalysisResult.model_validate(data)

    def _build_observation(self, capture: RawWindowCapture) -> ObservationCard | None:
        if self.observation_builder is None:
            return None
        return self.observation_builder.build_from_capture(capture)

    @staticmethod
    def _capture_fields(capture: RawWindowCapture) -> dict[str, object]:
        return {
            "app_name": capture.app_name,
            "window_title": capture.window_title,
            "process_id": capture.process_id,
            "screenshot_path": str(capture.screenshot_path),
            "screenshot_hash": capture.screenshot_hash,
            "captured_at": capture.captured_at.isoformat(),
        }


@lru_cache
def get_window_analysis_service() -> ObservationAgent:
    settings = get_settings()
    return ObservationAgent(
        runtime_manager=get_model_runtime_manager(),
        vision_model_client=get_vision_model_client(),
        runtime_store=get_runtime_store(),
        observation_builder=get_observation_builder(),
        memory_service=get_memory_service(),
        window_summary_store=get_window_summary_store(),
        latest_analysis_ttl_seconds=settings.latest_analysis_ttl_seconds,
    )