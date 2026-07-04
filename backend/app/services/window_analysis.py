from __future__ import annotations

import time
from datetime import UTC, datetime
from functools import lru_cache

from app.core.config import get_settings
from app.schemas.analyze import WindowAnalysis, WindowAnalysisResult
from app.schemas.observation import ObservationCard
from app.schemas.window import RawWindowCapture
from app.services.memory import MemoryService, get_memory_service
from app.services.model_runtime import ModelRuntimeManager, get_model_runtime_manager
from app.services.observation_builder import ObservationBuilder, get_observation_builder
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
        observation = self._build_observation(capture)
        if observation is not None and observation.privacy_state == "privacy":
            result = self._privacy_paused_result(capture, observation)
            self._latest = result
            self._write_latest(result)
            return result

        self.runtime_manager.ensure_server_ready()
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
        if self.window_summary_store is not None:
            self.window_summary_store.record(
                observation=observation,
                window_type=analysis.window_type,
                summary=analysis.summary,
                key_points=analysis.key_points,
                capture=capture,
                vision_input=vision_input,
            )
        if observation is not None and self.memory_service is not None:
            self.memory_service.save_observation(observation)
            self.memory_service.remember_analysis(
                observation=observation,
                analysis=analysis,
                latency_ms=latency_ms,
            )
        return result

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

    def _privacy_paused_result(
        self,
        capture: RawWindowCapture,
        observation: ObservationCard,
    ) -> WindowAnalysisResult:
        analysis = WindowAnalysis(
            window_type="unknown",
            summary="当前窗口可能包含敏感信息，已暂停自动分析。",
            key_points=[
                "系统检测到敏感窗口关键词。",
                "本次没有把截图发送给模型。",
                "如需继续，请切换到非敏感窗口或关闭隐私暂停。",
            ],
            candidate_questions=[],
            caution="不要在密码、验证码、支付或私钥窗口中启用自动分析。",
        )
        return WindowAnalysisResult(
            capture=capture,
            observation=observation,
            analysis=analysis,
            latency_ms=0,
            model_endpoint=self.vision_model_client.endpoint,
            analyzed_at=datetime.now(UTC),
        )


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
