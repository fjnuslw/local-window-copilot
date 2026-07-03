from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ChatStatus = Literal["streaming", "done", "error"]


class ChatQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ChatSession(BaseModel):
    session_id: str
    question: str
    answer: str = ""
    status: ChatStatus = "streaming"
    error: str | None = None
    resume_required: bool = True
    created_at: datetime
    updated_at: datetime


class ChatHistoryResponse(BaseModel):
    items: list[ChatSession] = Field(default_factory=list)
