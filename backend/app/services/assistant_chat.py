from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import queue
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem
from app.services.agent_tools import (
    AgentToolContext,
    AgentToolRuntime,
    get_agent_tool_registry,
)
from app.services.assistant_state import get_assistant_state_service
from app.services.local_copilot_identity import is_local_copilot_title
from app.services.memory import MemoryService, get_memory_service
from app.services.model_runtime import get_model_runtime_manager
from app.services.profile_store import get_profile_store
from app.services.runtime_store import RuntimeStore, get_runtime_store
from app.services.context_summary import (
    CompactExecutionResult,
    CompactPlannerConfig,
    CompactStateStore,
    CompactSummaryConfig,
    compact_execution_result_to_dict,
    compact_lock_to_payload,
    compact_metrics_to_payload,
    execute_compact,
    rolling_summary_to_payload,
)
from app.services.context_budget import (
    CONTEXT_BUDGET_SAFETY_TOKENS,
    ContextAssembler,
    build_chat_segment_hints,
    budget_report_to_dict,
    budget_tool_result_content,
    calculate_context_input_limit,
    tool_result_budget_report_to_dict,
)
from app.services.vision_model_client import (
    BASE_PREFIX,
    VisionModelClient,
    build_chat_messages,
    build_context_packet,
    format_window_observation,
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

class ContextBudgetExceededError(RuntimeError):
    """Raised when rough input tokens exceed the configured input limit."""

CHAT_IMAGE_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def build_context_budget_preview(
    *,
    messages: list[dict[str, Any]],
    profile_packet: str,
    context_packet: str,
    compact_summary: str = "",
    ctx_size: int,
    answer_max_tokens: int,
) -> dict[str, Any]:
    """生成可审计的 token 预算预览。

    不修改 messages；调用方决定用于展示或请求前拦截。
    """
    input_limit = calculate_context_input_limit(
        ctx_size=ctx_size,
        answer_max_tokens=answer_max_tokens,
        safety_tokens=CONTEXT_BUDGET_SAFETY_TOKENS,
    )
    hints = build_chat_segment_hints(
        messages,
        has_profile_packet=bool(profile_packet and profile_packet.strip()),
        has_compact_summary=bool(compact_summary and compact_summary.strip()),
        has_context_packet=bool(context_packet and context_packet.strip()),
    )
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(messages, hints=hints)
    report = assembler.build_report(
        segments, ctx_size=ctx_size, input_limit=input_limit
    )
    payload = budget_report_to_dict(report)
    estimated_input_tokens = report.estimated_input_tokens
    input_usage_percent = (
        round(estimated_input_tokens / input_limit * 100, 1)
        if input_limit > 0
        else 0.0
    )
    payload.update(
        {
            "estimate_source": "rough",
            "output_reserve": answer_max_tokens,
            "safety_tokens": CONTEXT_BUDGET_SAFETY_TOKENS,
            "input_usage_percent": input_usage_percent,
        }
    )
    return payload

@dataclass(frozen=True)
class AnswerContext:
    latest: Any | None
    context_latest: Any | None
    history_summaries: list[dict[str, Any]]
    chat_history: list[ChatSession]
    memory_items: list[MemoryItem]
    profile_packet: str
    context_packet: str
    messages: list[dict[str, Any]]
    registered_tools: list[dict[str, Any]]
    selected_image: str | None
    selected_reason: str
    image_path: Path | None
    compact_summary: str = ""
    compact_summary_tokens: int = 0
    compact_covered_session_ids: list[str] = field(default_factory=list)

class ChatAgent:
    """Chat agent with one model-visible context tool.

    The live path mirrors a normal tool-calling agent: profile, memory, and
    dialogue are sent directly; current/recent window observations are only
    exposed through memory.search when the model chooses to call it.
    """

    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
        analysis_service: ObservationAgent,
        vision_model_client: VisionModelClient,
        memory_service: MemoryService | None = None,
        window_summary_store: WindowSummaryStore | None = None,
        clear_history_on_start: bool = True,
    ) -> None:
        self.runtime_store = runtime_store
        self.analysis_service = analysis_service
        self.vision_model_client = vision_model_client
        self.memory_service = memory_service
        self.window_summary_store = window_summary_store
        # prefix cache 冻结快照（spec §6.1）：会话级冻结 profile_packet，
        # 保证 system+profile 前缀字节级一致，llama.cpp KV cache 稳定命中。
        self._frozen_profile_packet: str | None = None
        if clear_history_on_start:
            self.runtime_store.delete(CHAT_CURRENT_KEY)
            self.runtime_store.delete(CHAT_HISTORY_KEY)

    def _get_profile_packet(self) -> str:
        """获取冻结的 profile_packet。首次调用时缓存，后续复用同一对象。"""
        if self._frozen_profile_packet is None:
            self._frozen_profile_packet = get_profile_store().profile_packet()
        return self._frozen_profile_packet

    async def ask(
        self,
        question: str,
        *,
        image_base64: str | None = None,
        image_name: str | None = None,
        image_mime: str | None = None,
    ) -> ChatSession:
        user_image_path, stored_image_name = self._save_user_image(
            image_base64=image_base64,
            image_name=image_name,
            image_mime=image_mime,
        )
        self._archive_finished_current()
        await get_window_watcher_service().stop()
        await get_assistant_state_service().set_state(
            "analyzing",
            reason="user-question-started",
        )
        now = datetime.now(UTC)
        session = ChatSession(
            session_id=uuid.uuid4().hex,
            question=question.strip(),
            image_path=str(user_image_path) if user_image_path is not None else None,
            image_name=stored_image_name,
            created_at=now,
            updated_at=now,
        )
        self._save(session)
        asyncio.create_task(self._answer(session), name="assistant-chat-answer")
        return session

    async def ask_stream(
        self,
        question: str,
        *,
        image_base64: str | None = None,
        image_name: str | None = None,
        image_mime: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        user_image_path, stored_image_name = self._save_user_image(
            image_base64=image_base64,
            image_name=image_name,
            image_mime=image_mime,
        )
        self._archive_finished_current()
        await get_window_watcher_service().stop()
        await get_assistant_state_service().set_state(
            "analyzing",
            reason="user-question-started",
        )
        now = datetime.now(UTC)
        session = ChatSession(
            session_id=uuid.uuid4().hex,
            question=question.strip(),
            image_path=str(user_image_path) if user_image_path is not None else None,
            image_name=stored_image_name,
            created_at=now,
            updated_at=now,
        )
        self._save(session)

        events: queue.Queue[tuple[str, Any]] = queue.Queue()

        def emit(event: str, data: Any) -> None:
            events.put((event, data))

        def worker() -> None:
            try:
                latest = self.analysis_service.get_latest()
                self._prepare_answer_session(session, latest)
                self._stream_model_answer(
                    session,
                    latest,
                    on_chunk=lambda chunk: emit("delta", {"text": chunk}),
                )
                emit("done", session.model_dump(mode="json"))
            except Exception as exc:
                session.status = "error"
                session.error = str(exc)
                session.updated_at = datetime.now(UTC)
                self._save(session)
                self._trace(session, "session_error", {"error": str(exc)})
                self._append_history(session)
                emit("error", {"error": str(exc), "session": session.model_dump(mode="json")})
            finally:
                emit("end", None)

        thread = threading.Thread(target=worker, name="assistant-chat-stream-answer", daemon=True)
        thread.start()
        yield {"event": "session", "data": session.model_dump(mode="json")}

        try:
            while True:
                event, data = await asyncio.to_thread(events.get)
                if event == "end":
                    break
                yield {"event": event, "data": data}
        finally:
            await get_assistant_state_service().set_state(
                "idle",
                reason="user-question-finished",
            )
            if get_settings().auto_start_window_watch:
                get_window_watcher_service().start()

    def _save_user_image(
        self,
        *,
        image_base64: str | None,
        image_name: str | None,
        image_mime: str | None,
    ) -> tuple[Path | None, str | None]:
        if not image_base64:
            return None, None

        settings = get_settings()
        raw = image_base64.strip()
        mime = (image_mime or "").strip().lower()
        if raw.lower().startswith("data:") and "," in raw:
            header, raw = raw.split(",", 1)
            header_mime = header[5:].split(";", 1)[0].strip().lower()
            mime = mime or header_mime
        mime = mime or "image/png"
        ext = CHAT_IMAGE_MIME_EXTENSIONS.get(mime)
        if ext is None:
            raise ValueError(f"Unsupported image type: {mime}")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Image data is not valid base64.") from exc
        if not data:
            raise ValueError("Image data is empty.")
        if len(data) > settings.chat_image_max_bytes:
            mb = settings.chat_image_max_bytes / 1024 / 1024
            raise ValueError(f"Image is too large. Limit: {mb:.1f} MB.")

        settings.chat_upload_dir.mkdir(parents=True, exist_ok=True)
        target = settings.chat_upload_dir / f"{uuid.uuid4().hex}{ext}"
        target.write_bytes(data)
        display_name = Path(image_name or target.name).name[:160] or target.name
        return target, display_name

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

    def _archive_finished_current(self) -> None:
        current = self.current()
        if current is None or current.status not in {"done", "error"}:
            return
        self._append_history(current)

    async def resume_auto_watch(self) -> None:
        self.runtime_store.delete(CHAT_CURRENT_KEY)
        await get_assistant_state_service().set_state(
            "idle",
            reason="user-resumed-auto-watch",
        )
        get_window_watcher_service().start()

    async def _answer(self, session: ChatSession) -> None:
        try:
            latest = self.analysis_service.get_latest()
            self._prepare_answer_session(session, latest)
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
            if get_settings().auto_start_window_watch:
                get_window_watcher_service().start()

    def _prepare_answer_session(self, session: ChatSession, latest) -> None:
        self._trace(
            session,
            "session_started",
            {
                "question": session.question,
                "latest_present": latest is not None,
                "user_image": (
                    {"path": session.image_path, "name": session.image_name}
                    if session.image_path
                    else None
                ),
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

    def _request_budget_payload(
        self,
        *,
        context: AnswerContext,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        settings = get_settings()
        return build_context_budget_preview(
            messages=messages,
            profile_packet=context.profile_packet,
            context_packet=context.context_packet,
            compact_summary=context.compact_summary,
            ctx_size=max(1, settings.minicpm_ctx_size),
            answer_max_tokens=settings.answer_max_tokens,
        )

    @staticmethod
    def _context_budget_trace_payload(
        *,
        payload: dict[str, Any],
        phase: str,
        stream_round: int | None,
        messages_count: int,
    ) -> dict[str, Any]:
        return {
            "phase": phase,
            "stream_round": stream_round,
            "messages_count": messages_count,
            "ctx_size": payload.get("ctx_size"),
            "input_limit": payload.get("input_limit"),
            "estimated_input_tokens": payload.get("estimated_input_tokens"),
            "input_usage_percent": payload.get("input_usage_percent"),
            "over_limit": payload.get("over_limit"),
            "totals": payload.get("totals") or {},
            "actions": payload.get("actions") or [],
        }

    @staticmethod
    def _context_budget_error_message(payload: dict[str, Any]) -> str:
        estimated = int(payload.get("estimated_input_tokens") or 0)
        input_limit = int(payload.get("input_limit") or 0)
        return (
            f"上下文预算超限：本次请求约 {estimated} tokens，"
            f"输入上限 {input_limit}。请缩小问题范围、清理历史，或运行 compact 后重试。"
        )

    def _ensure_messages_within_budget(
        self,
        session: ChatSession,
        context: AnswerContext,
        messages: list[dict[str, Any]],
        *,
        phase: str,
        stream_round: int | None = None,
    ) -> dict[str, Any]:
        payload = self._request_budget_payload(context=context, messages=messages)
        trace_payload = self._context_budget_trace_payload(
            payload=payload,
            phase=phase,
            stream_round=stream_round,
            messages_count=len(messages),
        )
        if payload.get("over_limit"):
            self._trace(session, "context_budget.over_limit", trace_payload)
            raise ContextBudgetExceededError(
                self._context_budget_error_message(payload)
            )
        self._trace(session, "context_budget.checked", trace_payload)
        return payload

    def _stream_model_answer(
        self,
        session: ChatSession,
        latest,
        *,
        on_chunk: Callable[[str], None] | None = None,
    ) -> None:
        get_model_runtime_manager().ensure_server_ready()
        context = self._build_answer_context(session.question, latest, session=session)
        registry = get_agent_tool_registry()
        openai_tools = registry.openai_tools()
        self._trace(
            session,
            "context_built",
            {
                "answer_mode": "tool_auto",
                "history_summaries_count": len(context.history_summaries),
                "chat_history_count": len(context.chat_history),
                "memory_count": len(context.memory_items),
                "profile_chars": len(context.profile_packet),
                "context_chars": len(context.context_packet),
                "selected_image": context.selected_image,
                "selected_reason": context.selected_reason,
                "registered_tool_count": len(context.registered_tools),
            },
        )
        messages = list(context.messages)
        self._trace(session, "answer_messages", {"messages": messages})

        # ---- Phase 1: Probe（仅在无图片时执行）----
        used_tools = False
        if context.image_path is None:
            self._ensure_messages_within_budget(
                session, context, messages, phase="probe"
            )
            tool_probe = self.vision_model_client.complete_chat_response(
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
            )
            assistant_message = self._extract_response_message(tool_probe)
            tool_calls = registry.from_openai_tool_calls(assistant_message.get("tool_calls"))
            if tool_calls:
                used_tools = True
                self._trace(
                    session,
                    "tool_calls",
                    {"calls": [call.__dict__ for call in tool_calls]},
                )
                messages.append({
                    "role": "assistant",
                    "content": assistant_message.get("content") or "",
                    "tool_calls": assistant_message.get("tool_calls") or [],
                })
                messages = self._execute_and_append_tools(
                    session, messages, tool_calls, registry, context,
                )
            else:
                # probe content 不可信（详见 spec §2.2.1），丢弃。
                probe_content = str(assistant_message.get("content") or "")
                self._trace(
                    session,
                    "probe_without_tool_call",
                    {
                        "content_chars": len(probe_content),
                        "content_preview": probe_content[:80],
                    },
                )

        # ---- Phase 2: Stream + 可选的流式工具调用循环 ----
        # stream 总是携带 tools，即使 probe 已执行过工具。
        # 这样 probe 失败时模型在 stream 阶段仍有第二次机会调 memory.search。
        max_stream_rounds = 2
        for stream_round in range(max_stream_rounds):
            self._ensure_messages_within_budget(
                session,
                context,
                messages,
                phase="stream",
                stream_round=stream_round,
            )
            self._trace(
                session,
                "answer_messages_final",
                {
                    "stream_round": stream_round,
                    "messages_count": len(messages),
                    "tools_in_stream": openai_tools is not None,
                },
            )
            for chunk in self.vision_model_client.stream_chat(
                messages=messages,
                image_path=context.image_path,
                image_long_edge=get_settings().model_image_long_edge,
                tools=openai_tools if context.image_path is None else None,
            ):
                self._append(session, chunk)
                if on_chunk is not None:
                    on_chunk(chunk)

            # 检查流式输出中是否发起了工具调用
            stream_tool_calls = self.vision_model_client.last_stream_tool_calls
            if not stream_tool_calls:
                break  # 没有新工具调用，stream 完成

            used_tools = True
            parsed_calls = registry.from_openai_tool_calls(stream_tool_calls)
            self._trace(
                session,
                "stream_tool_calls",
                {
                    "round": stream_round,
                    "calls": [call.__dict__ for call in parsed_calls],
                },
            )
            # 追加 assistant message（含 tool_calls）
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": stream_tool_calls,
            })
            # 执行工具并追加结果
            messages = self._execute_and_append_tools(
                session, messages, parsed_calls, registry, context,
            )
            # 循环回到 stream 顶部，用更新后的 messages（含工具结果）继续生成

        session.status = "done"
        session.updated_at = datetime.now(UTC)
        self._save(session)
        self._append_history(session)
        self._index_session_to_fts(session)
        self._maybe_compact_history(session=session, force=False, source="auto")
        self._trace(
            session,
            "session_finished",
            {
                "status": session.status,
                "answer_chars": len(session.answer),
                "error": session.error,
            },
        )

    def _index_session_to_fts(self, session: ChatSession) -> None:
        """对话结束后写入持久 FTS5 索引（spec §6.2）。"""
        try:
            from app.services.chat_history_index import get_chat_history_index
            get_chat_history_index().index_session(
                session_id=session.session_id,
                question=session.question,
                answer=session.answer,
                created_at=session.created_at,
            )
        except Exception:
            # FTS5 索引失败不应阻断对话完成
            pass

    def _execute_and_append_tools(
        self,
        session: ChatSession,
        messages: list[dict[str, Any]],
        tool_calls: list[Any],
        registry: Any,
        context: AnswerContext,
    ) -> list[dict[str, Any]]:
        """执行工具调用并将结果追加到 messages。返回更新后的 messages（同一列表）。"""
        runtime = AgentToolRuntime(
            memory_service=self.memory_service,
        )
        tool_context = AgentToolContext(
            question=session.question,
            latest=context.context_latest,
            history_summaries=context.history_summaries,
            chat_history=context.chat_history,
        )
        results = runtime.execute_many(tool_calls, tool_context)
        self._trace(
            session,
            "tool_results",
            {"results": [result.__dict__ for result in results]},
        )
        settings = get_settings()
        remaining_tool_budget = max(1, settings.tool_result_budget_tokens)
        budget_reports = []
        total_final_tokens = 0
        for result in results:
            budgeted = budget_tool_result_content(
                result.content,
                tool_name=result.name,
                call_id=result.call_id,
                item_limit_tokens=settings.tool_result_item_budget_tokens,
                remaining_budget_tokens=remaining_tool_budget,
            )
            report = budgeted.report
            budget_reports.append(report)
            total_final_tokens += report.final_tokens
            remaining_tool_budget = max(0, remaining_tool_budget - report.final_tokens)
            messages.append({
                "role": "tool",
                "tool_call_id": result.call_id or "",
                "name": result.model_name or result.name.replace(".", "_"),
                "content": budgeted.content,
            })
        self._trace(
            session,
            "tool_result_budget",
            {
                "reports": [
                    tool_result_budget_report_to_dict(report)
                    for report in budget_reports
                ],
                "total_final_tokens": total_final_tokens,
                "truncated_count": sum(1 for report in budget_reports if report.truncated),
            },
        )
        return messages

    def _build_answer_context(
        self,
        question: str,
        latest,
        *,
        session: ChatSession | None = None,
    ) -> AnswerContext:
        settings = get_settings()
        history_summaries: list[dict[str, Any]] = []
        if self.window_summary_store is not None:
            history_summaries = self.window_summary_store.recent(
                limit=settings.window_summary_history_limit
            )

        chat_history = self.history(limit=settings.chat_history_turns)
        chat_history = [
            item for item in reversed(chat_history)
            if session is None or item.session_id != session.session_id
        ]
        profile_packet = self._get_profile_packet()
        compact_summary = ""
        compact_summary_tokens = 0
        compact_covered_session_ids: list[str] = []
        if settings.compact_enabled:
            compact_state = CompactStateStore(runtime_store=self.runtime_store).load_summary()
            compact_summary = compact_state.summary.strip()
            compact_summary_tokens = compact_state.estimate.tokens
            compact_covered_session_ids = list(compact_state.covered_session_ids)
        context_latest = latest if self._is_valid_latest_for_context(latest) else None
        memory_items: list[MemoryItem] = []
        if settings.memory_enabled and self.memory_service is not None:
            memory_items = [
                item for item in self.memory_service.recent_items(
                    limit=settings.memory_retrieve_count
                )
                if item.kind != "analysis_summary"
            ]
        context_packet = build_context_packet(
            current_app_name=None,
            current_window_title=None,
            current_window_type=None,
            current_summary=None,
            current_key_points=[],
            current_regions=[],
            current_visible_text=[],
            current_ui_elements=[],
            current_entities=[],
            current_uncertain_areas=[],
            current_vision_input=None,
            history_window_summaries=[],
            memory_items=memory_items,
        )
        messages = build_chat_messages(
            question=question,
            profile_packet=profile_packet,
            compact_summary=compact_summary,
            current_app_name=None,
            current_window_title=None,
            current_window_type=None,
            current_summary=None,
            current_key_points=[],
            current_regions=[],
            current_visible_text=[],
            current_ui_elements=[],
            current_entities=[],
            current_uncertain_areas=[],
            current_vision_input=None,
            history_window_summaries=[],
            chat_history=chat_history,
            memory_items=memory_items,
        )
        selected_image = None
        image_path = None
        selected_reason = "tool_available"
        if session is not None and session.image_path:
            candidate = Path(session.image_path)
            if candidate.exists():
                image_path = candidate
                selected_image = session.image_name or candidate.name
                selected_reason = "user_upload"
        return AnswerContext(
            latest=latest,
            context_latest=context_latest,
            history_summaries=history_summaries,
            chat_history=chat_history,
            memory_items=memory_items,
            profile_packet=profile_packet,
            context_packet=context_packet,
            compact_summary=compact_summary,
            compact_summary_tokens=compact_summary_tokens,
            compact_covered_session_ids=compact_covered_session_ids,
            messages=messages,
            registered_tools=get_agent_tool_registry().manifest(),
            selected_image=selected_image,
            selected_reason=selected_reason,
            image_path=image_path,
        )
    @staticmethod
    def _extract_response_message(response: dict[str, Any]) -> dict[str, Any]:
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Invalid chat completion response for tool call probe.") from exc
        if not isinstance(message, dict):
            raise RuntimeError("Chat completion message is not an object.")
        return message

    @staticmethod
    def _is_valid_latest_for_context(latest: Any | None) -> bool:
        if latest is None:
            return False
        title = str(getattr(latest.capture, "window_title", "") or "")
        if is_local_copilot_title(title):
            return False
        observation = getattr(latest, "observation", None)
        return not bool(observation is not None and observation.privacy_state == "privacy")

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
        self.runtime_store.record_event(
            "assistant:interaction_trace",
            {
                "session_id": session.session_id,
                "question": session.question,
                "stage": stage,
                "payload": payload,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    def _trace_compact(
        self,
        stage: str,
        payload: dict[str, Any],
        *,
        session: ChatSession | None = None,
    ) -> None:
        self.runtime_store.record_event(
            "assistant:interaction_trace",
            {
                "session_id": session.session_id if session is not None else None,
                "question": session.question if session is not None else "",
                "stage": stage,
                "payload": payload,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    def _compact_planner_config(self) -> CompactPlannerConfig:
        settings = get_settings()
        return CompactPlannerConfig(
            raw_tail_turns=settings.compact_raw_tail_turns,
            batch_session_limit=settings.compact_batch_session_limit,
            source_budget_tokens=settings.compact_source_budget_tokens,
            uncovered_session_threshold=settings.compact_uncovered_session_threshold,
            history_trigger_tokens=settings.compact_history_trigger_tokens,
        )

    def _compact_summary_config(self) -> CompactSummaryConfig:
        settings = get_settings()
        return CompactSummaryConfig(
            model_max_input_tokens=settings.compact_model_max_input_tokens,
            model_max_output_tokens=settings.compact_model_max_output_tokens,
            source_budget_tokens=settings.compact_source_budget_tokens,
            template_budget_tokens=settings.compact_template_budget_tokens,
            previous_summary_budget_tokens=settings.compact_previous_summary_budget_tokens,
            target_summary_tokens=settings.compact_target_summary_tokens,
        )

    def _compact_trace_payload(
        self,
        result: CompactExecutionResult,
    ) -> dict[str, Any]:
        plan = result.plan
        return {
            "attempted": result.attempted,
            "compacted": result.compacted,
            "status": result.status,
            "trigger": result.trigger,
            "source_session_ids": [s.session_id for s in plan.source_sessions],
            "tail_session_ids": [s.session_id for s in plan.tail_sessions],
            "uncovered_session_ids": list(plan.uncovered_session_ids),
            "skipped_covered_session_ids": list(plan.skipped_covered_session_ids),
            "skipped_budget_session_ids": list(plan.skipped_budget_session_ids),
            "estimated_source_tokens": plan.estimated_source_tokens,
            "estimated_tail_tokens": plan.estimated_tail_tokens,
            "summary_tokens": result.summary_state.estimate.tokens,
            "summary_chars": result.summary_state.estimate.chars,
            "covered_session_count": len(result.summary_state.covered_session_ids),
            "metrics_status": result.metrics.last_status,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "actions": list(result.actions),
            "started_at": result.started_at,
            "finished_at": result.finished_at,
        }

    def _trace_compact_result(
        self,
        result: CompactExecutionResult,
        *,
        session: ChatSession | None = None,
    ) -> None:
        payload = self._compact_trace_payload(result)
        if result.attempted:
            self._trace_compact("context_summary.started", payload, session=session)
        if result.status == "ok":
            self._trace_compact("context_summary.succeeded", payload, session=session)
        elif result.status == "error":
            self._trace_compact("context_summary.failed", payload, session=session)
        elif result.status == "locked":
            self._trace_compact("context_summary.locked", payload, session=session)
        elif result.status == "skipped":
            self._trace_compact("context_summary.skipped", payload, session=session)

    def _compact_history_sessions(self) -> list[ChatSession]:
        settings = get_settings()
        return [
            item for item in self.history(limit=settings.history_retention_limit)
            if item.status == "done"
        ]

    def _maybe_compact_history(
        self,
        *,
        session: ChatSession | None,
        force: bool,
        source: str,
    ) -> dict[str, Any]:
        settings = get_settings()
        if not settings.compact_enabled:
            payload = {
                "attempted": False,
                "compacted": False,
                "status": "disabled",
                "source": source,
                "actions": ["compact_disabled"],
            }
            self._trace_compact("context_summary.skipped", payload, session=session)
            return payload
        if source == "auto" and not settings.compact_auto_enabled:
            payload = {
                "attempted": False,
                "compacted": False,
                "status": "auto_disabled",
                "source": source,
                "actions": ["compact_auto_disabled"],
            }
            self._trace_compact("context_summary.skipped", payload, session=session)
            return payload

        state_store = CompactStateStore(runtime_store=self.runtime_store)
        result = execute_compact(
            sessions=self._compact_history_sessions(),
            state_store=state_store,
            model_client=self.vision_model_client,
            planner_config=self._compact_planner_config(),
            summary_config=self._compact_summary_config(),
            force=force,
            source=source,
            lock_ttl_seconds=settings.compact_timeout_seconds,
        )
        self._trace_compact_result(result, session=session)
        return compact_execution_result_to_dict(result)

    def compact_history(self) -> dict[str, Any]:
        return self._maybe_compact_history(
            session=None,
            force=True,
            source="manual",
        )

    def compact_status(self) -> dict[str, Any]:
        settings = get_settings()
        compact_store = CompactStateStore(runtime_store=self.runtime_store)
        summary = compact_store.load_summary()
        metrics = compact_store.load_metrics()
        lock = compact_store.load_lock()

        summary_payload = rolling_summary_to_payload(summary)
        estimate_payload = summary_payload.get("estimate") or {}
        covered_ids = list(summary.covered_session_ids)
        metrics_payload = compact_metrics_to_payload(metrics)
        lock_payload = compact_lock_to_payload(lock) if lock is not None else None

        return {
            "enabled": bool(settings.compact_enabled),
            "auto_enabled": bool(settings.compact_auto_enabled),
            "summary": {
                "version": int(summary_payload.get("version") or 0),
                "present": bool(str(summary_payload.get("summary") or "").strip()),
                "chars": int(estimate_payload.get("chars") or 0),
                "tokens": int(estimate_payload.get("tokens") or 0),
                "updated_at": summary_payload.get("updated_at"),
                "covered_session_count": len(covered_ids),
                "source_session_count": int(summary_payload.get("source_session_count") or 0),
                "covered_session_ids_tail": covered_ids[-12:],
                "text": str(summary_payload.get("summary") or ""),
            },
            "metrics": metrics_payload,
            "lock": {
                "active": lock_payload is not None,
                "owner": lock_payload.get("owner") if lock_payload else None,
                "source": lock_payload.get("source") if lock_payload else None,
                "started_at": lock_payload.get("started_at") if lock_payload else None,
                "expires_at": lock_payload.get("expires_at") if lock_payload else None,
            },
            "planner": {
                "raw_tail_turns": settings.compact_raw_tail_turns,
                "batch_session_limit": settings.compact_batch_session_limit,
                "source_budget_tokens": settings.compact_source_budget_tokens,
                "uncovered_session_threshold": settings.compact_uncovered_session_threshold,
                "history_trigger_tokens": settings.compact_history_trigger_tokens,
            },
        }

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
        settings = get_settings()
        latest = self.analysis_service.get_latest()
        context = self._build_answer_context(question, latest)

        def _short_hash(text: str) -> str:
            return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

        ctx_size = max(1, settings.minicpm_ctx_size)
        context_budget = build_context_budget_preview(
            messages=context.messages,
            profile_packet=context.profile_packet,
            context_packet=context.context_packet,
            compact_summary=context.compact_summary,
            ctx_size=ctx_size,
            answer_max_tokens=settings.answer_max_tokens,
        )
        message_chars = context_budget["estimated_chars"]
        estimated_tokens = context_budget["estimated_input_tokens"]
        usage_percent = round(estimated_tokens / ctx_size * 100, 1)
        tool_manifest = get_agent_tool_registry().manifest_for_prompt()
        cache_hashes = {
            "base_prefix_hash": _short_hash(BASE_PREFIX),
            "profile_hash": _short_hash(context.profile_packet),
            "context_hash": _short_hash(context.context_packet),
            "tool_manifest_hash": _short_hash(tool_manifest),
        }
        latest_observation_text = (
            format_window_observation(
                summary=context.context_latest.analysis.summary,
                key_points=context.context_latest.analysis.key_points,
                regions=context.context_latest.analysis.regions,
                visible_text=context.context_latest.analysis.visible_text,
                ui_elements=context.context_latest.analysis.ui_elements,
                entities=context.context_latest.analysis.entities,
                uncertain_areas=context.context_latest.analysis.uncertain_areas,
                vision_input=(
                    context.context_latest.vision_input.model_dump(mode="json")
                    if context.context_latest.vision_input
                    else None
                ),
            )
            if context.context_latest
            else ""
        )
        history_chars = sum(len(s.question) + len(s.answer) for s in context.chat_history)
        history_observations_chars = sum(
            len(str(item.get("summary", ""))) for item in context.history_summaries
        )
        memory_chars = sum(len(m.text) for m in context.memory_items)
        usage = {
            "answer_mode": "tool_auto",
            "answer_mode_raw": "tool_auto",
            "selected_image": context.selected_image,
            "selected_reason": context.selected_reason,
            "missing_visual_image": False,
            "current_window": (
                f"{context.context_latest.capture.app_name} · {context.context_latest.capture.window_title}"
                if context.context_latest
                else None
            ),
            "profile_chars": len(context.profile_packet),
            "current_observation_chars": 0,
            "recent_observations_count": 0,
            "recent_observations_chars": 0,
            "current_summary_chars": 0,
            "recent_summaries_count": 0,
            "recent_summaries_chars": 0,
            "memory_count": len(context.memory_items),
            "memory_chars": memory_chars,
            "compact_summary_chars": len(context.compact_summary),
            "compact_summary_tokens": context.compact_summary_tokens,
            "compact_covered_session_count": len(context.compact_covered_session_ids),
            "dialogue_turns": len(context.chat_history),
            "dialogue_chars": history_chars,
            "registered_tool_count": len(context.registered_tools),
            "available_current_observation_chars": len(latest_observation_text) if context.context_latest else 0,
            "available_recent_observations_count": len(context.history_summaries),
            "available_recent_observations_chars": history_observations_chars,
            "tool_manifest_chars": len(tool_manifest),
            "total_chars": message_chars,
            "estimated_tokens": estimated_tokens,
            "ctx_size": ctx_size,
            "usage_percent": usage_percent,
            "estimate_source": "rough",
            "input_limit": context_budget["input_limit"],
            "input_usage_percent": context_budget["input_usage_percent"],
            "over_limit": context_budget["over_limit"],
        }
        return {
            "question": question,
            "latest_analysis_present": latest is not None,
            "latest_observation": latest_observation_text or (latest.analysis.summary if latest else None),
            "latest_summary": latest.analysis.summary if latest else None,
            "latest_analysis": latest.analysis.model_dump(mode="json") if latest else None,
            "chat_history_turns_setting": settings.chat_history_turns,
            "memory_retrieve_count_setting": settings.memory_retrieve_count,
            "window_summary_retrieve_count_setting": settings.window_summary_retrieve_count,
            "window_summary_history_limit_setting": settings.window_summary_history_limit,
            "chat_include_screenshot": settings.chat_include_screenshot,
            "memory_enabled": settings.memory_enabled,
            "chat_history": [s.model_dump(mode="json") for s in context.chat_history],
            "memory_items": [m.model_dump(mode="json") for m in context.memory_items],
            "window_observations": context.history_summaries,
            "window_summaries": context.history_summaries,
            "profile_packet": context.profile_packet,
            "context_packet": context.context_packet,
            "compact_summary": context.compact_summary,
            "compact_covered_session_ids": context.compact_covered_session_ids,
            "messages": context.messages,
            "cache_hashes": cache_hashes,
            "personality": {
                "enabled": settings.personality_enabled,
                "name": settings.personality_name,
                "traits": settings.personality_traits,
                "answer_style_hint": settings.answer_style_hint,
            },
            "answer_mode": "tool_auto",
            "answer_mode_raw": "tool_auto",
            "selected_image": context.selected_image,
            "selected_reason": context.selected_reason,
            "registered_tools": context.registered_tools,
            "usage": usage,
            "context_budget": context_budget,
        }


    def context_status(self) -> dict[str, Any]:
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
            "answer_mode": str(usage.get("answer_mode") or "tool_auto"),
            "registered_tool_count": int(usage.get("registered_tool_count") or 0),
            "profile_chars": int(usage.get("profile_chars") or 0),
            "memory_count": int(usage.get("memory_count") or 0),
            "compact_summary_tokens": int(usage.get("compact_summary_tokens") or 0),
            "compact_covered_session_count": int(usage.get("compact_covered_session_count") or 0),
            "dialogue_turns": int(usage.get("dialogue_turns") or 0),
            "recent_observations_count": int(usage.get("recent_observations_count") or 0),
            "available_current_observation_chars": int(usage.get("available_current_observation_chars") or 0),
            "available_recent_observations_count": int(usage.get("available_recent_observations_count") or 0),
            "available_recent_observations_chars": int(usage.get("available_recent_observations_chars") or 0),
            "current_window": usage.get("current_window"),
        }

    def clear_history(self) -> int:
        existing = self.runtime_store.get_json(CHAT_HISTORY_KEY)
        count = len(existing) if isinstance(existing, list) else 0
        self.runtime_store.delete(CHAT_HISTORY_KEY)
        compact_store = CompactStateStore(runtime_store=self.runtime_store)
        compact_store.clear_summary()
        compact_store.clear_lock()
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
