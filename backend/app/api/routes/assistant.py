from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.schemas.assistant import AssistantStateResponse, AssistantStateUpdate
from app.services.assistant_state import get_assistant_state_service


router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.get("/state", response_model=AssistantStateResponse)
def get_state() -> AssistantStateResponse:
    return get_assistant_state_service().get_state()


@router.post("/state", response_model=AssistantStateResponse)
async def set_state(payload: AssistantStateUpdate) -> AssistantStateResponse:
    return await get_assistant_state_service().set_state(
        payload.state,
        reason=payload.reason,
    )


@router.get("/events")
async def events() -> StreamingResponse:
    return StreamingResponse(
        get_assistant_state_service().event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
