from __future__ import annotations

import json
import math

from app.services.context_budget import ContextTokenEstimator, TokenEstimate


def test_chinese_text_estimate_exceeds_english_chars_per_four():
    """中文每个字符按 1 token 计，大于同长度英文按 chars/4 的估算。"""
    estimator = ContextTokenEstimator()
    chinese = "上下文管理改造的第一个小切片" * 3
    english = "a" * len(chinese)

    cn_est = estimator.estimate_text(chinese)
    en_est = estimator.estimate_text(english)

    assert cn_est.tokens == len(chinese)
    assert cn_est.tokens > en_est.tokens
    assert en_est.tokens == math.ceil(len(english) / 4)


def test_english_long_text_close_to_chars_per_four():
    """纯 ASCII 字母数字长文本接近 ceil(chars/4)。"""
    estimator = ContextTokenEstimator()
    text = "The quick brown fox jumps over 42 lazy dogs " * 20
    est = estimator.estimate_text(text)

    assert est.tokens == math.ceil(len(text) / 4)
    assert est.chars == len(text)


def test_dict_list_uses_compact_json():
    """dict/list 使用紧凑 JSON 序列化，不受缩进影响。"""
    estimator = ContextTokenEstimator()
    value = {"name": "测试", "items": [1, 2, 3], "nested": {"k": "v"}}

    est = estimator.estimate_value(value)
    compact = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    expected = estimator.estimate_text(compact)

    assert est.tokens == expected.tokens
    assert est.chars == expected.chars

    # 紧凑 JSON 比格式化 JSON token 数更少（无缩进空白）。
    pretty = json.dumps(value, ensure_ascii=False, indent=2)
    pretty_est = estimator.estimate_text(pretty)
    assert est.tokens < pretty_est.tokens


def test_multimodal_image_part_counted_as_image_tokens():
    """multimodal image part 计入 image_tokens，按固定 2000 计。"""
    estimator = ContextTokenEstimator()
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "这张图里有什么"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }

    est = estimator.estimate_message(message)
    assert est.details["image_tokens"] == 2000
    assert est.details["text_tokens"] == estimator.estimate_text("这张图里有什么").tokens
    assert est.tokens == est.details["text_tokens"] + 2000 + 10


def test_assistant_tool_calls_envelope_counted():
    """assistant tool_calls envelope 完整计入 tool_call_tokens。"""
    estimator = ContextTokenEstimator()
    tool_calls = [
        {
            "id": "call_abc123",
            "type": "function",
            "function": {
                "name": "memory_search",
                "arguments": '{"query": "上下文管理"}',
            },
        }
    ]
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": tool_calls,
    }

    est = estimator.estimate_message(message)
    expected_envelope = estimator.estimate_value(tool_calls)
    assert est.details["tool_call_tokens"] == expected_envelope.tokens
    assert est.details["tool_call_tokens"] > 0
    assert est.tokens == expected_envelope.tokens + 10


def test_estimate_messages_empty_returns_zero():
    """estimate_messages([]) 返回 0 tokens 且 details messages 为 0。"""
    estimator = ContextTokenEstimator()
    est = estimator.estimate_messages([])

    assert est.tokens == 0
    assert est.chars == 0
    assert est.details["messages"] == 0
    assert est.details["text_tokens"] == 0
    assert est.details["json_tokens"] == 0
    assert est.details["image_tokens"] == 0
    assert est.details["overhead_tokens"] == 0
    assert est.details["tool_call_tokens"] == 0


def test_all_public_methods_return_rough_source():
    """所有公开方法返回 TokenEstimate(source="rough")。"""
    estimator = ContextTokenEstimator()

    assert estimator.estimate_text("hello").source == "rough"
    assert estimator.estimate_text("").source == "rough"
    assert estimator.estimate_value({"a": 1}).source == "rough"
    assert estimator.estimate_value([1, 2, 3]).source == "rough"
    assert estimator.estimate_value(42).source == "rough"
    assert estimator.estimate_message({"role": "user", "content": "hi"}).source == "rough"
    assert estimator.estimate_messages([]).source == "rough"
    assert estimator.estimate_messages(
        [{"role": "user", "content": "hi"}]
    ).source == "rough"


def test_estimate_messages_aggregates_all_buckets():
    """estimate_messages 汇总 text/json/image/overhead/tool_call 五个桶。"""
    estimator = ContextTokenEstimator()
    messages = [
        {"role": "system", "content": "你是助手"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
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
    ]

    est = estimator.estimate_messages(messages)
    assert est.details["messages"] == 4
    assert est.details["text_tokens"] > 0
    assert est.details["image_tokens"] == 2000
    assert est.details["overhead_tokens"] == 40  # 4 messages * 10
    assert est.details["tool_call_tokens"] > 0
    assert est.details["json_tokens"] > 0  # tool content is dict
    assert est.tokens == (
        est.details["text_tokens"]
        + est.details["json_tokens"]
        + est.details["image_tokens"]
        + est.details["overhead_tokens"]
        + est.details["tool_call_tokens"]
    )


def test_empty_string_returns_zero():
    """空字符串返回 0 tokens。"""
    estimator = ContextTokenEstimator()
    est = estimator.estimate_text("")
    assert est.tokens == 0
    assert est.chars == 0


def test_value_uses_str_for_non_serializable():
    """无法 JSON 序列化的值按字符串估算。"""
    estimator = ContextTokenEstimator()

    class Custom:
        def __str__(self) -> str:
            return "custom-object"

    est = estimator.estimate_value(Custom())
    assert est.tokens == estimator.estimate_text("custom-object").tokens
    assert est.source == "rough"


def test_multimodal_image_url_part_counts_2000():
    """image_url part 计入 2000 tokens。"""
    estimator = ContextTokenEstimator()
    message = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }
    est = estimator.estimate_message(message)
    assert est.details["image_tokens"] == 2000
    assert est.tokens == 2000 + 10


def test_multimodal_image_part_counts_2000():
    """image part 计入 2000 tokens。"""
    estimator = ContextTokenEstimator()
    message = {
        "role": "user",
        "content": [
            {"type": "image", "image": "data:image/png;base64,BBBB"},
        ],
    }
    est = estimator.estimate_message(message)
    assert est.details["image_tokens"] == 2000
    assert est.tokens == 2000 + 10


def test_multimodal_input_image_part_counts_2000():
    """input_image part 计入 2000 tokens。"""
    estimator = ContextTokenEstimator()
    message = {
        "role": "user",
        "content": [
            {"type": "input_image", "image_url": "data:image/jpeg;base64,CCCC"},
        ],
    }
    est = estimator.estimate_message(message)
    assert est.details["image_tokens"] == 2000
    assert est.tokens == 2000 + 10


def test_multimodal_base64_does_not_inflate_text_estimate():
    """含 base64 data URL 的图片按固定 token 计，不按字符串长度膨胀。"""
    estimator = ContextTokenEstimator()
    short_b64 = "data:image/png;base64,AAAA"
    long_b64 = "data:image/png;base64," + "A" * 100_000

    short_est = estimator.estimate_message(
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": short_b64}}]}
    )
    long_est = estimator.estimate_message(
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": long_b64}}]}
    )

    assert short_est.details["image_tokens"] == 2000
    assert long_est.details["image_tokens"] == 2000
    assert short_est.tokens == long_est.tokens
    # base64 字符串不进入 text_tokens 桶。
    assert short_est.details["text_tokens"] == 0
    assert long_est.details["text_tokens"] == 0


def test_cjk_extension_g_h_chars_counted_as_cjk():
    """CJK Extension G/H 字符按 CJK token 规则估算，每字符 1 token。"""
    estimator = ContextTokenEstimator()
    # Extension G 起始码点 0x30000 与 Extension H 区间内的字符。
    ext_g_h_chars = [chr(0x30000), chr(0x31350), chr(0x323AF)]
    text = "".join(ext_g_h_chars)

    est = estimator.estimate_text(text)
    assert est.details["cjk_tokens"] == len(ext_g_h_chars)
    assert est.tokens == len(ext_g_h_chars)
    assert est.chars == len(ext_g_h_chars)
