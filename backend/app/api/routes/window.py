from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.schemas.window import RawWindowCapture, WindowWatchStatus
from app.services.assistant_state import get_assistant_state_service
from app.services.window_capture import WindowCaptureError, get_window_capture_service
from app.services.window_watcher import get_window_watcher_service


router = APIRouter(prefix="/api/window", tags=["window"])


@router.post("/capture", response_model=RawWindowCapture)
async def capture_window() -> RawWindowCapture:
    state_service = get_assistant_state_service()
    await state_service.set_state("observing", reason="window-capture-started")
    try:
        capture = await asyncio.to_thread(
            get_window_capture_service().capture_foreground_window
        )
    except WindowCaptureError as exc:
        await state_service.set_state("error", reason="window-capture-failed")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        await state_service.set_state("error", reason="window-capture-failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await state_service.set_state("idle", reason="window-capture-finished")
    return capture


@router.post("/watch/start", response_model=WindowWatchStatus)
async def start_window_watch() -> WindowWatchStatus:
    return get_window_watcher_service().start()


@router.post("/watch/stop", response_model=WindowWatchStatus)
async def stop_window_watch() -> WindowWatchStatus:
    return await get_window_watcher_service().stop()


@router.get("/watch/status", response_model=WindowWatchStatus)
def get_window_watch_status() -> WindowWatchStatus:
    return get_window_watcher_service().status()
