from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas.chat import ChatSession
from app.schemas.memory import MemoryItem
from app.services.vision_model_client import BASE_PREFIX, build_chat_messages, build_context_packet
from app.services.profile_store import (
    LEGACY_DEFAULT_ASSISTANT_MD,
    LEGACY_DEFAULT_USER_MD,
    ProfileStore,
)


def test_base_prefix_stable_across_window_changes() -> None:
    """窗口观察变化时 messages[0] (base_prefix) 完全不变。"""
    msgs_a = build_chat_messages(
        question="q1",
        profile_packet="p",
        current_summary="窗口A",
        current_key_points=["a"],
    )
    msgs_b = build_chat_messages(
        question="q2",
        profile_packet="p",
        current_summary="窗口B完全不同",
        current_key_points=["x", "y", "z"],
    )
    assert msgs_a[0]["role"] == "system"
    assert msgs_a[0]["content"] == BASE_PREFIX
    assert msgs_a[0]["content"] == msgs_b[0]["content"]


def test_base_prefix_stable_across_profile_changes() -> None:
    """profile 变化时 messages[0] 不变。"""
    msgs_a = build_chat_messages(
        question="q", profile_packet="画像A", current_summary="s", current_key_points=[]
    )
    msgs_b = build_chat_messages(
        question="q", profile_packet="画像B完全不同", current_summary="s", current_key_points=[]
    )
    assert msgs_a[0]["content"] == msgs_b[0]["content"]


def test_base_prefix_stable_across_memory_changes() -> None:
    """记忆条目变化时 messages[0] 不变。"""
    msgs_a = build_chat_messages(
        question="q",
        profile_packet="p",
        current_summary="s",
        current_key_points=[],
        memory_items=[MemoryItem(scope="working", kind="observation", text="记忆A")],
    )
    msgs_b = build_chat_messages(
        question="q",
        profile_packet="p",
        current_summary="s",
        current_key_points=[],
        memory_items=[MemoryItem(scope="working", kind="observation", text="记忆B"), MemoryItem(scope="working", kind="observation", text="记忆C")],
    )
    assert msgs_a[0]["content"] == msgs_b[0]["content"]


def test_layered_structure() -> None:
    """验证 base_prefix + profile_packet + context_packet + dialogue_tail 分层。"""
    msgs = build_chat_messages(
        question="当前问题",
        profile_packet="# 助手画像",
        current_summary="窗口观察",
        current_key_points=["点1"],
    )
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == BASE_PREFIX
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "# 助手画像"
    assert msgs[2]["role"] == "user"
    assert "窗口观察" in msgs[2]["content"]
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "当前问题"


def test_empty_profile_skipped() -> None:
    """空 profile_packet 不产生 message。"""
    msgs = build_chat_messages(
        question="q", profile_packet="", current_summary="s", current_key_points=[]
    )
    # system + context + question = 3
    assert len(msgs) == 3


def test_empty_context_skipped() -> None:
    """空 context_packet 不产生 message。"""
    msgs = build_chat_messages(
        question="q", profile_packet="p", current_summary=None, current_key_points=[]
    )
    # system + profile + question = 3
    assert len(msgs) == 3


def test_context_packet_contains_all_sections() -> None:
    """context_packet 包含当前观察、历史窗口观察、记忆三部分。"""
    packet = build_context_packet(
        current_summary="当前窗口是IDE",
        current_key_points=["文件A", "文件B"],
        history_window_summaries=[
            {"created_at": "2026-07-03T10:00:00Z", "app_name": "Chrome", "window_title": "搜索", "window_type": "webpage", "summary": "搜索结果"},
        ],
        memory_items=[MemoryItem(scope="working", kind="observation", text="用户常用VSCode")],
    )
    assert "当前窗口是IDE" in packet
    assert "Chrome" in packet
    assert "用户常用VSCode" in packet


def test_context_packet_puts_current_window_metadata_first() -> None:
    packet = build_context_packet(
        current_app_name="chrome.exe",
        current_window_title="KV Cache Profile/Agent split - Codex",
        current_window_type="webpage",
        current_summary="网页显示一份项目文档。",
        current_key_points=["文档", "项目"],
    )

    assert packet.startswith("当前窗口元信息")
    assert "- 应用：chrome.exe" in packet
    assert "- 标题：KV Cache Profile/Agent split - Codex" in packet
    assert packet.index("当前窗口元信息") < packet.index("当前窗口观察")


def test_context_packet_filters_local_copilot_pollution() -> None:
    packet = build_context_packet(
        current_app_name="chrome.exe",
        current_window_title="KV Cache Profile/Agent split - Codex",
        current_window_type="webpage",
        current_summary="当前窗口是 Codex 文档页。",
        current_key_points=[],
        history_window_summaries=[
            {
                "created_at": "2026-07-03T10:00:00Z",
                "app_name": "python.exe",
                "window_title": "AlertWindow",
                "window_type": "chat",
                "summary": "当前窗口是对话工作台。",
            },
            {
                "created_at": "2026-07-03T10:01:00Z",
                "app_name": "Chrome",
                "window_title": "搜索",
                "window_type": "webpage",
                "summary": "搜索结果",
            },
        ],
        memory_items=[
            MemoryItem(scope="session", kind="assistant_answer", text="当前窗口是对话工作台。"),
            MemoryItem(scope="session", kind="observation", text="用户正在阅读 Codex 文档。"),
        ],
    )

    assert "AlertWindow" not in packet
    assert "对话工作台" not in packet
    assert "搜索结果" in packet
    assert "用户正在阅读 Codex 文档" in packet


def test_chat_messages_filter_local_copilot_dialogue_tail() -> None:
    now = datetime.now(UTC)
    messages = build_chat_messages(
        question="当前这个窗口的名字是什么？",
        profile_packet="p",
        current_app_name="chrome.exe",
        current_window_title="KV Cache Profile/Agent split - Codex",
        current_window_type="webpage",
        current_summary="当前窗口是 Codex 文档页。",
        current_key_points=[],
        chat_history=[
            ChatSession(
                session_id="polluted",
                question="当前窗口叫什么？",
                answer="当前窗口的名字是对话工作台。",
                status="done",
                created_at=now,
                updated_at=now,
            ),
            ChatSession(
                session_id="clean",
                question="这个页面在讲什么？",
                answer="它在讲 KV cache 与 profile 分层。",
                status="done",
                created_at=now,
                updated_at=now,
            ),
        ],
    )
    joined = "\n".join(str(message["content"]) for message in messages)

    assert "对话工作台" not in joined
    assert "KV Cache Profile/Agent split - Codex" in joined
    assert "它在讲 KV cache 与 profile 分层" in joined


def test_profile_packet_does_not_duplicate_default_headings() -> None:
    store = ProfileStore(profile_root=Path("__missing_profiles__"))

    packet = store.profile_packet()

    assert packet.count("# 助手画像") == 1
    assert packet.count("# 用户偏好") == 1


def test_profile_defaults_include_tool_and_memory_contracts(tmp_path: Path) -> None:
    store = ProfileStore(profile_root=tmp_path / "profiles")

    data = store.load()
    packet = store.profile_packet()

    assert "## 工具边界" in data["assistant_md"]
    assert "screen.look" in packet
    assert "memory.search" in packet
    assert "## 记忆原则" in data["assistant_md"]
    assert "不引入 Redis、PostgreSQL、Docker" in data["user_md"]


def test_profile_load_upgrades_legacy_defaults(tmp_path: Path) -> None:
    store = ProfileStore(profile_root=tmp_path / "profiles")
    store.profile_dir.mkdir(parents=True)
    (store.profile_dir / "ASSISTANT.md").write_text(
        LEGACY_DEFAULT_ASSISTANT_MD,
        encoding="utf-8",
    )
    (store.profile_dir / "USER.md").write_text(
        LEGACY_DEFAULT_USER_MD,
        encoding="utf-8",
    )

    data = store.load()

    assert "## 工具边界" in data["assistant_md"]
    assert "## 当前项目偏好" in data["user_md"]


def test_profile_load_keeps_custom_files(tmp_path: Path) -> None:
    store = ProfileStore(profile_root=tmp_path / "profiles")
    store.profile_dir.mkdir(parents=True)
    custom_assistant = "# 助手画像\n\n自定义助手。"
    custom_user = "# 用户偏好\n\n自定义用户偏好。"
    (store.profile_dir / "ASSISTANT.md").write_text(
        custom_assistant,
        encoding="utf-8",
    )
    (store.profile_dir / "USER.md").write_text(
        custom_user,
        encoding="utf-8",
    )

    data = store.load()

    assert data["assistant_md"] == custom_assistant
    assert data["user_md"] == custom_user
