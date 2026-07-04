from __future__ import annotations

import base64
import json
import re
import urllib.request
from functools import lru_cache
from io import BytesIO
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.config import get_settings
from app.schemas.analyze import VisionInput, WindowAnalysis
from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem
from app.services.dialogue_context import build_dialogue_bridge
from app.services.local_copilot_identity import (
    is_local_copilot_title,
    mentions_local_copilot,
)


class VisionModelClientError(RuntimeError):
    pass


class VisionModelClient:
    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        prompt_path: Path,
        image_long_edge: int,
        timeout_seconds: float,
        analyze_temperature: float = 0.1,
        analyze_max_tokens: int = 700,
        answer_temperature: float = 0.2,
        answer_max_tokens: int = 800,
    ) -> None:
        self.endpoint = endpoint
        self.model_name = model_name
        self.prompt_path = prompt_path
        self.image_long_edge = image_long_edge
        self.timeout_seconds = timeout_seconds
        self.analyze_temperature = analyze_temperature
        self.analyze_max_tokens = analyze_max_tokens
        self.answer_temperature = answer_temperature
        self.answer_max_tokens = answer_max_tokens

    def analyze_image(self, image_path: Path) -> tuple[WindowAnalysis, VisionInput]:
        """分析窗口截图。返回 (分析结果, 视觉输入元信息)。

        视觉输入元信息记录原图尺寸与送入模型的缩放后尺寸，便于调试与追溯。
        """
        prompt = self.prompt_path.read_text(encoding="utf-8")
        image_data_url, original_size, sent_size = self._image_to_data_url(image_path)
        payload = {
            "model": self.model_name,
            "temperature": self.analyze_temperature,
            "max_tokens": self.analyze_max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        analysis = parse_window_analysis(extract_message_content(raw))
        vision_input = VisionInput(
            original_size=original_size,
            sent_size=sent_size,
            long_edge=self.image_long_edge,
            detail_mode=_detail_mode_for_long_edge(self.image_long_edge),
        )
        return analysis, vision_input

    def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        image_path: Path | None = None,
        image_long_edge: int | None = None,
    ) -> Iterator[str]:
        """纯文本多轮对话（messages 结构）。可选在最后一条 user 消息附加截图。

        这是真正的对话 agent 入口：对话历史以 messages 多轮结构传递，
        模型能完整理解自己之前说了什么，从而"记得自己做了什么"。
        """
        payload_messages: list[dict[str, Any]] = [dict(m) for m in messages]
        if image_path is not None and payload_messages:
            image_data_url, _orig, _sent = self._image_to_data_url(
                image_path,
                image_long_edge=image_long_edge,
            )
            last = payload_messages[-1]
            if last.get("role") == "user" and isinstance(last.get("content"), str):
                last["content"] = [
                    {"type": "text", "text": last["content"]},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ]
        payload = {
            "model": self.model_name,
            "temperature": self.answer_temperature,
            "max_tokens": self.answer_max_tokens,
            "stream": True,
            "messages": payload_messages,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                text = extract_stream_delta(chunk)
                if text:
                    yield text

    def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload = {
            "model": self.model_name,
            "temperature": self.answer_temperature if temperature is None else temperature,
            "max_tokens": self.answer_max_tokens if max_tokens is None else max_tokens,
            "stream": False,
            "messages": messages,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return extract_message_content(raw)

    def stream_visual_answer(
        self,
        *,
        question: str,
        image_path: Path,
        visual_prompt: str,
        image_long_edge: int | None = None,
    ) -> Iterator[str]:
        """视觉追问：基于截图直接回答用户问题。

        与 stream_chat 的区别：
        - 使用 visual_question_answer prompt 作为 system 消息（而非 BASE_PREFIX + context）。
        - 不注入 profile/context/history，避免干扰视觉回答。
        - 图片直接附加在 user 消息上。
        """
        image_data_url, _orig, _sent = self._image_to_data_url(
            image_path,
            image_long_edge=image_long_edge,
        )
        payload = {
            "model": self.model_name,
            "temperature": self.answer_temperature,
            "max_tokens": self.answer_max_tokens,
            "stream": True,
            "messages": [
                {"role": "system", "content": visual_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                text = extract_stream_delta(chunk)
                if text:
                    yield text

    def _image_to_data_url(
        self,
        image_path: Path,
        *,
        image_long_edge: int | None = None,
    ) -> tuple[str, list[int], list[int]]:
        """缩放图片并转 data_url。返回 (data_url, original_size, sent_size)。"""
        long_edge = image_long_edge or self.image_long_edge
        with Image.open(image_path) as image:
            original_size = [image.width, image.height]
            image = image.convert("RGB")
            image.thumbnail((long_edge, long_edge))
            sent_size = [image.width, image.height]
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}", original_size, sent_size


def _detail_mode_for_long_edge(long_edge: int) -> str:
    """将长边像素映射回视觉清晰度档位名，用于记录与展示。"""
    if long_edge <= 768:
        return "fast"
    if long_edge <= 1024:
        return "standard"
    return "detailed"


def extract_message_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionModelClientError("Invalid llama-server chat completion response.") from exc
    if not isinstance(content, str):
        raise VisionModelClientError("llama-server response content is not text.")
    return content


def extract_json_object(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    start = cleaned.find("{")
    if start < 0:
        raise VisionModelClientError("No JSON object found in model output.")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : index + 1]
    raise VisionModelClientError("Unclosed JSON object in model output.")


def parse_window_analysis(text: str) -> WindowAnalysis:
    json_text = extract_json_object(text)
    return WindowAnalysis.model_validate_json(json_text)


def extract_stream_delta(response: dict[str, Any]) -> str:
    try:
        choice = response["choices"][0]
    except (KeyError, IndexError, TypeError):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    message = choice.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = choice.get("text")
    return text if isinstance(text, str) else ""


BASE_PREFIX = """你是本地桌宠式窗口 Copilot。
你会收到 profile / context / dialogue 三类输入。
不要输出 JSON、日志、接口名。
无法确定时说明不确定。
不要声称能自动点击、输入或操作电脑。
用中文回答。"""


def build_context_packet(
    *,
    current_app_name: str | None = None,
    current_window_title: str | None = None,
    current_window_type: str | None = None,
    current_summary: str | None,
    current_key_points: list[str],
    history_window_summaries: list[dict[str, Any]] | None = None,
    memory_items: list[MemoryItem] | None = None,
    memory_item_max_chars: int = 220,
) -> str:
    """构建 context_packet：当前窗口观察 + 历史窗口观察 + 记忆。高频变化。"""
    parts: list[str] = []
    current_meta_lines = []
    if current_app_name:
        current_meta_lines.append(f"- 应用：{current_app_name}")
    if current_window_title:
        current_meta_lines.append(f"- 标题：{current_window_title}")
    if current_window_type:
        current_meta_lines.append(f"- 类型：{current_window_type}")
    if current_meta_lines:
        parts.append(
            "当前窗口元信息（回答“当前/这个窗口”问题时优先使用）：\n"
            + "\n".join(current_meta_lines)
            + "\n- 注意：本应用自身浮窗不是用户正在询问的当前窗口。"
        )
    if current_summary:
        kp = "；".join(current_key_points[:6]) if current_key_points else ""
        block = f"当前窗口观察：\n{current_summary}"
        if kp:
            block += f"\n关键点：{kp}"
        parts.append(block)
    if history_window_summaries:
        lines: list[str] = []
        for rec in history_window_summaries:
            ts = str(rec.get("created_at", ""))[:19].replace("T", " ")
            app = rec.get("app_name", "") or ""
            title = rec.get("window_title", "") or ""
            if is_local_copilot_title(str(title)):
                continue
            wtype = rec.get("window_type", "") or ""
            summ = str(rec.get("summary", ""))[:300]
            if mentions_local_copilot(summ):
                continue
            head = f"[{ts}] {app} · {title}".strip(" ·")
            if wtype:
                head += f"（{wtype}）"
            lines.append(f"- {head}：{summ}")
        if lines:
            parts.append("最近观察到的窗口（按时间正序，仅作背景参考）：\n" + "\n".join(lines))
    if memory_items:
        mlines = [
            f"- {item.text[:memory_item_max_chars]}"
            for item in memory_items
            if item.text.strip() and not mentions_local_copilot(item.text)
        ]
        if mlines:
            parts.append("相关记忆：\n" + "\n".join(mlines))
    return "\n\n".join(parts)


def build_chat_messages(
    *,
    question: str,
    profile_packet: str,
    current_app_name: str | None = None,
    current_window_title: str | None = None,
    current_window_type: str | None = None,
    current_summary: str | None,
    current_key_points: list[str],
    history_window_summaries: list[dict[str, Any]] | None = None,
    chat_history: list[ChatSession] | None = None,
    memory_items: list[MemoryItem] | None = None,
    question_max_chars: int = 500,
    answer_max_chars: int = 800,
    memory_item_max_chars: int = 220,
) -> list[dict[str, Any]]:
    """构建 KV cache 友好的分层 messages。

    结构（见 kv_cache_profile_and_agent_split_spec_zh.md §5）：
        messages[0] system: base_prefix        稳定，不随用户/窗口/记忆变化
        messages[1] user:   <profile_packet>   低频变化（WebUI 编辑后生效）
        messages[2] user:   <context_packet>   高频变化（每次窗口变化）
        messages[3..]      dialogue_tail       user/assistant 多轮 + 当前问题

    base_prefix 完全由 BASE_PREFIX 常量决定，不依赖任何运行时参数，
    因此窗口观察/profile/记忆变化时 messages[0] 保持不变。
    """
    messages: list[dict[str, Any]] = []
    # [0] system: base_prefix（稳定）
    messages.append({"role": "system", "content": BASE_PREFIX})
    # [1] user: profile_packet（低频变化）
    packet = profile_packet.strip()
    if packet:
        messages.append({"role": "user", "content": packet})
    # [2] user: context_packet（高频变化）
    context_packet = build_context_packet(
        current_app_name=current_app_name,
        current_window_title=current_window_title,
        current_window_type=current_window_type,
        current_summary=current_summary,
        current_key_points=current_key_points,
        history_window_summaries=history_window_summaries,
        memory_items=memory_items,
        memory_item_max_chars=memory_item_max_chars,
    )
    if context_packet.strip():
        messages.append({"role": "user", "content": context_packet.strip()})
    # [3..] dialogue_tail（多轮历史 + 当前问题）
    if chat_history:
        for session in chat_history:
            q = session.question.strip()
            a = session.answer.strip()
            if mentions_local_copilot(q) or mentions_local_copilot(a):
                continue
            if q and a and session.status in {"done"}:
                messages.append({"role": "user", "content": q[:question_max_chars]})
                messages.append({"role": "assistant", "content": a[:answer_max_chars]})
    messages.append({"role": "user", "content": question})
    return messages


def build_companion_messages(
    *,
    question: str,
    companion_prompt: str,
    profile_packet: str,
    chat_history: list[ChatSession] | None = None,
    user_goals: list[dict[str, Any]] | None = None,
    question_max_chars: int = 500,
    answer_max_chars: int = 800,
) -> list[dict[str, Any]]:
    """构建陪伴模式的轻量 messages。

    结构（见 ambient_companion_product_spec_zh.md §4.2 / §6.1）：
        messages[0] system: companion_prompt  陪伴人设，稳定
        messages[1] user:   <profile_packet>  低频变化
        messages[2] user:   <user_goals>      用户最近关心的目标，低噪声陪伴记忆
        messages[3..]      dialogue_tail      user/assistant 多轮 + 当前问题

    与 build_chat_messages 的区别：不注入 context_packet（窗口观察），
    陪伴模式优先接住情绪和意图，不把窗口观察当作答案来源。
    """
    messages: list[dict[str, Any]] = []
    # [0] system: companion_prompt（陪伴人设）
    prompt = companion_prompt.strip()
    if prompt:
        messages.append({"role": "system", "content": prompt})
    else:
        messages.append({"role": "system", "content": BASE_PREFIX})
    # [1] user: profile_packet（低频变化）
    packet = profile_packet.strip()
    if packet:
        messages.append({"role": "user", "content": packet})
    if user_goals:
        lines: list[str] = []
        for item in user_goals[:3]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("situation_label") or "").strip()
            question_text = str(item.get("question") or "").strip()
            if label and question_text:
                lines.append(f"- {label}: {question_text[:160]}")
        if lines:
            messages.append({
                "role": "user",
                "content": "用户最近关心的事情（只用于理解语境，不要机械复述）：\n"
                + "\n".join(lines),
            })
    # [3..] dialogue_tail（多轮历史 + 当前问题，不注入窗口观察）
    bridge = build_dialogue_bridge(question, chat_history)
    effective_question = bridge.effective_question if bridge is not None else question

    if chat_history:
        for session in chat_history:
            q = session.question.strip()
            a = session.answer.strip()
            if mentions_local_copilot(q) or mentions_local_copilot(a):
                continue
            if q and a and session.status in {"done"}:
                messages.append({"role": "user", "content": q[:question_max_chars]})
                messages.append({"role": "assistant", "content": a[:answer_max_chars]})
    if bridge is not None:
        messages.append({"role": "user", "content": bridge.message})
        messages.append({
            "role": "user",
            "content": "请直接继续本轮实际任务："
            + effective_question
            + "\n不要反问用户是否需要继续，直接往下说。",
        })
    else:
        messages.append({"role": "user", "content": question})
    return messages


@lru_cache
def get_vision_model_client() -> VisionModelClient:
    settings = get_settings()
    return VisionModelClient(
        endpoint=settings.llama_chat_completions_endpoint,
        model_name=settings.minicpm_model_name,
        prompt_path=settings.window_analysis_prompt_path,
        image_long_edge=settings.model_image_long_edge,
        timeout_seconds=settings.llama_request_timeout_seconds,
        analyze_temperature=settings.analyze_temperature,
        analyze_max_tokens=settings.analyze_max_tokens,
        answer_temperature=settings.answer_temperature,
        answer_max_tokens=settings.answer_max_tokens,
    )
