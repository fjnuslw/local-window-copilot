from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.assistant import router as assistant_router
from app.core.config import get_settings
from app.services.assistant_state import get_assistant_state_service


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Local orchestration service for the window-aware desktop copilot.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assistant_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, object]:
    state = get_assistant_state_service().get_state()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "assistant_state": state.state,
    }
