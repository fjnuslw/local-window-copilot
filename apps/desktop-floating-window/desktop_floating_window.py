from __future__ import annotations

import ctypes
import json
import math
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont


if not sys.platform.startswith("win"):
    raise SystemExit("This desktop floating window currently targets Windows.")


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
ASSET_DIR = PROJECT_ROOT / "assets" / "mascot" / "rive_import"
STATE_FILE = APP_DIR / "state_bridge.json"

WINDOW_WIDTH = 360
WINDOW_HEIGHT = 372
MASCOT_WIDTH = 320
MASCOT_TOP = 20

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
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200

VK_ESCAPE = 0x1B
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOW = 5
SWP_NOSIZE = 0x0001
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
    bgra = bytearray(image.tobytes("raw", "BGRA"))
    for index in range(0, len(bgra), 4):
        alpha = bgra[index + 3]
        bgra[index] = bgra[index] * alpha // 255
        bgra[index + 1] = bgra[index + 1] * alpha // 255
        bgra[index + 2] = bgra[index + 2] * alpha // 255
    return bytes(bgra)


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

        self.base = load_rgba(ASSET_DIR / "mascot_base_idle.png")
        self.overlays = {
            state: load_rgba(ASSET_DIR / filename)
            for state, filename in OVERLAY_FILES.items()
        }
        self.label_font = load_font(15, bold=True)
        self.icon_font = load_font(17, bold=True)

        self.state = "idle"
        self.frame = 0
        self.last_state_mtime = 0.0
        self.button_regions: dict[str, tuple[int, int, int, int]] = {}
        self.close_region = (WINDOW_WIDTH - 40, 18, WINDOW_WIDTH - 16, 42)
        self.drag_start: tuple[int, int, int, int] | None = None
        self.drag_hit: str | None = None
        self.drag_moved = False

        self._ensure_state_file()
        self._register_class()
        self._create_window()

    def _ensure_state_file(self) -> None:
        if not STATE_FILE.exists():
            STATE_FILE.write_text(
                json.dumps({"state": self.state}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

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

    def run(self) -> None:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def set_state(self, state: str, write_bridge: bool = False) -> None:
        if state not in STATES:
            return
        self.state = state
        if write_bridge:
            STATE_FILE.write_text(
                json.dumps({"state": state}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _poll_state_file(self) -> None:
        try:
            mtime = STATE_FILE.stat().st_mtime
            if mtime > self.last_state_mtime:
                self.last_state_mtime = mtime
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.set_state(str(data.get("state", self.state)))
        except (OSError, json.JSONDecodeError):
            pass

    def _handle_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_TIMER:
            self._poll_state_file()
            self.render()
            self.frame += 1
            return 0
        if msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            user32.DestroyWindow(hwnd)
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
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_mouse_down(self, lparam: int) -> None:
        x = signed_lparam_word(lparam)
        y = signed_lparam_word(lparam >> 16)
        hit = self._hit_test(x, y)

        if hit == "close":
            user32.DestroyWindow(self.hwnd)
            return
        if hit in STATES:
            self.set_state(hit, write_bridge=True)
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
            self.set_state(STATES[(index + 1) % len(STATES)], write_bridge=True)

        self.drag_start = None
        self.drag_hit = None
        user32.ReleaseCapture()

    def _hit_test(self, x: int, y: int) -> str | None:
        x1, y1, x2, y2 = self.close_region
        if x1 <= x <= x2 and y1 <= y <= y2:
            return "close"

        for state, (bx1, by1, bx2, by2) in self.button_regions.items():
            if bx1 <= x <= bx2 and by1 <= y <= by2:
                return state

        if 18 <= x <= WINDOW_WIDTH - 18 and 26 <= y <= 268:
            return "mascot"
        return None

    def render(self) -> None:
        image = Image.new("RGBA", (WINDOW_WIDTH, WINDOW_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        t = self.frame / 30

        self._draw_aura(draw, t)
        image.alpha_composite(self._render_mascot(t))
        self._draw_status_chip(draw)
        self._draw_toolbar(draw)
        self._draw_close_button(draw)

        self._update_layered_window(image)

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

        layer = Image.new("RGBA", (WINDOW_WIDTH, WINDOW_HEIGHT), (0, 0, 0, 0))
        x = int((WINDOW_WIDTH - mascot.width) / 2 + x_offset)
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
        cx = WINDOW_WIDTH / 2
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
        x1 = WINDOW_WIDTH / 2 - 78 * pulse
        y1 = 96
        x2 = WINDOW_WIDTH / 2 + 78 * pulse
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
        rect = (139, 262, 221, 294)
        draw.rounded_rectangle(rect, radius=15, fill=(248, 255, 255, 230), outline=(217, 238, 245, 220), width=1)
        draw.ellipse((154, 274, 164, 284), fill=hex_to_rgba(color))
        bbox = draw.textbbox((0, 0), label, font=self.label_font)
        draw.text(
            (188 - (bbox[2] - bbox[0]) / 2, 277 - (bbox[3] - bbox[1]) / 2),
            label,
            fill=(23, 54, 74, 255),
            font=self.label_font,
        )

    def _draw_toolbar(self, draw: ImageDraw.ImageDraw) -> None:
        start_x = 56
        y = 306
        size = 42
        gap = 8
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

    def _draw_close_button(self, draw: ImageDraw.ImageDraw) -> None:
        x1, y1, x2, y2 = self.close_region
        draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=(248, 255, 255, 230), outline=(223, 238, 243, 220), width=1)
        draw.line((x1 + 8, y1 + 8, x2 - 8, y2 - 8), fill=(86, 112, 131, 255), width=2)
        draw.line((x2 - 8, y1 + 8, x1 + 8, y2 - 8), fill=(86, 112, 131, 255), width=2)

    def _update_layered_window(self, image: Image.Image) -> None:
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
        user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        dst = POINT(rect.left, rect.top)
        size = SIZE(width, height)
        src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            self.hwnd,
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
