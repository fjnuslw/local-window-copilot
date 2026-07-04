from __future__ import annotations

import re

from app.schemas.chat import ChatSession


ACKNOWLEDGEMENT_REPLIES = {
    "ok",
    "okay",
    "yes",
    "y",
    "可以",
    "可以的",
    "好",
    "好的",
    "好啊",
    "行",
    "行的",
    "嗯",
    "嗯嗯",
    "来吧",
    "开始吧",
    "继续",
    "继续吧",
}

ASSISTANT_OFFER_HINTS = (
    "要不要",
    "是否",
    "可以",
    "需要我",
    "我可以",
    "我来",
    "帮你",
    "要我",
    "告诉我",
    "继续",
    "分析",
    "看看",
    "计划",
)


def build_dialogue_bridge_message(
    question: str,
    chat_history: list[ChatSession] | None,
) -> str | None:
    """Describe short user replies as a continuation of the previous turn.

    This is not a router. It only restores conversational state for the planner
    and answer model so replies like "可以" are not treated as standalone text.
    """
    if not chat_history or not _is_short_acknowledgement(question):
        return None

    previous = _latest_done_session(chat_history)
    if previous is None:
        return None
    previous_answer = previous.answer.strip()
    if not previous_answer or not _looks_like_assistant_offer(previous_answer):
        return None

    previous_question = previous.question.strip()
    return (
        "对话承接提示：用户本轮的短回复是在回应上一轮助手。"
        "请把它理解为同意/确认上一轮助手提出的建议、问题或下一步，"
        "然后直接继续完成上一轮承诺的事；不要反问同一个问题。\n"
        f"上一轮用户：{previous_question[:300]}\n"
        f"上一轮助手：{previous_answer[:500]}\n"
        f"本轮用户短回复：{question.strip()}"
    )


def _latest_done_session(chat_history: list[ChatSession]) -> ChatSession | None:
    for session in reversed(chat_history):
        if session.status == "done" and session.question.strip() and session.answer.strip():
            return session
    return None


def _is_short_acknowledgement(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?,.;；：:~～…]+", "", text.strip()).lower()
    if not normalized:
        return False
    return normalized in ACKNOWLEDGEMENT_REPLIES or (
        len(normalized) <= 4
        and any(item in normalized for item in ("可以", "好的", "继续", "行", "嗯"))
    )


def _looks_like_assistant_offer(text: str) -> bool:
    if "?" in text or "？" in text:
        return True
    return any(hint in text for hint in ASSISTANT_OFFER_HINTS)
