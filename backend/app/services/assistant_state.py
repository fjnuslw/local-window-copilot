from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

from app.core.config import get_settings
from app.schemas.assistant import AssistantState, AssistantStateResponse


VALID_STATES: set[str] = {"idle", "observing", "analyzing", "privacy", "error"}


class AssistantStateService:
    def __init__(self, bridge_path: Path) -> None:
        self.bridge_path = bridge_path
        self._state: AssistantState = "idle"
        self._reason: str | None = None
        self._updated_at = datetime.now(UTC)
        self._bridge_mtime = 0.0
        self._subscribers: set[asyncio.Queue[AssistantStateResponse]] = set()
        self._load_from_bridge()
        self._write_bridge()

    def get_state(self) -> AssistantStateResponse:
        self._load_from_bridge()
        return AssistantStateResponse(
            state=self._state,
            updated_at=self._updated_at,
            reason=self._reason,
        )

    async def set_state(
        self,
        state: AssistantState,
        *,
        reason: str | None = None,
    ) -> AssistantStateResponse:
        self._state = state
        self._reason = reason
        self._updated_at = datetime.now(UTC)
        self._write_bridge()
        response = self.get_state()
        await self._publish(response)
        return response

    async def event_stream(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[AssistantStateResponse] = asyncio.Queue(maxsize=16)
        self._subscribers.add(queue)
        try:
            yield self._format_event(self.get_state())
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield self._format_event(event)
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            self._subscribers.discard(queue)

    async def _publish(self, event: AssistantStateResponse) -> None:
        stale: list[asyncio.Queue[AssistantStateResponse]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    def _load_from_bridge(self) -> None:
        try:
            mtime = self.bridge_path.stat().st_mtime
        except OSError:
            return

        if mtime <= self._bridge_mtime:
            return

        try:
            data = json.loads(self.bridge_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        state = data.get("state")
        if state in VALID_STATES:
            self._state = state
            self._reason = data.get("reason")
            updated_at = data.get("updated_at")
            if isinstance(updated_at, str):
                try:
                    self._updated_at = datetime.fromisoformat(updated_at)
                except ValueError:
                    self._updated_at = datetime.now(UTC)
            self._bridge_mtime = mtime

    def _write_bridge(self) -> None:
        self.bridge_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": self._state,
            "updated_at": self._updated_at.isoformat(),
            "source": "fastapi",
            "reason": self._reason,
        }
        tmp_path = self.bridge_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.bridge_path)
        self._bridge_mtime = self.bridge_path.stat().st_mtime

    @staticmethod
    def _format_event(event: AssistantStateResponse) -> str:
        payload = event.model_dump_json()
        return f"event: assistant_state\ndata: {payload}\n\n"


@lru_cache
def get_assistant_state_service() -> AssistantStateService:
    settings = get_settings()
    return AssistantStateService(settings.assistant_state_bridge_path)
