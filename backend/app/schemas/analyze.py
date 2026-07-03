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


class WindowAnalysisResult(BaseModel):
    capture: RawWindowCapture
    observation: ObservationCard | None = None
    analysis: WindowAnalysis
    latency_ms: int
    model_endpoint: str
    analyzed_at: datetime | None = None


class ModelRuntimeInfo(BaseModel):
    endpoint: str
    model_path: str
    mmproj_path: str
    running: bool
