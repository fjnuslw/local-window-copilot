from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class WindowBounds(BaseModel):
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class ForegroundWindowInfo(BaseModel):
    window_handle: int
    app_name: str | None = None
    process_id: int | None = None
    window_title: str = ""
    window_bounds: WindowBounds

    def signature(self) -> tuple[object, ...]:
        bounds = self.window_bounds
        return (
            self.window_handle,
            self.process_id,
            self.app_name,
            self.window_title,
            bounds.left,
            bounds.top,
            bounds.right,
            bounds.bottom,
        )


class RawWindowCapture(BaseModel):
    app_name: str | None = None
    process_id: int | None = None
    window_title: str
    window_bounds: WindowBounds
    screenshot_path: Path
    screenshot_hash: str
    captured_at: datetime


class WindowWatchStatus(BaseModel):
    running: bool
    interval_seconds: float = Field(gt=0)
    capture_min_interval_seconds: float = Field(ge=0)
    analysis_min_interval_seconds: float = Field(ge=0)
    last_capture: RawWindowCapture | None = None
    last_analysis: dict[str, Any] | None = None
    last_error: str | None = None
    captures_count: int = Field(ge=0)
    analyses_count: int = Field(ge=0)
