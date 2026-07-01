from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


AssistantState = Literal["idle", "observing", "analyzing", "privacy", "error"]


class AssistantStateUpdate(BaseModel):
    state: AssistantState
    reason: str | None = Field(
        default=None,
        max_length=200,
        description="Optional short reason for observability.",
    )


class AssistantStateResponse(BaseModel):
    state: AssistantState
    updated_at: datetime
    reason: str | None = None
