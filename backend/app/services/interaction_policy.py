"""Interaction Policy：决定桌宠是否主动出声。

职责边界（见 ambient_companion_product_spec_zh.md §8.3）：
- 默认 silent。
- 用户发言后立即回应（这条由 ChatAgent.ask 保证，不在此处）。
- 同一主题主动提示冷却 10 分钟。
- 没有明确把握时不提示。
- 不把屏幕摘要作为主动提示内容。

本模块只负责"是否主动提示"的判断，不负责生成回应内容。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# 同一主题冷却时间（秒）——spec §8.3：同一主题主动提示冷却 10 分钟
TOPIC_COOLDOWN_SECONDS: int = 600


class InteractionPolicy:
    """主动提示策略：决定是否在用户未发言时主动出声。

    纯本地规则 + 内存态冷却记录，不调用模型，不持久化。
    """

    def __init__(
        self,
        *,
        cooldown_seconds: int = TOPIC_COOLDOWN_SECONDS,
        now_func: Any = datetime.now,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._now_func = now_func
        # topic_label -> 最近一次主动提示时间
        self._last_nudge_at: dict[str, datetime] = {}

    def should_speak(
        self,
        situation: dict[str, str],
        *,
        user_speaking: bool = False,
        record: bool = True,
    ) -> tuple[bool, str]:
        """判断是否应该主动提示。

        参数：
        - situation：SituationBuilder.build_situation() 的返回值
        - user_speaking：用户是否正在发言（发言时由 ChatAgent 处理，不需要主动提示）
        - record：为 False 时只预览结果，不写入冷却记录

        返回：(should_speak, line)
        - should_speak=True 时 line 为要说的内容
        - should_speak=False 时 line 为空串
        """
        # 用户正在发言时，由对话链路处理，不需要主动提示
        if user_speaking:
            return False, ""

        interrupt_policy = situation.get("interrupt_policy", "silent")
        companion_line = situation.get("companion_line", "")
        topic = situation.get("situation_label", "ambient_idle")

        # 规则 1：默认 silent，不主动出声
        if interrupt_policy != "soft_nudge":
            return False, ""

        # 规则 2：没有明确把握时不提示（companion_line 为空表示没有把握）
        if not companion_line.strip():
            return False, ""

        # 规则 3：同一主题冷却 10 分钟
        now = self._now_func()
        last = self._last_nudge_at.get(topic)
        if last is not None:
            elapsed = now - last
            if elapsed < timedelta(seconds=self.cooldown_seconds):
                return False, ""

        # 通过全部检查，可以主动提示
        if record:
            self._last_nudge_at[topic] = now
        return True, companion_line

    def reset(self) -> None:
        """清空冷却记录（例如用户重新开始一段对话时）。"""
        self._last_nudge_at.clear()


# 单例（桌宠进程内共享同一份冷却记录）
_default_policy: InteractionPolicy | None = None


def get_interaction_policy() -> InteractionPolicy:
    global _default_policy
    if _default_policy is None:
        _default_policy = InteractionPolicy()
    return _default_policy
