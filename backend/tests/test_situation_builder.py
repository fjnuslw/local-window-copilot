"""Situation Builder 单元测试：覆盖情绪检测、意图检测、情境标签、interrupt_policy。"""
from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.chat import ChatSession
from app.services.situation_builder import (
    DEFAULT_SITUATION,
    build_situation,
)


def _make_session(question: str, *, session_id: str = "s1") -> ChatSession:
    now = datetime.now(UTC)
    return ChatSession(
        session_id=session_id,
        question=question,
        answer="",
        status="streaming",
        created_at=now,
        updated_at=now,
    )


# ---------- 默认情境 ----------


def test_build_situation_returns_default_when_no_input() -> None:
    """没有任何输入时返回默认 ambient_idle 情境。"""
    result = build_situation()
    assert result == DEFAULT_SITUATION
    assert result["interrupt_policy"] == "silent"
    assert result["companion_line"] == ""


def test_build_situation_returns_default_when_empty_question() -> None:
    """空问题返回默认情境。"""
    result = build_situation(current_question="")
    assert result["situation_label"] == "ambient_idle"
    assert result["user_mood_hint"] == "neutral"


# ---------- 情绪检测 ----------


def test_mood_dissatisfied_detection() -> None:
    """命中 dissatisfied 关键词。"""
    for q in ["这个方向不对", "现在的设计怪怪的", "我不满意这个结果"]:
        result = build_situation(current_question=q)
        assert result["user_mood_hint"] == "dissatisfied", f"应检测到 dissatisfied: {q}"


def test_mood_frustrated_detection() -> None:
    """命中 frustrated 关键词。"""
    for q in ["有点烦", "搞不定这个 bug", "改得头疼"]:
        result = build_situation(current_question=q)
        assert result["user_mood_hint"] == "frustrated", f"应检测到 frustrated: {q}"


def test_mood_confused_detection() -> None:
    """命中 confused 关键词。"""
    for q in ["看不懂这个报错", "怎么回事啊", "迷茫了"]:
        result = build_situation(current_question=q)
        assert result["user_mood_hint"] == "confused", f"应检测到 confused: {q}"


def test_mood_excited_detection() -> None:
    """命中 excited 关键词。"""
    for q in ["搞定了", "可以了，就这样", "太棒了"]:
        result = build_situation(current_question=q)
        assert result["user_mood_hint"] == "excited", f"应检测到 excited: {q}"


def test_mood_hesitant_detection() -> None:
    """命中 hesitant 关键词。"""
    for q in ["要不要换一种方式", "是不是应该改方向", "还是再想想"]:
        result = build_situation(current_question=q)
        assert result["user_mood_hint"] == "hesitant", f"应检测到 hesitant: {q}"


def test_mood_neutral_when_no_match() -> None:
    """无任何情绪关键词命中时为 neutral。"""
    result = build_situation(current_question="今天天气如何")
    assert result["user_mood_hint"] == "neutral"


# ---------- 意图检测 ----------


def test_intent_reframe_product_identity() -> None:
    """命中产品方向反思意图关键词。"""
    for q in ["我在想方向", "这个产品的定位是什么", "这应该是一个陪伴工具，不是分析器"]:
        result = build_situation(current_question=q)
        assert result["likely_intent"] == "reframe_product_identity", (
            f"应检测到 reframe_product_identity: {q}"
        )


def test_intent_seek_help() -> None:
    """命中求助意图关键词。"""
    for q in ["帮我看看", "怎么部署", "如何配置环境"]:
        result = build_situation(current_question=q)
        assert result["likely_intent"] == "seek_help", f"应检测到 seek_help: {q}"


def test_intent_analyze() -> None:
    """命中分析意图关键词。"""
    for q in ["分析一下这个页面", "细看这块", "识别图里的文字"]:
        result = build_situation(current_question=q)
        assert result["likely_intent"] == "analyze", f"应检测到 analyze: {q}"


def test_intent_chat_default() -> None:
    """默认意图为 chat。"""
    result = build_situation(current_question="随便说说")
    assert result["likely_intent"] == "chat"


# ---------- 情境标签 ----------


def test_situation_label_product_direction_reflection() -> None:
    """dissatisfied + reframe_product_identity -> 产品方向反思。"""
    result = build_situation(current_question="这个方向不对")
    assert result["situation_label"] == "产品方向反思"


def test_situation_label_product_direction_with_hesitant() -> None:
    """hesitant + reframe_product_identity -> 产品方向反思。"""
    result = build_situation(current_question="是不是该换方向了")
    assert result["situation_label"] == "产品方向反思"


def test_situation_label_work_help() -> None:
    """frustrated + seek_help -> 工作求助。"""
    result = build_situation(current_question="烦死了，怎么部署")
    assert result["situation_label"] == "工作求助"


def test_situation_label_invite_analysis() -> None:
    """analyze 意图 -> 邀请式分析。"""
    result = build_situation(current_question="分析一下这个页面")
    assert result["situation_label"] == "邀请式分析"


def test_situation_label_chat_company() -> None:
    """chat 意图 -> 闲聊陪伴。"""
    # 注意：避免命中 seek_help 的"怎么"关键词
    result = build_situation(current_question="聊聊吧")
    assert result["situation_label"] == "闲聊陪伴"


def test_situation_label_coding_when_window_is_ide() -> None:
    """最近窗口为 ide/terminal 时 -> 编码中。"""
    summaries = [{"window_type": "ide", "summary": "VS Code"}]
    # 无情绪无意图命中时走到窗口类型判断
    result = build_situation(
        current_question="继续",
        recent_window_summaries=summaries,
    )
    assert result["situation_label"] == "编码中"


def test_situation_label_browsing_when_window_is_webpage() -> None:
    """最近窗口为 webpage/browser 时 -> 浏览中。"""
    summaries = [{"window_type": "webpage", "summary": "Chrome"}]
    result = build_situation(
        current_question="继续",
        recent_window_summaries=summaries,
    )
    assert result["situation_label"] == "浏览中"


def test_situation_label_reading_when_window_is_document() -> None:
    """最近窗口为 document/pdf 时 -> 阅读中。"""
    summaries = [{"window_type": "document", "summary": "PDF"}]
    result = build_situation(
        current_question="继续",
        recent_window_summaries=summaries,
    )
    assert result["situation_label"] == "阅读中"


# ---------- interrupt_policy 与 companion_line ----------


def test_interrupt_policy_silent_by_default() -> None:
    """默认 silent。"""
    result = build_situation(current_question="今天天气如何")
    assert result["interrupt_policy"] == "silent"
    assert result["companion_line"] == ""


def test_interrupt_policy_soft_nudge_for_dissatisfied_reframe() -> None:
    """dissatisfied + reframe_product_identity -> soft_nudge。"""
    result = build_situation(current_question="这个方向不对")
    assert result["interrupt_policy"] == "soft_nudge"
    assert "方向" in result["companion_line"]


def test_interrupt_policy_soft_nudge_for_frustrated_seek_help() -> None:
    """frustrated + seek_help -> soft_nudge。"""
    result = build_situation(current_question="烦死了，怎么部署")
    assert result["interrupt_policy"] == "soft_nudge"
    assert "陪你" in result["companion_line"] or "拆" in result["companion_line"]


def test_interrupt_policy_silent_for_pure_chat() -> None:
    """闲聊不出声。"""
    result = build_situation(current_question="聊聊")
    assert result["interrupt_policy"] == "silent"
    assert result["companion_line"] == ""


# ---------- 从 chat_history 取信号 ----------


def test_build_situation_uses_chat_history_when_no_current_question() -> None:
    """无 current_question 时从 chat_history[0] 取信号。"""
    history = [_make_session("这个方向不对", session_id="h1")]
    result = build_situation(chat_history=history)
    assert result["user_mood_hint"] == "dissatisfied"
    assert result["likely_intent"] == "reframe_product_identity"
    assert result["situation_label"] == "产品方向反思"


def test_build_situation_prefers_current_question_over_history() -> None:
    """current_question 优先于 chat_history。"""
    history = [_make_session("这个方向不对", session_id="h1")]
    result = build_situation(
        chat_history=history,
        current_question="随便聊聊",
    )
    # current_question 是 chat，不是 dissatisfied
    assert result["likely_intent"] == "chat"
    assert result["user_mood_hint"] == "neutral"


# ---------- 输出结构完整性 ----------


def test_build_situation_returns_all_required_fields() -> None:
    """返回的 dict 必须包含 spec §8.2 定义的全部字段。"""
    result = build_situation(current_question="聊聊")
    required_keys = {
        "situation_label",
        "user_mood_hint",
        "likely_intent",
        "interrupt_policy",
        "companion_line",
    }
    assert set(result.keys()) == required_keys
