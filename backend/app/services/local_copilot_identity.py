from __future__ import annotations


LOCAL_COPILOT_CLASS_NAMES = {"LocalWindowAwareFloatingAssistant"}
LOCAL_COPILOT_WINDOW_TITLES = {
    "Floating Assistant",
    "Floating Chat",
    "AlertWindow",
    "对话工作台",
}

LOCAL_COPILOT_TEXT_MARKERS = {
    *LOCAL_COPILOT_WINDOW_TITLES,
    "Local Window Copilot",
    "本地桌宠式窗口 Copilot",
}

LOCAL_RUNTIME_TITLE_MARKERS = {
    "uvicorn app.main:app",
    "scripts\\start_dev.py",
    "scripts/start_dev.py",
    "Local Window Copilot Backend",
}


def is_local_copilot_window(*, class_name: str = "", title: str = "") -> bool:
    return class_name in LOCAL_COPILOT_CLASS_NAMES or is_local_copilot_window_title(title)


def is_local_copilot_window_title(title: str | None) -> bool:
    return (title or "").strip() in LOCAL_COPILOT_WINDOW_TITLES


def is_local_copilot_title(title: str | None) -> bool:
    return is_local_copilot_window_title(title) or is_local_runtime_title(title)


def is_local_runtime_title(title: str | None) -> bool:
    value = (title or "").strip()
    return any(
        marker and marker in value
        for marker in LOCAL_COPILOT_TEXT_MARKERS | LOCAL_RUNTIME_TITLE_MARKERS
    )


def mentions_local_copilot(text: str | None) -> bool:
    value = text or ""
    return any(marker and marker in value for marker in LOCAL_COPILOT_TEXT_MARKERS)
