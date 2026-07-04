from __future__ import annotations

import re
from dataclasses import dataclass

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
    "看吧",
    "你看吧",
    "看看吧",
    "那你看",
    "那看吧",
    "可以看",
    "可以你看",
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


@dataclass(frozen=True)
class DialogueBridge:
    message: str
    effective_question: str
    previous_question: str
    previous_answer: str


def build_dialogue_bridge(
    question: str,
    chat_history: list[ChatSession] | None,
) -> DialogueBridge | None:
    """Resolve short replies as a continuation of the previous assistant turn."""
    if not chat_history or not is_short_continuation_reply(question):
        return None

    previous = _latest_done_session(chat_history)
    if previous is None:
        return None
    previous_answer = previous.answer.strip()
    if not previous_answer or not _looks_like_assistant_offer(previous_answer):
        return None

    previous_question = previous.question.strip()
    effective_question = (
        _extract_offer_task(previous_answer)
        or _latest_substantive_question(chat_history)
        or previous_question
        or question.strip()
    )
    if effective_question and not effective_question.startswith("请"):
        effective_question = "请" + effective_question

    message = (
        "对话承接提示：用户本轮的短回复是在回应上一轮助手。"
        "请把它理解为同意/确认上一轮助手提出的建议、问题或下一步，"
        "然后直接继续完成上一轮承诺的事；不要反问同一个问题。\n"
        f"上一轮用户：{previous_question[:300]}\n"
        f"上一轮助手：{previous_answer[:500]}\n"
        f"本轮用户短回复：{question.strip()}\n"
        f"本轮实际任务：{effective_question}"
    )
    return DialogueBridge(
        message=message,
        effective_question=effective_question,
        previous_question=previous_question,
        previous_answer=previous_answer,
    )


def build_dialogue_bridge_message(
    question: str,
    chat_history: list[ChatSession] | None,
) -> str | None:
    bridge = build_dialogue_bridge(question, chat_history)
    return bridge.message if bridge else None


def resolve_effective_question(
    question: str,
    chat_history: list[ChatSession] | None,
    *,
    user_image_name: str | None = None,
) -> str:
    bridge = build_dialogue_bridge(question, chat_history)
    if bridge is not None:
        return bridge.effective_question
    if user_image_name and is_short_continuation_reply(question):
        return "请分析本轮用户附带的图片内容"
    return question.strip()


def is_short_continuation_reply(text: str) -> bool:
    return _is_short_acknowledgement(text)


def _latest_done_session(chat_history: list[ChatSession]) -> ChatSession | None:
    for session in reversed(chat_history):
        if session.status == "done" and session.question.strip() and session.answer.strip():
            return session
    return None


def _latest_substantive_question(chat_history: list[ChatSession]) -> str | None:
    for session in reversed(chat_history):
        question = session.question.strip()
        if question and not _is_short_acknowledgement(question):
            return question
    return None


def _is_short_acknowledgement(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?,.;；：:~～…]+", "", text.strip()).lower()
    if not normalized:
        return False
    return normalized in ACKNOWLEDGEMENT_REPLIES or (
        len(normalized) <= 5
        and any(
            item in normalized
            for item in ("可以", "好的", "继续", "看吧", "看看", "行", "嗯")
        )
    )


def _looks_like_assistant_offer(text: str) -> bool:
    if "?" in text or "？" in text:
        return True
    return any(hint in text for hint in ASSISTANT_OFFER_HINTS)


def _extract_offer_task(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text.strip())
    if not compact:
        return None
    patterns = (
        r"(?:我帮你|帮你|我可以帮你)([^。！？!?]{2,80})(?:吗|么)?[？?]?",
        r"(?:要不要|是否)(?:我)?(?:来|帮你)?([^。！？!?]{2,80})(?:[？?]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        task = _clean_offer_task(match.group(1))
        if task:
            return task
    return None


def _clean_offer_task(task: str) -> str | None:
    cleaned = task.strip("，,。.？?！!：:；; ")
    cleaned = re.sub(r"^(?:我|来|继续)", "", cleaned)
    cleaned = re.sub(r"(?:吗|么)$", "", cleaned)
    cleaned = cleaned.strip("，,。.？?！!：:；; ")
    if len(cleaned) < 2:
        return None
    return cleaned
