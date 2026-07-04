from __future__ import annotations

from typing import Any

from dotenv import set_key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import ENV_FILE_PATH, get_settings, reload_settings
from app.services.assistant_chat import get_assistant_chat_service
from app.services.memory import get_memory_service
from app.services.model_runtime import get_model_runtime_manager
from app.services.profile_store import get_profile_store
from app.services.runtime_store import get_runtime_store
from app.services.vision_model_client import get_vision_model_client
from app.services.window_analysis import get_window_analysis_service
from app.services.window_summary_store import get_window_summary_store
from app.services.window_watcher import get_window_watcher_service


router = APIRouter(prefix="/api/webui", tags=["webui"])


# 字段元数据：key 必须与 Settings 字段名一致，env_key 自动派生为 LWC_<KEY.upper()>
# advanced: true 的字段在普通模式下隐藏，仅在「高级设置」开关打开后显示。
# type: "segmented" 为分段控件，options 形如 [{label,value}]，写入时取对应 value。
FIELD_META: list[dict[str, Any]] = [
    # --- 识别（基础页签）---
    {"key": "model_image_long_edge", "group": "vision", "label": "视觉清晰度", "type": "segmented", "advanced": False,
     "options": [{"label": "快速", "value": 768}, {"label": "标准", "value": 1024}, {"label": "细致", "value": 1344}],
     "description": "送入模型的截图长边像素。快速省显存，细致可识别更多文字细节。实际写入 LWC_MODEL_IMAGE_LONG_EDGE。"},
    {"key": "analyze_max_tokens", "group": "vision", "label": "分析详细度", "type": "segmented", "advanced": False,
     "options": [{"label": "简洁", "value": 900}, {"label": "标准", "value": 1400}, {"label": "详细", "value": 1800}],
     "description": "窗口摘要生成的最大 token 数。详细模式摘要更长。实际写入 LWC_ANALYZE_MAX_TOKENS。"},
    {"key": "auto_start_window_watch", "group": "vision", "label": "自动观察", "type": "boolean", "advanced": False,
     "description": "开启后后台自动观察前台窗口并生成摘要。"},
    {"key": "window_watch_interval_seconds", "group": "watcher", "label": "自动观察间隔(秒)", "type": "number", "advanced": True,
     "description": "自动观察循环的轮询周期。普通用户通常不需要修改。"},

    # --- 上下文（基础页签）---
    {"key": "chat_history_turns", "group": "context", "label": "历史对话轮数", "type": "number", "advanced": False,
     "description": "每次回答时注入的最近对话轮数，用于理解追问。默认 4。"},
    {"key": "window_summary_retrieve_count", "group": "context", "label": "最近窗口摘要数", "type": "number", "advanced": False,
     "description": "注入多少条最近窗口摘要作为背景。默认 3。"},
    {"key": "memory_enabled", "group": "context", "label": "记忆开关", "type": "boolean", "advanced": False,
     "description": "关闭后不再写入或检索短期记忆。默认开。"},

    # --- 高级：模型与调用 ---
    {"key": "analyze_temperature", "group": "model", "label": "分析采样温度", "type": "number", "advanced": True, "description": "窗口分析时的采样温度，越低越确定，越高越发散"},
    {"key": "answer_temperature", "group": "model", "label": "问答采样温度", "type": "number", "advanced": True, "description": "追问回答时的采样温度"},
    {"key": "answer_max_tokens", "group": "model", "label": "问答最大 token", "type": "number", "advanced": True, "description": "追问回答单次生成的最大 token 数"},
    {"key": "tool_planner_temperature", "group": "model", "label": "工具规划温度", "type": "number", "advanced": True, "description": "工具规划器采样温度，默认 0，越低越稳定"},
    {"key": "tool_planner_max_tokens", "group": "model", "label": "工具规划 token", "type": "number", "advanced": True, "description": "工具规划器输出 JSON 的最大 token 数"},
    {"key": "minicpm_ctx_size", "group": "model", "label": "模型上下文长度", "type": "number", "advanced": True, "description": "llama-server 启动时的 ctx_size（改后需重启后端生效）"},

    # --- 高级：上下文窗口 ---
    {"key": "chat_history_question_max_chars", "group": "context", "label": "历史问题截断字数", "type": "number", "advanced": True, "description": "注入历史时每条用户问题的最大字符数"},
    {"key": "chat_history_answer_max_chars", "group": "context", "label": "历史回答截断字数", "type": "number", "advanced": True, "description": "注入历史时每条助手回答的最大字符数"},
    {"key": "history_retention_limit", "group": "context", "label": "历史保留条数", "type": "number", "advanced": True, "description": "历史对话列表最多保留多少条，超出则丢弃最旧的"},
    {"key": "chat_include_screenshot", "group": "context", "label": "对话带截图", "type": "boolean", "advanced": True, "description": "开启后对话时仍附带当前截图；关闭则纯文本对话（推荐关闭，职责分离）"},
    {"key": "window_summary_history_limit", "group": "context", "label": "窗口摘要存档条数", "type": "number", "advanced": True, "description": "识图摘要服务在 SQLite 中保留多少条窗口摘要快照"},

    # --- 高级：记忆系统 ---
    {"key": "memory_max_items", "group": "memory", "label": "最大记忆条数", "type": "number", "advanced": True, "description": "记忆列表最多保留多少条，超出丢弃最旧的"},
    {"key": "memory_retrieve_count", "group": "memory", "label": "检索注入条数", "type": "number", "advanced": True, "description": "每次回答时检索多少条相关记忆注入提示"},
    {"key": "memory_item_max_chars", "group": "memory", "label": "单条记忆字符数", "type": "number", "advanced": True, "description": "注入记忆时每条截断到的最大字符数"},

    # --- 高级：性格与人设（旧字段，已被 profile md 取代，保留向后兼容）---
    {"key": "personality_enabled", "group": "personality", "label": "启用人设(旧)", "type": "boolean", "advanced": True, "description": "旧字段，已被 profile md 取代。开启后下方的名字/性格/风格将注入提示"},
    {"key": "personality_name", "group": "personality", "label": "助手名字(旧)", "type": "string", "advanced": True, "description": "旧字段。助手在对话中的自称名字"},
    {"key": "personality_traits", "group": "personality", "label": "性格描述(旧)", "type": "text", "advanced": True, "description": "旧字段。性格特征描述"},
    {"key": "system_prompt_prefix", "group": "personality", "label": "系统提示前缀(旧)", "type": "text", "advanced": True, "description": "旧字段。拼在系统提示最前面的自定义指令"},
    {"key": "answer_style_hint", "group": "personality", "label": "回答风格提示(旧)", "type": "text", "advanced": True, "description": "旧字段。如「用中文、分点回答、不超过 200 字」"},

    # --- 高级：观察节奏 ---
    {"key": "window_capture_min_interval_seconds", "group": "watcher", "label": "截图最小间隔(秒)", "type": "number", "advanced": True, "description": "两次截图之间最小间隔，避免频繁抓屏"},
    {"key": "window_analysis_min_interval_seconds", "group": "watcher", "label": "分析最小间隔(秒)", "type": "number", "advanced": True, "description": "两次窗口分析之间最小间隔"},

    # --- 高级：运行时 ---
    {"key": "llama_server_host", "group": "runtime", "label": "模型服务地址", "type": "string", "advanced": True, "description": "llama-server 监听地址（改后需重启后端）"},
    {"key": "llama_server_port", "group": "runtime", "label": "模型服务端口", "type": "number", "advanced": True, "description": "llama-server 监听端口（改后需重启后端）"},
    {"key": "latest_analysis_ttl_seconds", "group": "runtime", "label": "分析缓存TTL(秒)", "type": "number", "advanced": True, "description": "最新窗口分析结果的缓存有效期"},

    # --- 高级：调试审计 ---
    {"key": "interaction_trace_payload_max_chars", "group": "debug", "label": "交互轨迹最大字符", "type": "number", "advanced": True, "description": "单条交互轨迹 payload 的最大 JSON 字符数，超出会截断，便于调试工具规划与上下文"},
]

GROUP_LABELS = {
    "vision": "识别",
    "context": "上下文",
    "model": "模型与调用（高级）",
    "memory": "记忆系统（高级）",
    "personality": "性格与人设（高级，旧）",
    "watcher": "观察节奏（高级）",
    "runtime": "运行时（高级）",
    "debug": "调试审计（高级）",
}

# 基础页签顺序：识别、上下文（普通用户可见）；高级组在其后
GROUP_ORDER = ["vision", "context", "model", "memory", "personality", "watcher", "runtime", "debug"]


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
    get_model_runtime_manager.cache_clear()
    get_vision_model_client.cache_clear()
    get_window_summary_store.cache_clear()
    get_window_analysis_service.cache_clear()
    get_window_watcher_service.cache_clear()
    get_assistant_chat_service.cache_clear()
    get_memory_service.cache_clear()
    get_profile_store.cache_clear()


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
            field_info: dict[str, Any] = {
                "key": field_key,
                "label": meta["label"],
                "type": meta["type"],
                "description": meta["description"],
                "advanced": meta.get("advanced", False),
                "value": getattr(settings, field_key),
                "default": _field_default(field_key),
                "env_key": _env_key(field_key),
            }
            if meta["type"] == "segmented":
                field_info["options"] = meta.get("options", [])
            fields.append(field_info)
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


@router.get("/interaction-traces")
def list_interaction_traces(
    limit: int = 80,
    session_id: str | None = None,
) -> dict[str, Any]:
    """返回最近交互轨迹，用于高级调试页复盘 planner/tool/messages。"""
    events = get_runtime_store().list_events(
        names=["assistant:interaction_trace"],
        limit=limit,
    )
    if session_id:
        events = [
            event for event in events
            if isinstance(event.get("payload"), dict)
            and event["payload"].get("session_id") == session_id
        ]
    return {"items": events, "count": len(events)}


# ---------- Profile md 库 ----------

class ProfileUpdateRequest(BaseModel):
    assistant_md: str
    user_md: str


@router.get("/profile")
def get_profile() -> dict[str, str]:
    """返回当前 profile 的 md 文件内容。"""
    return get_profile_store().load()


@router.put("/profile")
def update_profile(payload: ProfileUpdateRequest) -> dict[str, Any]:
    """保存 profile md。base_prefix 不受影响，下次对话使用新 profile_packet。"""
    try:
        get_profile_store().save(
            assistant_md=payload.assistant_md,
            user_md=payload.user_md,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # profile 变化只需清除 profile_store 缓存，无需重读 .env
    get_profile_store.cache_clear()
    packet = get_profile_store().profile_packet()
    return {
        "saved": True,
        "profile_packet_chars": len(packet),
        "message": "已保存 profile，下次对话生效",
    }


@router.post("/profile/reload")
def reload_profile() -> dict[str, Any]:
    """重新读取 profile md 并清除缓存。"""
    get_profile_store.cache_clear()
    packet = get_profile_store().profile_packet()
    return {"message": "已重新加载 profile", "profile_packet_chars": len(packet)}
