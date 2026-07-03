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
from app.schemas.analyze import WindowAnalysis, WindowAnalysisResult
from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem


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
        chat_history_question_max_chars: int = 300,
        chat_history_answer_max_chars: int = 400,
        memory_item_max_chars: int = 220,
        personality_enabled: bool = False,
        personality_name: str = "",
        personality_traits: str = "",
        system_prompt_prefix: str = "",
        answer_style_hint: str = "",
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
        self.chat_history_question_max_chars = chat_history_question_max_chars
        self.chat_history_answer_max_chars = chat_history_answer_max_chars
        self.memory_item_max_chars = memory_item_max_chars
        self.personality_enabled = personality_enabled
        self.personality_name = personality_name
        self.personality_traits = personality_traits
        self.system_prompt_prefix = system_prompt_prefix
        self.answer_style_hint = answer_style_hint

    def analyze_image(self, image_path: Path) -> WindowAnalysis:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        image_data_url = self._image_to_data_url(image_path)
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
        return parse_window_analysis(extract_message_content(raw))

    def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        image_path: Path | None = None,
    ) -> Iterator[str]:
        """纯文本多轮对话（messages 结构）。可选在最后一条 user 消息附加截图。

        这是真正的对话 agent 入口：对话历史以 messages 多轮结构传递，
        模型能完整理解自己之前说了什么，从而"记得自己做了什么"。
        """
        payload_messages: list[dict[str, Any]] = [dict(m) for m in messages]
        if image_path is not None and payload_messages:
            image_data_url = self._image_to_data_url(image_path)
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

    def stream_answer(
        self,
        *,
        latest: WindowAnalysisResult,
        question: str,
        memory_items: list[MemoryItem] | None = None,
        chat_history: list[ChatSession] | None = None,
    ) -> Iterator[str]:
        image_data_url = self._image_to_data_url(latest.capture.screenshot_path)
        prompt = build_question_prompt(
            latest,
            question,
            memory_items=memory_items,
            chat_history=chat_history,
            question_max_chars=self.chat_history_question_max_chars,
            answer_max_chars=self.chat_history_answer_max_chars,
            memory_item_max_chars=self.memory_item_max_chars,
            personality_enabled=self.personality_enabled,
            personality_name=self.personality_name,
            personality_traits=self.personality_traits,
            system_prompt_prefix=self.system_prompt_prefix,
            answer_style_hint=self.answer_style_hint,
        )
        payload = {
            "model": self.model_name,
            "temperature": self.answer_temperature,
            "max_tokens": self.answer_max_tokens,
            "stream": True,
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

    def _image_to_data_url(self, image_path: Path) -> str:
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            image.thumbnail((self.image_long_edge, self.image_long_edge))
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"


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


def build_question_prompt(
    latest: WindowAnalysisResult,
    question: str,
    *,
    memory_items: list[MemoryItem] | None = None,
    chat_history: list[ChatSession] | None = None,
    question_max_chars: int = 300,
    answer_max_chars: int = 400,
    memory_item_max_chars: int = 220,
    personality_enabled: bool = False,
    personality_name: str = "",
    personality_traits: str = "",
    system_prompt_prefix: str = "",
    answer_style_hint: str = "",
) -> str:
    analysis = latest.analysis
    key_points = "\n".join(f"- {item}" for item in analysis.key_points[:6])

    # 性格与人设描述
    persona_block = ""
    if personality_enabled:
        persona_lines: list[str] = []
        if personality_name.strip():
            persona_lines.append(f"你的名字是「{personality_name.strip()}」。")
        if personality_traits.strip():
            persona_lines.append(f"性格特征：{personality_traits.strip()}")
        if persona_lines:
            persona_block = "\n".join(persona_lines) + "\n"

    # 自定义系统提示前缀
    prefix_block = ""
    if system_prompt_prefix.strip():
        prefix_block = system_prompt_prefix.strip() + "\n\n"

    # 回答风格提示
    style_line = ""
    if answer_style_hint.strip():
        style_line = f"- 回答风格：{answer_style_hint.strip()}"

    memory_text = ""
    if memory_items:
        lines = [
            f"- {item.text[:memory_item_max_chars]}"
            for item in memory_items
            if item.text.strip()
        ]
        if lines:
            memory_text = "\n相关记忆：\n" + "\n".join(lines) + "\n"
    history_text = ""
    if chat_history:
        turns: list[str] = []
        for session in chat_history:
            q = session.question.strip()
            a = session.answer.strip()
            if q and a and session.status in {"done"}:
                turns.append(f"用户：{q[:question_max_chars]}\n助手：{a[:answer_max_chars]}")
        if turns:
            history_text = (
                "\n之前的对话（用于理解追问上下文，可参考但不要重复已有内容）：\n"
                + "\n\n".join(turns)
                + "\n"
            )
    return f"""{prefix_block}你是一个本地桌面悬浮 AI 助手。用户正在询问当前窗口内容。
{persona_block}
回答要求：
- 只回答用户问题，不输出系统状态、来源、接口名、日志、JSON。
- 如果截图或摘要不足以确定答案，直接说明不确定，并给出下一步需要用户补充的信息。
- 使用简洁中文，优先给可执行建议。
- 相关记忆只作为辅助线索，不能覆盖当前截图和当前窗口摘要。
- 如果用户在追问，结合之前的对话上下文理解意图，保持连贯。{style_line}

当前窗口摘要：
{analysis.summary}

关键点：
{key_points}{memory_text}{history_text}
用户问题：
{question}
"""


def build_chat_messages(
    *,
    question: str,
    current_summary: str | None,
    current_key_points: list[str],
    history_window_summaries: list[dict[str, Any]] | None = None,
    chat_history: list[ChatSession] | None = None,
    memory_items: list[MemoryItem] | None = None,
    question_max_chars: int = 500,
    answer_max_chars: int = 800,
    memory_item_max_chars: int = 220,
    personality_enabled: bool = False,
    personality_name: str = "",
    personality_traits: str = "",
    system_prompt_prefix: str = "",
    answer_style_hint: str = "",
) -> list[dict[str, Any]]:
    """构建对话 agent 的 messages 数组（system + 多轮历史 + 当前问题）。

    与 build_question_prompt 的区别：
    - 对话历史以 user/assistant 交替的 messages 多轮结构传递，而非文本拼接，
      模型能真正理解自己之前的回答，实现"记得自己做了什么"的可迭代 agent。
    - 当前窗口摘要、历史窗口摘要、记忆作为 system message 背景。
    """
    prefix_block = system_prompt_prefix.strip() + "\n\n" if system_prompt_prefix.strip() else ""

    persona_block = ""
    if personality_enabled:
        persona_lines: list[str] = []
        if personality_name.strip():
            persona_lines.append(f"你的名字是「{personality_name.strip()}」。")
        if personality_traits.strip():
            persona_lines.append(f"性格特征：{personality_traits.strip()}")
        if persona_lines:
            persona_block = "\n".join(persona_lines) + "\n"

    style_line = f"- 回答风格：{answer_style_hint.strip()}" if answer_style_hint.strip() else ""

    # 当前窗口摘要
    current_block = ""
    if current_summary:
        kp = "；".join(current_key_points[:6]) if current_key_points else ""
        current_block = f"当前窗口摘要：\n{current_summary}"
        if kp:
            current_block += f"\n关键点：{kp}"
        current_block += "\n\n"

    # 历史窗口摘要（最近 N 条，标注时间）
    history_block = ""
    if history_window_summaries:
        lines: list[str] = []
        for rec in history_window_summaries:
            ts = str(rec.get("created_at", ""))[:19].replace("T", " ")
            app = rec.get("app_name", "") or ""
            title = rec.get("window_title", "") or ""
            wtype = rec.get("window_type", "") or ""
            summ = str(rec.get("summary", ""))[:300]
            head = f"[{ts}] {app} · {title}".strip(" ·")
            if wtype:
                head += f"（{wtype}）"
            lines.append(f"- {head}：{summ}")
        if lines:
            history_block = "最近观察到的窗口（按时间正序，仅作背景参考）：\n" + "\n".join(lines) + "\n\n"

    # 记忆
    memory_block = ""
    if memory_items:
        mlines = [
            f"- {item.text[:memory_item_max_chars]}"
            for item in memory_items
            if item.text.strip()
        ]
        if mlines:
            memory_block = "相关记忆：\n" + "\n".join(mlines) + "\n\n"

    system_text = f"""{prefix_block}你是一个本地桌面悬浮 AI 助手，正在与用户进行多轮对话。
{persona_block}
你会持续观察用户的前台窗口（由识图摘要服务提供），用户的问题通常围绕当前窗口内容。
你的职责是基于窗口摘要与对话历史回答用户问题、给出可执行建议。

回答要求：
- 只回答用户问题，不输出系统状态、来源、接口名、日志、JSON。
- 如果窗口摘要不足以确定答案，直接说明不确定，并给出下一步需要用户补充的信息。
- 使用简洁中文，优先给可执行建议。
- 你能看到完整的多轮对话历史，请保持连贯，记住自己之前说过什么，不要重复已有内容。{style_line}

{current_block}{history_block}{memory_block}"""
    # 修剪 system_text 末尾多余换行
    system_text = system_text.rstrip() + "\n"

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]

    # 多轮对话历史（旧→新），仅取 done 且非空
    if chat_history:
        for session in chat_history:
            q = session.question.strip()
            a = session.answer.strip()
            if q and a and session.status in {"done"}:
                messages.append({"role": "user", "content": q[:question_max_chars]})
                messages.append({"role": "assistant", "content": a[:answer_max_chars]})

    # 当前问题
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
        chat_history_question_max_chars=settings.chat_history_question_max_chars,
        chat_history_answer_max_chars=settings.chat_history_answer_max_chars,
        memory_item_max_chars=settings.memory_item_max_chars,
        personality_enabled=settings.personality_enabled,
        personality_name=settings.personality_name,
        personality_traits=settings.personality_traits,
        system_prompt_prefix=settings.system_prompt_prefix,
        answer_style_hint=settings.answer_style_hint,
    )
