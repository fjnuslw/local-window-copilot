from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.window import WindowBounds


ObservationSource = Literal["window_title", "process", "screenshot", "analysis", "memory"]
PrivacyState = Literal["normal", "privacy"]


class ObservationCard(BaseModel):
    observation_id: str = Field(default_factory=lambda: f"obs_{uuid.uuid4().hex}")
    app_name: str | None = None
    process_id: int | None = None
    window_title: str = ""
    window_kind_hint: str = "unknown"
    window_bounds: WindowBounds
    screenshot_path: Path
    screenshot_hash: str
    source_signals: list[ObservationSource] = Field(default_factory=list)
    privacy_state: PrivacyState = "normal"
    privacy_reasons: list[str] = Field(default_factory=list)
    captured_at: datetime
