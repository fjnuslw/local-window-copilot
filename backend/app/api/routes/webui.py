from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import ENV_FILE_PATH, PROJECT_ROOT, get_settings, reload_settings
from app.services.assistant_chat import get_assistant_chat_service
from app.services.memory import get_memory_service
from app.services.model_runtime import get_model_runtime_manager
from app.services.profile_store import get_profile_store
from app.services.runtime_log import LOG_EVENT_NAME, get_runtime_log_service
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
    {"key": "auto_start_window_watch", "group": "vision", "label": "自动观察", "type": "boolean", "advanced": False,
     "description": "开启后后台自动观察前台窗口并生成观察。"},
    {"key": "window_watch_interval_seconds", "group": "watcher", "label": "自动观察间隔(秒)", "type": "number", "advanced": True,
     "description": "自动观察循环的轮询周期。普通用户通常不需要修改。"},

    # --- 上下文（基础页签）---
    {"key": "chat_history_turns", "group": "context", "label": "历史对话轮数", "type": "number", "advanced": False,
     "description": "每次回答时注入的最近对话轮数，用于理解追问。默认 4。"},
    {"key": "window_summary_retrieve_count", "group": "context", "label": "最近窗口观察数", "type": "number", "advanced": False,
     "description": "工具检索时可读取的最近窗口观察候选数量提示；观察不会默认注入对话。"},
    {"key": "memory_enabled", "group": "context", "label": "记忆开关", "type": "boolean", "advanced": False,
     "description": "关闭后不再写入或检索短期记忆。默认开。"},

    # --- 高级：模型与调用 ---
    {"key": "analyze_temperature", "group": "model", "label": "分析采样温度", "type": "number", "advanced": True, "description": "窗口分析时的采样温度，越低越确定，越高越发散"},
    {"key": "answer_temperature", "group": "model", "label": "问答采样温度", "type": "number", "advanced": True, "description": "追问回答时的采样温度"},
    {"key": "minicpm_reasoning", "group": "model", "label": "模型思考模式", "type": "segmented", "advanced": True, "options": [{"label": "关闭（推荐）", "value": "off"}, {"label": "自动", "value": "auto"}, {"label": "开启", "value": "on"}], "description": "llama-server reasoning 模式。观察 JSON 链路建议关闭；改后需重启后端和模型服务。"},
    {"key": "minicpm_reasoning_format", "group": "model", "label": "思考输出格式", "type": "segmented", "advanced": True, "options": [{"label": "none（推荐）", "value": "none"}, {"label": "deepseek", "value": "deepseek"}, {"label": "legacy", "value": "deepseek-legacy"}], "description": "reasoning 关闭时使用 none；避免 reasoning_content 或 <think> 污染观察 JSON。"},

    # --- 高级：上下文窗口 ---
    {"key": "history_retention_limit", "group": "context", "label": "历史保留条数", "type": "number", "advanced": True, "description": "历史对话列表最多保留多少条，超出则丢弃最旧的"},
    {"key": "chat_include_screenshot", "group": "context", "label": "对话带截图", "type": "boolean", "advanced": True, "description": "开启后对话时仍附带当前截图；关闭则纯文本对话（推荐关闭，职责分离）"},
    {"key": "window_summary_history_limit", "group": "context", "label": "窗口观察存档条数", "type": "number", "advanced": True, "description": "识图观察服务在 SQLite 中保留多少条窗口观察快照"},

    # --- 高级：记忆系统 ---
    {"key": "memory_max_items", "group": "memory", "label": "最大记忆条数", "type": "number", "advanced": True, "description": "记忆列表最多保留多少条，超出丢弃最旧的"},
    {"key": "memory_retrieve_count", "group": "memory", "label": "检索注入条数", "type": "number", "advanced": True, "description": "每次回答时检索多少条相关记忆注入提示"},

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


class RuntimeJsonDeleteRequest(BaseModel):
    names: list[str]


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
    """返回最近 N 条窗口观察快照（识图观察存档）。"""
    store = get_window_summary_store()
    items = store.recent(limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/window-summaries/clear")
def clear_window_summaries() -> dict[str, int]:
    cleared = get_window_summary_store().clear()
    return {"cleared": cleared}


@router.get("/runtime-store/json")
def list_runtime_json(limit: int = 200, prefix: str | None = None) -> dict[str, Any]:
    """查看 RuntimeStore.runtime_json 中真实保存的 key/value。"""
    store = get_runtime_store()
    items = store.list_json(limit=max(1, min(limit, 1000)), prefix=prefix or None)
    return {
        "items": items,
        "count": len(items),
        "status": store.status(),
    }


@router.post("/runtime-store/json/delete")
def delete_runtime_json(payload: RuntimeJsonDeleteRequest) -> dict[str, Any]:
    """按 key 删除 RuntimeStore.runtime_json 项。"""
    deleted = get_runtime_store().delete_many_json(payload.names)
    return {"deleted": deleted, "requested": payload.names}


@router.post("/observations/clear")
def clear_observations() -> dict[str, int]:
    """清空窗口观察历史，并删除 latest_analysis。截图文件保留用于人工排查。"""
    summaries = get_window_summary_store().clear()
    get_runtime_store().delete("window:latest_analysis")
    return {"summaries_cleared": summaries, "latest_deleted": 1}


@router.post("/memory/clear")
def clear_memory_items() -> dict[str, int]:
    """清空 memory:items（长期记忆条目）。working observation 不受影响。"""
    cleared = get_memory_service().clear_items()
    return {"cleared": cleared}


@router.post("/runtime-events/clear")
def clear_all_runtime_events() -> dict[str, int]:
    """清空 runtime_events 全表（含 interaction_trace、system:log 等所有事件）。"""
    deleted = get_runtime_store().clear_events()
    return {"deleted": deleted}


@router.post("/reset-all")
def reset_all() -> dict[str, Any]:
    """一键全清：对话+观察+记忆+日志+trace+FTS5。截图文件保留。

    原子操作：按顺序执行，任一步失败则停止并返回已完成的部分。
    """
    results: dict[str, Any] = {}
    store = get_runtime_store()

    # 1. 对话历史
    results["conversations"] = get_assistant_chat_service().clear_history()
    store.delete("assistant:chat:current")

    # 2. 窗口观察
    results["window_summaries"] = get_window_summary_store().clear()
    store.delete("window:latest_analysis")

    # 3. 记忆
    results["memory_items"] = get_memory_service().clear_items()
    store.delete("memory:working:observation")

    # 4. 事件全表（日志 + trace）
    results["runtime_events"] = store.clear_events()

    # 5. chat_history FTS5（如果存在）
    try:
        from app.services.chat_history_index import get_chat_history_index
        results["chat_history_fts"] = get_chat_history_index().clear()
    except Exception:
        results["chat_history_fts"] = 0

    return {"cleared": results}

def _observation_image_path(record: dict[str, Any]) -> Path:
    raw_path = str(record.get("screenshot_path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=404, detail="Observation has no screenshot_path.")
    target = Path(raw_path)
    if not target.is_absolute():
        project_relative = (PROJECT_ROOT / target).resolve()
        cwd_relative = (Path.cwd() / target).resolve()
        target = project_relative if project_relative.exists() else cwd_relative
    else:
        target = target.resolve()
    capture_root = get_settings().window_capture_dir.resolve()
    if target != capture_root and capture_root not in target.parents:
        raise HTTPException(status_code=403, detail="Screenshot path is outside capture directory.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Screenshot file does not exist.")
    return target


@router.get("/observations/latest")
def latest_observation() -> dict[str, Any]:
    latest = get_window_analysis_service().get_latest()
    if latest is None:
        return {"latest": None, "record": None}
    record = get_window_summary_store().find_by_screenshot_hash(latest.capture.screenshot_hash)
    return {
        "latest": latest.model_dump(mode="json"),
        "record": record,
    }


@router.get("/observations")
def list_observations(limit: int = 30) -> dict[str, Any]:
    store = get_window_summary_store()
    items = store.recent(limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/observations/{record_id}")
def get_observation(record_id: str) -> dict[str, Any]:
    record = get_window_summary_store().find_by_record_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Observation record not found.")
    return {"record": record}


@router.get("/observations/{record_id}/image")
def get_observation_image(record_id: str) -> FileResponse:
    record = get_window_summary_store().find_by_record_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Observation record not found.")
    return FileResponse(_observation_image_path(record))


@router.get("/tool-traces")
def list_tool_traces(limit: int = 30) -> dict[str, Any]:
    events = get_runtime_store().list_events(
        names=["assistant:interaction_trace"],
        limit=max(1, limit * 4),
    )
    tool_events = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        stage = str(payload.get("stage") or "")
        if stage in {"tool_calls", "tool_results"}:
            tool_events.append(event)
        if len(tool_events) >= limit:
            break
    return {"items": tool_events, "count": len(tool_events)}



def _compact_runtime_log_value(value: Any, *, max_text: int = 2400) -> Any:
    if isinstance(value, str):
        if len(value) <= max_text:
            return value
        return {
            "preview": value[:max_text],
            "truncated_chars": len(value) - max_text,
        }
    if isinstance(value, list):
        visible = value[:80]
        compacted = [_compact_runtime_log_value(item, max_text=max_text) for item in visible]
        if len(value) > len(visible):
            compacted.append({"truncated_items": len(value) - len(visible)})
        return compacted
    if isinstance(value, dict):
        return {
            str(key): _compact_runtime_log_value(item, max_text=max_text)
            for key, item in value.items()
        }
    return value


def _compact_runtime_log_event(event: dict[str, Any]) -> dict[str, Any]:
    return _compact_runtime_log_value(event)


@router.get("/runtime-logs")
def list_runtime_logs(
    limit: int = 100,
    level: str | None = None,
    component: str | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """返回结构化运行日志，用于定位截图/VLM/写库/工具调用失败。"""
    items = get_runtime_log_service().list(
        limit=max(1, min(limit, 500)),
        level=level,
        component=component,
    )
    if not full:
        items = [_compact_runtime_log_event(item) for item in items]
    return {"items": items, "count": len(items), "full": full}


@router.delete("/runtime-events/{event_id}")
def delete_runtime_event(event_id: int) -> dict[str, Any]:
    deleted = get_runtime_store().delete_event(event_id)
    return {"deleted": deleted, "event_id": event_id}


@router.post("/runtime-logs/clear")
def clear_runtime_logs() -> dict[str, int]:
    deleted = get_runtime_store().clear_events(names=[LOG_EVENT_NAME])
    return {"deleted": deleted}

@router.get("/interaction-traces")
def list_interaction_traces(
    limit: int = 80,
    session_id: str | None = None,
) -> dict[str, Any]:
    """返回最近交互轨迹，用于高级调试页复盘 context/messages。"""
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
