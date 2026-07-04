"""Interaction Policy 单元测试：覆盖 spec §8.3 全部规则。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.interaction_policy import InteractionPolicy


def _situation(
    *,
    interrupt_policy: str = "silent",
    companion_line: str = "",
    situation_label: str = "ambient_idle",
) -> dict[str, str]:
    return {
        "situation_label": situation_label,
        "user_mood_hint": "neutral",
        "likely_intent": "chat",
        "interrupt_policy": interrupt_policy,
        "companion_line": companion_line,
    }


def _make_policy(now: datetime) -> InteractionPolicy:
    """构造一个 now 固定的 policy，便于测试冷却逻辑。"""
    return InteractionPolicy(now_func=lambda: now)


# ---------- 规则 1：默认 silent ----------


def test_silent_policy_does_not_speak() -> None:
    """interrupt_policy=silent 时不主动出声。"""
    policy = _make_policy(datetime(2026, 7, 4, 12, 0, 0))
    situation = _situation(interrupt_policy="silent", companion_line="你好")
    should, line = policy.should_speak(situation)
    assert should is False
    assert line == ""


# ---------- 规则 2：没有明确把握时不提示 ----------


def test_soft_nudge_without_line_does_not_speak() -> None:
    """soft_nudge 但 companion_line 为空时不提示。"""
    policy = _make_policy(datetime(2026, 7, 4, 12, 0, 0))
    situation = _situation(interrupt_policy="soft_nudge", companion_line="")
    should, line = policy.should_speak(situation)
    assert should is False
    assert line == ""


def test_soft_nudge_with_whitespace_only_line_does_not_speak() -> None:
    """companion_line 只有空白时不提示。"""
    policy = _make_policy(datetime(2026, 7, 4, 12, 0, 0))
    situation = _situation(interrupt_policy="soft_nudge", companion_line="   ")
    should, line = policy.should_speak(situation)
    assert should is False
    assert line == ""


# ---------- 正常主动提示 ----------


def test_soft_nudge_with_line_speaks() -> None:
    """soft_nudge 且 companion_line 非空时主动提示。"""
    policy = _make_policy(datetime(2026, 7, 4, 12, 0, 0))
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向，不只是实现。",
        situation_label="产品方向反思",
    )
    should, line = policy.should_speak(situation)
    assert should is True
    assert "方向" in line


# ---------- 规则 3：同一主题冷却 10 分钟 ----------


def test_same_topic_within_cooldown_does_not_speak() -> None:
    """同一主题在 10 分钟内不重复提示。"""
    now = datetime(2026, 7, 4, 12, 0, 0)
    policy = _make_policy(now)
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向。",
        situation_label="产品方向反思",
    )

    # 第一次提示成功
    should1, _ = policy.should_speak(situation)
    assert should1 is True

    # 9 分钟后仍在冷却期
    policy._now_func = lambda: now + timedelta(minutes=9)
    should2, line2 = policy.should_speak(situation)
    assert should2 is False
    assert line2 == ""


def test_same_topic_after_cooldown_speaks_again() -> None:
    """同一主题超过 10 分钟冷却后可再次提示。"""
    now = datetime(2026, 7, 4, 12, 0, 0)
    policy = _make_policy(now)
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向。",
        situation_label="产品方向反思",
    )

    # 第一次提示
    should1, _ = policy.should_speak(situation)
    assert should1 is True

    # 10 分钟后可以再次提示
    policy._now_func = lambda: now + timedelta(minutes=10, seconds=1)
    should2, line2 = policy.should_speak(situation)
    assert should2 is True
    assert "方向" in line2


def test_different_topic_does_not_share_cooldown() -> None:
    """不同主题不共享冷却。"""
    now = datetime(2026, 7, 4, 12, 0, 0)
    policy = _make_policy(now)
    situation_a = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向。",
        situation_label="产品方向反思",
    )
    situation_b = _situation(
        interrupt_policy="soft_nudge",
        companion_line="要不要我陪你拆一下？",
        situation_label="工作求助",
    )

    # 主题 A 提示
    should_a, _ = policy.should_speak(situation_a)
    assert should_a is True

    # 立即提示主题 B（不同主题不受 A 的冷却影响）
    should_b, line_b = policy.should_speak(situation_b)
    assert should_b is True
    assert "陪你" in line_b


# ---------- 用户发言时不主动提示 ----------


def test_user_speaking_suppresses_proactive_nudge() -> None:
    """用户正在发言时由对话链路处理，不需要主动提示。"""
    policy = _make_policy(datetime(2026, 7, 4, 12, 0, 0))
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向。",
    )
    should, line = policy.should_speak(situation, user_speaking=True)
    assert should is False
    assert line == ""


def test_preview_does_not_consume_cooldown() -> None:
    """record=False 只预览主动提示，不应写入冷却记录。"""
    now = datetime(2026, 7, 4, 12, 0, 0)
    policy = _make_policy(now)
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向。",
        situation_label="产品方向反思",
    )

    preview_should, preview_line = policy.should_speak(situation, record=False)
    assert preview_should is True
    assert "方向" in preview_line

    should, line = policy.should_speak(situation)
    assert should is True
    assert "方向" in line


# ---------- reset ----------


def test_reset_clears_cooldown() -> None:
    """reset 后冷却记录清空，可立即再次提示同一主题。"""
    now = datetime(2026, 7, 4, 12, 0, 0)
    policy = _make_policy(now)
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line="你像是在纠结方向。",
        situation_label="产品方向反思",
    )

    # 第一次提示
    should1, _ = policy.should_speak(situation)
    assert should1 is True

    # 同一时刻再提示被冷却
    should2, _ = policy.should_speak(situation)
    assert should2 is False

    # reset 后可再次提示
    policy.reset()
    should3, line3 = policy.should_speak(situation)
    assert should3 is True
    assert "方向" in line3


# ---------- 不把屏幕摘要作为主动提示内容 ----------


def test_proactive_line_is_not_window_summary() -> None:
    """主动提示内容应来自 SituationBuilder 的 companion_line，不是窗口摘要。

    这条规则由 SituationBuilder 保证 companion_line 是陪伴式短句，
    InteractionPolicy 只透传，不做摘要注入。这里验证透传正确。
    """
    policy = _make_policy(datetime(2026, 7, 4, 12, 0, 0))
    companion_line = "要不要我陪你拆一下？"
    situation = _situation(
        interrupt_policy="soft_nudge",
        companion_line=companion_line,
        situation_label="工作求助",
    )
    should, line = policy.should_speak(situation)
    assert should is True
    assert line == companion_line
