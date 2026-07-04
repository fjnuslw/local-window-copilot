from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.chat import ChatSession
from app.services.dialogue_context import build_dialogue_bridge_message


def _session(question: str, answer: str) -> ChatSession:
    now = datetime.now(UTC)
    return ChatSession(
        session_id="previous",
        question=question,
        answer=answer,
        status="done",
        created_at=now,
        updated_at=now,
    )


def test_short_acknowledgement_bridges_previous_assistant_offer() -> None:
    bridge = build_dialogue_bridge_message(
        "可以",
        [
            _session(
                "帮我看看继续问什么比较好",
                "要不要我帮你分析当前任务进展或下一步计划？",
            )
        ],
    )

    assert bridge is not None
    assert "对话承接提示" in bridge
    assert "不要反问同一个问题" in bridge
    assert "帮我看看继续问什么比较好" in bridge


def test_short_acknowledgement_without_offer_does_not_bridge() -> None:
    bridge = build_dialogue_bridge_message(
        "可以",
        [_session("这个页面是什么", "这是一个项目说明页面。")],
    )

    assert bridge is None
