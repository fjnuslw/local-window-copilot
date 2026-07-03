from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem
from app.services.assistant_state import get_assistant_state_service
from app.services.memory import MemoryService, get_memory_service
from app.services.runtime_store import RuntimeStore, get_runtime_store
from app.services.vision_model_client import (
    VisionModelClient,
    build_chat_messages,
    get_vision_model_client,
)
from app.services.window_analysis import WindowAnalysisService, get_window_analysis_service
from app.services.window_summary_store import (
    WindowSummaryStore,
    get_window_summary_store,
)
from app.services.window_watcher import get_window_watcher_service


CHAT_CURRENT_KEY = "assistant:chat:current"
CHAT_HISTORY_KEY = "assistant:chat:history"


class AssistantChatService:
    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
        analysis_service: WindowAnalysisService,
        vision_model_client: VisionModelClient,
        memory_service: MemoryService | None = None,
        window_summary_store: WindowSummaryStore | None = None,
    ) -> None:
        self.runtime_store = runtime_store
        self.analysis_service = analysis_service
        self.vision_model_client = vision_model_client
        self.memory_service = memory_service
        self.window_summary_store = window_summary_store

    async def ask(self, question: str) -> ChatSession:
        await get_window_watcher_service().stop()
        await get_assistant_state_service().set_state(
            "analyzing",
            reason="user-question-started",
        )
        now = datetime.now(UTC)
        session = ChatSession(
            session_id=uuid.uuid4().hex,
            question=question.strip(),
            created_at=now,
            updated_at=now,
        )
        self._save(session)
        asyncio.create_task(self._answer(session), name="assistant-chat-answer")
        return session

    def current(self) -> ChatSession | None:
        data = self.runtime_store.get_json(CHAT_CURRENT_KEY)
        if not isinstance(data, dict):
            return None
        return ChatSession.model_validate(data)

    def history(self, *, limit: int | None = None) -> list[ChatSession]:
        data = self.runtime_store.get_json(CHAT_HISTORY_KEY)
        if not isinstance(data, list):
            return []
        sessions = [
            ChatSession.model_validate(item)
            for item in data
            if isinstance(item, dict)
        ]
        retention = get_settings().history_retention_limit
        effective_limit = retention if limit is None else max(0, min(limit, retention))
        return sessions[:effective_limit]

    async def resume_auto_watch(self) -> None:
        self.runtime_store.delete(CHAT_CURRENT_KEY)
        await get_assistant_state_service().set_state(
            "idle",
            reason="user-resumed-auto-watch",
        )
        get_window_watcher_service().start()

    async def _answer(self, session: ChatSession) -> None:
        try:
            settings = get_settings()
            latest = self.analysis_service.get_latest()
            if latest is None:
                self._append(
                    session,
                    "我还没有当前窗口的分析结果。请先让自动观察完成一次页面摘要，再继续提问。",
                    done=True,
                )
                return
            if latest.observation is not None and latest.observation.privacy_state == "privacy":
                self._append(
                    session,
                    "当前窗口可能包含敏感信息，我不会基于截图继续回答。请切换到非敏感窗口后再提问。",
                    done=True,
                )
                return
            if settings.memory_enabled and self.memory_service is not None:
                self.memory_service.remember_user_question(
                    question=session.question,
                    observation_id=(
                        latest.observation.observation_id
                        if latest.observation
                        else None
                    ),
                )
            await asyncio.to_thread(self._stream_model_answer, session, latest)
        except Exception as exc:
            session.status = "error"
            session.error = str(exc)
            session.updated_at = datetime.now(UTC)
            self._save(session)
            self._append_history(session)
        finally:
            await get_assistant_state_service().set_state(
                "idle",
                reason="user-question-finished",
            )

    def _stream_model_answer(self, session: ChatSession, latest) -> None:
        settings = get_settings()
        memory_items: list[MemoryItem] = []
        if settings.memory_enabled and self.memory_service is not None:
            memory_items = self.memory_service.retrieve_for_observation(
                latest.observation,
                question=session.question,
                limit=settings.memory_retrieve_count,
            )
        # 对话历史：旧→新，排除当前 session
        chat_history = self.history(limit=settings.chat_history_turns)
        chat_history = [
            item for item in reversed(chat_history) if item.session_id != session.session_id
        ]
        # 历史窗口摘要（最近 N 条）
        history_summaries: list[dict[str, Any]] = []
        if self.window_summary_store is not None:
            history_summaries = self.window_summary_store.recent(
                limit=settings.window_summary_retrieve_count
            )
        # 构建 messages 多轮结构
        messages = build_chat_messages(
            question=session.question,
            current_summary=latest.analysis.summary,
            current_key_points=latest.analysis.key_points,
            history_window_summaries=history_summaries,
            chat_history=chat_history,
            memory_items=memory_items,
            question_max_chars=settings.chat_history_question_max_chars,
            answer_max_chars=settings.chat_history_answer_max_chars,
            memory_item_max_chars=settings.memory_item_max_chars,
            personality_enabled=settings.personality_enabled,
            personality_name=settings.personality_name,
            personality_traits=settings.personality_traits,
            system_prompt_prefix=settings.system_prompt_prefix,
            answer_style_hint=settings.answer_style_hint,
        )
        # 是否带当前截图（默认不带，纯文本对话 agent）
        screenshot_path = None
        if settings.chat_include_screenshot:
            screenshot_path = latest.capture.screenshot_path
        for chunk in self.vision_model_client.stream_chat(
            messages=messages,
            image_path=screenshot_path,
        ):
            self._append(session, chunk)
        session.status = "done"
        session.updated_at = datetime.now(UTC)
        self._save(session)
        self._append_history(session)
        if settings.memory_enabled and self.memory_service is not None and session.answer.strip():
            self.memory_service.remember_assistant_answer(
                answer=session.answer,
                observation_id=(
                    latest.observation.observation_id
                    if latest.observation
                    else None
                ),
            )

    def _append(self, session: ChatSession, text: str, *, done: bool = False) -> None:
        session.answer += text
        session.status = "done" if done else "streaming"
        session.updated_at = datetime.now(UTC)
        self._save(session)

    def _save(self, session: ChatSession) -> None:
        payload = session.model_dump(mode="json")
        self.runtime_store.set_json(CHAT_CURRENT_KEY, payload)
        self.runtime_store.record_event("assistant:chat", payload)

    def _append_history(self, session: ChatSession) -> None:
        existing = self.runtime_store.get_json(CHAT_HISTORY_KEY)
        items = existing if isinstance(existing, list) else []
        payload = session.model_dump(mode="json")
        next_items = [
            item
            for item in items
            if isinstance(item, dict) and item.get("session_id") != session.session_id
        ]
        next_items.insert(0, payload)
        retention = get_settings().history_retention_limit
        self.runtime_store.set_json(CHAT_HISTORY_KEY, next_items[:retention])

    def inspect_context(self, question: str) -> dict[str, Any]:
        """上下文透视：返回下一次回答时将被注入的历史与记忆，供 webui 调试。"""
        settings = get_settings()
        latest = self.analysis_service.get_latest()
        chat_history = self.history(limit=settings.chat_history_turns)
        chat_history = [
            item for item in reversed(chat_history)
        ]
        memory_items: list[MemoryItem] = []
        if settings.memory_enabled and self.memory_service is not None and latest is not None:
            memory_items = self.memory_service.retrieve_for_observation(
                latest.observation,
                question=question,
                limit=settings.memory_retrieve_count,
            )
        history_summaries: list[dict[str, Any]] = []
        if self.window_summary_store is not None:
            history_summaries = self.window_summary_store.recent(
                limit=settings.window_summary_retrieve_count
            )
        return {
            "question": question,
            "latest_analysis_present": latest is not None,
            "latest_summary": latest.analysis.summary if latest else None,
            "chat_history_turns_setting": settings.chat_history_turns,
            "memory_retrieve_count_setting": settings.memory_retrieve_count,
            "window_summary_retrieve_count_setting": settings.window_summary_retrieve_count,
            "chat_include_screenshot": settings.chat_include_screenshot,
            "memory_enabled": settings.memory_enabled,
            "chat_history": [s.model_dump(mode="json") for s in chat_history],
            "memory_items": [m.model_dump(mode="json") for m in memory_items],
            "window_summaries": history_summaries,
            "personality": {
                "enabled": settings.personality_enabled,
                "name": settings.personality_name,
                "traits": settings.personality_traits,
                "answer_style_hint": settings.answer_style_hint,
            },
        }

    def clear_history(self) -> int:
        """清空历史对话，返回被清除的条数。"""
        existing = self.runtime_store.get_json(CHAT_HISTORY_KEY)
        count = len(existing) if isinstance(existing, list) else 0
        self.runtime_store.delete(CHAT_HISTORY_KEY)
        return count


@lru_cache
def get_assistant_chat_service() -> AssistantChatService:
    return AssistantChatService(
        runtime_store=get_runtime_store(),
        analysis_service=get_window_analysis_service(),
        vision_model_client=get_vision_model_client(),
        memory_service=get_memory_service(),
        window_summary_store=get_window_summary_store(),
    )
