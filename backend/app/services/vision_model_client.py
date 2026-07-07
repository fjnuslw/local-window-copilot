from __future__ import annotations

import base64
import json
import math
import re
import urllib.error
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
from app.services.local_copilot_identity import (
    is_local_copilot_title,
    mentions_local_copilot,
)
from app.services.runtime_log import get_runtime_log_service


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
        image_max_pixels: int | None,
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
        self.image_max_pixels = image_max_pixels
        self.timeout_seconds = timeout_seconds
        self.analyze_temperature = analyze_temperature
        self.analyze_max_tokens = analyze_max_tokens
        self.answer_temperature = answer_temperature
        self.answer_max_tokens = answer_max_tokens

    def analyze_image(self, image_path: Path) -> tuple[WindowAnalysis, VisionInput]:
        """分析窗口截图。返回 (分析结果, 视觉输入元信息)。

        视觉输入元信息记录原图尺寸与送入模型的缩放后尺寸，便于调试与追溯。
        """
        log = get_runtime_log_service()
        prompt = self.prompt_path.read_text(encoding="utf-8")
        image_data_url, original_size, sent_size = self._image_to_data_url(image_path)
        fields = {
            "endpoint": self.endpoint,
            "model": self.model_name,
            "image_path": str(image_path),
            "original_size": original_size,
            "sent_size": sent_size,
            "long_edge": self.image_long_edge,
            "max_pixels": self.image_max_pixels or 0,
            "max_tokens": self.analyze_max_tokens,
            "response_format_schema_enabled": False,
        }
        payload = {
            "model": self.model_name,
            "temperature": self.analyze_temperature,
            "max_tokens": self.analyze_max_tokens,
            "response_format": {"type": "json_object"},
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
        log.info("vision_model", "request", "Vision analysis request started.", **fields)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
            response_info = extract_response_info(raw)
            content = response_info["content"]
            finish_reason = response_info["finish_reason"]
            reasoning_content = response_info["reasoning_content"]
            log.info(
                "vision_model",
                "response",
                "Vision analysis response received.",
                **fields,
                finish_reason=finish_reason,
                response_chars=len(content),
                reasoning_chars=len(reasoning_content),
                usage=response_info["usage"],
            )
            if reasoning_content.strip():
                log.warning(
                    "vision_model",
                    "reasoning_content_present",
                    "Vision response still contains reasoning_content; restart llama-server with reasoning off if observation parsing is unstable.",
                    **fields,
                    finish_reason=finish_reason,
                    reasoning_chars=len(reasoning_content),
                    usage=response_info["usage"],
                )
            try:
                analysis = parse_window_analysis(content)
            except Exception as exc:
                log.exception(
                    "vision_model",
                    "parse_failure",
                    "Vision analysis response could not be parsed.",
                    exc,
                    **fields,
                    finish_reason=finish_reason,
                    usage=response_info["usage"],
                    raw_text=content,
                    reasoning_text=reasoning_content,
                )
                if finish_reason == "length":
                    raise VisionModelClientError(
                        "Vision analysis output reached max_tokens before JSON completed. "
                        "The observation schema/prompt produced an oversized response; "
                        "raise LWC_ANALYZE_MAX_TOKENS or tighten the observation contract."
                    ) from exc
                raise
            if finish_reason and finish_reason not in {"stop", "tool_calls"}:
                log.warning(
                    "vision_model",
                    "non_stop_finish_parsed",
                    "Vision analysis parsed despite non-stop finish reason.",
                    **fields,
                    finish_reason=finish_reason,
                    usage=response_info["usage"],
                )
        except urllib.error.HTTPError as exc:
            message = format_http_error(exc)
            wrapped = VisionModelClientError(message)
            log.exception(
                "vision_model",
                "request_failure",
                "Vision analysis request failed.",
                wrapped,
                **fields,
                http_error=message,
            )
            raise wrapped from exc
        except Exception as exc:
            log.exception(
                "vision_model",
                "request_failure",
                "Vision analysis request failed.",
                exc,
                **fields,
            )
            raise
        vision_input = VisionInput(
            original_size=original_size,
            sent_size=sent_size,
            long_edge=self.image_long_edge,
            max_pixels=self.image_max_pixels or 0,
            detail_mode=_detail_mode_for_long_edge(self.image_long_edge),
        )
        log.info(
            "vision_model",
            "parse_success",
            "Vision analysis response parsed.",
            **fields,
            window_type=analysis.window_type,
            summary=analysis.summary,
        )
        return analysis, vision_input

    def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        image_path: Path | None = None,
        image_long_edge: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[str]:
        """纯文本多轮对话（messages 结构）。可选在最后一条 user 消息附加截图。

        这是真正的对话 agent 入口：对话历史以 messages 多轮结构传递，
        模型能完整理解自己之前说了什么，从而"记得自己做了什么"。

        当传入 tools 时，流式输出中如果模型发起 tool_call，会通过
        ``last_stream_tool_calls`` 属性返回完整的 tool_calls 列表（与
        OpenAI 非流式格式一致）。调用方应在消费完生成器后检查此属性。
        """
        self._collected_stream_tool_calls: list[dict[str, Any]] = []
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
        payload: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.answer_temperature,
            "max_tokens": self.answer_max_tokens,
            "stream": True,
            "messages": payload_messages,
        }
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
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
                    # 1) 提取文本 delta 并 yield
                    text = extract_stream_delta(chunk)
                    if text:
                        yield text
                    # 2) 收集 tool_call delta（不 yield，调用方通过属性读取）
                    self._accumulate_stream_tool_calls(chunk)
        except urllib.error.HTTPError as exc:
            raise VisionModelClientError(format_http_error(exc)) from exc

    @property
    def last_stream_tool_calls(self) -> list[dict[str, Any]]:
        """最近一次 ``stream_chat(tools=...)`` 调用中模型发起的 tool_calls。

        仅当传入 ``tools`` 参数时可能非空。每次 ``stream_chat`` 调用会重置。
        返回 OpenAI 非流式格式的完整 tool_calls 列表。
        """
        return getattr(self, "_collected_stream_tool_calls", [])

    def _accumulate_stream_tool_calls(self, chunk: dict[str, Any]) -> None:
        """从 SSE chunk 中提取并累积 tool_call delta 到 _collected_stream_tool_calls。

        OpenAI 流式 tool_calls 格式：每个 chunk 的 delta.tool_calls 是偏量数组，
        需要按 index 追加到对应位置。最终拼出完整 tool_calls（与 non-streaming
        格式一致）。
        """
        try:
            choice = chunk["choices"][0]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                return
            tc_deltas = delta.get("tool_calls")
            if not tc_deltas:
                return
            for tc_delta in tc_deltas:
                idx = tc_delta.get("index", 0)
                # 确保列表足够长
                while len(self._collected_stream_tool_calls) <= idx:
                    self._collected_stream_tool_calls.append({
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                existing = self._collected_stream_tool_calls[idx]
                if "id" in tc_delta and tc_delta["id"]:
                    existing["id"] = tc_delta["id"]
                func_delta = tc_delta.get("function", {})
                if "name" in func_delta and func_delta["name"]:
                    existing["function"]["name"] = func_delta["name"]
                if "arguments" in func_delta and func_delta["arguments"]:
                    existing["function"]["arguments"] += func_delta["arguments"]
        except (KeyError, IndexError, TypeError):
            pass
        except (KeyError, IndexError, TypeError):
            pass

    def complete_chat_response(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.answer_temperature if temperature is None else temperature,
            "max_tokens": self.answer_max_tokens if max_tokens is None else max_tokens,
            "stream": False,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto" if tool_choice is None else tool_choice
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise VisionModelClientError(format_http_error(exc)) from exc
        if not isinstance(raw, dict):
            raise VisionModelClientError("Invalid llama-server chat completion response.")
        return raw

    def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        raw = self.complete_chat_response(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return extract_message_content(raw)

    def _image_to_data_url(
        self,
        image_path: Path,
        *,
        image_long_edge: int | None = None,
    ) -> tuple[str, list[int], list[int]]:
        """缩放图片并转 data_url。返回 (data_url, original_size, sent_size)。"""
        long_edge = image_long_edge or self.image_long_edge
        max_pixels = self.image_max_pixels
        with Image.open(image_path) as image:
            original_size = [image.width, image.height]
            image = image.convert("RGB")
            scale = 1.0
            max_dim = max(image.width, image.height)
            if long_edge > 0 and max_dim > long_edge:
                scale = min(scale, long_edge / max_dim)
            if max_pixels and max_pixels > 0:
                pixel_count = image.width * image.height
                if pixel_count * scale * scale > max_pixels:
                    scale = min(scale, math.sqrt(max_pixels / pixel_count))
            if scale < 1.0:
                target_size = (
                    max(1, int(round(image.width * scale))),
                    max(1, int(round(image.height * scale))),
                )
                image = image.resize(target_size, Image.Resampling.LANCZOS)
            sent_size = [image.width, image.height]
            output = BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}", original_size, sent_size

def format_http_error(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace").strip()
    if len(body) > 2000:
        body = body[:2000] + "..."
    suffix = f": {body}" if body else ""
    return f"llama-server HTTP {exc.code} {exc.reason}{suffix}"

def _detail_mode_for_long_edge(long_edge: int) -> str:
    """将长边像素映射回视觉清晰度档位名，用于记录与展示。"""
    if long_edge <= 768:
        return "fast"
    if long_edge <= 1024:
        return "standard"
    return "detailed"


def extract_response_info(response: dict[str, Any]) -> dict[str, Any]:
    try:
        choice = response["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionModelClientError("Invalid llama-server chat completion response.") from exc
    if not isinstance(choice, dict) or not isinstance(message, dict):
        raise VisionModelClientError("Invalid llama-server chat completion response shape.")

    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    if content is None:
        content = ""
    if reasoning_content is None:
        reasoning_content = ""
    if not isinstance(content, str):
        raise VisionModelClientError("llama-server response content is not text.")
    if not isinstance(reasoning_content, str):
        reasoning_content = str(reasoning_content)
    return {
        "content": content,
        "reasoning_content": reasoning_content,
        "finish_reason": choice.get("finish_reason"),
        "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
        "message_keys": sorted(str(key) for key in message.keys()),
    }

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
    return _normalize_window_analysis(WindowAnalysis.model_validate_json(json_text))


def _unique_nonempty(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_window_analysis(analysis: WindowAnalysis) -> WindowAnalysis:
    if not analysis.visible_text:
        analysis.visible_text = _unique_nonempty(
            [text for region in analysis.regions for text in region.visible_text]
        )
    if not analysis.ui_elements:
        analysis.ui_elements = _unique_nonempty(
            [item for region in analysis.regions for item in region.ui_elements]
        )
    return analysis


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


BASE_PREFIX = """你是“小窗”，一个本地桌面伙伴。
你的目标是在用户工作时保持安静在场，并在用户主动提问时把窗口、代码、页面、记忆和对话线索整理成清楚的中文回答。
你会收到 profile / memory context / dialogue。profile 决定你的角色与语气；memory context 是已取回的本地证据；dialogue 是当前对话。
当用户询问屏幕、当前窗口、页面、代码、最近看到的内容、记忆或历史对话时，先调用 memory.search(query) 获取证据，再回答。
如果 memory.search 没有返回可用证据，说明缺少可靠观察，并回答仅凭当前对话可以确定的部分。
面向用户输出自然中文；内部工具名、JSON、日志和接口细节留在后台。
表达要具体、温和、有判断。能确定就直接说清楚；不能确定就说明缺口和下一步。
涉及点击、输入、提交、删除、安装等真实系统操作时，给建议、步骤或确认请求。
"""


def _dict_like(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _list_line(label: str, value: Any) -> str | None:
    items = _string_list(value)
    if not items:
        return None
    return f"{label}：" + "；".join(items)


def format_window_observation(
    *,
    summary: str | None,
    key_points: list[str] | None = None,
    regions: list[Any] | None = None,
    visible_text: list[str] | None = None,
    ui_elements: list[str] | None = None,
    entities: list[str] | None = None,
    uncertain_areas: list[str] | None = None,
    vision_input: Any | None = None,
) -> str:
    """Format the structured observation index for chat context."""
    parts: list[str] = []
    if summary:
        parts.append("观察文本：\n" + summary)
    region_lines: list[str] = []
    for region in regions or []:
        data = _dict_like(region)
        if not data:
            continue
        name = str(data.get("name") or "区域").strip()
        location = str(data.get("location") or "").strip()
        content = str(data.get("content") or "").strip()
        head = f"- {name}"
        if location:
            head += f"（{location}）"
        if content:
            head += f"：{content}"
        details: list[str] = [head]
        text_line = _list_line("  可读文字", data.get("visible_text"))
        if text_line:
            details.append(text_line)
        ui_line = _list_line("  UI 元素", data.get("ui_elements"))
        if ui_line:
            details.append(ui_line)
        uncertainty = str(data.get("uncertainty") or "").strip()
        if uncertainty:
            details.append(f"  不确定：{uncertainty}")
        region_lines.append("\n".join(details))
    if region_lines:
        parts.append("区域结构：\n" + "\n".join(region_lines))
    for label, value in (
        ("全局可读文字", visible_text),
        ("全局 UI 元素", ui_elements),
        ("可检索实体", entities),
        ("关键点", key_points),
        ("不确定区域", uncertain_areas),
    ):
        line = _list_line(label, value)
        if line:
            parts.append(line)
    if vision_input:
        data = _dict_like(vision_input)
        if data:
            parts.append(
                "视觉输入："
                f"sent_size={data.get('sent_size') or []}；"
                f"long_edge={data.get('long_edge') or 0}；"
                f"max_pixels={data.get('max_pixels') or 0}；"
                f"detail={data.get('detail_mode') or ''}"
            )
    return "\n".join(parts).strip()


def build_context_packet(
    *,
    current_app_name: str | None = None,
    current_window_title: str | None = None,
    current_window_type: str | None = None,
    current_summary: str | None,
    current_key_points: list[str],
    current_regions: list[Any] | None = None,
    current_visible_text: list[str] | None = None,
    current_ui_elements: list[str] | None = None,
    current_entities: list[str] | None = None,
    current_uncertain_areas: list[str] | None = None,
    current_vision_input: Any | None = None,
    history_window_summaries: list[dict[str, Any]] | None = None,
    memory_items: list[MemoryItem] | None = None,
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
            + "\n- 注意：本应用自身浮窗属于助手界面；用户询问当前窗口时优先看真实工作窗口。"
        )
    current_observation = format_window_observation(
        summary=current_summary,
        key_points=current_key_points,
        regions=current_regions,
        visible_text=current_visible_text,
        ui_elements=current_ui_elements,
        entities=current_entities,
        uncertain_areas=current_uncertain_areas,
        vision_input=current_vision_input,
    )
    if current_observation:
        parts.append("当前窗口观察：\n" + current_observation)
    if history_window_summaries:
        lines: list[str] = []
        for rec in history_window_summaries:
            ts = str(rec.get("created_at", ""))[:19].replace("T", " ")
            app = rec.get("app_name", "") or ""
            title = rec.get("window_title", "") or ""
            if is_local_copilot_title(str(title)):
                continue
            wtype = rec.get("window_type", "") or ""
            observation_text = format_window_observation(
                summary=str(rec.get("summary", "")),
                key_points=_string_list(rec.get("key_points")),
                regions=rec.get("regions") if isinstance(rec.get("regions"), list) else None,
                visible_text=_string_list(rec.get("visible_text")),
                ui_elements=_string_list(rec.get("ui_elements")),
                entities=_string_list(rec.get("entities")),
                uncertain_areas=_string_list(rec.get("uncertain_areas")),
                vision_input=rec.get("vision_input"),
            )
            if mentions_local_copilot(observation_text):
                continue
            head = f"[{ts}] {app} · {title}".strip(" ·")
            if wtype:
                head += f"（{wtype}）"
            lines.append(f"- {head}：\n{observation_text}")
        if lines:
            parts.append("最近观察到的窗口（按时间正序，仅作背景参考）：\n" + "\n".join(lines))
    if memory_items:
        mlines = [
            f"- {item.text}"
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
    compact_summary: str | None = None,
    current_app_name: str | None = None,
    current_window_title: str | None = None,
    current_window_type: str | None = None,
    current_summary: str | None,
    current_key_points: list[str],
    current_regions: list[Any] | None = None,
    current_visible_text: list[str] | None = None,
    current_ui_elements: list[str] | None = None,
    current_entities: list[str] | None = None,
    current_uncertain_areas: list[str] | None = None,
    current_vision_input: Any | None = None,
    history_window_summaries: list[dict[str, Any]] | None = None,
    chat_history: list[ChatSession] | None = None,
    memory_items: list[MemoryItem] | None = None,
) -> list[dict[str, Any]]:
    """构建 KV cache 友好的分层 messages。

    结构（见 kv_cache_profile_and_agent_split_spec_zh.md §5）：
        messages[0] system: base_prefix        稳定，不随用户/窗口/记忆变化
        messages[1] user:   <profile_packet>   低频变化（WebUI 编辑后生效）
        messages[2] user:   <compact_state>    对话历史状态指针
        messages[3] user:   <context_packet>   高频变化（每次窗口变化）
        messages[4..]      dialogue_tail       user/assistant 多轮 + 当前问题

    base_prefix 完全由 BASE_PREFIX 常量决定，不依赖任何运行时参数，
    因此窗口观察/profile/记忆变化时 messages[0] 保持不变。
    """
    messages: list[dict[str, Any]] = []
    messages.append({"role": "system", "content": BASE_PREFIX})
    packet = profile_packet.strip()
    if packet:
        messages.append({"role": "user", "content": packet})
    compact_packet = (compact_summary or "").strip()
    if compact_packet:
        messages.append({
            "role": "user",
            "content": (
                "[compact_state]\n"
                "以下是较早对话的工作状态指针。逐字历史通过 memory.search(query) 检索。\n\n"
                f"{compact_packet}"
            ),
        })
    context_packet = build_context_packet(
        current_app_name=current_app_name,
        current_window_title=current_window_title,
        current_window_type=current_window_type,
        current_summary=current_summary,
        current_key_points=current_key_points,
        current_regions=current_regions,
        current_visible_text=current_visible_text,
        current_ui_elements=current_ui_elements,
        current_entities=current_entities,
        current_uncertain_areas=current_uncertain_areas,
        current_vision_input=current_vision_input,
        history_window_summaries=history_window_summaries,
        memory_items=memory_items,
    )
    if context_packet.strip():
        messages.append({"role": "user", "content": context_packet.strip()})
    if chat_history:
        for session in chat_history:
            q = session.question.strip()
            a = session.answer.strip()
            if mentions_local_copilot(q) or mentions_local_copilot(a):
                continue
            if q and a and session.status in {"done"}:
                messages.append({"role": "user", "content": q})
                messages.append({"role": "assistant", "content": a})
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
        image_max_pixels=settings.model_image_max_pixels,
        timeout_seconds=settings.llama_request_timeout_seconds,
        analyze_temperature=settings.analyze_temperature,
        analyze_max_tokens=settings.analyze_max_tokens,
        answer_temperature=settings.answer_temperature,
        answer_max_tokens=settings.answer_max_tokens,
    )
