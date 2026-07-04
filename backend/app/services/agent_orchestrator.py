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


DIRECT_ACTION_WORDS = {"answer", "direct_answer", "respond", "reply", "chat", "none", "no_tool"}

TOOL_PLANNER_SYSTEM_PROMPT = """你是本地桌面伙伴的行动规划模型。
你会看到注册工具、最近多轮对话、本轮用户输入，以及可选的用户附图。
你只决定下一步动作，不负责回答用户。

核心原则：
- 不使用固定短语规则；必须根据完整对话语义判断用户到底在承接什么。
- 用户说“可以、看吧、继续、行、嗯”等短回复时，结合上一轮助手和用户的真实对话自行判断：直接回答、看屏幕、查记忆、写记忆，或不调用工具。
- 如果问题需要当前/历史屏幕、页面、窗口、截图、可见文字、按钮、布局或图片细节，调用 screen.look。
- 如果问题需要过去对话、用户偏好、已有记忆、项目背景或历史窗口索引，调用 memory.search。
- 只有用户明确要求“记住/以后记得/保存”稳定偏好或事实时，调用 memory.remember。
- 如果已有知识和对话历史足够，就选择 answer，不要为了显得聪明而调用工具。
- 最多调用 3 个工具。

输出必须是严格 JSON 对象，二选一：
{"action":"answer","tool_calls":[]}
{"action":"tools","tool_calls":[{"name":"screen.look","arguments":{"question":"用户真正想看的问题"}}]}
不要输出 Markdown、解释、步骤名或自然语言。"""


TOOL_ANSWER_SYSTEM_PROMPT = """你是用户的本地桌面伙伴。
你会收到工具执行结果，然后直接回答用户。

原则：
- 工具已经执行过，不要复述工具名或接口名。
- 如果 screen.look 返回了屏幕/图片细看结果，把它当作视觉证据；你负责理解、取舍和回答。
- 如果用户问“推荐哪个/哪个好看/选哪个/怎么排”，必须给出明确选择和理由；不要只描述页面。
- 如果用户问具体文字或细节，优先列出能看清的原文、标题、按钮、数字或位置。
- 如果工具结果说看不清，就明确说看不清，不编造。
- 不要输出 JSON、日志、工具名或接口名。
- 不要声称能自动点击、输入或操作电脑。
- 用中文，自然、简洁、有温度。"""


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
        chat_history: list[ChatSession] | None = None,
        trace: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AgentPlan:
        settings = get_settings()
        messages = self._build_planner_messages(
            question=question,
            user_image_name=user_image_name,
            chat_history=chat_history,
        )
        if trace is not None:
            trace("planner_request", {"messages": messages})
        raw_text = self.vision_model_client.complete_chat(
            messages=messages,
            temperature=settings.tool_planner_temperature,
            max_tokens=settings.tool_planner_max_tokens,
        )
        if trace is not None:
            trace("planner_raw_response", {"text": raw_text})
        try:
            calls = self._parse_tool_name_plan(raw_text, question)
        except ValueError as exc:
            if trace is not None:
                trace("planner_parse_error", {"error": str(exc), "raw_text": raw_text})
            calls = []
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

    def _build_planner_messages(
        self,
        *,
        question: str,
        user_image_name: str | None,
        chat_history: list[ChatSession] | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": TOOL_PLANNER_SYSTEM_PROMPT
                + "\n\n注册工具：\n"
                + self.registry.manifest_for_prompt(),
            }
        ]
        if chat_history:
            for session in chat_history:
                q = session.question.strip()
                a = session.answer.strip()
                if not q or not a or session.status != "done":
                    continue
                if mentions_local_copilot(q) or mentions_local_copilot(a):
                    continue
                messages.append({"role": "user", "content": q})
                messages.append({"role": "assistant", "content": a})
        user_lines = ["本轮用户输入：", question.strip()]
        if user_image_name:
            user_lines.extend(["", f"本轮用户附图：{user_image_name}"])
        messages.append({"role": "user", "content": "\n".join(user_lines)})
        return messages

    def _parse_tool_name_plan(self, raw_text: str, question: str) -> list[AgentToolCall]:
        raw_calls = _tool_calls_from_plan_text(
            raw_text,
            allowed_tool_names=("none", *AGENT_TOOL_NAMES),
            default_question=question,
        )
        return self.registry.validate_calls(raw_calls)

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
            chat_history=chat_history,
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
    messages.append(
        {
            "role": "user",
            "content": "请基于上面的工具证据回答本轮用户输入。"
            "你可以结合前面的多轮对话理解省略和指代；如果证据不足，要明确说明不足。\n"
            f"本轮用户输入：{question.strip()}",
        }
    )
    return messages


def _tool_calls_from_plan_text(
    raw_text: str,
    *,
    allowed_tool_names: tuple[str, ...],
    default_question: str,
) -> list[dict[str, Any]]:
    text = _strip_planner_markup(raw_text)
    if text.lower() in DIRECT_ACTION_WORDS:
        return []

    value = _json_value_from_text(text)
    if value is not None:
        calls = _tool_calls_from_json_value(
            value,
            allowed_tool_names=allowed_tool_names,
            default_question=default_question,
        )
        if calls is not None:
            return calls

    tool = _normalize_tool_name(text, allowed_tool_names)
    if tool == "none" or tool.lower() in DIRECT_ACTION_WORDS:
        return []
    if tool not in allowed_tool_names:
        return []
    return [{"name": tool, "arguments": _default_arguments(tool, default_question)}]


def _json_value_from_text(text: str) -> Any | None:
    candidates = [text]
    extracted = _extract_json_object_from_text(text)
    if extracted and extracted != text:
        candidates.append(extracted)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            inner = _json_value_from_text(value["text"].strip())
            return inner if inner is not None else value
        return value
    return None


def _tool_calls_from_json_value(
    value: Any,
    *,
    allowed_tool_names: tuple[str, ...],
    default_question: str,
) -> list[dict[str, Any]] | None:
    if isinstance(value, str):
        nested = _json_value_from_text(value.strip())
        if nested is not None and nested is not value:
            return _tool_calls_from_json_value(
                nested,
                allowed_tool_names=allowed_tool_names,
                default_question=default_question,
            )
        text = value.strip()
        if text.lower() in DIRECT_ACTION_WORDS:
            return []
        tool = _normalize_tool_name(text, allowed_tool_names)
        if tool == "none" or tool.lower() in DIRECT_ACTION_WORDS:
            return []
        return [{"name": tool, "arguments": _default_arguments(tool, default_question)}]

    if isinstance(value, list):
        return _coerce_tool_call_list(
            value,
            allowed_tool_names=allowed_tool_names,
            default_question=default_question,
        )

    if not isinstance(value, dict):
        return None

    if "tool_calls" in value:
        raw_calls = _parse_maybe_json(value.get("tool_calls"))
        return _coerce_tool_call_list(
            raw_calls,
            allowed_tool_names=allowed_tool_names,
            default_question=default_question,
        )

    action = str(value.get("action") or value.get("next") or "").strip().lower()
    if action in DIRECT_ACTION_WORDS:
        return []

    if any(key in value for key in ("name", "tool", "function", "function_call")):
        call = _coerce_single_tool_call(
            value,
            allowed_tool_names=allowed_tool_names,
            default_question=default_question,
        )
        return [] if call is None else [call]

    matches = _tool_names_in_json_value(value, allowed_tool_names)
    if len(matches) == 1:
        tool = matches[0]
        if tool == "none":
            return []
        return [{"name": tool, "arguments": _default_arguments(tool, default_question)}]
    if len(matches) > 1:
        raise ValueError(
            "Tool planner returned multiple tool names in JSON. "
            f"Expected exactly one of: {', '.join(allowed_tool_names)}."
        )
    return None


def _parse_maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return []
    parsed = _json_value_from_text(text)
    return parsed if parsed is not None else value


def _coerce_tool_call_list(
    value: Any,
    *,
    allowed_tool_names: tuple[str, ...],
    default_question: str,
) -> list[dict[str, Any]]:
    value = _parse_maybe_json(value)
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        if _json_has_empty_tool_calls(value):
            return []
        call = _coerce_single_tool_call(
            value,
            allowed_tool_names=allowed_tool_names,
            default_question=default_question,
        )
        return [] if call is None else [call]
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in DIRECT_ACTION_WORDS:
            return []
        matches = _tool_names_in_text(text, allowed_tool_names)
        if len(matches) == 1 and matches[0] != "none":
            return [{"name": matches[0], "arguments": _default_arguments(matches[0], default_question)}]
        if len(matches) > 1:
            raise ValueError("tool_calls string contains multiple tool names.")
        return []
    if not isinstance(value, list):
        raise ValueError("tool_calls must be a list.")
    calls: list[dict[str, Any]] = []
    for item in value:
        call = _coerce_single_tool_call(
            item,
            allowed_tool_names=allowed_tool_names,
            default_question=default_question,
        )
        if call is not None:
            calls.append(call)
    return calls


def _coerce_single_tool_call(
    value: Any,
    *,
    allowed_tool_names: tuple[str, ...],
    default_question: str,
) -> dict[str, Any] | None:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in DIRECT_ACTION_WORDS:
            return None
        tool = _normalize_tool_name(text, allowed_tool_names)
        if tool == "none" or tool.lower() in DIRECT_ACTION_WORDS:
            return None
        return {"name": tool, "arguments": _default_arguments(tool, default_question)}
    if not isinstance(value, dict):
        raise ValueError("Each tool call must be an object.")

    function = value.get("function") if isinstance(value.get("function"), dict) else None
    function_call = value.get("function_call") if isinstance(value.get("function_call"), dict) else None
    name_value = (
        value.get("name")
        or value.get("tool")
        or (function or {}).get("name")
        or (function_call or {}).get("name")
    )
    if name_value is None:
        matches = _tool_names_in_json_value(value, allowed_tool_names)
        if len(matches) == 1:
            name_value = matches[0]
        elif len(matches) > 1:
            raise ValueError(
                "Tool planner returned multiple tool names in one call. "
                f"Expected exactly one of: {', '.join(allowed_tool_names)}."
            )
        else:
            raise ValueError("Each tool call must include a tool name.")

    tool = _normalize_tool_name(str(name_value), allowed_tool_names)
    if tool == "none" or tool.lower() in DIRECT_ACTION_WORDS:
        return None

    raw_args = (
        value.get("arguments")
        if "arguments" in value
        else value.get("args")
        if "args" in value
        else value.get("parameters")
    )
    if raw_args is None and function is not None:
        raw_args = function.get("arguments") or function.get("parameters")
    if raw_args is None and function_call is not None:
        raw_args = function_call.get("arguments") or function_call.get("parameters")
    raw_args = _parse_maybe_json(raw_args)
    args = raw_args if isinstance(raw_args, dict) else {}

    merged_args = _default_arguments(tool, default_question)
    merged_args.update(args)
    return {"name": tool, "arguments": merged_args}


def _default_arguments(tool: str, question: str) -> dict[str, Any]:
    if tool == "screen.look":
        return {"question": question}
    if tool == "memory.search":
        return {"query": question}
    if tool == "memory.remember":
        return {"note": question}
    return {}


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
    text = re.sub(r"<think>.*?</think>", "", raw_text.strip(), flags=re.DOTALL | re.IGNORECASE)
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


def _extract_json_object_from_text(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


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
