from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE_PATH = PROJECT_ROOT / "backend" / ".env"


class Settings(BaseSettings):
    app_name: str = "Local Window Copilot Backend"
    app_version: str = "0.1.0"
    backend_host: str = "127.0.0.1"
    backend_port: int = 18081
    window_capture_dir: Path = PROJECT_ROOT / "backend" / "data" / "captures"
    chat_upload_dir: Path = PROJECT_ROOT / "backend" / "data" / "chat_uploads"
    # 截图轮转：capture_dir 内只保留最近 N 张 PNG，超过则异步清理最旧文件。
    window_capture_max_files: int = 200
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
    minicpm_ctx_size: int = 256000
    minicpm_reasoning: str = "off"
    minicpm_reasoning_format: str = "none"
    minicpm_reasoning_budget: int = 0
    llama_server_host: str = "127.0.0.1"
    llama_server_port: int = 18181
    llama_chat_completions_path: str = "/v1/chat/completions"
    llama_startup_timeout_seconds: float = 180.0
    llama_request_timeout_seconds: float = 600.0
    model_image_long_edge: int = 4096
    model_image_max_pixels: int = 1_800_000
    runtime_store_path: Path = PROJECT_ROOT / "backend" / "data" / "runtime" / "runtime.sqlite3"
    window_analysis_prompt_path: Path = (
        PROJECT_ROOT / "experiments" / "prompts" / "analyze_window_v2.txt"
    )
    latest_analysis_ttl_seconds: int = 86400
    cors_origins: list[str] = [
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]

    # --- 模型调用参数（原硬编码于 vision_model_client.py）---
    analyze_temperature: float = 0.0
    analyze_max_tokens: int = 8192
    answer_temperature: float = 0.2
    answer_max_tokens: int = 32768

    # --- 上下文窗口（原硬编码于 assistant_chat.py / vision_model_client.py）---
    chat_history_turns: int = 4
    history_retention_limit: int = 30
    chat_include_screenshot: bool = False
    tool_result_budget_tokens: int = 8000
    tool_result_item_budget_tokens: int = 3000

    # --- Compact 上下文管理 ---
    compact_enabled: bool = True
    compact_auto_enabled: bool = True
    compact_raw_tail_turns: int = 2
    compact_batch_session_limit: int = 12
    compact_source_budget_tokens: int = 18000
    compact_uncovered_session_threshold: int = 6
    compact_history_trigger_tokens: int = 24000
    compact_model_max_input_tokens: int = 24000
    compact_model_max_output_tokens: int = 1600
    compact_template_budget_tokens: int = 2000
    compact_previous_summary_budget_tokens: int = 2000
    compact_target_summary_tokens: int = 1200
    compact_timeout_seconds: int = 90

    # --- 窗口观察历史（识图观察存档，供对话 agent 检索）---
    window_summary_history_limit: int = 30
    window_summary_retrieve_count: int = 3

    # --- 记忆系统（原硬编码于 memory.py / assistant_chat.py）---
    memory_enabled: bool = True
    memory_max_items: int = 40
    memory_retrieve_count: int = 3

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
