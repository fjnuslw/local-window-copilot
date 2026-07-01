from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Local Window Copilot Backend"
    app_version: str = "0.1.0"
    assistant_state_bridge_path: Path = (
        PROJECT_ROOT / "apps" / "desktop-floating-window" / "state_bridge.json"
    )
    cors_origins: list[str] = [
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LWC_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
