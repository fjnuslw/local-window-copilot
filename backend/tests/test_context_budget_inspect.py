from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.assistant_chat import (
    AnswerContext,
    ChatAgent,
    build_context_budget_preview,
)
from app.services.context_budget import (
    CONTEXT_BUDGET_SAFETY_TOKENS,
    ContextAssembler,
    ContextSegmentHint,
    build_chat_segment_hints,
    calculate_context_input_limit,
)


# ---------------------------------------------------------------------------
# 1. calculate_context_input_limit
# ---------------------------------------------------------------------------


def test_calculate_input_limit_basic():
    assert calculate_context_input_limit(
        ctx_size=256000, answer_max_tokens=32768
    ) == 215040


def test_calculate_input_limit_handles_negatives_and_returns_at_least_one():
    # answer_max_tokens / safety_tokens 负数按 0 处理
    assert calculate_context_input_limit(
        ctx_size=100, answer_max_tokens=-10, safety_tokens=-20
    ) == 100
    # ctx_size 过小或负数时返回至少 1
    assert calculate_context_input_limit(
        ctx_size=0, answer_max_tokens=0, safety_tokens=0
    ) == 1
    assert calculate_context_input_limit(
        ctx_size=-5, answer_max_tokens=10, safety_tokens=10
    ) == 1
    assert calculate_context_input_limit(
        ctx_size=10, answer_max_tokens=100, safety_tokens=100
    ) == 1


# ---------------------------------------------------------------------------
# 2. segments_from_messages with hints
# ---------------------------------------------------------------------------


def test_hints_override_default_inference():
    """有 hint 的 index 使用 hint 覆盖默认推断。"""
    assembler = ContextAssembler()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "profile"},
        {"role": "user", "content": "memory"},
        {"role": "user", "content": "q"},
    ]
    hints = {
        0: ContextSegmentHint(
            kind="system", label="base_prefix", required=True, priority=100
        ),
        1: ContextSegmentHint(
            kind="profile", label="profile_packet", required=True, priority=90
        ),
        2: ContextSegmentHint(
            kind="memory", label="context_packet", required=False, priority=60
        ),
        3: ContextSegmentHint(
            kind="question", label="current_question", required=True, priority=95
        ),
    }
    segments = assembler.segments_from_messages(messages, hints=hints)
    kinds = [s.kind for s in segments]
    labels = [s.label for s in segments]
    required = [s.required for s in segments]
    assert kinds == ["system", "profile", "memory", "question"]
    assert labels == ["base_prefix", "profile_packet", "context_packet", "current_question"]
    assert required == [True, True, False, True]


def test_hint_metadata_merges_with_index():
    """hint metadata 与原始 index 合并。"""
    assembler = ContextAssembler()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
    ]
    hints = {
        0: ContextSegmentHint(
            kind="system",
            label="base_prefix",
            required=True,
            priority=100,
            metadata={"source": "build_chat_messages"},
        ),
    }
    segments = assembler.segments_from_messages(messages, hints=hints)
    seg0 = segments[0]
    assert seg0.metadata["index"] == 0
    assert seg0.metadata["source"] == "build_chat_messages"


# ---------------------------------------------------------------------------
# 3. build_chat_segment_hints
# ---------------------------------------------------------------------------


def test_hints_no_context_packet_does_not_mark_history_user_as_memory():
    """has_context_packet=False 时，index 2 的历史 user 不会被标记为 memory。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "profile"},
        {"role": "user", "content": "history user (no context packet)"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "final question"},
    ]
    hints = build_chat_segment_hints(
        messages, has_profile_packet=True, has_context_packet=False
    )
    # index 2 不应被标记为 memory
    assert 2 not in hints or hints[2].kind != "memory"
    # index 1 是 profile
    assert hints[1].kind == "profile"
    # 最后一条 user 标记为 question
    assert hints[4].kind == "question"


def test_hints_with_context_packet_marks_memory():
    """has_context_packet=True 时，context packet 被标记为 memory/context_packet。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "profile"},
        {"role": "user", "content": "context packet"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "final question"},
    ]
    hints = build_chat_segment_hints(
        messages, has_profile_packet=True, has_context_packet=True
    )
    assert hints[2].kind == "memory"
    assert hints[2].label == "context_packet"
    assert hints[2].required is False
    assert hints[2].priority == 60


def test_hints_system_and_question_always_present():
    """system 和 question 在任何情况下都被标记。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
    ]
    hints = build_chat_segment_hints(
        messages, has_profile_packet=False, has_context_packet=False
    )
    assert hints[0].kind == "system"
    assert hints[1].kind == "question"


# ---------------------------------------------------------------------------
# 4. build_context_budget_preview
# ---------------------------------------------------------------------------


def _preview_messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "你是本地助手"},
        {"role": "user", "content": "profile packet"},
        {"role": "user", "content": "context packet"},
        {"role": "user", "content": "当前问题"},
    ]


def test_build_context_budget_preview_returns_expected_fields():
    """build_context_budget_preview 返回 estimate_source/input_limit 等字段。"""
    payload = build_context_budget_preview(
        messages=_preview_messages(),
        profile_packet="profile packet",
        context_packet="context packet",
        ctx_size=256000,
        answer_max_tokens=32768,
    )
    expected_keys = {
        "estimate_source",
        "input_limit",
        "output_reserve",
        "safety_tokens",
        "input_usage_percent",
        "segments",
        "totals",
        "estimated_input_tokens",
        "estimated_chars",
        "over_limit",
        "ctx_size",
        "actions",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["estimate_source"] == "rough"
    assert payload["output_reserve"] == 32768
    assert payload["safety_tokens"] == CONTEXT_BUDGET_SAFETY_TOKENS
    assert payload["input_limit"] == 215040


def test_build_context_budget_preview_tokens_equal_segment_sum():
    """estimated_input_tokens 与 segment token 之和一致。"""
    payload = build_context_budget_preview(
        messages=_preview_messages(),
        profile_packet="profile packet",
        context_packet="context packet",
        ctx_size=256000,
        answer_max_tokens=32768,
    )
    seg_sum = sum(seg["tokens"] for seg in payload["segments"])
    assert payload["estimated_input_tokens"] == seg_sum


def test_build_context_budget_preview_serializable():
    """preview payload 可被 json.dumps(ensure_ascii=False) 处理。"""
    payload = build_context_budget_preview(
        messages=_preview_messages(),
        profile_packet="profile packet",
        context_packet="context packet",
        ctx_size=256000,
        answer_max_tokens=32768,
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert isinstance(serialized, str)


def test_build_context_budget_preview_marks_profile_memory_question_with_hints():
    """has_profile_packet=True 且 has_context_packet=True 时，
    system/profile/memory/question 四类 segment 被正确归类。"""
    payload = build_context_budget_preview(
        messages=_preview_messages(),
        profile_packet="profile packet",
        context_packet="context packet",
        ctx_size=256000,
        answer_max_tokens=32768,
    )
    kinds = [seg["kind"] for seg in payload["segments"]]
    assert kinds == ["system", "profile", "memory", "question"]


# ---------------------------------------------------------------------------
# 5. inspect_context 集成测试（最小 fake agent + monkeypatch）
# ---------------------------------------------------------------------------


class _FakeAnalysisService:
    def get_latest(self):
        return None


class _FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def set_json(self, name, payload, *, ttl_seconds=None):
        self.data[name] = payload

    def get_json(self, name):
        return self.data.get(name)

    def delete(self, name):
        self.data.pop(name, None)


class _FakeVisionModelClient:
    pass


def _make_fake_agent_with_context(monkeypatch, answer_context: AnswerContext) -> ChatAgent:
    """构造一个最小 ChatAgent，并 monkeypatch _build_answer_context。"""
    agent = ChatAgent(
        runtime_store=_FakeRuntimeStore(),
        analysis_service=_FakeAnalysisService(),
        vision_model_client=_FakeVisionModelClient(),
        memory_service=None,
        window_summary_store=None,
        clear_history_on_start=False,
    )
    monkeypatch.setattr(agent, "_build_answer_context", lambda question, latest, **kw: answer_context)
    return agent


def _build_answer_context_for_test() -> AnswerContext:
    messages = [
        {"role": "system", "content": "你是本地助手"},
        {"role": "user", "content": "profile packet"},
        {"role": "user", "content": "context packet"},
        {"role": "user", "content": "当前问题"},
    ]
    return AnswerContext(
        latest=None,
        context_latest=None,
        history_summaries=[],
        chat_history=[],
        memory_items=[],
        profile_packet="profile packet",
        context_packet="context packet",
        messages=messages,
        registered_tools=[],
        selected_image=None,
        selected_reason="",
        image_path=None,
    )


def test_inspect_context_returns_top_level_context_budget(monkeypatch):
    """inspect_context 返回顶层 context_budget。"""
    agent = _make_fake_agent_with_context(
        monkeypatch, _build_answer_context_for_test()
    )
    result = agent.inspect_context("当前问题")

    assert "context_budget" in result
    cb = result["context_budget"]
    assert cb["estimate_source"] == "rough"
    assert "segments" in cb
    assert "totals" in cb


def test_inspect_context_usage_estimated_tokens_equals_context_budget(monkeypatch):
    """usage['estimated_tokens'] 等于 context_budget['estimated_input_tokens']。"""
    agent = _make_fake_agent_with_context(
        monkeypatch, _build_answer_context_for_test()
    )
    result = agent.inspect_context("当前问题")

    assert result["usage"]["estimated_tokens"] == result["context_budget"]["estimated_input_tokens"]


def test_inspect_context_usage_total_chars_equals_context_budget_chars(monkeypatch):
    """usage['total_chars'] 等于 context_budget['estimated_chars']。"""
    agent = _make_fake_agent_with_context(
        monkeypatch, _build_answer_context_for_test()
    )
    result = agent.inspect_context("当前问题")

    assert result["usage"]["total_chars"] == result["context_budget"]["estimated_chars"]


def test_inspect_context_usage_has_new_budget_fields(monkeypatch):
    """usage 包含 estimate_source/input_limit/input_usage_percent/over_limit。"""
    agent = _make_fake_agent_with_context(
        monkeypatch, _build_answer_context_for_test()
    )
    result = agent.inspect_context("当前问题")

    usage = result["usage"]
    assert usage["estimate_source"] == "rough"
    assert usage["input_limit"] == result["context_budget"]["input_limit"]
    assert usage["input_usage_percent"] == result["context_budget"]["input_usage_percent"]
    assert usage["over_limit"] == result["context_budget"]["over_limit"]


def test_inspect_context_messages_unchanged(monkeypatch):
    """inspect_context 不改变 context.messages。"""
    ctx = _build_answer_context_for_test()
    snapshot = [dict(m) for m in ctx.messages]
    agent = _make_fake_agent_with_context(monkeypatch, ctx)
    agent.inspect_context("当前问题")

    assert ctx.messages == snapshot


# ---------------------------------------------------------------------------
# 6. context_status 兼容测试
# ---------------------------------------------------------------------------


def test_context_status_returns_legacy_fields(monkeypatch):
    """context_status 继续返回 estimated_tokens/usage_percent/remaining_percent。"""
    agent = _make_fake_agent_with_context(
        monkeypatch, _build_answer_context_for_test()
    )
    status = agent.context_status()

    assert "estimated_tokens" in status
    assert "usage_percent" in status
    assert "remaining_percent" in status


def test_calculate_input_limit_rejects_non_int_inputs():
    with pytest.raises(ValueError, match="ctx_size"):
        calculate_context_input_limit(ctx_size=True, answer_max_tokens=0)
    with pytest.raises(ValueError, match="ctx_size"):
        calculate_context_input_limit(ctx_size="256000", answer_max_tokens=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="answer_max_tokens"):
        calculate_context_input_limit(ctx_size=256000, answer_max_tokens=True)
    with pytest.raises(ValueError, match="safety_tokens"):
        calculate_context_input_limit(
            ctx_size=256000, answer_max_tokens=0, safety_tokens=True
        )


def test_hints_without_system_can_mark_profile_and_question():
    messages = [
        {"role": "user", "content": "profile packet"},
        {"role": "user", "content": "final question"},
    ]
    hints = build_chat_segment_hints(
        messages, has_profile_packet=True, has_context_packet=False
    )

    assert hints[0].kind == "profile"
    assert hints[0].label == "profile_packet"
    assert hints[1].kind == "question"
    assert hints[1].label == "current_question"


def test_hints_single_user_message_is_question():
    messages = [{"role": "user", "content": "final question"}]
    hints = build_chat_segment_hints(
        messages, has_profile_packet=True, has_context_packet=True
    )

    assert list(hints) == [0]
    assert hints[0].kind == "question"
    assert hints[0].label == "current_question"


def test_hints_with_compact_summary_marks_summary_before_memory():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "profile"},
        {"role": "user", "content": "compact"},
        {"role": "user", "content": "memory"},
        {"role": "user", "content": "question"},
    ]
    hints = build_chat_segment_hints(
        messages,
        has_profile_packet=True,
        has_compact_summary=True,
        has_context_packet=True,
    )

    assert hints[2].kind == "summary"
    assert hints[2].label == "rolling_summary"
    assert hints[3].kind == "memory"
    assert hints[3].label == "context_packet"