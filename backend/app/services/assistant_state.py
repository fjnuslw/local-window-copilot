from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import lru_cache
from typing import AsyncIterator

from app.schemas.assistant import AssistantState, AssistantStateResponse
from app.services.runtime_store import RuntimeStore, get_runtime_store


VALID_STATES: set[str] = {"idle", "observing", "analyzing", "privacy", "error"}


class AssistantStateService:
    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
    ) -> None:
        self.runtime_store = runtime_store
        self._state: AssistantState = "idle"
        self._reason: str | None = None
        self._updated_at = datetime.now(UTC)
        self._subscribers: set[asyncio.Queue[AssistantStateResponse]] = set()
        self._load_from_store()
        self._write_state()

    def get_state(self) -> AssistantStateResponse:
        self._load_from_store()
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
        self._write_state()
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

    def _load_from_store(self) -> None:
        data = self.runtime_store.get_json("assistant:state")
        if not isinstance(data, dict):
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

    def _write_state(self) -> None:
        payload = {
            "state": self._state,
            "updated_at": self._updated_at.isoformat(),
            "source": "fastapi",
            "reason": self._reason,
        }
        self.runtime_store.set_json("assistant:state", payload)
        self.runtime_store.record_event("assistant:state", payload)

    @staticmethod
    def _format_event(event: AssistantStateResponse) -> str:
        payload = event.model_dump_json()
        return f"event: assistant_state\ndata: {payload}\n\n"


@lru_cache
def get_assistant_state_service() -> AssistantStateService:
    return AssistantStateService(runtime_store=get_runtime_store())
