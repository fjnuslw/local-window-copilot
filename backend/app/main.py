from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.assistant import router as assistant_router
from app.api.routes.webui import router as webui_router
from app.api.routes.window import router as window_router
from app.core.config import get_settings
from app.services.assistant_state import get_assistant_state_service
from app.services.runtime_store import get_runtime_store
from app.services.window_watcher import get_window_watcher_service


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_runtime_store().require_ready()
    watcher = get_window_watcher_service()
    if settings.auto_start_window_watch:
        watcher.start()
    try:
        yield
    finally:
        await watcher.stop()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Local orchestration service for the window-aware desktop copilot.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assistant_router)
app.include_router(window_router)
app.include_router(webui_router)

# 挂载 webui 静态前端
WEBUI_STATIC_DIR = Path(__file__).resolve().parent / "webui" / "static"
if WEBUI_STATIC_DIR.exists():
    app.mount("/webui", StaticFiles(directory=str(WEBUI_STATIC_DIR), html=True), name="webui-static")


@app.get("/health", tags=["health"])
def health() -> dict[str, object]:
    state = get_assistant_state_service().get_state()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "assistant_state": state.state,
        "runtime_store": get_runtime_store().status(),
    }
