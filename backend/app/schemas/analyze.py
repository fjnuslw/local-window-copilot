from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.observation import ObservationCard
from app.schemas.window import RawWindowCapture


WindowType = Literal[
    "error_dialog",
    "form",
    "document",
    "webpage",
    "ide",
    "settings",
    "installer",
    "chat",
    "file_explorer",
    "unknown",
]


class CandidateQuestion(BaseModel):
    question: str
    category: str
    reason: str
    priority: float = Field(ge=0, le=1)


class WindowAnalysis(BaseModel):
    window_type: WindowType
    summary: str
    key_points: list[str]
    candidate_questions: list[CandidateQuestion]
    caution: str | None = None
    uncertain_areas: list[str] = Field(default_factory=list)


class VisionInput(BaseModel):
    """送入模型的图片元信息，用于调试与追溯。"""
    original_size: list[int] = Field(default_factory=list, description="[width, height] 原图像素")
    sent_size: list[int] = Field(default_factory=list, description="[width, height] 缩放后送入模型的像素")
    long_edge: int = 0
    detail_mode: str = ""


class WindowAnalysisResult(BaseModel):
    capture: RawWindowCapture
    observation: ObservationCard | None = None
    analysis: WindowAnalysis
    latency_ms: int
    model_endpoint: str
    analyzed_at: datetime | None = None
    vision_input: VisionInput | None = None


class ModelRuntimeInfo(BaseModel):
    endpoint: str
    model_path: str
    mmproj_path: str
    running: bool
