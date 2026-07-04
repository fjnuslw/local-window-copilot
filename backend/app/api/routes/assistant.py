from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.assistant import AssistantStateResponse, AssistantStateUpdate
from app.schemas.analyze import WindowAnalysisResult
from app.schemas.chat import ChatHistoryResponse, ChatQuestionRequest, ChatSession
from app.services.assistant_chat import get_assistant_chat_service
from app.services.assistant_state import get_assistant_state_service
from app.services.window_analysis import get_window_analysis_service
from app.services.window_watcher import get_window_watcher_service


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


@router.get("/latest", response_model=WindowAnalysisResult | None)
def latest_analysis() -> WindowAnalysisResult | None:
    return get_window_analysis_service().get_latest()


@router.post("/questions", response_model=ChatSession)
async def ask_question(payload: ChatQuestionRequest) -> ChatSession:
    try:
        return await get_assistant_chat_service().ask(
            payload.question,
            image_base64=payload.image_base64,
            image_name=payload.image_name,
            image_mime=payload.image_mime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conversation", response_model=ChatSession | None)
def current_conversation() -> ChatSession | None:
    return get_assistant_chat_service().current()


@router.get("/conversations", response_model=ChatHistoryResponse)
def conversation_history() -> ChatHistoryResponse:
    return ChatHistoryResponse(items=get_assistant_chat_service().history())


@router.post("/conversations/clear")
def clear_conversations() -> dict[str, int]:
    cleared = get_assistant_chat_service().clear_history()
    return {"cleared": cleared}


@router.post("/context-preview")
def context_preview(payload: ChatQuestionRequest) -> dict[str, object]:
    return get_assistant_chat_service().inspect_context(payload.question)


@router.get("/context-status")
def context_status() -> dict[str, object]:
    return get_assistant_chat_service().context_status()


@router.post("/resume")
async def resume_auto_watch() -> dict[str, bool]:
    await get_assistant_chat_service().resume_auto_watch()
    return {"running": True}


@router.post("/pause")
async def pause_auto_watch() -> dict[str, bool]:
    await get_window_watcher_service().pause(reason="chat-workbench-opened")
    return {"running": False}


@router.post("/observe")
async def observe_once() -> dict[str, bool]:
    get_window_watcher_service().request_observe_once(resume_after=True)
    return {"started": True, "resume_after": True}
