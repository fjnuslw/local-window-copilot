from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any


# Spec §5: 每条 message 的 role/key 固定开销。
_MESSAGE_OVERHEAD_TOKENS = 10
# Spec §5: 每张图片固定估算值，默认 2000。
_MULTIMODAL_IMAGE_TOKENS = 2000
# multimodal 图片 part 类型：统一按固定 token 计入 image_tokens，不估算 base64 长度。
_MULTIMODAL_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})

# Spec §6 / §F: 输入预算的安全余量，预留回答空间和意外开销。
CONTEXT_BUDGET_SAFETY_TOKENS = 8192


def _require_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be int")
    return value


def calculate_context_input_limit(
    *,
    ctx_size: int,
    answer_max_tokens: int,
    safety_tokens: int = CONTEXT_BUDGET_SAFETY_TOKENS,
) -> int:
    """计算模型输入预算上限。

    规则：input_limit = ctx_size - answer_max_tokens - safety_tokens。
    负数输入按 0 处理，返回值至少为 1。
    """
    ctx = max(0, _require_int(ctx_size, "ctx_size"))
    answer = max(0, _require_int(answer_max_tokens, "answer_max_tokens"))
    safety = max(0, _require_int(safety_tokens, "safety_tokens"))
    return max(1, ctx - answer - safety)


@dataclass(frozen=True)
class TokenEstimate:
    source: str
    tokens: int
    chars: int
    details: dict[str, int] = field(default_factory=dict)


def _is_cjk_char(ch: str) -> bool:
    """CJK/Hangul/Kana 字符按 1 token 计（spec §5）。"""
    cp = ord(ch)
    # CJK Unified Ideographs 及 Extension A
    if 0x4E00 <= cp <= 0x9FFF:
        return True
    if 0x3400 <= cp <= 0x4DBF:
        return True
    # CJK Extension B-F 及兼容补充（astral plane）
    if 0x20000 <= cp <= 0x2FA1F:
        return True
    # CJK Extension G/H
    if 0x30000 <= cp <= 0x323AF:
        return True
    # CJK Compatibility Ideographs
    if 0xF900 <= cp <= 0xFAFF:
        return True
    # Hangul Syllables / Jamo / Compatibility Jamo
    if 0xAC00 <= cp <= 0xD7AF:
        return True
    if 0x1100 <= cp <= 0x11FF:
        return True
    if 0x3130 <= cp <= 0x318F:
        return True
    # Hiragana / Katakana
    if 0x3040 <= cp <= 0x309F:
        return True
    if 0x30A0 <= cp <= 0x30FF:
        return True
    if 0x31F0 <= cp <= 0x31FF:
        return True
    return False


def _is_ascii_text_char(ch: str) -> bool:
    """ASCII 字母数字和常见空白按 ceil(chars/4) 计（spec §5）。"""
    if ch in " \t\n\r\f\v":
        return True
    return ch.isascii() and ch.isalnum()


def _is_multimodal_content(content: list[Any]) -> bool:
    """OpenAI multimodal content: 非空列表且每个元素是含 "type" 的 dict。"""
    if not content:
        return False
    return all(isinstance(part, dict) and "type" in part for part in content)


class ContextTokenEstimator:
    """确定性 rough token 估算器。

    所有输出固定 source="rough"，严禁展示为真实 tokenizer 数值（spec §5、§13）。
    仅使用标准库，无网络依赖，无业务链路副作用。
    """

    source = "rough"

    def estimate_text(self, text: str) -> TokenEstimate:
        chars = len(text)
        if chars == 0:
            return TokenEstimate(source=self.source, tokens=0, chars=0, details={})

        cjk = 0
        ascii_text = 0
        other = 0
        for ch in text:
            if _is_cjk_char(ch):
                cjk += 1
            elif _is_ascii_text_char(ch):
                ascii_text += 1
            else:
                # JSON 标点 / 符号按 ceil(chars/2) 计。
                other += 1

        cjk_tokens = cjk
        ascii_tokens = math.ceil(ascii_text / 4) if ascii_text else 0
        other_tokens = math.ceil(other / 2) if other else 0
        tokens = cjk_tokens + ascii_tokens + other_tokens
        return TokenEstimate(
            source=self.source,
            tokens=tokens,
            chars=chars,
            details={
                "cjk_tokens": cjk_tokens,
                "ascii_tokens": ascii_tokens,
                "other_tokens": other_tokens,
            },
        )

    def estimate_value(self, value: Any) -> TokenEstimate:
        if isinstance(value, str):
            return self.estimate_text(value)
        if isinstance(value, (dict, list)):
            # 紧凑 JSON：不转义非 ASCII、无缩进（spec §5）。
            # 无法序列化的值按字符串估算。
            try:
                payload = json.dumps(
                    value, ensure_ascii=False, separators=(",", ":")
                )
            except (TypeError, ValueError):
                payload = str(value)
            return self.estimate_text(payload)
        # 其他类型转成 str 再估算。
        return self.estimate_text(str(value))

    def estimate_message(self, message: dict[str, Any]) -> TokenEstimate:
        text_tokens = 0
        json_tokens = 0
        image_tokens = 0
        tool_call_tokens = 0
        chars = 0

        content = message.get("content")
        if isinstance(content, str):
            est = self.estimate_text(content)
            text_tokens += est.tokens
            chars += est.chars
        elif isinstance(content, list) and _is_multimodal_content(content):
            for part in content:
                if not isinstance(part, dict):
                    est = self.estimate_value(part)
                    json_tokens += est.tokens
                    chars += est.chars
                    continue
                part_type = part.get("type")
                if part_type == "text":
                    est = self.estimate_text(part.get("text", ""))
                    text_tokens += est.tokens
                    chars += est.chars
                elif part_type in _MULTIMODAL_IMAGE_PART_TYPES:
                    # 图片 part 按固定 token 计，不估算 base64 长度。
                    image_tokens += _MULTIMODAL_IMAGE_TOKENS
                else:
                    est = self.estimate_value(part)
                    json_tokens += est.tokens
                    chars += est.chars
        elif isinstance(content, (dict, list)):
            est = self.estimate_value(content)
            json_tokens += est.tokens
            chars += est.chars
        elif content is not None:
            est = self.estimate_value(content)
            text_tokens += est.tokens
            chars += est.chars

        # tool_calls envelope：完整估算 id/type/function.name/function.arguments。
        tool_calls = message.get("tool_calls")
        if tool_calls:
            est = self.estimate_value(tool_calls)
            tool_call_tokens += est.tokens
            chars += est.chars

        overhead_tokens = _MESSAGE_OVERHEAD_TOKENS
        tokens = (
            text_tokens
            + json_tokens
            + image_tokens
            + overhead_tokens
            + tool_call_tokens
        )
        return TokenEstimate(
            source=self.source,
            tokens=tokens,
            chars=chars,
            details={
                "text_tokens": text_tokens,
                "json_tokens": json_tokens,
                "image_tokens": image_tokens,
                "overhead_tokens": overhead_tokens,
                "tool_call_tokens": tool_call_tokens,
            },
        )

    def estimate_messages(self, messages: list[dict[str, Any]]) -> TokenEstimate:
        total_text = 0
        total_json = 0
        total_image = 0
        total_overhead = 0
        total_tool_call = 0
        total_chars = 0
        for msg in messages:
            est = self.estimate_message(msg)
            d = est.details
            total_text += d.get("text_tokens", 0)
            total_json += d.get("json_tokens", 0)
            total_image += d.get("image_tokens", 0)
            total_overhead += d.get("overhead_tokens", 0)
            total_tool_call += d.get("tool_call_tokens", 0)
            total_chars += est.chars

        total_tokens = (
            total_text
            + total_json
            + total_image
            + total_overhead
            + total_tool_call
        )
        return TokenEstimate(
            source=self.source,
            tokens=total_tokens,
            chars=total_chars,
            details={
                "messages": len(messages),
                "text_tokens": total_text,
                "json_tokens": total_json,
                "image_tokens": total_image,
                "overhead_tokens": total_overhead,
                "tool_call_tokens": total_tool_call,
            },
        )


# ---------------------------------------------------------------------------
# Segment / Report 数据结构（spec §4）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextSegment:
    kind: str
    role: str
    label: str
    message: dict[str, Any]
    required: bool
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextSegmentHint:
    """显式 segment 归类提示，覆盖纯 index 推断的边界。"""
    kind: str
    label: str
    required: bool
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextSegmentReport:
    kind: str
    role: str
    label: str
    tokens: int
    chars: int
    required: bool
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)
    details: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextBudgetReport:
    ctx_size: int
    input_limit: int
    estimated_input_tokens: int
    estimated_chars: int
    over_limit: bool
    segments: list[ContextSegmentReport]
    totals: dict[str, int] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)


def _classify_segment(
    index: int, message: dict[str, Any], total: int
) -> tuple[str, str, bool, int]:
    """按 message 顺序和 role 轻量推断 kind/label/required/priority。

    不解析提示词正文；最后一条 user message 优先标记为 question，
    覆盖 index=1/2 的 profile/memory 默认推断。
    """
    role = message.get("role", "")
    is_last = index == total - 1

    if is_last and role == "user":
        return "question", "current_question", True, 95
    if index == 0 and role == "system":
        return "system", "base_prefix", True, 100
    if index == 1 and role == "user":
        return "profile", "profile_packet", True, 90
    if index == 2 and role == "user":
        return "memory", "context_packet", False, 60
    if role == "tool":
        return "tool_result", f"tool_result:{index}", False, 50
    if role in ("user", "assistant"):
        return "history", f"history:{index}", False, 70
    return "other", f"message:{index}", False, 10


class ContextAssembler:
    """把 OpenAI style messages 映射成 segment 并生成预算报告。

    segments_from_messages 只做轻量 role/index 推断，不修改输入 messages。
    build_report 使用 rough estimate 生成可审计账本，不接入业务链路。
    """

    def __init__(self, estimator: ContextTokenEstimator | None = None) -> None:
        self.estimator = estimator or ContextTokenEstimator()

    def segments_from_messages(
        self,
        messages: list[dict[str, Any]],
        hints: dict[int, ContextSegmentHint] | None = None,
    ) -> list[ContextSegment]:
        total = len(messages)
        segments: list[ContextSegment] = []
        for index, message in enumerate(messages):
            hint = hints.get(index) if hints else None
            if hint is not None:
                kind = hint.kind
                label = hint.label
                required = hint.required
                priority = hint.priority
                metadata = {"index": index}
                metadata.update(hint.metadata)
            else:
                kind, label, required, priority = _classify_segment(
                    index, message, total
                )
                metadata = {"index": index}
            segments.append(
                ContextSegment(
                    kind=kind,
                    role=message.get("role", ""),
                    label=label,
                    message=message,
                    required=required,
                    priority=priority,
                    metadata=metadata,
                )
            )
        return segments

    def build_report(
        self,
        segments: list[ContextSegment],
        *,
        ctx_size: int,
        input_limit: int,
    ) -> ContextBudgetReport:
        segment_reports: list[ContextSegmentReport] = []
        total_text = 0
        total_json = 0
        total_image = 0
        total_overhead = 0
        total_tool_call = 0
        total_tokens = 0
        total_chars = 0

        for segment in segments:
            est = self.estimator.estimate_message(segment.message)
            d = est.details
            seg_text = d.get("text_tokens", 0)
            seg_json = d.get("json_tokens", 0)
            seg_image = d.get("image_tokens", 0)
            seg_overhead = d.get("overhead_tokens", 0)
            seg_tool_call = d.get("tool_call_tokens", 0)

            total_text += seg_text
            total_json += seg_json
            total_image += seg_image
            total_overhead += seg_overhead
            total_tool_call += seg_tool_call
            total_tokens += est.tokens
            total_chars += est.chars

            segment_reports.append(
                ContextSegmentReport(
                    kind=segment.kind,
                    role=segment.role,
                    label=segment.label,
                    tokens=est.tokens,
                    chars=est.chars,
                    required=segment.required,
                    priority=segment.priority,
                    metadata=dict(segment.metadata),
                    details={
                        "text_tokens": seg_text,
                        "json_tokens": seg_json,
                        "image_tokens": seg_image,
                        "overhead_tokens": seg_overhead,
                        "tool_call_tokens": seg_tool_call,
                    },
                )
            )

        over_limit = total_tokens > input_limit
        actions: list[str] = []
        if over_limit:
            actions.append("over_limit_detected")

        return ContextBudgetReport(
            ctx_size=ctx_size,
            input_limit=input_limit,
            estimated_input_tokens=total_tokens,
            estimated_chars=total_chars,
            over_limit=over_limit,
            segments=segment_reports,
            totals={
                "segments": len(segments),
                "text_tokens": total_text,
                "json_tokens": total_json,
                "image_tokens": total_image,
                "overhead_tokens": total_overhead,
                "tool_call_tokens": total_tool_call,
            },
            actions=actions,
        )


def budget_report_to_dict(report: ContextBudgetReport) -> dict[str, Any]:
    """把 ContextBudgetReport 转成可 JSON 序列化的 dict。"""
    return {
        "ctx_size": report.ctx_size,
        "input_limit": report.input_limit,
        "estimated_input_tokens": report.estimated_input_tokens,
        "estimated_chars": report.estimated_chars,
        "over_limit": report.over_limit,
        "segments": [
            {
                "kind": seg.kind,
                "role": seg.role,
                "label": seg.label,
                "tokens": seg.tokens,
                "chars": seg.chars,
                "required": seg.required,
                "priority": seg.priority,
                "metadata": dict(seg.metadata),
                "details": dict(seg.details),
            }
            for seg in report.segments
        ],
        "totals": dict(report.totals),
        "actions": list(report.actions),
    }


def build_chat_segment_hints(
    messages: list[dict[str, Any]],
    *,
    has_profile_packet: bool,
    has_compact_summary: bool = False,
    has_context_packet: bool = False,
) -> dict[int, ContextSegmentHint]:
    """按 build_chat_messages 实际顺序生成显式 segment hints。

    profile、compact summary 与 context packet 的 index 由 messages 顺序推进决定，
    不靠正文内容匹配。context packet 为空时，历史 user 不会被标记为 memory。
    """
    hints: dict[int, ContextSegmentHint] = {}
    total = len(messages)
    if total == 0:
        return hints

    has_system = messages[0].get("role") == "system"
    if has_system:
        hints[0] = ContextSegmentHint(
            kind="system",
            label="base_prefix",
            required=True,
            priority=100,
        )

    cursor = 1 if has_system else 0
    # profile packet 在 system 后（仅当存在且 role=user）
    if (
        has_profile_packet
        and cursor < total
        and messages[cursor].get("role") == "user"
    ):
        hints[cursor] = ContextSegmentHint(
            kind="profile",
            label="profile_packet",
            required=True,
            priority=90,
        )
        cursor += 1

    # compact summary 紧随 profile（仅当存在且 role=user）
    if (
        has_compact_summary
        and cursor < total
        and messages[cursor].get("role") == "user"
    ):
        hints[cursor] = ContextSegmentHint(
            kind="summary",
            label="rolling_summary",
            required=False,
            priority=80,
        )
        cursor += 1

    # context packet 紧随 compact summary（仅当存在且 role=user）
    if (
        has_context_packet
        and cursor < total
        and messages[cursor].get("role") == "user"
    ):
        hints[cursor] = ContextSegmentHint(
            kind="memory",
            label="context_packet",
            required=False,
            priority=60,
        )
        cursor += 1

    # 最后一条 role=user 标记为 question（覆盖历史 user 推断）
    last_user_index = -1
    for i in range(total - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_index = i
            break
    if last_user_index >= 0:
        hints[last_user_index] = ContextSegmentHint(
            kind="question",
            label="current_question",
            required=True,
            priority=95,
        )

    return hints

@dataclass(frozen=True)
class ToolResultBudgetReport:
    tool_name: str
    call_id: str
    original_tokens: int
    final_tokens: int
    item_limit_tokens: int
    remaining_budget_tokens: int
    truncated: bool
    actions: tuple[str, ...]


@dataclass(frozen=True)
class ToolResultBudgetedContent:
    content: str
    report: ToolResultBudgetReport


def _tool_result_json_type(value: Any) -> str:
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def _head_tail_preview(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return f"[TRUNCATED middle_chars={len(text)}]"
    head_chars = max(0, max_chars // 2)
    tail_chars = max(0, max_chars - head_chars)
    middle_chars = max(0, len(text) - head_chars - tail_chars)
    tail = text[-tail_chars:] if tail_chars else ""
    return f"{text[:head_chars]}\n[TRUNCATED middle_chars={middle_chars}]\n{tail}"


def _tool_result_budget_envelope(
    *,
    tool_name: str,
    call_id: str,
    original_tokens: int,
    budget_tokens: int,
    preview: str,
    original_json_type: str,
) -> str:
    payload = {
        "tool_result_budget": {
            "tool_name": tool_name,
            "call_id": call_id,
            "truncated": True,
            "original_tokens": original_tokens,
            "budget_tokens": budget_tokens,
            "message": "Result shortened before model input. Use memory.search with a narrower query for more detail.",
        },
        "content_preview": preview,
        "original_json_type": original_json_type,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def budget_tool_result_content(
    content: str,
    *,
    tool_name: str,
    call_id: str | None = None,
    item_limit_tokens: int = 3000,
    remaining_budget_tokens: int = 8000,
    estimator: ContextTokenEstimator | None = None,
) -> ToolResultBudgetedContent:
    if not isinstance(content, str):
        raise ValueError("content must be str")
    est = estimator or ContextTokenEstimator()
    item_limit = max(1, _require_int(item_limit_tokens, "item_limit_tokens"))
    remaining_limit = max(1, _require_int(remaining_budget_tokens, "remaining_budget_tokens"))
    effective_budget = max(1, min(item_limit, remaining_limit))
    tool_name_value = str(tool_name or "")
    call_id_value = str(call_id or "")

    original_estimate = est.estimate_text(content)
    if original_estimate.tokens <= effective_budget:
        return ToolResultBudgetedContent(
            content=content,
            report=ToolResultBudgetReport(
                tool_name=tool_name_value,
                call_id=call_id_value,
                original_tokens=original_estimate.tokens,
                final_tokens=original_estimate.tokens,
                item_limit_tokens=item_limit,
                remaining_budget_tokens=remaining_limit,
                truncated=False,
                actions=(),
            ),
        )

    try:
        parsed = json.loads(content)
        source_text = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        original_json_type = _tool_result_json_type(parsed)
    except json.JSONDecodeError:
        source_text = content
        original_json_type = "text"

    actions = ["tool_result_truncated"]
    if remaining_limit < item_limit:
        actions.append("tool_result_total_budget_limited")

    candidate_char_limits = []
    for value in (
        effective_budget * 4,
        effective_budget * 3,
        effective_budget * 2,
        effective_budget,
        512,
        256,
        128,
        64,
        0,
    ):
        limit = max(0, min(len(source_text), value))
        if limit not in candidate_char_limits:
            candidate_char_limits.append(limit)

    selected_content = ""
    selected_tokens = 0
    for char_limit in candidate_char_limits:
        preview = _head_tail_preview(source_text, char_limit)
        candidate = _tool_result_budget_envelope(
            tool_name=tool_name_value,
            call_id=call_id_value,
            original_tokens=original_estimate.tokens,
            budget_tokens=effective_budget,
            preview=preview,
            original_json_type=original_json_type,
        )
        candidate_tokens = est.estimate_text(candidate).tokens
        selected_content = candidate
        selected_tokens = candidate_tokens
        if candidate_tokens <= effective_budget:
            break

    if selected_tokens > effective_budget:
        actions.append("tool_result_minimal_notice")

    return ToolResultBudgetedContent(
        content=selected_content,
        report=ToolResultBudgetReport(
            tool_name=tool_name_value,
            call_id=call_id_value,
            original_tokens=original_estimate.tokens,
            final_tokens=selected_tokens,
            item_limit_tokens=item_limit,
            remaining_budget_tokens=remaining_limit,
            truncated=True,
            actions=tuple(actions),
        ),
    )


def tool_result_budget_report_to_dict(
    report: ToolResultBudgetReport,
) -> dict[str, Any]:
    return {
        "tool_name": report.tool_name,
        "call_id": report.call_id,
        "original_tokens": report.original_tokens,
        "final_tokens": report.final_tokens,
        "item_limit_tokens": report.item_limit_tokens,
        "remaining_budget_tokens": report.remaining_budget_tokens,
        "truncated": report.truncated,
        "actions": list(report.actions),
    }