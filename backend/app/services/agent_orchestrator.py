from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Iterator

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.services.agent_tools import (
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
你只负责选择工具，不负责回答用户。

输出必须只是一行工具名，四选一：
screen.look
memory.search
memory.remember
none

原则：
- 工具列表之外的工具一律不能使用。
- 用户想让你看屏幕、看页面、看窗口、看截图、读可见文字、分析界面细节时，调用 screen.look。
- 用户问过去聊过什么、用户偏好、项目方向、已有记忆、上下文背景时，调用 memory.search。
- 只有用户明确说“记住/以后记得/保存这个偏好”时，调用 memory.remember。
- 普通陪伴、情绪回应、闲聊，不调用工具，输出 none。
- 如果用户已经指向当前屏幕，不要反问“你想看什么”，直接调用 screen.look。
- 不要输出解释、Markdown、JSON 或自然语言，只输出一个工具名。"""


TOOL_ANSWER_SYSTEM_PROMPT = """你是用户的本地桌面伙伴。
你会收到工具结果，然后直接回答用户。

原则：
- 工具已经执行过，不要再要求用户说明“想看什么”。
- 如果 screen.look 返回了屏幕细看结果，优先基于它回答。
- 如果工具结果说看不清，就明确说看不清，不编造。
- 不要输出 JSON、日志、工具名或接口名。
- 不要声称能自动点击、输入或操作电脑。
- 用中文，自然、简洁、有温度。"""


VISUAL_REGION_HINTS = (
    "左侧",
    "左边",
    "右侧",
    "右边",
    "上方",
    "上面",
    "顶部",
    "底部",
    "下方",
    "下面",
    "中间",
    "中部",
    "中央",
)
VISUAL_TARGET_HINTS = (
    "页面",
    "屏幕",
    "截图",
    "图里",
    "画面",
    "代码",
    "文字",
    "按钮",
    "区域",
    "面板",
    "菜单",
    "输入框",
    "图标",
    "列表",
    "内容",
)
VISUAL_ACTION_HINTS = (
    "看",
    "识别",
    "读",
    "描述",
    "分析",
    "显示",
    "写了什么",
    "有什么",
    "是什么",
    "具体",
)
WINDOW_METADATA_HINTS = ("名字", "标题", "叫什么")
REMEMBER_HINTS = ("记住", "记一下", "以后记得", "保存这个偏好")
REMEMBER_NEGATIONS = ("不要记住", "不用记住", "别记住")


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
        trace: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AgentPlan:
        forced_tool = _forced_tool_for_question(question)
        if forced_tool is not None:
            if trace is not None:
                trace("planner_gate", {"tool": forced_tool})
            calls = self._parse_tool_name_plan(forced_tool, question)
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
            return AgentPlan(tool_calls=calls, raw_text=forced_tool)

        settings = get_settings()
        messages = [
            {"role": "system", "content": TOOL_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": "用户问题：\n" + question.strip()},
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
        tool = _normalize_tool_name(raw_text)
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
                "Expected screen.look, memory.search, memory.remember, or none. "
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
        plan = self.plan(question=question, trace=trace)
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
            "content": "工具执行结果（只用于回答用户，不要复述工具名）：\n\n"
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


def _normalize_tool_name(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("```")
        ]
        text = lines[0] if lines else ""
    text = text.strip().strip('"').strip("'").strip("`").strip()
    first_line = text.splitlines()[0].strip() if text else ""
    first_line = first_line.strip().strip('"').strip("'").strip("`").strip()
    return first_line


def _forced_tool_for_question(question: str) -> str | None:
    """High-confidence gate for tiny models.

    The planner remains model-driven for ambiguous cases. This gate only catches
    explicit screen-detail and explicit remember requests so obvious tool calls
    do not depend on a small model guessing the route.
    """
    text = question.strip()
    if not text:
        return None

    if any(hint in text for hint in REMEMBER_HINTS) and not any(
        negation in text for negation in REMEMBER_NEGATIONS
    ):
        return "memory.remember"

    if any(hint in text for hint in WINDOW_METADATA_HINTS) and "窗口" in text:
        return None

    has_region = any(hint in text for hint in VISUAL_REGION_HINTS)
    has_target = any(hint in text for hint in VISUAL_TARGET_HINTS)
    has_action = any(hint in text for hint in VISUAL_ACTION_HINTS)
    mentions_current_window_content = (
        "窗口" in text
        and any(hint in text for hint in ("看", "分析", "显示", "有什么", "内容"))
    )

    if has_region and (has_target or has_action):
        return "screen.look"
    if has_target and has_action:
        return "screen.look"
    if mentions_current_window_content:
        return "screen.look"
    return None
