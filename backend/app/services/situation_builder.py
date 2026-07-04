"""Situation Builder：构建情境状态，替代"直接展示摘要"的产品层。

职责边界（见 ambient_companion_product_spec_zh.md §6.3 / §8.2）：
- 输入：最近对话、最近窗口类型和变化、用户 profile、记忆。
- 输出：situation_label / user_mood_hint / likely_intent / interrupt_policy / companion_line。
- 不调用模型，纯本地规则匹配。
- 情境状态比窗口摘要更重要——摘要降级为内部索引。
"""
from __future__ import annotations

from typing import Any

from app.schemas.chat import ChatSession


# 情绪信号关键词（命中即暗示对应用户情绪）
MOOD_SIGNALS: dict[str, tuple[str, ...]] = {
    "dissatisfied": ("不对", "不行", "不好", "怪怪的", "有问题", "不满意", "反感"),
    "frustrated": ("烦", "累", "卡住", "搞不定", "崩溃", "头疼", "麻烦"),
    "confused": ("不理解", "看不懂", "为什么", "怎么回事", "搞不清", "迷茫"),
    "excited": ("好了", "搞定", "可以了", "太棒", "不错", "对了"),
    "hesitant": ("要不要", "是不是", "能不能", "可以吗", "还是"),
}

# 意图信号关键词
INTENT_SIGNALS: dict[str, tuple[str, ...]] = {
    "reframe_product_identity": ("方向", "灵魂", "定位", "不是", "应该是"),
    "seek_help": ("帮我", "怎么", "如何", "能不能帮我"),
    "analyze": ("分析", "看看", "细看", "识别"),
    "chat": ("聊聊", "说一下", "觉得", "感觉"),
}

# 默认情境
DEFAULT_SITUATION: dict[str, str] = {
    "situation_label": "ambient_idle",
    "user_mood_hint": "neutral",
    "likely_intent": "chat",
    "interrupt_policy": "silent",
    "companion_line": "",
}


def build_situation(
    *,
    chat_history: list[ChatSession] | None = None,
    recent_window_summaries: list[dict[str, Any]] | None = None,
    current_question: str | None = None,
) -> dict[str, str]:
    """构建情境状态。

    返回字段：
    - situation_label：简短情境标签（如 "产品方向反思" / "工作求助" / "ambient_idle"）
    - user_mood_hint：用户情绪暗示（dissatisfied / frustrated / confused / excited / hesitant / neutral）
    - likely_intent：用户可能意图（reframe_product_identity / seek_help / analyze / chat）
    - interrupt_policy：主动提示策略（silent / soft_nudge）
    - companion_line：建议的陪伴开场白（空串表示不出声）
    """
    if not chat_history and not current_question:
        return dict(DEFAULT_SITUATION)

    # 取最近一条用户输入作为主要信号
    latest_question = ""
    if current_question:
        latest_question = current_question
    elif chat_history:
        latest_question = chat_history[0].question

    if not latest_question:
        return dict(DEFAULT_SITUATION)

    # 检测情绪
    mood = "neutral"
    for m, keywords in MOOD_SIGNALS.items():
        if any(kw in latest_question for kw in keywords):
            mood = m
            break

    # 检测意图（None 表示未命中任何意图关键词，与显式 chat 区分）
    intent: str | None = None
    for i, keywords in INTENT_SIGNALS.items():
        if any(kw in latest_question for kw in keywords):
            intent = i
            break
    intent_explicit = intent is not None
    if intent is None:
        intent = "chat"

    # 构建情境标签
    label = _build_situation_label(
        mood,
        intent,
        chat_history,
        recent_window_summaries,
        intent_explicit=intent_explicit,
    )

    # 主动提示策略：默认 silent，只有强烈情绪信号时才 soft_nudge
    interrupt_policy = "silent"
    companion_line = ""
    if mood in {"dissatisfied", "frustrated"} and intent == "reframe_product_identity":
        interrupt_policy = "soft_nudge"
        companion_line = "你像是在纠结方向，不只是实现。"
    elif mood == "frustrated" and intent == "seek_help":
        interrupt_policy = "soft_nudge"
        companion_line = "要不要我陪你拆一下？"

    return {
        "situation_label": label,
        "user_mood_hint": mood,
        "likely_intent": intent,
        "interrupt_policy": interrupt_policy,
        "companion_line": companion_line,
    }


def _build_situation_label(
    mood: str,
    intent: str,
    chat_history: list[ChatSession] | None,
    recent_window_summaries: list[dict[str, Any]] | None,
    *,
    intent_explicit: bool = True,
) -> str:
    """根据情绪、意图、历史构建简短情境标签。"""
    if intent == "reframe_product_identity" and mood in {"dissatisfied", "hesitant"}:
        return "产品方向反思"
    if intent == "seek_help" and mood == "frustrated":
        return "工作求助"
    if intent == "analyze":
        return "邀请式分析"
    # 只有显式命中 chat 关键词才标记为闲聊陪伴，否则落到窗口类型判断
    if intent == "chat" and intent_explicit:
        return "闲聊陪伴"
    # 根据最近窗口类型判断
    if recent_window_summaries:
        latest_window = recent_window_summaries[-1] if recent_window_summaries else None
        if latest_window:
            wtype = latest_window.get("window_type", "")
            if wtype in {"ide", "terminal"}:
                return "编码中"
            if wtype in {"webpage", "browser"}:
                return "浏览中"
            if wtype in {"document", "pdf"}:
                return "阅读中"
    return "ambient_idle"
