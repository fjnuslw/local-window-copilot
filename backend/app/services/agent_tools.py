from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem
from app.services.local_copilot_identity import (
    is_local_copilot_title,
    mentions_local_copilot,
)
from app.services.memory import MemoryService
from app.services.profile_store import get_profile_store
from app.services.screenshot_crop import maybe_crop_for_question
from app.services.vision_model_client import VisionModelClient


AGENT_TOOL_NAMES = ("screen.look", "memory.search", "memory.remember")


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class AgentToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentToolResult:
    name: str
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    user_error: str | None = None


@dataclass(frozen=True)
class AgentToolContext:
    question: str
    latest: Any | None
    history_summaries: list[dict[str, Any]]
    chat_history: list[ChatSession]
    user_goals: list[dict[str, Any]]


@dataclass(frozen=True)
class _SelectedScreen:
    image_id: str
    image_path: Path
    app_name: str
    window_title: str
    window_type: str
    source: str
    summary: str = ""


class AgentToolRegistry:
    """Small model visible tool registry.

    Only three tools are exposed to the planner. Internal providers may use
    screenshots, profile md, runtime memory, and conversation history.
    """

    def __init__(self) -> None:
        self._specs = {
            spec.name: spec
            for spec in (
                AgentToolSpec(
                    name="screen.look",
                    description=(
                        "Inspect the user's current or recent screen image. Use this when "
                        "the user asks what is on a page/window/screenshot, asks about UI "
                        "details, visible text, buttons, layout, or says they want you to "
                        "look at the screen. Do not ask the user what to inspect if their "
                        "request already points at the visible screen."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The user's visual question in natural language.",
                            }
                        },
                        "required": ["question"],
                    },
                ),
                AgentToolSpec(
                    name="memory.search",
                    description=(
                        "Search local profile, useful notes, recent conversation, and recent "
                        "screen index. Use this for questions about preferences, prior talk, "
                        "project direction, or non-visual context."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The user's question or search query.",
                            }
                        },
                        "required": ["query"],
                    },
                ),
                AgentToolSpec(
                    name="memory.remember",
                    description=(
                        "Write a durable local note. Use only when the user explicitly asks "
                        "you to remember something or the note is clearly a stable preference."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "note": {
                                "type": "string",
                                "description": "One concise memory note to save.",
                            }
                        },
                        "required": ["note"],
                    },
                ),
            )
        }

    def manifest(self) -> list[dict[str, Any]]:
        return [self._specs[name].to_model_dict() for name in AGENT_TOOL_NAMES]

    def manifest_for_prompt(self) -> str:
        return json.dumps(self.manifest(), ensure_ascii=False, indent=2)

    def validate_calls(self, raw_calls: Any) -> list[AgentToolCall]:
        if not isinstance(raw_calls, list):
            raise ValueError("tool_calls must be a list.")
        calls: list[AgentToolCall] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                raise ValueError("Each tool call must be an object.")
            name = str(raw.get("name") or "").strip()
            if name not in self._specs:
                raise ValueError(f"Unknown tool: {name or '<empty>'}.")
            args = raw.get("arguments") or {}
            if not isinstance(args, dict):
                raise ValueError(f"Tool arguments for {name} must be an object.")
            calls.append(AgentToolCall(name=name, arguments=args))
        limit = get_settings().agent_tool_call_limit
        if len(calls) > limit:
            raise ValueError(f"Too many tool calls: {len(calls)} > {limit}.")
        return calls


class AgentToolRuntime:
    def __init__(
        self,
        *,
        vision_model_client: VisionModelClient,
        memory_service: MemoryService | None,
    ) -> None:
        self.vision_model_client = vision_model_client
        self.memory_service = memory_service

    def execute_many(
        self,
        calls: list[AgentToolCall],
        context: AgentToolContext,
    ) -> list[AgentToolResult]:
        return [self.execute(call, context) for call in calls]

    def execute(
        self,
        call: AgentToolCall,
        context: AgentToolContext,
    ) -> AgentToolResult:
        if call.name == "screen.look":
            return self._screen_look(call, context)
        if call.name == "memory.search":
            return self._memory_search(call, context)
        if call.name == "memory.remember":
            return self._memory_remember(call, context)
        raise ValueError(f"Unknown tool: {call.name}")

    def _screen_look(
        self,
        call: AgentToolCall,
        context: AgentToolContext,
    ) -> AgentToolResult:
        question = str(call.arguments.get("question") or context.question).strip()
        if not question:
            return AgentToolResult(
                name=call.name,
                ok=False,
                content="screen.look failed: empty question.",
                user_error="我没拿到要看的问题，不能执行看图。",
            )
        if context.latest is not None:
            title = str(getattr(context.latest.capture, "window_title", "") or "")
            observation = getattr(context.latest, "observation", None)
            if is_local_copilot_title(title):
                return AgentToolResult(
                    name=call.name,
                    ok=False,
                    content="screen.look failed: latest screen is local copilot UI.",
                    user_error="当前观察来自对话窗或桌宠自身，不能当作用户窗口。请先切回目标窗口并点击「观察」。",
                )
            if observation is not None and observation.privacy_state == "privacy":
                return AgentToolResult(
                    name=call.name,
                    ok=False,
                    content="screen.look failed: latest screen is privacy protected.",
                    user_error="当前窗口可能包含敏感信息，我不会基于截图继续回答。请切换到非敏感窗口后再观察。",
                )
        selected = self._select_screen(question, context)
        if selected is None:
            return AgentToolResult(
                name=call.name,
                ok=False,
                content="screen.look failed: no valid user screen image.",
                user_error="我现在没有可用的目标窗口截图。请先切回目标窗口并点击「观察」。",
            )

        settings = get_settings()
        visual_prompt = settings.visual_question_answer_prompt_path.read_text(
            encoding="utf-8"
        )
        effective_image_path, crop_reason = maybe_crop_for_question(
            question,
            selected.image_path,
        )
        chunks = list(
            self.vision_model_client.stream_visual_answer(
                question=question,
                image_path=effective_image_path,
                visual_prompt=visual_prompt,
                image_long_edge=settings.visual_answer_image_long_edge,
            )
        )
        visual_answer = "".join(chunks).strip()
        if not visual_answer:
            return AgentToolResult(
                name=call.name,
                ok=False,
                content="screen.look failed: vision model returned empty content.",
                user_error="我看了截图，但视觉模型没有返回内容。",
            )
        meta_lines = [
            f"- image_id: {selected.image_id}",
            f"- source: {selected.source}",
            f"- app: {selected.app_name}",
            f"- title: {selected.window_title}",
            f"- type: {selected.window_type}",
            f"- crop: {crop_reason}",
        ]
        if selected.summary:
            meta_lines.append(f"- indexed_summary: {selected.summary[:300]}")
        return AgentToolResult(
            name=call.name,
            ok=True,
            content="屏幕细看结果：\n"
            + visual_answer
            + "\n\n窗口元信息：\n"
            + "\n".join(meta_lines),
            data={
                "image_id": selected.image_id,
                "image_path": str(effective_image_path),
                "source": selected.source,
                "app_name": selected.app_name,
                "window_title": selected.window_title,
                "window_type": selected.window_type,
                "crop_reason": crop_reason,
            },
        )

    def _memory_search(
        self,
        call: AgentToolCall,
        context: AgentToolContext,
    ) -> AgentToolResult:
        query = str(call.arguments.get("query") or context.question).strip()
        settings = get_settings()
        parts: list[str] = []
        profile_packet = get_profile_store().profile_packet().strip()
        if profile_packet:
            parts.append("profile.md：\n" + profile_packet)

        if context.latest is not None and not self._is_invalid_latest(context.latest):
            parts.append(
                "当前窗口索引：\n"
                f"- 应用：{context.latest.capture.app_name}\n"
                f"- 标题：{context.latest.capture.window_title}\n"
                f"- 类型：{context.latest.analysis.window_type}\n"
                f"- 摘要：{context.latest.analysis.summary}"
            )

        recent_screen_lines: list[str] = []
        for item in context.history_summaries[-settings.window_summary_retrieve_count :]:
            title = str(item.get("window_title") or "")
            summary = str(item.get("summary") or "")
            if is_local_copilot_title(title) or mentions_local_copilot(summary):
                continue
            app = str(item.get("app_name") or "")
            ts = str(item.get("created_at") or "")[:19].replace("T", " ")
            recent_screen_lines.append(f"- [{ts}] {app} · {title}: {summary[:220]}")
        if recent_screen_lines:
            parts.append("最近屏幕索引：\n" + "\n".join(recent_screen_lines))

        if self.memory_service is not None:
            observation = (
                context.latest.observation
                if context.latest is not None and context.latest.observation is not None
                else None
            )
            memory_items = self.memory_service.retrieve_for_observation(
                observation,
                question=query,
                limit=settings.memory_retrieve_count,
            )
            memory_lines = [
                f"- {item.text[:settings.memory_item_max_chars]}"
                for item in memory_items
                if item.text.strip() and not mentions_local_copilot(item.text)
            ]
            if memory_lines:
                parts.append("相关记忆：\n" + "\n".join(memory_lines))

        conversation_lines: list[str] = []
        for session in context.chat_history[-settings.chat_history_turns :]:
            q = session.question.strip()
            a = session.answer.strip()
            if not q or not a or session.status != "done":
                continue
            if mentions_local_copilot(q) or mentions_local_copilot(a):
                continue
            conversation_lines.append(f"- 用户：{q[:180]}\n  助手：{a[:260]}")
        if conversation_lines:
            parts.append("最近对话：\n" + "\n".join(conversation_lines))

        if context.user_goals:
            goal_lines = []
            for item in context.user_goals[:5]:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("situation_label") or "").strip()
                question_text = str(item.get("question") or "").strip()
                if label and question_text:
                    goal_lines.append(f"- {label}: {question_text[:180]}")
            if goal_lines:
                parts.append("用户近期关注：\n" + "\n".join(goal_lines))

        content = "\n\n".join(parts).strip() or "没有检索到相关本地记忆。"
        return AgentToolResult(
            name=call.name,
            ok=True,
            content=content,
            data={"query": query},
        )

    def _memory_remember(
        self,
        call: AgentToolCall,
        context: AgentToolContext,
    ) -> AgentToolResult:
        note = str(call.arguments.get("note") or "").strip()
        if not note:
            return AgentToolResult(
                name=call.name,
                ok=False,
                content="memory.remember failed: empty note.",
                user_error="我没有拿到要记住的内容。",
            )
        if self.memory_service is None:
            return AgentToolResult(
                name=call.name,
                ok=False,
                content="memory.remember failed: memory service disabled.",
                user_error="当前记忆服务没有启用，不能写入记忆。",
            )
        observation_id = (
            context.latest.observation.observation_id
            if context.latest is not None and context.latest.observation is not None
            else None
        )
        item: MemoryItem = self.memory_service.remember_note(
            note=note,
            observation_id=observation_id,
            tags=["agent_note"],
        )
        return AgentToolResult(
            name=call.name,
            ok=True,
            content=f"已写入记忆：{item.text}",
            data={"memory_id": item.memory_id},
        )

    def _select_screen(
        self,
        question: str,
        context: AgentToolContext,
    ) -> _SelectedScreen | None:
        candidates: list[tuple[int, _SelectedScreen]] = []
        if context.latest is not None and not self._is_invalid_latest(context.latest):
            path = Path(context.latest.capture.screenshot_path)
            if path.exists():
                candidates.append(
                    (
                        1_000 + _score_screen(question, (
                            context.latest.capture.app_name,
                            context.latest.capture.window_title,
                            context.latest.analysis.window_type,
                            context.latest.analysis.summary,
                        )),
                        _SelectedScreen(
                            image_id="current",
                            image_path=path,
                            app_name=context.latest.capture.app_name,
                            window_title=context.latest.capture.window_title,
                            window_type=context.latest.analysis.window_type,
                            source="current_screen",
                            summary=context.latest.analysis.summary,
                        ),
                    )
                )

        for item in context.history_summaries:
            title = str(item.get("window_title") or "")
            summary = str(item.get("summary") or "")
            if is_local_copilot_title(title) or mentions_local_copilot(summary):
                continue
            path_text = str(item.get("screenshot_path") or "")
            if not path_text:
                continue
            path = Path(path_text)
            if not path.exists():
                continue
            app = str(item.get("app_name") or "")
            wtype = str(item.get("window_type") or "")
            score = _score_screen(question, (app, title, wtype, summary))
            if score <= 0:
                continue
            image_id = str(item.get("record_id") or item.get("screenshot_hash") or path.name)
            candidates.append(
                (
                    score,
                    _SelectedScreen(
                        image_id=image_id,
                        image_path=path,
                        app_name=app,
                        window_title=title,
                        window_type=wtype,
                        source="screen_history",
                        summary=summary,
                    ),
                )
            )

        if not candidates:
            return None
        candidates.sort(key=lambda row: row[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _is_invalid_latest(latest: Any) -> bool:
        title = str(getattr(latest.capture, "window_title", "") or "")
        if is_local_copilot_title(title):
            return True
        observation = getattr(latest, "observation", None)
        return bool(observation is not None and observation.privacy_state == "privacy")


def _score_screen(question: str, values: tuple[str, ...]) -> int:
    haystack = " ".join(values).lower()
    score = 0
    for token in _query_tokens(question):
        if token.lower() in haystack:
            score += len(token)
    return score


def _query_tokens(text: str) -> list[str]:
    tokens = [
        item.strip()
        for item in re.split(r"[，。？！\s,.\?!；;：:、（）()【】\[\]\"']+", text)
        if len(item.strip()) >= 2
    ]
    english = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", text)
    tokens.extend(english)
    return list(dict.fromkeys(tokens))


def get_agent_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry()
