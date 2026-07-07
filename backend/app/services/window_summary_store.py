"""窗口观察索引历史存储：每次识图分析存档，供对话 agent 检索。

与 memory.py 的区别：
- memory.py 存稳定用户记忆或用户明确要求保存的信息
- 本模块专存窗口观察快照，按时间倒序，供对话 agent 了解最近看过哪些窗口

每条记录可追溯到截图文件（screenshot_path / screenshot_hash），便于调试或未来视觉追问时回看。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.schemas.analyze import VisionInput, WindowAnalysis
from app.schemas.observation import ObservationCard
from app.schemas.window import RawWindowCapture
from app.services.runtime_store import RuntimeStore, get_runtime_store


WINDOW_SUMMARIES_KEY = "window:summaries"


class WindowSummaryRecord:
    """一条窗口观察快照（dict 结构，避免引入新存储层）。"""

    __slots__ = (
        "record_id",
        "created_at",
        "app_name",
        "window_title",
        "window_type",
        "summary",
        "key_points",
        "regions",
        "visible_text",
        "ui_elements",
        "entities",
        "uncertain_areas",
        "screenshot_path",
        "screenshot_hash",
        "window_bounds",
        "process_id",
        "analyzed_at",
        "vision_input",
    )

    def __init__(self, data: dict[str, Any]) -> None:
        self.record_id: str = data.get("record_id", "")
        self.created_at: str = data.get("created_at", "")
        self.app_name: str = data.get("app_name", "")
        self.window_title: str = data.get("window_title", "")
        self.window_type: str = data.get("window_type", "")
        self.summary: str = data.get("summary", "")
        self.key_points: list[str] = data.get("key_points", []) or []
        self.regions: list[dict[str, Any]] = data.get("regions", []) or []
        self.visible_text: list[str] = data.get("visible_text", []) or []
        self.ui_elements: list[str] = data.get("ui_elements", []) or []
        self.entities: list[str] = data.get("entities", []) or []
        self.uncertain_areas: list[str] = data.get("uncertain_areas", []) or []
        self.screenshot_path: str = data.get("screenshot_path", "")
        self.screenshot_hash: str = data.get("screenshot_hash", "")
        self.window_bounds: dict[str, int] = data.get("window_bounds", {}) or {}
        self.process_id: int | None = data.get("process_id")
        self.analyzed_at: str = data.get("analyzed_at", "")
        self.vision_input: dict[str, Any] = data.get("vision_input", {}) or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "created_at": self.created_at,
            "app_name": self.app_name,
            "window_title": self.window_title,
            "window_type": self.window_type,
            "summary": self.summary,
            "key_points": self.key_points,
            "regions": self.regions,
            "visible_text": self.visible_text,
            "ui_elements": self.ui_elements,
            "entities": self.entities,
            "uncertain_areas": self.uncertain_areas,
            "screenshot_path": self.screenshot_path,
            "screenshot_hash": self.screenshot_hash,
            "window_bounds": self.window_bounds,
            "process_id": self.process_id,
            "analyzed_at": self.analyzed_at,
            "vision_input": self.vision_input,
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
        analysis: WindowAnalysis | None = None,
        capture: RawWindowCapture | None = None,
        vision_input: VisionInput | None = None,
    ) -> dict[str, Any]:
        """记录一次窗口分析观察，同时保存截图路径与结构化视觉索引。"""
        analysis_regions = []
        visible_text: list[str] = []
        ui_elements: list[str] = []
        entities: list[str] = []
        uncertain_areas: list[str] = []
        if analysis is not None:
            summary = analysis.summary
            key_points = list(analysis.key_points)
            analysis_regions = [region.model_dump(mode="json") for region in analysis.regions]
            visible_text = list(analysis.visible_text)
            ui_elements = list(analysis.ui_elements)
            entities = list(analysis.entities)
            uncertain_areas = list(analysis.uncertain_areas)

        payload: dict[str, Any] = {
            "record_id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "app_name": observation.app_name if observation else (capture.app_name if capture else ""),
            "window_title": observation.window_title if observation else (capture.window_title if capture else ""),
            "window_type": window_type,
            "summary": summary,
            "key_points": list(key_points),
            "regions": analysis_regions,
            "visible_text": visible_text,
            "ui_elements": ui_elements,
            "entities": entities,
            "uncertain_areas": uncertain_areas,
        }
        if capture is not None:
            payload["screenshot_path"] = str(capture.screenshot_path)
            payload["screenshot_hash"] = capture.screenshot_hash
            payload["window_bounds"] = capture.window_bounds.model_dump()
            payload["process_id"] = capture.process_id
            payload["analyzed_at"] = datetime.now(UTC).isoformat()
        if vision_input is not None:
            payload["vision_input"] = vision_input.model_dump()
        items = self._load_raw()
        items.append(payload)
        items = items[-self.history_limit:]
        self.runtime_store.set_json(WINDOW_SUMMARIES_KEY, items)
        self.runtime_store.record_event("window:summary_record", payload)
        return payload

    def recent(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """返回最近 N 条窗口观察（旧→新），便于按时间顺序注入 prompt。"""
        items = self._load_raw()
        effective = self.history_limit if limit is None else max(0, min(limit, len(items)))
        recent = items[-effective:] if effective > 0 else []
        return list(recent)

    def find_by_screenshot_hash(self, screenshot_hash: str) -> dict[str, Any] | None:
        """按截图哈希查找记录，用于回看对应截图。"""
        if not screenshot_hash:
            return None
        for item in reversed(self._load_raw()):
            if item.get("screenshot_hash") == screenshot_hash:
                return item
        return None

    def find_by_record_id(self, record_id: str) -> dict[str, Any] | None:
        """按观察记录 id 查找完整结构化记录。"""
        if not record_id:
            return None
        for item in reversed(self._load_raw()):
            if item.get("record_id") == record_id:
                return item
        return None

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