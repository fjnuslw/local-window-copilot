"""窗口摘要历史存储：每次识图分析存档，供对话 agent 检索。

与 memory.py 的区别：
- memory.py 存"记忆条目"（观察/分析摘要/问答），基于关键字检索，作用域 session
- 本模块专存"窗口摘要快照"，按时间倒序，供对话 agent 了解最近看过哪些窗口
"""
from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.schemas.observation import ObservationCard
from app.services.runtime_store import RuntimeStore, get_runtime_store


WINDOW_SUMMARIES_KEY = "window:summaries"


class WindowSummaryRecord:
    """一条窗口摘要快照（dict 结构，避免引入新 schema）。"""

    __slots__ = ("record_id", "created_at", "app_name", "window_title",
                 "window_type", "summary", "key_points")

    def __init__(self, data: dict[str, Any]) -> None:
        self.record_id: str = data.get("record_id", "")
        self.created_at: str = data.get("created_at", "")
        self.app_name: str = data.get("app_name", "")
        self.window_title: str = data.get("window_title", "")
        self.window_type: str = data.get("window_type", "")
        self.summary: str = data.get("summary", "")
        self.key_points: list[str] = data.get("key_points", []) or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "created_at": self.created_at,
            "app_name": self.app_name,
            "window_title": self.window_title,
            "window_type": self.window_type,
            "summary": self.summary,
            "key_points": self.key_points,
        }


class WindowSummaryStore:
    def __init__(self, *, runtime_store: RuntimeStore, history_limit: int = 30) -> None:
        self.runtime_store = runtime_store
        self.history_limit = history_limit

    def record(
        self,
        *,
        observation: ObservationCard | None,
        window_type: str,
        summary: str,
        key_points: list[str],
    ) -> dict[str, Any]:
        """记录一次窗口分析摘要。"""
        import uuid
        payload = {
            "record_id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "app_name": observation.app_name if observation else "",
            "window_title": observation.window_title if observation else "",
            "window_type": window_type,
            "summary": summary,
            "key_points": list(key_points),
        }
        items = self._load_raw()
        items.append(payload)
        items = items[-self.history_limit:]
        self.runtime_store.set_json(WINDOW_SUMMARIES_KEY, items)
        self.runtime_store.record_event("window:summary_record", payload)
        return payload

    def recent(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """返回最近 N 条窗口摘要（新的在前）。"""
        items = self._load_raw()
        effective = self.history_limit if limit is None else max(0, min(limit, len(items)))
        # 倒序取最近 N 条，再翻转为时间正序（旧→新），便于注入 prompt
        recent = items[-effective:] if effective > 0 else []
        return list(recent)

    def clear(self) -> int:
        items = self._load_raw()
        count = len(items)
        self.runtime_store.delete(WINDOW_SUMMARIES_KEY)
        return count

    def _load_raw(self) -> list[dict[str, Any]]:
        data = self.runtime_store.get_json(WINDOW_SUMMARIES_KEY)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]


@lru_cache
def get_window_summary_store() -> WindowSummaryStore:
    return WindowSummaryStore(
        runtime_store=get_runtime_store(),
        history_limit=get_settings().window_summary_history_limit,
    )
