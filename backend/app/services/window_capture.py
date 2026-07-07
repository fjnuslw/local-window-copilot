from __future__ import annotations

import ctypes
import hashlib
import sys
import time
from ctypes import wintypes
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from PIL import ImageGrab

from app.core.config import get_settings
from app.schemas.window import ForegroundWindowInfo, RawWindowCapture, WindowBounds
from app.services.local_copilot_identity import (
    is_local_copilot_window,
    is_local_runtime_title,
)


if not sys.platform.startswith("win"):
    raise RuntimeError("Window capture currently supports Windows only.")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


class WindowCaptureError(RuntimeError):
    pass


IGNORED_SHELL_CLASS_NAMES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
MIN_CAPTURE_SIZE = 80


class WindowCaptureService:
    def __init__(
        self,
        capture_dir: Path,
        *,
        max_files: int = 200,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        self.capture_dir = capture_dir
        # 轮转配置：保留最近 max_files 张截图；清理动作节流到 cleanup_interval_seconds。
        self.max_files = max(1, int(max_files))
        self.cleanup_interval_seconds = max(1.0, float(cleanup_interval_seconds))
        self._last_cleanup_monotonic = 0.0
        # 启动即清理一次历史残留，避免 capture_dir 无限增长。
        self._prune_old_captures(force=True)

    def get_foreground_window_info(self) -> ForegroundWindowInfo:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            fallback_hwnd = self._find_top_capture_window()
            if fallback_hwnd is None:
                raise WindowCaptureError("No foreground window found.")
            hwnd = fallback_hwnd
        hwnd = self._resolve_capture_window(int(hwnd))
        return self._get_window_info(hwnd)

    def capture_foreground_window(
        self,
        *,
        preferred_window: ForegroundWindowInfo | None = None,
    ) -> RawWindowCapture:
        info = self._select_capture_info(preferred_window=preferred_window)
        return self.capture_window_info(info)

    def capture_window_info(self, info: ForegroundWindowInfo) -> RawWindowCapture:
        hwnd = int(info.window_handle)
        if hwnd and self._is_capture_candidate(hwnd):
            info = self._get_window_info(hwnd)
        bounds = info.window_bounds
        image = ImageGrab.grab(
            bbox=(bounds.left, bounds.top, bounds.right, bounds.bottom),
            all_screens=True,
        ).convert("RGB")

        screenshot_hash = self._hash_image(image)
        captured_at = datetime.now(UTC)
        timestamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = self.capture_dir / f"{timestamp}_{screenshot_hash[:12]}.png"
        image.save(screenshot_path)
        self._prune_old_captures()

        return RawWindowCapture(
            app_name=info.app_name,
            process_id=info.process_id,
            window_title=info.window_title,
            window_bounds=info.window_bounds,
            screenshot_path=screenshot_path,
            screenshot_hash=screenshot_hash,
            captured_at=captured_at,
        )

    def _select_capture_info(
        self,
        *,
        preferred_window: ForegroundWindowInfo | None = None,
    ) -> ForegroundWindowInfo:
        if preferred_window is not None:
            hwnd = int(preferred_window.window_handle)
            if (
                hwnd
                and self._is_capture_candidate(hwnd)
                and not self._is_low_priority_window(hwnd)
            ):
                return self._get_window_info(hwnd)
        return self.get_foreground_window_info()

    def _resolve_capture_window(self, hwnd: int) -> int:
        hard_ignored = self._should_ignore_window(hwnd)
        low_priority = False if hard_ignored else self._is_low_priority_window(hwnd)
        if not hard_ignored and not low_priority:
            return hwnd

        replacement = self._find_top_capture_window(excluded_hwnd=int(hwnd))
        if replacement is not None:
            return replacement
        if not hard_ignored:
            return hwnd
        raise WindowCaptureError("No capturable foreground window outside Local Window Copilot.")

    def _find_top_capture_window(self, *, excluded_hwnd: int | None = None) -> int | None:
        normal_candidates: list[int] = []
        low_priority_candidates: list[int] = []

        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if excluded_hwnd is not None and int(hwnd) == excluded_hwnd:
                return True
            if self._is_capture_candidate(hwnd):
                if self._is_low_priority_window(hwnd):
                    low_priority_candidates.append(int(hwnd))
                    return True
                normal_candidates.append(int(hwnd))
                return False
            return True

        callback = WNDENUMPROC(enum_proc)
        user32.EnumWindows(callback, 0)
        if normal_candidates:
            return normal_candidates[0]
        return low_priority_candidates[0] if low_priority_candidates else None

    def _is_capture_candidate(self, hwnd: int) -> bool:
        if self._should_ignore_window(hwnd):
            return False
        try:
            bounds = self._get_window_bounds(hwnd)
        except Exception:
            return False
        return bounds.width >= MIN_CAPTURE_SIZE and bounds.height >= MIN_CAPTURE_SIZE

    def _should_ignore_window(self, hwnd: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        class_name = self._get_window_class(hwnd)
        if class_name in IGNORED_SHELL_CLASS_NAMES:
            return True
        title = self._get_window_title(hwnd)
        return is_local_copilot_window(class_name=class_name, title=title)

    def _is_low_priority_window(self, hwnd: int) -> bool:
        title = self._get_window_title(hwnd)
        return is_local_runtime_title(title)

    def _get_window_info(self, hwnd: int) -> ForegroundWindowInfo:
        title = self._get_window_title(hwnd)
        bounds = self._get_window_bounds(hwnd)
        if bounds.width <= 0 or bounds.height <= 0:
            raise WindowCaptureError("Foreground window has invalid bounds.")

        process_id = self._get_process_id(hwnd)
        app_name = self._get_process_name(process_id) if process_id is not None else None
        return ForegroundWindowInfo(
            window_handle=int(hwnd),
            app_name=app_name,
            process_id=process_id,
            window_title=title,
            window_bounds=bounds,
        )

    @staticmethod
    def _get_window_title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    @staticmethod
    def _get_window_class(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value if length > 0 else ""

    @staticmethod
    def _get_window_bounds(hwnd: int) -> WindowBounds:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
        return WindowBounds(
            left=int(rect.left),
            top=int(rect.top),
            right=int(rect.right),
            bottom=int(rect.bottom),
        )

    @staticmethod
    def _get_process_id(hwnd: int) -> int | None:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) if pid.value else None

    @staticmethod
    def _get_process_name(process_id: int) -> str | None:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def _hash_image(image) -> str:
        digest = hashlib.sha256()
        digest.update(image.mode.encode("utf-8"))
        digest.update(str(image.size).encode("utf-8"))
        digest.update(image.tobytes())
        return digest.hexdigest()

    def _prune_old_captures(self, *, force: bool = False) -> int:
        """删除 capture_dir 中超出的最旧 PNG，保留最近 self.max_files 张。

        节流：非 force 时距上次清理不足 cleanup_interval_seconds 则跳过。
        返回删除的文件数。容错：任何 IO 异常均忽略，绝不影响截图主路径。
        """
        now = time.monotonic()
        if not force and now - self._last_cleanup_monotonic < self.cleanup_interval_seconds:
            return 0
        self._last_cleanup_monotonic = now

        try:
            files = [p for p in self.capture_dir.glob("*.png") if p.is_file()]
        except OSError:
            return 0
        sortable: list[tuple[float, Path]] = []
        for path in files:
            try:
                sortable.append((path.stat().st_mtime, path))
            except OSError:
                continue
        excess = len(sortable) - self.max_files
        if excess <= 0:
            return 0
        sortable.sort(key=lambda item: item[0])
        deleted = 0
        for _mtime, path in sortable[:excess]:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                continue
        return deleted


@lru_cache
def get_window_capture_service() -> WindowCaptureService:
    settings = get_settings()
    return WindowCaptureService(
        settings.window_capture_dir,
        max_files=settings.window_capture_max_files,
    )
