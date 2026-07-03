from __future__ import annotations

import ctypes
import json
import math
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable
from urllib import error, request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


if not sys.platform.startswith("win"):
    raise SystemExit("This desktop floating window currently targets Windows.")


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
ASSET_DIR = PROJECT_ROOT / "assets" / "mascot" / "rive_import"
BACKEND_BASE_URL = "http://127.0.0.1:18080"
BACKEND_TIMEOUT_SECONDS = 0.8
STATE_POLL_SECONDS = 0.5
ANALYSIS_POLL_SECONDS = 1.0
CHAT_POLL_SECONDS = 0.75
CHAT_HISTORY_POLL_SECONDS = 6.0

CANVAS_WIDTH = 520
BASE_CANVAS_HEIGHT = 372
PANEL_TOP = 360
PANEL_HEIGHT = 470
EXPANDED_CANVAS_HEIGHT = PANEL_TOP + PANEL_HEIGHT + 22
UI_SCALE = 0.78
WINDOW_WIDTH = int(round(CANVAS_WIDTH * UI_SCALE))
WINDOW_HEIGHT = int(round(EXPANDED_CANVAS_HEIGHT * UI_SCALE))
MASCOT_WIDTH = 300
MASCOT_TOP = 20
CHAT_CANVAS_WIDTH = 660
CHAT_CANVAS_HEIGHT = 560
CHAT_WINDOW_WIDTH = CHAT_CANVAS_WIDTH
CHAT_WINDOW_HEIGHT = CHAT_CANVAS_HEIGHT

STATES = ["idle", "observing", "analyzing", "privacy", "error"]
STATE_LABELS = {
    "idle": "待命",
    "observing": "观察",
    "analyzing": "分析",
    "privacy": "隐私",
    "error": "异常",
}
STATE_COLORS = {
    "idle": "#15c7f3",
    "observing": "#16b8ee",
    "analyzing": "#2f8dff",
    "privacy": "#25d199",
    "error": "#ffb02e",
}
OVERLAY_FILES = {
    "observing": "face_observing_overlay.png",
    "analyzing": "face_analyzing_overlay.png",
    "privacy": "face_privacy_overlay.png",
    "error": "face_error_overlay.png",
}


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
LRESULT = ctypes.c_ssize_t
HICON = wintypes.HANDLE
HCURSOR = wintypes.HANDLE
HBRUSH = wintypes.HANDLE
HMENU = wintypes.HANDLE
HBITMAP = wintypes.HANDLE
HDC = wintypes.HANDLE
UINT_PTR = ctypes.c_size_t

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

WM_DESTROY = 0x0002
WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A

VK_ESCAPE = 0x1B
VK_BACK = 0x08
VK_RETURN = 0x0D
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOW = 5
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
HWND_TOPMOST = wintypes.HWND(-1)

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetTimer.argtypes = [wintypes.HWND, UINT_PTR, wintypes.UINT, ctypes.c_void_p]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.ReleaseCapture.argtypes = []
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleDC.restype = HDC
gdi32.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [HDC]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_byte),
        ("rgbGreen", ctypes.c_byte),
        ("rgbRed", ctypes.c_byte),
        ("rgbReserved", ctypes.c_byte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND,
    HDC,
    ctypes.POINTER(POINT),
    ctypes.POINTER(SIZE),
    HDC,
    ctypes.POINTER(POINT),
    wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
gdi32.CreateDIBSection.argtypes = [
    HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = HBITMAP


def signed_lparam_word(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def load_rgba(path: Path) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(f"Missing mascot asset: {path}")
    return Image.open(path).convert("RGBA")


def hex_to_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha)


def premultiply_bgra(image: Image.Image) -> bytes:
    r, g, b, a = image.split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (r, g, b, a)).tobytes("raw", "BGRA")


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


class FloatingAssistantWindow:
    def __init__(self) -> None:
        self.hinstance = kernel32.GetModuleHandleW(None)
        self.class_name = "LocalWindowAwareFloatingAssistant"
        self.wndproc = WNDPROC(self._handle_message)
        self.hwnd: int | None = None
        self.chat_hwnd: int | None = None

        self.base = load_rgba(ASSET_DIR / "mascot_base_idle.png")
        self.overlays = {
            state: load_rgba(ASSET_DIR / filename)
            for state, filename in OVERLAY_FILES.items()
        }
        self.label_font = load_font(16, bold=True)
        self.icon_font = load_font(18, bold=True)
        self.panel_title_font = load_font(22, bold=True)
        self.panel_body_font = load_font(20)
        self.panel_small_font = load_font(17)
        self.panel_button_font = load_font(16, bold=True)

        self.state = "idle"
        self.frame = 0
        self.last_state_poll = 0.0
        self.last_analysis_poll = 0.0
        self.last_chat_poll = 0.0
        self.last_chat_history_poll = 0.0
        self.last_analysis_signature: str | None = None
        self.latest_analysis: dict[str, object] | None = None
        self.conversation: dict[str, object] | None = None
        self.chat_history: list[dict[str, object]] = []
        self.custom_question = ""
        self.chat_question = ""
        self.input_focused = False
        self.chat_input_focused = False
        self.chat_visible = False
        self.button_regions: dict[str, tuple[int, int, int, int]] = {}
        self.question_regions: list[tuple[tuple[int, int, int, int], str]] = []
        self.input_region: tuple[int, int, int, int] | None = None
        self.send_region: tuple[int, int, int, int] | None = None
        self.ask_region: tuple[int, int, int, int] | None = None
        self.resume_region: tuple[int, int, int, int] | None = None
        self.chat_input_region: tuple[int, int, int, int] | None = None
        self.chat_send_region: tuple[int, int, int, int] | None = None
        self.chat_resume_region: tuple[int, int, int, int] | None = None
        self.chat_close_region = (CHAT_CANVAS_WIDTH - 44, 18, CHAT_CANVAS_WIDTH - 18, 44)
        self.close_region = (CANVAS_WIDTH - 40, 18, CANVAS_WIDTH - 16, 42)
        self.drag_start: tuple[int, int, int, int] | None = None
        self.chat_drag_start: tuple[int, int, int, int] | None = None
        self.drag_hit: str | None = None
        self.chat_drag_moved = False
        self.drag_moved = False
        self._request_lock = threading.Lock()
        self._inflight_requests: set[str] = set()
        self._last_conversation_status: str | None = None
        self._chat_dirty = False
        self._chat_render_skip = 0
        self._chat_auto_scroll = True
        self.chat_scroll_offset = 0
        self.chat_max_scroll = 0
        self.chat_scrollbar_region: tuple[int, int, int, int] | None = None

        self._register_class()
        self._create_window()

    def _register_class(self) -> None:
        wc = WNDCLASS()
        wc.lpfnWndProc = self.wndproc
        wc.hInstance = self.hinstance
        wc.lpszClassName = self.class_name
        wc.hCursor = HCURSOR()
        user32.RegisterClassW(ctypes.byref(wc))

    def _create_window(self) -> None:
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = max(24, screen_w - WINDOW_WIDTH - 42)
        y = max(24, screen_h - WINDOW_HEIGHT - 92)
        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        hwnd = user32.CreateWindowExW(
            ex_style,
            self.class_name,
            "Floating Assistant",
            WS_POPUP,
            x,
            y,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
            None,
            None,
            self.hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError()

        self.hwnd = hwnd
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetTimer(hwnd, 1, 33, None)
        self.render()

    def _create_chat_window(self) -> None:
        if self.chat_hwnd is not None:
            return
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = max(24, screen_w - CHAT_WINDOW_WIDTH - WINDOW_WIDTH - 62)
        y = max(24, screen_h - CHAT_WINDOW_HEIGHT - 96)
        if self.hwnd is not None:
            rect = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
            x = max(24, rect.left - CHAT_WINDOW_WIDTH - 18)
            y = max(24, min(rect.top + 24, screen_h - CHAT_WINDOW_HEIGHT - 24))
        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        hwnd = user32.CreateWindowExW(
            ex_style,
            self.class_name,
            "Floating Chat",
            WS_POPUP,
            x,
            y,
            CHAT_WINDOW_WIDTH,
            CHAT_WINDOW_HEIGHT,
            None,
            None,
            self.hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError()
        self.chat_hwnd = hwnd
        self.render_chat()

    def _open_chat_window(self) -> None:
        self._create_chat_window()
        if self.chat_hwnd is None:
            return
        self.chat_visible = True
        self._chat_auto_scroll = True
        self.last_chat_history_poll = 0.0
        self._poll_chat_history(force=True)
        user32.ShowWindow(self.chat_hwnd, SW_SHOW)
        user32.SetWindowPos(
            self.chat_hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        user32.SetFocus(self.chat_hwnd)
        self.render_chat()

    def _hide_chat_window(self) -> None:
        self.chat_visible = False
        self.chat_input_focused = False
        if self.chat_hwnd is not None:
            user32.ShowWindow(self.chat_hwnd, SW_HIDE)

    def run(self) -> None:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def set_state(self, state: str, publish: bool = False) -> None:
        if state not in STATES:
            return
        self.state = state
        if publish:
            self._request_json_async(
                f"state:{state}",
                "POST",
                "/api/assistant/state",
                {"state": state, "reason": "desktop-button"},
            )

    def _poll_backend_state(self) -> None:
        now = time.monotonic()
        if now - self.last_state_poll < STATE_POLL_SECONDS:
            return
        self.last_state_poll = now
        self._request_json_async(
            "state",
            "GET",
            "/api/assistant/state",
            on_success=self._apply_backend_state,
        )

    def _poll_latest_analysis(self) -> None:
        now = time.monotonic()
        if now - self.last_analysis_poll < ANALYSIS_POLL_SECONDS:
            return
        self.last_analysis_poll = now
        self._request_json_async(
            "latest",
            "GET",
            "/api/assistant/latest",
            on_success=self._apply_latest_analysis,
        )

    def _apply_backend_state(self, data: object) -> None:
        if isinstance(data, dict):
            self.set_state(str(data.get("state", self.state)))

    def _apply_latest_analysis(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        signature = str(data.get("analyzed_at") or data.get("model_endpoint") or "")
        if signature and signature == self.last_analysis_signature:
            return
        self.last_analysis_signature = signature
        self.latest_analysis = data

    def _poll_conversation(self) -> None:
        now = time.monotonic()
        if now - self.last_chat_poll < CHAT_POLL_SECONDS:
            return
        self.last_chat_poll = now
        self._request_json_async(
            "conversation",
            "GET",
            "/api/assistant/conversation",
            on_success=self._apply_conversation,
        )

    def _apply_conversation(self, data: object) -> None:
        self.conversation = data if isinstance(data, dict) else None
        status = None
        if isinstance(self.conversation, dict):
            status = str(self.conversation.get("status") or "")
        if status and status != self._last_conversation_status:
            if status == "streaming":
                self._chat_auto_scroll = True
            elif status in {"done", "error"}:
                self._poll_chat_history(force=True)
        self._last_conversation_status = status
        if status in {"streaming", "done"}:
            self._chat_dirty = True

    def _poll_chat_history(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_chat_history_poll < CHAT_HISTORY_POLL_SECONDS:
            return
        self.last_chat_history_poll = now
        self._request_json_async(
            "chat-history",
            "GET",
            "/api/assistant/conversations",
            on_success=self._apply_chat_history,
        )

    def _apply_chat_history(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        items = data.get("items")
        self.chat_history = items if isinstance(items, list) else []

    def _request_json_async(
        self,
        key: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        on_success: Callable[[object], None] | None = None,
    ) -> None:
        with self._request_lock:
            if key in self._inflight_requests:
                return
            self._inflight_requests.add(key)
        thread = threading.Thread(
            target=self._request_json_worker,
            args=(key, method, path, payload, on_success),
            daemon=True,
        )
        thread.start()

    def _request_json_worker(
        self,
        key: str,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        on_success: Callable[[object], None] | None,
    ) -> None:
        try:
            data = self._request_json(method, path, payload)
            if data is not None and on_success is not None:
                on_success(data)
        finally:
            with self._request_lock:
                self._inflight_requests.discard(key)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object | None:
        url = f"{BACKEND_BASE_URL}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=BACKEND_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except (OSError, error.URLError, TimeoutError):
            return None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _should_show_panel(self) -> bool:
        return (
            self.latest_analysis is not None
            or self.state in {"observing", "analyzing", "error"}
        )

    def _handle_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if hwnd == self.chat_hwnd:
            return self._handle_chat_message(hwnd, msg, wparam, lparam)
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_TIMER:
            self._poll_backend_state()
            self._poll_latest_analysis()
            self._poll_conversation()
            if self.chat_visible:
                self._poll_chat_history()
            self.render()
            if self.chat_visible:
                if self._chat_dirty or self._chat_render_skip <= 0:
                    self.render_chat()
                    self._chat_dirty = False
                    self._chat_render_skip = 3
                else:
                    self._chat_render_skip -= 1
            self.frame += 1
            return 0
        if msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_KEYDOWN and wparam == VK_BACK:
            if self.input_focused and self.custom_question:
                self.custom_question = self.custom_question[:-1]
                self.render()
            return 0
        if msg == WM_KEYDOWN and wparam == VK_RETURN:
            if self.input_focused:
                self._ask_custom_question()
            return 0
        if msg == WM_CHAR:
            self._on_char(wparam)
            return 0
        if msg == WM_LBUTTONDOWN:
            self._on_mouse_down(lparam)
            return 0
        if msg == WM_MOUSEMOVE:
            self._on_mouse_move()
            return 0
        if msg == WM_LBUTTONUP:
            self._on_mouse_up(lparam)
            return 0
        if msg == WM_MOUSEWHEEL:
            self._maybe_forward_chat_wheel(wparam)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_chat_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_DESTROY:
            self.chat_hwnd = None
            self.chat_visible = False
            return 0
        if msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            self._hide_chat_window()
            return 0
        if msg == WM_KEYDOWN and wparam == VK_BACK:
            if self.chat_input_focused and self.chat_question:
                self.chat_question = self.chat_question[:-1]
                self._chat_dirty = True
            return 0
        if msg == WM_KEYDOWN and wparam == VK_RETURN:
            if self.chat_input_focused:
                self._ask_chat_question()
            return 0
        if msg == WM_CHAR:
            self._on_chat_char(wparam)
            return 0
        if msg == WM_MOUSEWHEEL:
            wheel_delta = signed_lparam_word((wparam >> 16) & 0xFFFF)
            if wheel_delta:
                self.chat_scroll_offset = max(
                    0,
                    min(self.chat_scroll_offset - wheel_delta // 40, self.chat_max_scroll),
                )
                self._chat_auto_scroll = False
                self._chat_dirty = True
            return 0
        if msg == WM_LBUTTONDOWN:
            self._on_chat_mouse_down(lparam)
            return 0
        if msg == WM_MOUSEMOVE:
            self._on_chat_mouse_move()
            return 0
        if msg == WM_LBUTTONUP:
            self._on_chat_mouse_up()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_mouse_down(self, lparam: int) -> None:
        x = signed_lparam_word(lparam)
        y = signed_lparam_word(lparam >> 16)
        hit = self._hit_test(x, y)
        user32.SetFocus(self.hwnd)

        if hit == "close":
            user32.DestroyWindow(self.hwnd)
            return
        if hit == "ask":
            self._open_chat_window()
            return
        if hit == "input":
            self.input_focused = True
            self.render()
            return
        self.input_focused = False
        if hit == "send":
            self._ask_custom_question()
            return
        if hit == "resume":
            self._resume_auto_watch()
            return
        if hit and hit.startswith("question:"):
            index = int(hit.split(":", 1)[1])
            if 0 <= index < len(self.question_regions):
                self._ask_question(self.question_regions[index][1])
            return
        if hit in STATES:
            self.set_state(hit, publish=True)
            return

        cursor = POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        rect = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        self.drag_start = (cursor.x, cursor.y, rect.left, rect.top)
        self.drag_hit = hit
        self.drag_moved = False
        user32.SetCapture(self.hwnd)

    def _on_mouse_move(self) -> None:
        if self.drag_start is None:
            return

        cursor = POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        start_x, start_y, win_x, win_y = self.drag_start
        dx = cursor.x - start_x
        dy = cursor.y - start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self.drag_moved = True
        user32.SetWindowPos(
            self.hwnd,
            HWND_TOPMOST,
            win_x + dx,
            win_y + dy,
            0,
            0,
            SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _on_mouse_up(self, lparam: int) -> None:
        if self.drag_start is None:
            return

        x = signed_lparam_word(lparam)
        y = signed_lparam_word(lparam >> 16)
        hit = self._hit_test(x, y)
        if not self.drag_moved and self.drag_hit == "mascot" and hit == "mascot":
            index = STATES.index(self.state)
            self.set_state(STATES[(index + 1) % len(STATES)], publish=True)

        self.drag_start = None
        self.drag_hit = None
        user32.ReleaseCapture()

    def _maybe_forward_chat_wheel(self, wparam: int) -> None:
        if not self.chat_visible or self.chat_hwnd is None:
            return
        cursor = POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        rect = wintypes.RECT()
        user32.GetWindowRect(self.chat_hwnd, ctypes.byref(rect))
        if not (rect.left <= cursor.x <= rect.right and rect.top <= cursor.y <= rect.bottom):
            return
        wheel_delta = signed_lparam_word((wparam >> 16) & 0xFFFF)
        if wheel_delta == 0:
            return
        self.chat_scroll_offset = max(
            0,
            min(self.chat_scroll_offset - wheel_delta // 40, self.chat_max_scroll),
        )
        self._chat_auto_scroll = False
        self._chat_dirty = True

    def _hit_test(self, x: int, y: int) -> str | None:
        if UI_SCALE != 1:
            x = int(x / UI_SCALE)
            y = int(y / UI_SCALE)

        x1, y1, x2, y2 = self.close_region
        if x1 <= x <= x2 and y1 <= y <= y2:
            return "close"

        for state, (bx1, by1, bx2, by2) in self.button_regions.items():
            if bx1 <= x <= bx2 and by1 <= y <= by2:
                return state

        if self.ask_region is not None:
            ax1, ay1, ax2, ay2 = self.ask_region
            if ax1 <= x <= ax2 and ay1 <= y <= ay2:
                return "ask"
        if self.input_region is not None:
            ix1, iy1, ix2, iy2 = self.input_region
            if ix1 <= x <= ix2 and iy1 <= y <= iy2:
                return "input"
        if self.send_region is not None:
            sx1, sy1, sx2, sy2 = self.send_region
            if sx1 <= x <= sx2 and sy1 <= y <= sy2:
                return "send"
        if self.resume_region is not None:
            rx1, ry1, rx2, ry2 = self.resume_region
            if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                return "resume"
        for index, (region, _question) in enumerate(self.question_regions):
            qx1, qy1, qx2, qy2 = region
            if qx1 <= x <= qx2 and qy1 <= y <= qy2:
                return f"question:{index}"

        if 18 <= x <= CANVAS_WIDTH - 18 and 26 <= y <= 268:
            return "mascot"
        return None

    def _chat_hit_test(self, x: int, y: int) -> str | None:
        x1, y1, x2, y2 = self.chat_close_region
        if x1 <= x <= x2 and y1 <= y <= y2:
            return "close"
        if self.chat_input_region is not None:
            ix1, iy1, ix2, iy2 = self.chat_input_region
            if ix1 <= x <= ix2 and iy1 <= y <= iy2:
                return "input"
        if self.chat_send_region is not None:
            sx1, sy1, sx2, sy2 = self.chat_send_region
            if sx1 <= x <= sx2 and sy1 <= y <= sy2:
                return "send"
        if self.chat_resume_region is not None:
            rx1, ry1, rx2, ry2 = self.chat_resume_region
            if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                return "resume"
        if self.chat_scrollbar_region is not None:
            sb_x1, sb_y1, sb_x2, sb_y2 = self.chat_scrollbar_region
            if sb_x1 <= x <= sb_x2 and sb_y1 <= y <= sb_y2:
                return "scrollbar"
        return "drag" if 0 <= y <= 78 else None

    def _on_char(self, wparam: int) -> None:
        if not self.input_focused:
            return
        if wparam in (VK_BACK, VK_RETURN, VK_ESCAPE):
            return
        if wparam < 32:
            return
        if len(self.custom_question) >= 120:
            return
        self.custom_question += chr(wparam)
        self.render()

    def _on_chat_char(self, wparam: int) -> None:
        if not self.chat_input_focused:
            return
        if wparam in (VK_BACK, VK_RETURN, VK_ESCAPE):
            return
        if wparam < 32:
            return
        if len(self.chat_question) >= 240:
            return
        self.chat_question += chr(wparam)
        self._chat_dirty = True

    def _on_chat_mouse_down(self, lparam: int) -> None:
        x = signed_lparam_word(lparam)
        y = signed_lparam_word(lparam >> 16)
        hit = self._chat_hit_test(x, y)
        if self.chat_hwnd is not None:
            user32.SetFocus(self.chat_hwnd)

        if hit == "close":
            self._hide_chat_window()
            return
        if hit == "input":
            self.chat_input_focused = True
            self._chat_dirty = True
            return
        self.chat_input_focused = False
        if hit == "send":
            self._ask_chat_question()
            return
        if hit == "resume":
            self._resume_auto_watch()
            return
        if hit == "scrollbar":
            return

        cursor = POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        rect = wintypes.RECT()
        if self.chat_hwnd is not None:
            user32.GetWindowRect(self.chat_hwnd, ctypes.byref(rect))
            self.chat_drag_start = (cursor.x, cursor.y, rect.left, rect.top)
            self.chat_drag_moved = False
            user32.SetCapture(self.chat_hwnd)

    def _on_chat_mouse_move(self) -> None:
        if self.chat_drag_start is None or self.chat_hwnd is None:
            return

        cursor = POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        start_x, start_y, win_x, win_y = self.chat_drag_start
        dx = cursor.x - start_x
        dy = cursor.y - start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self.chat_drag_moved = True
        user32.SetWindowPos(
            self.chat_hwnd,
            HWND_TOPMOST,
            win_x + dx,
            win_y + dy,
            0,
            0,
            SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _on_chat_mouse_up(self) -> None:
        if self.chat_drag_start is None:
            return
        self.chat_drag_start = None
        self.chat_drag_moved = False
        user32.ReleaseCapture()

    def _ask_custom_question(self) -> None:
        question = self.custom_question.strip()
        if not question:
            return
        self.custom_question = ""
        self.input_focused = False
        self._ask_question(question)

    def _ask_question(self, question: str) -> None:
        self._open_chat_window()
        self.conversation = {
            "question": question,
            "answer": "",
            "status": "streaming",
            "resume_required": True,
        }
        self._last_conversation_status = "streaming"
        self.set_state("analyzing")
        self._request_json_async(
            "ask-question",
            "POST",
            "/api/assistant/questions",
            {"question": question},
            on_success=self._apply_conversation,
        )
        self.render()
        self.render_chat()

    def _resume_auto_watch(self) -> None:
        self._request_json_async("resume", "POST", "/api/assistant/resume")
        self.conversation = None
        self._last_conversation_status = None
        self.set_state("idle")
        self.render()
        if self.chat_visible:
            self._chat_dirty = True

    def _ask_chat_question(self) -> None:
        question = self.chat_question.strip()
        if not question:
            return
        self.chat_question = ""
        self.chat_input_focused = False
        self._ask_question(question)

    def render(self) -> None:
        show_panel = self._should_show_panel()
        canvas_height = EXPANDED_CANVAS_HEIGHT if show_panel else BASE_CANVAS_HEIGHT
        image = Image.new("RGBA", (CANVAS_WIDTH, canvas_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        t = self.frame / 30

        self._draw_aura(draw, t)
        image.alpha_composite(self._render_mascot(t))
        self._draw_status_chip(draw)
        self._draw_toolbar(draw)
        if show_panel:
            self._draw_summary_panel(draw)
        else:
            self.question_regions.clear()
            self.input_region = None
            self.send_region = None
            self.ask_region = None
            self.resume_region = None
        self._draw_close_button(draw)

        if UI_SCALE != 1:
            target_height = int(round(canvas_height * UI_SCALE))
            image = image.resize((WINDOW_WIDTH, target_height), Image.Resampling.LANCZOS)

        self._update_layered_window(image)

    def render_chat(self) -> None:
        if self.chat_hwnd is None:
            return
        image = Image.new("RGBA", (CHAT_CANVAS_WIDTH, CHAT_CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        color = STATE_COLORS.get(self.state, STATE_COLORS["idle"])

        shadow = (22, 72, 92, 32)
        draw.rounded_rectangle((18, 24, CHAT_CANVAS_WIDTH - 14, CHAT_CANVAS_HEIGHT - 10), radius=24, fill=shadow)
        panel = (14, 14, CHAT_CANVAS_WIDTH - 22, CHAT_CANVAS_HEIGHT - 22)
        draw.rounded_rectangle(
            panel,
            radius=22,
            fill=(248, 255, 255, 246),
            outline=hex_to_rgba(color, 165),
            width=2,
        )

        msg_top = 188
        msg_bottom = CHAT_CANVAS_HEIGHT - 92
        visible_height = msg_bottom - msg_top

        messages = self._chat_messages()
        total_height = 0
        if messages:
            heights = [self._bubble_height(draw, s, t, st) + 8 for s, t, st in messages]
            total_height = sum(heights)
            if total_height <= visible_height:
                self.chat_max_scroll = 0
                self.chat_scroll_offset = 0
            else:
                self.chat_max_scroll = total_height - visible_height
                if self._chat_auto_scroll:
                    self.chat_scroll_offset = self.chat_max_scroll
                else:
                    self.chat_scroll_offset = min(self.chat_scroll_offset, self.chat_max_scroll)

            y = msg_top - self.chat_scroll_offset
            for i, (speaker, text, status) in enumerate(messages):
                if y + heights[i] < msg_top:
                    y += heights[i]
                    continue
                if y >= msg_bottom:
                    break
                y = self._draw_chat_bubble(draw, speaker, text, y, status=status)
                y += 8
        else:
            draw.text((38, msg_top + 18), "点击候选问题，或在下方输入一个问题。", fill=(92, 125, 143, 255), font=self.panel_body_font)
            self.chat_max_scroll = 0
            self.chat_scroll_offset = 0

        draw.rounded_rectangle((28, 28, CHAT_CANVAS_WIDTH - 36, 84), radius=18, fill=(235, 251, 255, 224))
        draw.ellipse((42, 47, 54, 59), fill=hex_to_rgba(color))
        draw.text((66, 35), "对话工作台", fill=(18, 52, 72, 255), font=self.panel_title_font)
        draw.text((66, 63), "基于最近窗口摘要和短期记忆", fill=(73, 106, 126, 255), font=self.panel_small_font)
        self._draw_chat_context(draw, 104)
        self._draw_chat_close_button(draw)

        if messages and self.chat_max_scroll > 0:
            self._draw_chat_scrollbar(draw, msg_top, msg_bottom, total_height, visible_height)
        else:
            self.chat_scrollbar_region = None

        self._draw_chat_input(draw)
        self._update_layered_window(image, hwnd=self.chat_hwnd)

    def _draw_chat_close_button(self, draw: ImageDraw.ImageDraw) -> None:
        x1, y1, x2, y2 = self.chat_close_region
        draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=(248, 255, 255, 230), outline=(210, 231, 238, 230), width=1)
        draw.line((x1 + 8, y1 + 8, x2 - 8, y2 - 8), fill=(72, 102, 120, 255), width=2)
        draw.line((x2 - 8, y1 + 8, x1 + 8, y2 - 8), fill=(72, 102, 120, 255), width=2)

    def _draw_chat_context(self, draw: ImageDraw.ImageDraw, y: int) -> int:
        analysis = self._analysis_payload()
        capture = self._capture_payload()
        title = str(capture.get("window_title") or "等待窗口摘要")
        summary = str(analysis.get("summary") or "自动观察线会在这里提供最近窗口摘要。")
        draw.rounded_rectangle((32, y, CHAT_CANVAS_WIDTH - 40, y + 74), radius=16, fill=(255, 255, 255, 232), outline=(213, 235, 242, 210), width=1)
        draw.text((48, y + 12), self._single_line_text(draw, title, self.panel_small_font, CHAT_CANVAS_WIDTH - 112), fill=(56, 88, 108, 255), font=self.panel_small_font)
        self._draw_wrapped_text(
            draw,
            summary,
            (48, y + 38),
            self.panel_small_font,
            (28, 66, 88, 255),
            CHAT_CANVAS_WIDTH - 112,
            max_lines=2,
            line_gap=4,
        )
        return y + 74

    def _chat_messages(self) -> list[tuple[str, str, str]]:
        messages: list[tuple[str, str, str]] = []
        current_id = self.conversation.get("session_id") if isinstance(self.conversation, dict) else None
        history = [
            item
            for item in reversed(self.chat_history[:6])
            if isinstance(item, dict) and item.get("session_id") != current_id
        ]
        for item in history:
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            status = str(item.get("status") or "done")
            if question:
                messages.append(("user", question, status))
            if answer:
                messages.append(("assistant", answer, status))
        if isinstance(self.conversation, dict):
            question = str(self.conversation.get("question") or "").strip()
            answer = str(self.conversation.get("answer") or "").strip()
            status = str(self.conversation.get("status") or "streaming")
            if question:
                messages.append(("user", question, status))
            if answer:
                messages.append(("assistant", answer, status))
            elif status == "streaming":
                messages.append(("assistant", "正在回答...", status))
        return messages

    def _draw_chat_bubble(self, draw: ImageDraw.ImageDraw, speaker: str, text: str, y: int, *, status: str) -> int:
        is_user = speaker == "user"
        max_width = 390 if is_user else 440
        font = self.panel_small_font if is_user else self.panel_body_font
        lines = self._wrap_text(draw, text, font, max_width - 28, 4 if is_user else 6)
        bbox = draw.textbbox((0, 0), "国", font=font)
        line_height = (bbox[3] - bbox[1]) + 5
        height = max(34, len(lines) * line_height + 20)
        width = min(max_width, max((draw.textbbox((0, 0), line, font=font)[2] for line in lines), default=80) + 28)
        x1 = CHAT_CANVAS_WIDTH - 44 - width if is_user else 38
        x2 = x1 + width
        fill = (217, 249, 255, 242) if is_user else (255, 255, 255, 240)
        outline = (91, 210, 235, 190) if is_user else (210, 231, 238, 220)
        draw.rounded_rectangle((x1, y, x2, y + height), radius=15, fill=fill, outline=outline, width=1)
        text_fill = (19, 70, 91, 255) if is_user else (18, 52, 72, 255)
        line_y = y + 10
        for line in lines:
            draw.text((x1 + 14, line_y), line, fill=text_fill, font=font)
            line_y += line_height
        if not is_user and status == "streaming":
            dots = "." * ((self.frame // 12) % 4)
            draw.text((x1 + 14, y + height - 19), f"生成中{dots}", fill=(92, 125, 143, 255), font=self.panel_small_font)
        return y + height

    def _bubble_height(
        self,
        draw: ImageDraw.ImageDraw,
        speaker: str,
        text: str,
        status: str,
    ) -> int:
        is_user = speaker == "user"
        max_width = 390 if is_user else 440
        font = self.panel_small_font if is_user else self.panel_body_font
        lines = self._wrap_text(draw, text, font, max_width - 28, 4 if is_user else 6)
        bbox = draw.textbbox((0, 0), "国", font=font)
        line_height = (bbox[3] - bbox[1]) + 5
        return max(34, len(lines) * line_height + 20)

    def _draw_chat_scrollbar(
        self,
        draw: ImageDraw.ImageDraw,
        top: int,
        bottom: int,
        total_height: int,
        visible_height: int,
    ) -> None:
        bar_x = CHAT_CANVAS_WIDTH - 28
        bar_width = 4
        bar_top = top + 4
        bar_bottom = bottom - 4
        bar_height = bar_bottom - bar_top

        draw.rounded_rectangle(
            (bar_x, bar_top, bar_x + bar_width, bar_bottom),
            radius=2,
            fill=(206, 229, 237, 160),
        )

        thumb_height = max(20, int(bar_height * visible_height / total_height))
        thumb_top = bar_top + int(
            (bar_height - thumb_height) * self.chat_scroll_offset / max(1, self.chat_max_scroll)
        )
        draw.rounded_rectangle(
            (bar_x, thumb_top, bar_x + bar_width, thumb_top + thumb_height),
            radius=2,
            fill=(120, 180, 200, 220),
        )
        self.chat_scrollbar_region = (bar_x, bar_top, bar_x + bar_width, bar_bottom)

    def _draw_chat_input(self, draw: ImageDraw.ImageDraw) -> None:
        y = CHAT_CANVAS_HEIGHT - 74
        input_rect = (34, y, CHAT_CANVAS_WIDTH - 190, y + 42)
        send_rect = (CHAT_CANVAS_WIDTH - 178, y, CHAT_CANVAS_WIDTH - 112, y + 42)
        resume_rect = (CHAT_CANVAS_WIDTH - 100, y, CHAT_CANVAS_WIDTH - 34, y + 42)
        self.chat_input_region = input_rect
        self.chat_send_region = send_rect
        self.chat_resume_region = resume_rect
        outline = (67, 201, 227, 230) if self.chat_input_focused else (206, 229, 237, 230)
        draw.rounded_rectangle(input_rect, radius=15, fill=(255, 255, 255, 242), outline=outline, width=2)
        text = self.chat_question or "继续提问"
        fill = (20, 58, 78, 255) if self.chat_question else (112, 139, 154, 255)
        if self.chat_input_focused and (self.frame // 18) % 2 == 0:
            text = f"{text}|"
        clipped = self._single_line_text(draw, text, self.panel_small_font, input_rect[2] - input_rect[0] - 26)
        draw.text((input_rect[0] + 13, input_rect[1] + 10), clipped, fill=fill, font=self.panel_small_font)
        self._draw_text_button(draw, send_rect, "发送", active=bool(self.chat_question.strip()))
        self._draw_text_button(draw, resume_rect, "观察", active=True)

    def _render_mascot(self, t: float) -> Image.Image:
        mascot = self.base.copy()
        overlay = self.overlays.get(self.state)
        if overlay is not None:
            mascot.alpha_composite(overlay)

        y_offset = math.sin(t * 1.9) * 6
        x_offset = 0
        angle = 0.0
        scale = 1.0
        if self.state == "analyzing":
            angle = math.sin(t * 5.2) * 2.2
        elif self.state == "privacy":
            scale = 1 + (math.sin(t * 2.8) + 1) * 0.012
        elif self.state == "error":
            x_offset = math.sin(t * 22) * 4
            y_offset = math.cos(t * 18) * 2

        target_w = int(MASCOT_WIDTH * scale)
        target_h = int(target_w * mascot.height / mascot.width)
        mascot = mascot.resize((target_w, target_h), Image.Resampling.LANCZOS)
        if abs(angle) > 0.1:
            mascot = mascot.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)

        layer = Image.new("RGBA", (CANVAS_WIDTH, BASE_CANVAS_HEIGHT), (0, 0, 0, 0))
        x = int((CANVAS_WIDTH - mascot.width) / 2 + x_offset)
        y = int(MASCOT_TOP + y_offset)

        if self.state == "privacy":
            glow = self._make_glow(mascot, STATE_COLORS["privacy"])
            layer.alpha_composite(glow, (x, y))

        layer.alpha_composite(mascot, (x, y))
        effect_draw = ImageDraw.Draw(layer)
        if self.state == "observing":
            self._draw_scan_brackets(effect_draw, t)
        elif self.state == "error":
            self._draw_error_sparks(effect_draw, t)
        return layer

    def _make_glow(self, image: Image.Image, color: str) -> Image.Image:
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        alpha = image.getchannel("A").filter(ImageFilter.GaussianBlur(12))
        glow.paste(hex_to_rgba(color, 95), mask=alpha)
        glow.alpha_composite(image)
        return glow

    def _draw_aura(self, draw: ImageDraw.ImageDraw, t: float) -> None:
        color = STATE_COLORS[self.state]
        pulse = 1 + math.sin(t * 2.6) * 0.08
        width = 170 * pulse
        height = 36 * (1 / pulse)
        cx = CANVAS_WIDTH / 2
        cy = 242
        draw.ellipse(
            (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2),
            outline=hex_to_rgba(color, 190),
            width=2,
        )
        inner_color = "#7be9ff" if self.state != "privacy" else "#8bf5cc"
        draw.ellipse(
            (cx - width * 0.28, cy + 14, cx + width * 0.28, cy + 28),
            outline=hex_to_rgba(inner_color, 145),
            width=1,
        )

    def _draw_scan_brackets(self, draw: ImageDraw.ImageDraw, t: float) -> None:
        color = hex_to_rgba("#62ddff", 230)
        pulse = 1 + math.sin(t * 5.5) * 0.04
        x1 = CANVAS_WIDTH / 2 - 78 * pulse
        y1 = 96
        x2 = CANVAS_WIDTH / 2 + 78 * pulse
        y2 = 162
        arm = 18
        segments = [
            (x1, y1, x1 + arm, y1),
            (x1, y1, x1, y1 + arm),
            (x2, y1, x2 - arm, y1),
            (x2, y1, x2, y1 + arm),
            (x1, y2, x1 + arm, y2),
            (x1, y2, x1, y2 - arm),
            (x2, y2, x2 - arm, y2),
            (x2, y2, x2, y2 - arm),
        ]
        for segment in segments:
            draw.line(segment, fill=color, width=3)

    def _draw_error_sparks(self, draw: ImageDraw.ImageDraw, t: float) -> None:
        color = hex_to_rgba("#ffad2f", 235)
        shift = 3 if int(t * 8) % 2 else 0
        for x, y, radius in ((270 + shift, 110, 5), (286 - shift, 130, 3)):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    def _draw_status_chip(self, draw: ImageDraw.ImageDraw) -> None:
        label = STATE_LABELS[self.state]
        color = STATE_COLORS[self.state]
        center_x = CANVAS_WIDTH / 2
        rect = (center_x - 41, 262, center_x + 41, 294)
        draw.rounded_rectangle(rect, radius=15, fill=(248, 255, 255, 230), outline=(217, 238, 245, 220), width=1)
        draw.ellipse((center_x - 26, 274, center_x - 16, 284), fill=hex_to_rgba(color))
        bbox = draw.textbbox((0, 0), label, font=self.label_font)
        draw.text(
            (center_x + 8 - (bbox[2] - bbox[0]) / 2, 277 - (bbox[3] - bbox[1]) / 2),
            label,
            fill=(23, 54, 74, 255),
            font=self.label_font,
        )

    def _draw_toolbar(self, draw: ImageDraw.ImageDraw) -> None:
        y = 306
        size = 42
        gap = 8
        total_width = 5 * size + 4 * gap
        start_x = int((CANVAS_WIDTH - total_width) / 2)
        draw.rounded_rectangle(
            (start_x - 10, y - 8, start_x + 5 * size + 4 * gap + 10, y + size + 8),
            radius=18,
            fill=(247, 254, 255, 230),
            outline=(220, 238, 245, 230),
            width=1,
        )
        self.button_regions.clear()
        for index, state in enumerate(STATES):
            x = start_x + index * (size + gap)
            self.button_regions[state] = (x, y, x + size, y + size)
            active = state == self.state
            fill = (201, 245, 255, 240) if active else (247, 254, 255, 215)
            outline = hex_to_rgba(STATE_COLORS[state], 220) if active else (247, 254, 255, 210)
            draw.rounded_rectangle((x, y, x + size, y + size), radius=13, fill=fill, outline=outline, width=1)
            self._draw_icon(draw, state, x + size / 2, y + size / 2, active)

    def _draw_icon(self, draw: ImageDraw.ImageDraw, state: str, cx: float, cy: float, active: bool) -> None:
        color = (5, 63, 85, 255) if active else (56, 84, 105, 255)
        icons: dict[str, Callable[[ImageDraw.ImageDraw, float, float, tuple[int, int, int, int]], None]] = {
            "idle": self._icon_idle,
            "observing": self._icon_observing,
            "analyzing": self._icon_analyzing,
            "privacy": self._icon_privacy,
            "error": self._icon_error,
        }
        icons[state](draw, cx, cy, color)

    def _icon_idle(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, color: tuple[int, int, int, int]) -> None:
        draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline=color, width=2)
        draw.ellipse((cx - 4, cy - 2, cx - 2, cy), fill=color)
        draw.ellipse((cx + 2, cy - 2, cx + 4, cy), fill=color)
        draw.arc((cx - 6, cy - 4, cx + 6, cy + 8), start=205, end=335, fill=color, width=2)

    def _icon_observing(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, color: tuple[int, int, int, int]) -> None:
        draw.ellipse((cx - 11, cy - 6, cx + 11, cy + 6), outline=color, width=2)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=color)

    def _icon_analyzing(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, color: tuple[int, int, int, int]) -> None:
        for angle in range(0, 180, 45):
            radians = math.radians(angle)
            draw.line(
                (
                    cx - math.cos(radians) * 10,
                    cy - math.sin(radians) * 10,
                    cx + math.cos(radians) * 10,
                    cy + math.sin(radians) * 10,
                ),
                fill=color,
                width=2,
            )
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), outline=color, width=2)

    def _icon_privacy(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, color: tuple[int, int, int, int]) -> None:
        points = [(cx, cy - 12), (cx - 9, cy - 7), (cx - 7, cy + 7), (cx, cy + 12), (cx + 7, cy + 7), (cx + 9, cy - 7)]
        draw.line(points + [points[0]], fill=color, width=2)
        draw.line((cx - 5, cy + 1, cx - 1, cy + 5, cx + 6, cy - 5), fill=color, width=2)

    def _icon_error(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, color: tuple[int, int, int, int]) -> None:
        draw.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), outline=color, width=2)
        draw.line((cx, cy - 6, cx, cy + 3), fill=color, width=2)
        draw.ellipse((cx - 1.5, cy + 6, cx + 1.5, cy + 9), fill=color)

    def _analysis_payload(self) -> dict[str, object]:
        if not isinstance(self.latest_analysis, dict):
            return {}
        analysis = self.latest_analysis.get("analysis")
        return analysis if isinstance(analysis, dict) else {}

    def _capture_payload(self) -> dict[str, object]:
        if not isinstance(self.latest_analysis, dict):
            return {}
        capture = self.latest_analysis.get("capture")
        return capture if isinstance(capture, dict) else {}

    def _draw_close_button(self, draw: ImageDraw.ImageDraw) -> None:
        x1, y1, x2, y2 = self.close_region
        draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=(248, 255, 255, 230), outline=(223, 238, 243, 220), width=1)
        draw.line((x1 + 8, y1 + 8, x2 - 8, y2 - 8), fill=(86, 112, 131, 255), width=2)
        draw.line((x2 - 8, y1 + 8, x1 + 8, y2 - 8), fill=(86, 112, 131, 255), width=2)

    def _draw_summary_panel(self, draw: ImageDraw.ImageDraw) -> None:
        self.question_regions.clear()
        self.input_region = None
        self.send_region = None
        self.resume_region = None

        x1 = 34
        y1 = PANEL_TOP
        x2 = CANVAS_WIDTH - 34
        y2 = PANEL_TOP + PANEL_HEIGHT
        color = STATE_COLORS[self.state]
        draw.rounded_rectangle(
            (x1, y1, x2, y2),
            radius=22,
            fill=(248, 255, 255, 244),
            outline=hex_to_rgba(color, 150),
            width=2,
        )
        draw.line(
            (CANVAS_WIDTH / 2, y1 - 18, CANVAS_WIDTH / 2, y1),
            fill=hex_to_rgba(color, 155),
            width=2,
        )

        analysis = self._analysis_payload()
        summary = analysis.get("summary") or self._empty_summary_text()
        key_points = analysis.get("key_points") if isinstance(analysis.get("key_points"), list) else []

        cursor_y = y1 + 20
        draw.text((x1 + 20, cursor_y), "当前页面摘要", fill=(18, 52, 72, 255), font=self.panel_title_font)
        cursor_y += 40
        cursor_y = self._draw_wrapped_text(
            draw,
            str(summary),
            (x1 + 20, cursor_y),
            self.panel_body_font,
            (25, 60, 80, 255),
            x2 - x1 - 40,
            max_lines=6,
            line_gap=7,
        )

        if key_points:
            cursor_y += 16
            for item in key_points[:4]:
                cursor_y = self._draw_wrapped_text(
                    draw,
                    f"· {item}",
                    (x1 + 20, cursor_y),
                    self.panel_small_font,
                    (35, 72, 94, 255),
                    x2 - x1 - 40,
                    max_lines=1,
                    line_gap=6,
                )

        self._draw_ask_button(draw, x1, y2 - 64, x2)

    def _draw_ask_button(self, draw: ImageDraw.ImageDraw, x1: int, y: int, x2: int) -> None:
        """底部提问按钮：点击直接打开对话框。"""
        rect = (x1 + 20, y, x2 - 20, y + 38)
        self.ask_region = rect
        active = self.state in {"idle", "observing", "analyzing"}
        fill = (201, 245, 255, 245) if active else (246, 253, 255, 238)
        outline = hex_to_rgba("#15c7f3", 220) if active else (205, 230, 238, 230)
        draw.rounded_rectangle(rect, radius=14, fill=fill, outline=outline, width=2)
        label = "💬  点击提问"
        clipped = self._single_line_text(draw, label, self.panel_button_font, rect[2] - rect[0] - 24)
        bbox = draw.textbbox((0, 0), clipped, font=self.panel_button_font)
        draw.text(
            (rect[0] + (rect[2] - rect[0] - (bbox[2] - bbox[0])) / 2,
             rect[1] + (rect[3] - rect[1] - (bbox[3] - bbox[1])) / 2 - 2),
            clipped,
            fill=(22, 68, 90, 255),
            font=self.panel_button_font,
        )

    def _draw_question_input(self, draw: ImageDraw.ImageDraw, x1: int, y: int, x2: int) -> None:
        input_rect = (x1 + 20, y, x2 - 92, y + 38)
        send_rect = (x2 - 82, y, x2 - 20, y + 38)
        self.input_region = input_rect
        self.send_region = send_rect

        outline = (67, 201, 227, 230) if self.input_focused else (206, 229, 237, 230)
        draw.rounded_rectangle(input_rect, radius=13, fill=(255, 255, 255, 238), outline=outline, width=2)
        text = self.custom_question or "自定义提问"
        fill = (20, 58, 78, 255) if self.custom_question else (112, 139, 154, 255)
        if self.input_focused and (self.frame // 18) % 2 == 0:
            text = f"{text}|"
        clipped = self._single_line_text(draw, text, self.panel_small_font, input_rect[2] - input_rect[0] - 24)
        draw.text((input_rect[0] + 12, input_rect[1] + 8), clipped, fill=fill, font=self.panel_small_font)
        self._draw_text_button(draw, send_rect, "发送", active=bool(self.custom_question.strip()))

    def _draw_text_button(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
        text: str,
        *,
        active: bool,
    ) -> None:
        fill = (216, 249, 255, 245) if active else (246, 253, 255, 238)
        outline = hex_to_rgba("#15c7f3", 210) if active else (205, 230, 238, 230)
        draw.rounded_rectangle(rect, radius=14, fill=fill, outline=outline, width=1)
        clipped = self._single_line_text(draw, text, self.panel_button_font, rect[2] - rect[0] - 22)
        bbox = draw.textbbox((0, 0), clipped, font=self.panel_button_font)
        draw.text(
            (rect[0] + 11, rect[1] + (rect[3] - rect[1] - (bbox[3] - bbox[1])) / 2 - 2),
            clipped,
            fill=(22, 68, 90, 255),
            font=self.panel_button_font,
        )

    def _empty_summary_text(self) -> str:
        if self.state == "observing":
            return "正在观察当前窗口。"
        if self.state == "analyzing":
            return "正在生成当前页面摘要。"
        if self.state == "error":
            return "后端或模型暂时不可用。"
        return "窗口变化后会在这里显示摘要。"

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        position: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
        max_width: int,
        *,
        max_lines: int,
        line_gap: int,
    ) -> int:
        x, y = position
        lines = self._wrap_text(draw, text, font, max_width, max_lines)
        bbox = draw.textbbox((0, 0), "国", font=font)
        line_height = (bbox[3] - bbox[1]) + line_gap
        for line in lines:
            draw.text((x, y), line, fill=fill, font=font)
            y += line_height
        return y

    @staticmethod
    def _wrap_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
        max_lines: int,
    ) -> list[str]:
        normalized = " ".join(str(text).split())
        if not normalized:
            return []

        lines: list[str] = []
        current = ""
        for char in normalized:
            candidate = current + char
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines and len("".join(lines)) < len(normalized):
            lines[-1] = lines[-1].rstrip("，。！？；,.!?; ") + "..."
        return lines

    @staticmethod
    def _single_line_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
        ellipsis = "..."
        current = ""
        for char in text:
            candidate = current + char + ellipsis
            if draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                break
            current += char
        return current + ellipsis if current else ellipsis

    def _update_layered_window(self, image: Image.Image, *, hwnd: int | None = None) -> None:
        target_hwnd = hwnd or self.hwnd
        if target_hwnd is None:
            return
        image = image.convert("RGBA")
        width, height = image.size
        bitmap_bytes = premultiply_bgra(image)

        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        bits = ctypes.c_void_p()
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        hbitmap = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not hbitmap:
            raise ctypes.WinError()

        ctypes.memmove(bits, bitmap_bytes, len(bitmap_bytes))
        old_bitmap = gdi32.SelectObject(hdc_mem, hbitmap)

        rect = wintypes.RECT()
        user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
        dst = POINT(rect.left, rect.top)
        size = SIZE(width, height)
        src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            target_hwnd,
            hdc_screen,
            ctypes.byref(dst),
            ctypes.byref(size),
            hdc_mem,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )

        gdi32.SelectObject(hdc_mem, old_bitmap)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

        if not ok:
            raise ctypes.WinError()


def main() -> None:
    app = FloatingAssistantWindow()
    app.run()


if __name__ == "__main__":
    main()
