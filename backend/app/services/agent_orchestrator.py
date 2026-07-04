from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Iterator

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.services.agent_tools import (
    AGENT_TOOL_NAMES,
    AgentToolCall,
    AgentToolContext,
    AgentToolRegistry,
    AgentToolResult,
    AgentToolRuntime,
)
from app.services.local_copilot_identity import mentions_local_copilot
from app.services.vision_model_client import (
    VisionModelClient,
    build_companion_messages,
)


TOOL_PLANNER_SYSTEM_PROMPT = """你是本地桌面伙伴的工具规划器。
你只负责判断是否需要工具，不负责回答用户。

可用工具只有：
screen.look
memory.search
memory.remember
none

输出要求：
- 优先只输出一行工具名，例如：screen.look
- 也可以输出 JSON：{"tool":"screen.look"}
- 不要输出解释、Markdown、步骤名或自然语言。

判断原则：
- 用户要求看屏幕、页面、窗口、截图、图片、可见文字、界面细节、画面里的选项，选 screen.look。
- 用户问过去说过什么、偏好、已有记忆、项目背景、历史窗口记录，选 memory.search。
- 只有用户明确说“记住/以后记得/保存这个偏好”时，选 memory.remember。
- 普通陪伴、情绪回应、想法讨论、无需外部上下文的问题，选 none。
- 如果本轮用户附带了图片，并且问题与这张图有关，选 screen.look。
- 如果不确定是否需要看图，不要用模板反问；根据用户意图自由选择最有帮助的工具。
"""


TOOL_ANSWER_SYSTEM_PROMPT = """你是用户的本地桌面伙伴。
你会收到工具执行结果，然后直接回答用户。

原则：
- 工具已经执行过，不要再要求用户说明“想看什么”。
- 如果 screen.look 返回了屏幕/图片细看结果，把它当作视觉证据；你负责理解、取舍和回答。
- 如果用户问“推荐哪个/哪个好看/选哪个/怎么排”，必须给出明确选择和理由；不要只描述页面。
- 如果用户问具体文字或细节，优先列出能看清的原文、标题、按钮、数字或位置。
- 如果工具结果说看不清，就明确说看不清，不编造。
- 不要输出 JSON、日志、工具名或接口名。
- 不要声称能自动点击、输入或操作电脑。
- 用中文，自然、简洁、有温度。
"""


@dataclass(frozen=True)
class AgentPlan:
    tool_calls: list[AgentToolCall]
    raw_text: str


class AgentOrchestrator:
    def __init__(
        self,
        *,
        vision_model_client: VisionModelClient,
        registry: AgentToolRegistry,
        runtime: AgentToolRuntime,
    ) -> None:
        self.vision_model_client = vision_model_client
        self.registry = registry
        self.runtime = runtime

    def plan(
        self,
        *,
        question: str,
        user_image_name: str | None = None,
        trace: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AgentPlan:
        settings = get_settings()
        user_lines = ["用户问题：", question.strip()]
        if user_image_name:
            user_lines.extend(["", f"本轮用户附带图片：{user_image_name}"])
        messages = [
            {"role": "system", "content": TOOL_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user_lines)},
        ]
        if trace is not None:
            trace("planner_request", {"messages": messages})
        raw_text = self.vision_model_client.complete_chat(
            messages=messages,
            temperature=settings.tool_planner_temperature,
            max_tokens=settings.tool_planner_max_tokens,
        )
        if trace is not None:
            trace("planner_raw_response", {"text": raw_text})
        calls = self._parse_tool_name_plan(raw_text, question)
        if trace is not None:
            trace(
                "planner_parsed",
                {
                    "tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in calls
                    ]
                },
            )
        return AgentPlan(tool_calls=calls, raw_text=raw_text)

    def _parse_tool_name_plan(self, raw_text: str, question: str) -> list[AgentToolCall]:
        tool = _normalize_tool_name(raw_text, ("none", *AGENT_TOOL_NAMES))
        if tool == "none":
            return []
        if tool == "screen.look":
            calls = [AgentToolCall(name=tool, arguments={"question": question})]
        elif tool == "memory.search":
            calls = [AgentToolCall(name=tool, arguments={"query": question})]
        elif tool == "memory.remember":
            calls = [AgentToolCall(name=tool, arguments={"note": question})]
        else:
            raise ValueError(
                "Tool planner returned invalid tool name. "
                f"Expected one of: {', '.join(('none', *AGENT_TOOL_NAMES))}. "
                f"Raw: {raw_text[:120]}"
            )
        return self.registry.validate_calls([
            {"name": call.name, "arguments": call.arguments}
            for call in calls
        ])

    def stream_answer(
        self,
        *,
        question: str,
        profile_packet: str,
        chat_history: list[ChatSession],
        user_goals: list[dict[str, Any]],
        context: AgentToolContext,
        companion_prompt: str,
        trace: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> Iterator[str]:
        plan = self.plan(
            question=question,
            user_image_name=context.user_image_name,
            trace=trace,
        )
        tool_results = self.runtime.execute_many(plan.tool_calls, context)
        if trace is not None:
            trace(
                "tool_results",
                {
                    "results": [
                        {
                            "name": result.name,
                            "ok": result.ok,
                            "content": result.content,
                            "data": result.data,
                            "user_error": result.user_error,
                        }
                        for result in tool_results
                    ]
                },
            )
        blocking_errors = [
            result.user_error
            for result in tool_results
            if not result.ok and result.user_error
        ]
        if blocking_errors and not any(result.ok for result in tool_results):
            yield blocking_errors[0]
            return

        if not tool_results:
            messages = build_companion_messages(
                question=question,
                companion_prompt=companion_prompt,
                profile_packet=profile_packet,
                chat_history=chat_history,
                user_goals=user_goals,
                question_max_chars=get_settings().chat_history_question_max_chars,
                answer_max_chars=get_settings().chat_history_answer_max_chars,
            )
        else:
            messages = build_tool_answer_messages(
                question=question,
                profile_packet=profile_packet,
                chat_history=chat_history,
                tool_results=tool_results,
            )
        if trace is not None:
            trace("answer_messages", {"messages": messages})
        yield from self.vision_model_client.stream_chat(messages=messages)


def build_tool_answer_messages(
    *,
    question: str,
    profile_packet: str,
    chat_history: list[ChatSession],
    tool_results: list[AgentToolResult],
) -> list[dict[str, Any]]:
    settings = get_settings()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": TOOL_ANSWER_SYSTEM_PROMPT},
    ]
    packet = profile_packet.strip()
    if packet:
        messages.append({"role": "user", "content": packet})

    result_blocks = []
    for index, result in enumerate(tool_results, start=1):
        status = "ok" if result.ok else "error"
        result_blocks.append(
            f"[tool_result_{index}]\nstatus: {status}\ncontent:\n{result.content}"
        )
    messages.append(
        {
            "role": "user",
            "content": "工具执行结果（这是证据，不要复述工具名）：\n\n"
            + "\n\n".join(result_blocks),
        }
    )

    for session in chat_history:
        q = session.question.strip()
        a = session.answer.strip()
        if mentions_local_copilot(q) or mentions_local_copilot(a):
            continue
        if q and a and session.status == "done":
            messages.append({
                "role": "user",
                "content": q[: settings.chat_history_question_max_chars],
            })
            messages.append({
                "role": "assistant",
                "content": a[: settings.chat_history_answer_max_chars],
            })
    messages.append({"role": "user", "content": question})
    return messages


def _normalize_tool_name(raw_text: str, allowed_tool_names: tuple[str, ...]) -> str:
    text = _strip_planner_markup(raw_text)
    if text in allowed_tool_names:
        return text

    json_tool = _tool_name_from_json(text, allowed_tool_names)
    if json_tool is not None:
        return json_tool

    matches = _tool_names_in_text(text, allowed_tool_names)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            "Tool planner returned multiple tool names. "
            f"Expected exactly one of: {', '.join(allowed_tool_names)}. "
            f"Raw: {raw_text[:120]}"
        )

    first_line = text.splitlines()[0].strip() if text else ""
    return first_line.strip().strip('"').strip("'").strip("`").strip()


def _strip_planner_markup(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("```")
        ]
        if lines and lines[0].lower() in {"json", "text", "txt"}:
            lines = lines[1:]
        text = "\n".join(lines)
    return text.strip().strip('"').strip("'").strip("`").strip()


def _tool_name_from_json(text: str, allowed_tool_names: tuple[str, ...]) -> str | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if _json_has_empty_tool_calls(value) and "none" in allowed_tool_names:
        return "none"
    matches = _tool_names_in_json_value(value, allowed_tool_names)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            "Tool planner returned multiple tool names in JSON. "
            f"Expected exactly one of: {', '.join(allowed_tool_names)}."
        )
    return None


def _json_has_empty_tool_calls(value: Any) -> bool:
    return isinstance(value, dict) and value.get("tool_calls") == []


def _tool_names_in_json_value(
    value: Any,
    allowed_tool_names: tuple[str, ...],
) -> list[str]:
    if isinstance(value, str):
        return _tool_names_in_text(value, allowed_tool_names)
    if isinstance(value, dict):
        matches: list[str] = []
        for item in value.values():
            matches.extend(_tool_names_in_json_value(item, allowed_tool_names))
        return list(dict.fromkeys(matches))
    if isinstance(value, list):
        matches = []
        for item in value:
            matches.extend(_tool_names_in_json_value(item, allowed_tool_names))
        return list(dict.fromkeys(matches))
    return []


def _tool_names_in_text(text: str, allowed_tool_names: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for tool_name in allowed_tool_names:
        pattern = (
            r"(?<![A-Za-z0-9_.-])"
            + re.escape(tool_name)
            + r"(?![A-Za-z0-9_.-])"
        )
        if re.search(pattern, text):
            matches.append(tool_name)
    return matches