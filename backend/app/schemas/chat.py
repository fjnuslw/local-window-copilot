from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ChatStatus = Literal["streaming", "done", "error"]


class ChatQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    image_base64: str | None = None
    image_name: str | None = Field(default=None, max_length=160)
    image_mime: str | None = Field(default=None, max_length=80)


class ChatSession(BaseModel):
    session_id: str
    question: str
    image_path: str | None = None
    image_name: str | None = None
    answer: str = ""
    status: ChatStatus = "streaming"
    error: str | None = None
    resume_required: bool = True
    created_at: datetime
    updated_at: datetime


class ChatHistoryResponse(BaseModel):
    items: list[ChatSession] = Field(default_factory=list)
