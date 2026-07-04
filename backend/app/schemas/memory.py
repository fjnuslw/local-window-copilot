from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.observation import ObservationCard


MemoryScope = Literal["working", "session"]
MemoryKind = Literal[
    "observation",
    "analysis_summary",
    "user_question",
    "assistant_answer",
    "user_note",
]


class MemoryItem(BaseModel):
    memory_id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex}")
    scope: MemoryScope
    kind: MemoryKind
    text: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_observation_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySnapshot(BaseModel):
    working_observation: ObservationCard | None = None
    relevant_items: list[MemoryItem] = Field(default_factory=list)
