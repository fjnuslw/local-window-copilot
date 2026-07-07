from __future__ import annotations

import traceback
from datetime import UTC, datetime
from typing import Any, Literal

from app.services.runtime_store import RuntimeStore, get_runtime_store


LogLevel = Literal["debug", "info", "warning", "error"]
LOG_EVENT_NAME = "system:log"


class RuntimeLogService:
    """Structured runtime logs for debugging local orchestration.

    Logs are persisted to RuntimeStore.runtime_events and are not model memory.
    Logging must never break the main path, so write failures are swallowed.
    """

    def __init__(self, *, runtime_store: RuntimeStore) -> None:
        self.runtime_store = runtime_store

    def debug(self, component: str, action: str, message: str, **fields: Any) -> None:
        self.emit("debug", component, action, message, fields=fields)

    def info(self, component: str, action: str, message: str, **fields: Any) -> None:
        self.emit("info", component, action, message, fields=fields)

    def warning(self, component: str, action: str, message: str, **fields: Any) -> None:
        self.emit("warning", component, action, message, fields=fields)

    def error(self, component: str, action: str, message: str, **fields: Any) -> None:
        self.emit("error", component, action, message, fields=fields)

    def exception(
        self,
        component: str,
        action: str,
        message: str,
        exc: BaseException,
        **fields: Any,
    ) -> None:
        self.emit(
            "error",
            component,
            action,
            message,
            fields=fields,
            exception={
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
            },
        )

    def emit(
        self,
        level: LogLevel,
        component: str,
        action: str,
        message: str,
        *,
        fields: dict[str, Any] | None = None,
        exception: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "component": component,
            "action": action,
            "message": message,
            "fields": fields or {},
        }
        if exception is not None:
            payload["exception"] = exception
        try:
            self.runtime_store.record_event(LOG_EVENT_NAME, payload)
        except Exception:
            return

    def list(
        self,
        *,
        limit: int = 100,
        level: str | None = None,
        component: str | None = None,
    ) -> list[dict[str, Any]]:
        events = self.runtime_store.list_events(names=[LOG_EVENT_NAME], limit=limit)
        filtered: list[dict[str, Any]] = []
        for event in events:
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if level and str(payload.get("level") or "") != level:
                continue
            if component and str(payload.get("component") or "") != component:
                continue
            filtered.append(event)
        return filtered


class NullRuntimeLogService:
    def debug(self, component: str, action: str, message: str, **fields: Any) -> None:
        return None

    def info(self, component: str, action: str, message: str, **fields: Any) -> None:
        return None

    def warning(self, component: str, action: str, message: str, **fields: Any) -> None:
        return None

    def error(self, component: str, action: str, message: str, **fields: Any) -> None:
        return None

    def exception(
        self,
        component: str,
        action: str,
        message: str,
        exc: BaseException,
        **fields: Any,
    ) -> None:
        return None

    def list(
        self,
        *,
        limit: int = 100,
        level: str | None = None,
        component: str | None = None,
    ) -> list[dict[str, Any]]:
        return []


def get_runtime_log_service() -> RuntimeLogService | NullRuntimeLogService:
    try:
        return RuntimeLogService(runtime_store=get_runtime_store())
    except Exception:
        return NullRuntimeLogService()