from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


AssistantState = Literal[
    "idle",
    "observing",
    "analyzing",
    "privacy",
    "error",
    # Ambient Companion 情感状态（见 ambient_companion_product_spec_zh.md §8.1）
    "present",
    "curious",
    "focused",
    "waiting",
    "concerned",
    "cheering",
]


class AssistantStateUpdate(BaseModel):
    state: AssistantState
    reason: str | None = Field(
        default=None,
        max_length=200,
        description="Optional short reason for observability.",
    )
    error: str | None = Field(
        default=None,
        description="Optional error detail for observability.",
    )


class AssistantStateResponse(BaseModel):
    state: AssistantState
    updated_at: datetime
    reason: str | None = None
    error: str | None = None