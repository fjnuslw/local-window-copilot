from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.chat import ChatSession
from app.services.agent_orchestrator import (
    AgentOrchestrator,
    _normalize_tool_name,
    build_tool_answer_messages,
)
from app.services.agent_tools import AGENT_TOOL_NAMES, AgentToolRegistry, AgentToolResult


ALLOWED_TOOL_NAMES = ("none", *AGENT_TOOL_NAMES)


def test_planner_parser_accepts_prefixed_tool_step() -> None:
    assert _normalize_tool_name("current_step screen.look", ALLOWED_TOOL_NAMES) == "screen.look"


def test_planner_parser_accepts_json_tool_payload() -> None:
    assert _normalize_tool_name('{"tool":"memory.search"}', ALLOWED_TOOL_NAMES) == "memory.search"


def test_planner_parser_rejects_ambiguous_tool_names() -> None:
    with pytest.raises(ValueError, match="multiple tool names"):
        _normalize_tool_name("screen.look then memory.search", ALLOWED_TOOL_NAMES)


class FakePlannerVisionClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages = []

    def complete_chat(self, *, messages, temperature=None, max_tokens=None):
        self.messages.append(messages)
        return self.text


def _done_session(question: str, answer: str) -> ChatSession:
    now = datetime.now(UTC)
    return ChatSession(
        session_id=question,
        question=question,
        answer=answer,
        status="done",
        created_at=now,
        updated_at=now,
    )


def test_planner_uses_effective_question_for_short_reply_tool_arguments() -> None:
    history = [
        _done_session(
            "当前窗口是做什么的",
            "当前窗口是 workbuddy 网页界面。你可以告诉我，我帮你分析当前窗口在做什么吗？",
        )
    ]
    orchestrator = AgentOrchestrator(
        vision_model_client=FakePlannerVisionClient("screen.look"),
        registry=AgentToolRegistry(),
        runtime=object(),
    )

    plan = orchestrator.plan(question="看吧", chat_history=history)

    assert plan.tool_calls[0].name == "screen.look"
    assert plan.tool_calls[0].arguments["question"] == "请分析当前窗口在做什么"
    planner_prompt = "\n".join(str(m["content"]) for m in orchestrator.vision_model_client.messages[-1])
    assert "用户原始短回复" in planner_prompt
    assert "本轮实际任务" in planner_prompt


def test_tool_answer_final_message_uses_effective_question_after_tool_evidence() -> None:
    history = [
        _done_session(
            "当前窗口是做什么的",
            "当前窗口是 workbuddy 网页界面。你可以告诉我，我帮你分析当前窗口在做什么吗？",
        )
    ]
    messages = build_tool_answer_messages(
        question="看吧",
        profile_packet="",
        chat_history=history,
        tool_results=[
            AgentToolResult(
                name="screen.look",
                ok=True,
                content="屏幕细看结果：右侧是 README.md，左侧是 WorkBuddy 任务列表。",
            )
        ],
    )

    assert "工具执行结果" in str(messages[-2]["content"])
    assert "请基于上面的工具证据" in str(messages[-1]["content"])
    assert "请分析当前窗口在做什么" in str(messages[-1]["content"])
    assert str(messages[-1]["content"]).strip() != "看吧"
