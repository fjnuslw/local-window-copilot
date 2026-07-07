from __future__ import annotations

import copy
import json

from app.services.context_budget import (
    ContextAssembler,
    ContextTokenEstimator,
    budget_report_to_dict,
)


def _canonical_messages() -> list[dict]:
    return [
        {"role": "system", "content": "你是本地助手"},
        {"role": "user", "content": "profile packet"},
        {"role": "user", "content": "memory context packet"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "现在开始"},
    ]


def test_canonical_messages_map_to_expected_kinds():
    """canonical messages 映射成 system/profile/memory/history/question。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())

    kinds = [s.kind for s in segments]
    labels = [s.label for s in segments]
    assert kinds == ["system", "profile", "memory", "history", "question"]
    assert labels == [
        "base_prefix",
        "profile_packet",
        "context_packet",
        "history:3",
        "current_question",
    ]


def test_required_segments_marked():
    """system/profile/question 为 required。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())

    required = {s.kind: s.required for s in segments}
    assert required["system"] is True
    assert required["profile"] is True
    assert required["question"] is True
    assert required["memory"] is False
    assert required["history"] is False


def test_segment_metadata_preserves_index():
    """segment metadata 保留原始 index。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())

    for index, seg in enumerate(segments):
        assert seg.metadata["index"] == index


def test_input_messages_unchanged_after_call():
    """输入 messages 在调用后保持不变。"""
    assembler = ContextAssembler()
    original = _canonical_messages()
    snapshot = copy.deepcopy(original)

    assembler.segments_from_messages(original)
    assembler.build_report(
        assembler.segments_from_messages(original),
        ctx_size=256000,
        input_limit=64000,
    )

    assert original == snapshot


def test_report_total_tokens_equal_sum_of_segments():
    """report 总 tokens 等于逐段 tokens 之和。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())
    report = assembler.build_report(segments, ctx_size=256000, input_limit=64000)

    seg_sum = sum(seg.tokens for seg in report.segments)
    assert report.estimated_input_tokens == seg_sum


def test_report_totals_aggregate_five_buckets():
    """report totals 汇总 text/json/image/overhead/tool_call 五个桶。"""
    assembler = ContextAssembler()
    messages = [
        {"role": "system", "content": "你是助手"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": {"result": "ok", "count": 3}},
        {"role": "user", "content": "继续"},
    ]
    segments = assembler.segments_from_messages(messages)
    report = assembler.build_report(segments, ctx_size=256000, input_limit=64000)

    totals = report.totals
    assert totals["segments"] == 5
    assert totals["text_tokens"] > 0
    assert totals["image_tokens"] == 2000
    assert totals["tool_call_tokens"] > 0
    assert totals["json_tokens"] > 0  # tool content is dict
    assert totals["overhead_tokens"] == 50  # 5 messages * 10

    # totals 各桶之和等于 estimated_input_tokens
    assert (
        totals["text_tokens"]
        + totals["json_tokens"]
        + totals["image_tokens"]
        + totals["overhead_tokens"]
        + totals["tool_call_tokens"]
    ) == report.estimated_input_tokens


def test_image_message_counts_2000_image_tokens():
    """图片 message 的 image_tokens 为 2000。"""
    assembler = ContextAssembler()
    messages = [
        {"role": "system", "content": "你是助手"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
    ]
    segments = assembler.segments_from_messages(messages)
    report = assembler.build_report(segments, ctx_size=256000, input_limit=64000)

    image_seg = next(seg for seg in report.segments if seg.details["image_tokens"] > 0)
    assert image_seg.details["image_tokens"] == 2000


def test_assistant_tool_calls_counted_in_tool_call_tokens():
    """assistant tool_calls 计入 tool_call_tokens。"""
    assembler = ContextAssembler()
    tool_calls = [
        {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "memory_search", "arguments": '{"q":"x"}'},
        }
    ]
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "user", "content": "继续"},
    ]
    segments = assembler.segments_from_messages(messages)
    report = assembler.build_report(segments, ctx_size=256000, input_limit=64000)

    assistant_seg = next(seg for seg in report.segments if seg.role == "assistant")
    assert assistant_seg.details["tool_call_tokens"] > 0
    assert report.totals["tool_call_tokens"] == assistant_seg.details["tool_call_tokens"]


def test_over_limit_sets_flag_and_action():
    """estimated_input_tokens > input_limit 时 over_limit=True 且 actions 包含 over_limit_detected。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())
    # 用一个很小的 input_limit 触发 over_limit
    report = assembler.build_report(segments, ctx_size=256000, input_limit=1)

    assert report.estimated_input_tokens > 1
    assert report.over_limit is True
    assert "over_limit_detected" in report.actions


def test_within_limit_has_empty_actions():
    """未超限时 actions 为空。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())
    report = assembler.build_report(segments, ctx_size=256000, input_limit=10**9)

    assert report.over_limit is False
    assert report.actions == []


def test_report_serializable_to_json():
    """report 序列化结果可被 json.dumps(..., ensure_ascii=False) 处理。"""
    assembler = ContextAssembler()
    segments = assembler.segments_from_messages(_canonical_messages())
    report = assembler.build_report(segments, ctx_size=256000, input_limit=64000)

    payload = budget_report_to_dict(report)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert isinstance(serialized, str)

    # 反序列化后字段保持完整。
    restored = json.loads(serialized)
    assert restored["ctx_size"] == 256000
    assert restored["input_limit"] == 64000
    assert restored["estimated_input_tokens"] == report.estimated_input_tokens
    assert len(restored["segments"]) == len(report.segments)
    assert "over_limit" in restored
    assert "totals" in restored
    assert "actions" in restored
