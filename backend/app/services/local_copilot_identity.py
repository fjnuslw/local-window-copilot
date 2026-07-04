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


def is_local_copilot_window(*, class_name: str = "", title: str = "") -> bool:
    return class_name in LOCAL_COPILOT_CLASS_NAMES or is_local_copilot_title(title)


def is_local_copilot_title(title: str | None) -> bool:
    return (title or "").strip() in LOCAL_COPILOT_WINDOW_TITLES


def mentions_local_copilot(text: str | None) -> bool:
    value = text or ""
    return any(marker and marker in value for marker in LOCAL_COPILOT_TEXT_MARKERS)
