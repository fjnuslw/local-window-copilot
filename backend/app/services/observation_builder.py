from __future__ import annotations

from functools import lru_cache

from app.schemas.observation import ObservationCard
from app.schemas.window import RawWindowCapture


PRIVACY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("password", "password keyword"),
    ("passcode", "passcode keyword"),
    ("密码", "password keyword"),
    ("验证码", "verification code keyword"),
    ("captcha", "captcha keyword"),
    ("payment", "payment keyword"),
    ("支付", "payment keyword"),
    ("银行卡", "bank card keyword"),
    ("private key", "private key keyword"),
    ("secret", "secret keyword"),
    ("2fa", "two factor authentication keyword"),
    ("otp", "one-time password keyword"),
)

WINDOW_KIND_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("code", "vscode", "pycharm", "visual studio"), "coding"),
    (("terminal", "powershell", "cmd.exe", "windows terminal"), "terminal"),
    (("chrome", "edge", "firefox", "browser"), "web"),
    (("installer", "setup", "安装"), "installer"),
    (("settings", "设置"), "settings"),
    (("explorer.exe", "file explorer", "资源管理器"), "file_explorer"),
)


class ObservationBuilder:
    def build_from_capture(self, capture: RawWindowCapture) -> ObservationCard:
        privacy_reasons = self._privacy_reasons(capture)
        return ObservationCard(
            app_name=capture.app_name,
            process_id=capture.process_id,
            window_title=capture.window_title,
            window_kind_hint=self._window_kind_hint(capture),
            window_bounds=capture.window_bounds,
            screenshot_path=capture.screenshot_path,
            screenshot_hash=capture.screenshot_hash,
            source_signals=["window_title", "process", "screenshot"],
            privacy_state="privacy" if privacy_reasons else "normal",
            privacy_reasons=privacy_reasons,
            captured_at=capture.captured_at,
        )

    @staticmethod
    def _privacy_reasons(capture: RawWindowCapture) -> list[str]:
        haystack = " ".join(
            part for part in (capture.app_name or "", capture.window_title) if part
        ).lower()
        return [
            reason
            for keyword, reason in PRIVACY_KEYWORDS
            if keyword.lower() in haystack
        ]

    @staticmethod
    def _window_kind_hint(capture: RawWindowCapture) -> str:
        haystack = " ".join(
            part for part in (capture.app_name or "", capture.window_title) if part
        ).lower()
        for keywords, hint in WINDOW_KIND_HINTS:
            if any(keyword.lower() in haystack for keyword in keywords):
                return hint
        return "unknown"


@lru_cache
def get_observation_builder() -> ObservationBuilder:
    return ObservationBuilder()
