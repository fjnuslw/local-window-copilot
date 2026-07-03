from __future__ import annotations

from typing import Any

from dotenv import set_key
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import ENV_FILE_PATH, get_settings, reload_settings
from app.services.assistant_chat import get_assistant_chat_service
from app.services.memory import get_memory_service
from app.services.vision_model_client import get_vision_model_client
from app.services.window_summary_store import get_window_summary_store


router = APIRouter(prefix="/api/webui", tags=["webui"])


# 字段元数据：key 必须与 Settings 字段名一致，env_key 自动派生为 LWC_<KEY.upper()>
FIELD_META: list[dict[str, Any]] = [
    # --- 模型与调用 ---
    {"key": "analyze_temperature", "group": "model", "label": "分析采样温度", "type": "number", "description": "窗口分析时的采样温度，越低越确定，越高越发散"},
    {"key": "analyze_max_tokens", "group": "model", "label": "分析最大 token", "type": "number", "description": "窗口分析单次生成的最大 token 数"},
    {"key": "answer_temperature", "group": "model", "label": "问答采样温度", "type": "number", "description": "追问回答时的采样温度"},
    {"key": "answer_max_tokens", "group": "model", "label": "问答最大 token", "type": "number", "description": "追问回答单次生成的最大 token 数"},
    {"key": "model_image_long_edge", "group": "model", "label": "图片长边像素", "type": "number", "description": "送入模型的截图缩放长边，越小越省显存但细节越少"},
    {"key": "minicpm_ctx_size", "group": "model", "label": "模型上下文长度", "type": "number", "description": "llama-server 启动时的 ctx_size（改后需重启后端生效）"},

    # --- 上下文窗口 ---
    {"key": "chat_history_turns", "group": "context", "label": "注入历史轮数", "type": "number", "description": "每次回答时注入的最近对话轮数，用于理解追问"},
    {"key": "chat_history_question_max_chars", "group": "context", "label": "历史问题截断字数", "type": "number", "description": "注入历史时每条用户问题的最大字符数"},
    {"key": "chat_history_answer_max_chars", "group": "context", "label": "历史回答截断字数", "type": "number", "description": "注入历史时每条助手回答的最大字符数"},
    {"key": "history_retention_limit", "group": "context", "label": "历史保留条数", "type": "number", "description": "历史对话列表最多保留多少条，超出则丢弃最旧的"},
    {"key": "chat_include_screenshot", "group": "context", "label": "对话带截图", "type": "boolean", "description": "开启后对话时仍附带当前截图；关闭则纯文本对话（推荐关闭，职责分离）"},
    {"key": "window_summary_history_limit", "group": "context", "label": "窗口摘要存档条数", "type": "number", "description": "识图摘要服务在 SQLite 中保留多少条窗口摘要快照"},
    {"key": "window_summary_retrieve_count", "group": "context", "label": "对话注入窗口摘要数", "type": "number", "description": "对话 agent 检索多少条最近窗口摘要作为背景上下文"},

    # --- 记忆系统 ---
    {"key": "memory_enabled", "group": "memory", "label": "启用记忆", "type": "boolean", "description": "关闭后不再写入或检索短期记忆"},
    {"key": "memory_max_items", "group": "memory", "label": "最大记忆条数", "type": "number", "description": "记忆列表最多保留多少条，超出丢弃最旧的"},
    {"key": "memory_retrieve_count", "group": "memory", "label": "检索注入条数", "type": "number", "description": "每次回答时检索多少条相关记忆注入提示"},
    {"key": "memory_item_max_chars", "group": "memory", "label": "单条记忆字符数", "type": "number", "description": "注入记忆时每条截断到的最大字符数"},

    # --- 性格与人设 ---
    {"key": "personality_enabled", "group": "personality", "label": "启用人设", "type": "boolean", "description": "开启后下方的名字/性格/风格将注入提示"},
    {"key": "personality_name", "group": "personality", "label": "助手名字", "type": "string", "description": "助手在对话中的自称名字"},
    {"key": "personality_traits", "group": "personality", "label": "性格描述", "type": "text", "description": "性格特征描述，如「友善、简洁、爱用比喻」"},
    {"key": "system_prompt_prefix", "group": "personality", "label": "系统提示前缀", "type": "text", "description": "高级：拼在系统提示最前面的自定义指令"},
    {"key": "answer_style_hint", "group": "personality", "label": "回答风格提示", "type": "text", "description": "如「用中文、分点回答、不超过 200 字」"},

    # --- 观察节奏 ---
    {"key": "window_watch_interval_seconds", "group": "watcher", "label": "自动观察间隔(秒)", "type": "number", "description": "自动观察循环的轮询周期"},
    {"key": "window_capture_min_interval_seconds", "group": "watcher", "label": "截图最小间隔(秒)", "type": "number", "description": "两次截图之间最小间隔，避免频繁抓屏"},
    {"key": "window_analysis_min_interval_seconds", "group": "watcher", "label": "分析最小间隔(秒)", "type": "number", "description": "两次窗口分析之间最小间隔"},

    # --- 运行时 ---
    {"key": "llama_server_host", "group": "runtime", "label": "模型服务地址", "type": "string", "description": "llama-server 监听地址（改后需重启后端）"},
    {"key": "llama_server_port", "group": "runtime", "label": "模型服务端口", "type": "number", "description": "llama-server 监听端口（改后需重启后端）"},
    {"key": "latest_analysis_ttl_seconds", "group": "runtime", "label": "分析缓存TTL(秒)", "type": "number", "description": "最新窗口分析结果的缓存有效期"},
]

GROUP_LABELS = {
    "model": "模型与调用",
    "context": "上下文窗口",
    "memory": "记忆系统",
    "personality": "性格与人设",
    "watcher": "观察节奏",
    "runtime": "运行时",
}

GROUP_ORDER = ["model", "context", "memory", "personality", "watcher", "runtime"]


def _env_key(field_key: str) -> str:
    return f"LWC_{field_key.upper()}"


def _field_default(field_key: str) -> Any:
    field_info = type(get_settings()).model_fields.get(field_key)
    if field_info is None:
        return None
    return field_info.default


def _coerce_value(value: Any, field_type: str) -> str:
    """将 Python 值转为 .env 可写的字符串。"""
    if field_type == "boolean":
        return "true" if value else "false"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _hot_reload() -> None:
    """清除 settings 及依赖它的服务单例缓存，使新配置立即生效。"""
    reload_settings()
    get_vision_model_client.cache_clear()
    get_assistant_chat_service.cache_clear()
    get_memory_service.cache_clear()


class ConfigUpdateRequest(BaseModel):
    values: dict[str, Any]


@router.get("/config")
def get_config() -> dict[str, Any]:
    """返回所有可配置字段及当前值、默认值、分组，供前端动态渲染表单。"""
    settings = get_settings()
    groups: list[dict[str, Any]] = []
    for group_key in GROUP_ORDER:
        fields: list[dict[str, Any]] = []
        for meta in FIELD_META:
            if meta["group"] != group_key:
                continue
            field_key = meta["key"]
            fields.append({
                "key": field_key,
                "label": meta["label"],
                "type": meta["type"],
                "description": meta["description"],
                "value": getattr(settings, field_key),
                "default": _field_default(field_key),
                "env_key": _env_key(field_key),
            })
        groups.append({
            "key": group_key,
            "label": GROUP_LABELS[group_key],
            "fields": fields,
        })
    return {"groups": groups}


@router.put("/config")
def update_config(payload: ConfigUpdateRequest) -> dict[str, Any]:
    """写回 .env 并热重载。只接受 FIELD_META 中已声明的字段。"""
    allowed_keys = {meta["key"]: meta for meta in FIELD_META}
    meta_by_key = {meta["key"]: meta for meta in FIELD_META}
    updated: list[str] = []
    skipped: list[str] = []

    # 确保 .env 文件存在
    if not ENV_FILE_PATH.exists():
        ENV_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE_PATH.touch()

    for field_key, raw_value in payload.values.items():
        if field_key not in allowed_keys:
            skipped.append(field_key)
            continue
        meta = meta_by_key[field_key]
        env_key = _env_key(field_key)
        env_value = _coerce_value(raw_value, meta["type"])
        set_key(str(ENV_FILE_PATH), env_key, env_value)
        updated.append(field_key)

    _hot_reload()
    return {
        "updated": updated,
        "skipped": skipped,
        "message": f"已更新 {len(updated)} 项配置并热重载",
    }


@router.post("/reload")
def reload_config() -> dict[str, Any]:
    """手动触发热重载（从 .env 重新读取）。"""
    _hot_reload()
    return {"message": "已重新加载 .env 配置"}


@router.get("/window-summaries")
def list_window_summaries(limit: int = 30) -> dict[str, Any]:
    """返回最近 N 条窗口摘要快照（识图摘要存档）。"""
    store = get_window_summary_store()
    items = store.recent(limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/window-summaries/clear")
def clear_window_summaries() -> dict[str, int]:
    cleared = get_window_summary_store().clear()
    return {"cleared": cleared}
