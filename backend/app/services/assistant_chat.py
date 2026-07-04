from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem
from app.services.agent_orchestrator import (
    TOOL_ANSWER_SYSTEM_PROMPT,
    TOOL_PLANNER_SYSTEM_PROMPT,
    AgentOrchestrator,
)
from app.services.agent_tools import (
    AgentToolContext,
    AgentToolRuntime,
    get_agent_tool_registry,
)
from app.services.assistant_state import get_assistant_state_service
from app.services.local_copilot_identity import is_local_copilot_title
from app.services.memory import MemoryService, get_memory_service
from app.services.profile_store import get_profile_store
from app.services.interaction_policy import get_interaction_policy
from app.services.runtime_store import RuntimeStore, get_runtime_store
from app.services.situation_builder import build_situation
from app.services.vision_model_client import (
    BASE_PREFIX,
    VisionModelClient,
    build_context_packet,
    get_vision_model_client,
)
from app.services.window_analysis import ObservationAgent, get_window_analysis_service
from app.services.window_summary_store import (
    WindowSummaryStore,
    get_window_summary_store,
)
from app.services.window_watcher import get_window_watcher_service


CHAT_CURRENT_KEY = "assistant:chat:current"
CHAT_HISTORY_KEY = "assistant:chat:history"
# 用户最近目标和困惑（spec §6.2：记录用户真正关心的目标、反复犹豫的判断）
COMPANION_GOALS_KEY = "companion:user_goals"
COMPANION_GOALS_LIMIT = 10


class ChatAgent:
    """对话 agent：读取 profile/摘要/记忆，构造 KV cache 友好的分层 messages，流式回答。

    职责边界（见 kv_cache_profile_and_agent_split_spec_zh.md §4.2）：
    - 输入：question + profile_packet + context_packet + dialogue_tail
    - 输出：assistant answer（流式）
    - 暂停自动观察避免上下文漂移；默认纯文本，不带截图
    """

    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
        analysis_service: ObservationAgent,
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
            self._trace(
                session,
                "session_started",
                {
                    "question": session.question,
                    "latest_present": latest is not None,
                    "latest_window": (
                        {
                            "app_name": latest.capture.app_name,
                            "window_title": latest.capture.window_title,
                            "window_type": latest.analysis.window_type,
                            "screenshot_path": str(latest.capture.screenshot_path),
                        }
                        if latest
                        else None
                    ),
                },
            )

            # 记录用户最近目标和困惑（spec §6.2）
            self._record_user_goal(session.question)

            if settings.memory_enabled and self.memory_service is not None:
                self.memory_service.remember_user_question(
                    question=session.question,
                    observation_id=(
                        latest.observation.observation_id
                        if latest and latest.observation
                        else None
                    ),
                )
            await asyncio.to_thread(self._stream_model_answer, session, latest)
        except Exception as exc:
            session.status = "error"
            session.error = str(exc)
            session.updated_at = datetime.now(UTC)
            self._save(session)
            self._trace(session, "session_error", {"error": str(exc)})
            self._append_history(session)
        finally:
            await get_assistant_state_service().set_state(
                "idle",
                reason="user-question-finished",
            )

    def _stream_model_answer(self, session: ChatSession, latest) -> None:
        settings = get_settings()
        history_summaries: list[dict[str, Any]] = []
        if self.window_summary_store is not None:
            history_summaries = self.window_summary_store.recent(
                limit=settings.window_summary_retrieve_count
            )

        chat_history = self.history(limit=settings.chat_history_turns)
        chat_history = [
            item for item in reversed(chat_history) if item.session_id != session.session_id
        ]
        profile_packet = get_profile_store().profile_packet()
        companion_prompt = settings.companion_chat_prompt_path.read_text(encoding="utf-8")
        registry = get_agent_tool_registry()
        runtime = AgentToolRuntime(
            vision_model_client=self.vision_model_client,
            memory_service=self.memory_service if settings.memory_enabled else None,
        )
        context = AgentToolContext(
            question=session.question,
            latest=latest,
            history_summaries=history_summaries,
            chat_history=chat_history,
            user_goals=self._get_user_goals(),
        )
        self._trace(
            session,
            "context_built",
            {
                "history_summaries_count": len(history_summaries),
                "chat_history_count": len(chat_history),
                "user_goals_count": len(self._get_user_goals()),
                "profile_chars": len(profile_packet),
            },
        )
        orchestrator = AgentOrchestrator(
            vision_model_client=self.vision_model_client,
            registry=registry,
            runtime=runtime,
        )
        try:
            chunks = orchestrator.stream_answer(
                question=session.question,
                profile_packet=profile_packet,
                chat_history=chat_history,
                user_goals=self._get_user_goals(),
                context=context,
                companion_prompt=companion_prompt,
                trace=lambda stage, payload: self._trace(session, stage, payload),
            )
            for chunk in chunks:
                self._append(session, chunk)
        except ValueError as exc:
            self._append(session, f"工具规划失败：{exc}", done=True)
            self._trace(session, "planner_error", {"error": str(exc)})
            return
        session.status = "done"
        session.updated_at = datetime.now(UTC)
        self._save(session)
        self._append_history(session)
        self._trace(
            session,
            "session_finished",
            {
                "status": session.status,
                "answer_chars": len(session.answer),
                "error": session.error,
            },
        )
        if settings.memory_enabled and self.memory_service is not None and session.answer.strip():
            self.memory_service.remember_assistant_answer(
                answer=session.answer,
                observation_id=(
                    latest.observation.observation_id
                    if latest and latest.observation
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

    def _trace(self, session: ChatSession, stage: str, payload: dict[str, Any]) -> None:
        max_chars = get_settings().interaction_trace_payload_max_chars
        safe_payload: Any = payload
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > max_chars:
            safe_payload = {
                "truncated": True,
                "original_chars": len(text),
                "text": text[:max_chars],
            }
        self.runtime_store.record_event(
            "assistant:interaction_trace",
            {
                "session_id": session.session_id,
                "question": session.question,
                "stage": stage,
                "payload": safe_payload,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

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

    def _record_user_goal(self, question: str) -> None:
        """记录用户最近目标和困惑（spec §6.2）。

        只记录有意义的情境（非 ambient_idle），避免把闲聊也存进去。
        记录内容：situation_label / mood / intent / question / recorded_at。
        """
        situation = build_situation(current_question=question)
        label = situation.get("situation_label", "ambient_idle")
        mood = situation.get("user_mood_hint", "neutral")
        intent = situation.get("likely_intent", "chat")
        # 只记录有意义的情境：方向反思、工作求助、犹豫、不满
        if label == "ambient_idle" and mood == "neutral" and intent == "chat":
            return
        existing = self.runtime_store.get_json(COMPANION_GOALS_KEY)
        items = existing if isinstance(existing, list) else []
        entry = {
            "situation_label": label,
            "user_mood_hint": mood,
            "likely_intent": intent,
            "question": question[:200],
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        # 去重：同一 situation_label 只保留最近一条
        items = [
            item for item in items
            if isinstance(item, dict) and item.get("situation_label") != label
        ]
        items.insert(0, entry)
        self.runtime_store.set_json(COMPANION_GOALS_KEY, items[:COMPANION_GOALS_LIMIT])

    def _get_user_goals(self) -> list[dict[str, Any]]:
        """读取用户最近目标和困惑记录。"""
        existing = self.runtime_store.get_json(COMPANION_GOALS_KEY)
        return existing if isinstance(existing, list) else []

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
        profile_packet = get_profile_store().profile_packet()
        context_packet = build_context_packet(
            current_app_name=latest.capture.app_name if latest else None,
            current_window_title=latest.capture.window_title if latest else None,
            current_window_type=latest.analysis.window_type if latest else None,
            current_summary=latest.analysis.summary if latest else None,
            current_key_points=latest.analysis.key_points if latest else [],
            history_window_summaries=history_summaries,
            memory_items=memory_items,
            memory_item_max_chars=settings.memory_item_max_chars,
        )

        def _short_hash(text: str) -> str:
            return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

        registered_tools = get_agent_tool_registry().manifest()
        tool_manifest = get_agent_tool_registry().manifest_for_prompt()
        agent_mode = "agent_orchestrated"
        cache_hashes = {
            "base_prefix_hash": _short_hash(BASE_PREFIX),
            "profile_hash": _short_hash(profile_packet),
            "context_hash": _short_hash(context_packet),
            "tool_manifest_hash": _short_hash(tool_manifest),
        }

        # 上下文使用率估算（见 ambient_companion_product_spec_zh.md §7）
        # estimated_tokens = chars / 2，usage = estimated_tokens / minicpm_ctx_size
        history_chars = sum(len(s.question) + len(s.answer) for s in chat_history)
        history_summaries_chars = sum(
            len(str(item.get("summary", ""))) for item in history_summaries
        )
        memory_chars = sum(len(m.text) for m in memory_items)
        profile_chars = len(profile_packet)
        context_chars = len(context_packet)
        base_prefix_chars = len(BASE_PREFIX)
        planner_chars = len(TOOL_PLANNER_SYSTEM_PROMPT) + len(tool_manifest) + len(question)
        answer_prompt_chars = len(TOOL_ANSWER_SYSTEM_PROMPT)
        companion_prompt_chars = len(
            settings.companion_chat_prompt_path.read_text(encoding="utf-8")
        )
        total_chars = (
            base_prefix_chars
            + planner_chars
            + answer_prompt_chars
            + companion_prompt_chars
            + profile_chars
            + context_chars
            + history_chars
            + memory_chars
            + history_summaries_chars
        )

        estimated_tokens = max(1, total_chars // 2)
        ctx_size = max(1, settings.minicpm_ctx_size)
        usage_percent = round(estimated_tokens / ctx_size * 100, 1)

        usage = {
            "answer_mode": agent_mode,
            "answer_mode_raw": agent_mode,
            "selected_image": None,
            "selected_reason": "tool_planner_runtime",
            "missing_visual_image": False,
            "current_window": (
                f"{latest.capture.app_name} · {latest.capture.window_title}"
                if latest
                else None
            ),
            # 扁平字段，供 WebUI 直接渲染
            "profile_chars": profile_chars,
            "current_summary_chars": (
                len(latest.analysis.summary) if latest else 0
            ),
            "recent_summaries_count": len(history_summaries),
            "recent_summaries_chars": history_summaries_chars,
            "memory_count": len(memory_items),
            "memory_chars": memory_chars,
            "dialogue_turns": len(chat_history),
            "dialogue_chars": history_chars,
            "registered_tool_count": len(registered_tools),
            "tool_manifest_chars": len(tool_manifest),
            "total_chars": total_chars,
            "estimated_tokens": estimated_tokens,
            "ctx_size": ctx_size,
            "usage_percent": usage_percent,
        }

        # 情境状态（spec §6.3 / §8.2）：替代"直接展示摘要"的产品层
        situation = build_situation(
            chat_history=chat_history,
            recent_window_summaries=history_summaries,
            current_question=question,
        )
        # 主动提示策略（spec §8.3）：debug 视图展示"如果用户没在发言，是否可以主动提示"
        should_nudge, nudge_line = get_interaction_policy().should_speak(
            situation, user_speaking=False, record=False
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
            "profile_packet": profile_packet,
            "context_packet": context_packet,
            "cache_hashes": cache_hashes,
            "personality": {
                "enabled": settings.personality_enabled,
                "name": settings.personality_name,
                "traits": settings.personality_traits,
                "answer_style_hint": settings.answer_style_hint,
            },
            "answer_mode": agent_mode,
            "answer_mode_raw": agent_mode,
            "selected_image": None,
            "selected_reason": "tool_planner_runtime",
            "registered_tools": registered_tools,
            "usage": usage,
            # 情境状态与主动提示（spec §6.3 / §8.2 / §8.3）
            "situation": situation,
            "proactive_nudge": {
                "should_speak": should_nudge,
                "line": nudge_line,
            },
            "user_goals": self._get_user_goals(),
        }

    def context_status(self) -> dict[str, Any]:
        """轻量上下文状态：供悬浮对话窗展示预算与模型，不调用模型。"""
        current = self.current()
        question = current.question if current is not None else ""
        preview = self.inspect_context(question or "当前对话上下文状态")
        usage = preview.get("usage") if isinstance(preview.get("usage"), dict) else {}
        settings = get_settings()
        usage_percent = float(usage.get("usage_percent") or 0)
        remaining_percent = max(0.0, round(100.0 - usage_percent, 1))
        return {
            "model_name": settings.minicpm_model_name,
            "model_endpoint": settings.llama_chat_completions_endpoint,
            "ctx_size": int(usage.get("ctx_size") or settings.minicpm_ctx_size),
            "estimated_tokens": int(usage.get("estimated_tokens") or 0),
            "usage_percent": usage_percent,
            "remaining_percent": remaining_percent,
            "answer_mode": str(usage.get("answer_mode") or "agent_orchestrated"),
            "registered_tool_count": int(usage.get("registered_tool_count") or 0),
            "profile_chars": int(usage.get("profile_chars") or 0),
            "memory_count": int(usage.get("memory_count") or 0),
            "dialogue_turns": int(usage.get("dialogue_turns") or 0),
            "recent_summaries_count": int(usage.get("recent_summaries_count") or 0),
            "current_window": usage.get("current_window"),
        }

    def clear_history(self) -> int:
        """清空历史对话，返回被清除的条数。"""
        existing = self.runtime_store.get_json(CHAT_HISTORY_KEY)
        count = len(existing) if isinstance(existing, list) else 0
        self.runtime_store.delete(CHAT_HISTORY_KEY)
        return count


@lru_cache
def get_assistant_chat_service() -> ChatAgent:
    return ChatAgent(
        runtime_store=get_runtime_store(),
        analysis_service=get_window_analysis_service(),
        vision_model_client=get_vision_model_client(),
        memory_service=get_memory_service(),
        window_summary_store=get_window_summary_store(),
    )
