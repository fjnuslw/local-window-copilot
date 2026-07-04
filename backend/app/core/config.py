from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE_PATH = PROJECT_ROOT / "backend" / ".env"


class Settings(BaseSettings):
    app_name: str = "Local Window Copilot Backend"
    app_version: str = "0.1.0"
    window_capture_dir: Path = PROJECT_ROOT / "backend" / "data" / "captures"
    chat_upload_dir: Path = PROJECT_ROOT / "backend" / "data" / "chat_uploads"
    chat_image_max_bytes: int = 8_000_000
    auto_start_window_watch: bool = True
    window_watch_interval_seconds: float = 1.0
    window_capture_min_interval_seconds: float = 2.0
    window_analysis_min_interval_seconds: float = 6.0
    llama_server_path: Path = PROJECT_ROOT / "runtime" / "llama.cpp" / "llama-server.exe"
    minicpm_model_path: Path = (
        PROJECT_ROOT / "runtime" / "models" / "minicpm-v4.6" / "MiniCPM-V-4_6-F16.gguf"
    )
    minicpm_mmproj_path: Path = (
        PROJECT_ROOT / "runtime" / "models" / "minicpm-v4.6" / "mmproj-model-f16.gguf"
    )
    minicpm_model_name: str = "minicpm-v4.6-f16"
    minicpm_ctx_size: int = 8192
    minicpm_reasoning: str = "on"
    minicpm_reasoning_format: str = "deepseek"
    minicpm_reasoning_budget: int = 512
    llama_server_host: str = "127.0.0.1"
    llama_server_port: int = 18181
    llama_chat_completions_path: str = "/v1/chat/completions"
    llama_startup_timeout_seconds: float = 45.0
    llama_request_timeout_seconds: float = 120.0
    model_image_long_edge: int = 1536
    visual_answer_image_long_edge: int = 1536
    runtime_store_path: Path = PROJECT_ROOT / "backend" / "data" / "runtime" / "runtime.sqlite3"
    window_analysis_prompt_path: Path = (
        PROJECT_ROOT / "experiments" / "prompts" / "analyze_window_v2.txt"
    )
    visual_question_answer_prompt_path: Path = (
        PROJECT_ROOT / "experiments" / "prompts" / "visual_question_answer_v1.txt"
    )
    companion_chat_prompt_path: Path = (
        PROJECT_ROOT / "experiments" / "prompts" / "companion_chat_v1.txt"
    )
    latest_analysis_ttl_seconds: int = 86400
    cors_origins: list[str] = [
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]

    # --- 模型调用参数（原硬编码于 vision_model_client.py）---
    analyze_temperature: float = 0.1
    analyze_max_tokens: int = 3200
    answer_temperature: float = 0.2
    answer_max_tokens: int = 1200
    tool_planner_temperature: float = 0.0
    tool_planner_max_tokens: int = 500
    agent_tool_call_limit: int = 3
    interaction_trace_payload_max_chars: int = 20000

    # --- 上下文窗口（原硬编码于 assistant_chat.py / vision_model_client.py）---
    chat_history_turns: int = 4
    chat_history_question_max_chars: int = 500
    chat_history_answer_max_chars: int = 800
    history_retention_limit: int = 30
    chat_include_screenshot: bool = False

    # --- 窗口观察历史（识图观察存档，供对话 agent 检索）---
    window_summary_history_limit: int = 30
    window_summary_retrieve_count: int = 3

    # --- 记忆系统（原硬编码于 memory.py / assistant_chat.py）---
    memory_enabled: bool = True
    memory_max_items: int = 40
    memory_retrieve_count: int = 3
    memory_item_max_chars: int = 220

    # --- 性格与人设（新增，用于上下文管理）---
    personality_enabled: bool = False
    personality_name: str = ""
    personality_traits: str = ""
    system_prompt_prefix: str = ""
    answer_style_hint: str = ""

    @property
    def llama_chat_completions_endpoint(self) -> str:
        return (
            f"http://{self.llama_server_host}:{self.llama_server_port}"
            f"{self.llama_chat_completions_path}"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LWC_",
        extra="ignore",
        protected_namespaces=("settings_",),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """清除 settings 缓存并重新读取 .env。

    注意：依赖 settings 的服务单例（vision_model_client / assistant_chat /
    memory）各自也有 lru_cache，调用方需一并清除它们的缓存才能真正生效。
    """
    get_settings.cache_clear()
    return get_settings()
