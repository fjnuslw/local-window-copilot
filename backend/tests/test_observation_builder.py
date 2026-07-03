from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas.window import RawWindowCapture, WindowBounds
from app.services.observation_builder import ObservationBuilder


def make_capture(title: str, tmp_path: Path, app_name: str = "Code.exe") -> RawWindowCapture:
    image_path = tmp_path / "capture.png"
    image_path.write_bytes(b"fake-image")
    return RawWindowCapture(
        app_name=app_name,
        process_id=1234,
        window_title=title,
        window_bounds=WindowBounds(left=10, top=20, right=810, bottom=620),
        screenshot_path=image_path,
        screenshot_hash="hash",
        captured_at=datetime.now(UTC),
    )


def test_observation_builder_creates_minimal_coding_card(tmp_path) -> None:
    capture = make_capture("README.md - Visual Studio Code", tmp_path)

    observation = ObservationBuilder().build_from_capture(capture)

    assert observation.app_name == "Code.exe"
    assert observation.window_title == "README.md - Visual Studio Code"
    assert observation.window_kind_hint == "coding"
    assert observation.privacy_state == "normal"
    assert observation.source_signals == ["window_title", "process", "screenshot"]


def test_observation_builder_marks_privacy_without_redacting_or_fallback(tmp_path) -> None:
    capture = make_capture("Payment password and OTP", tmp_path)

    observation = ObservationBuilder().build_from_capture(capture)

    assert observation.privacy_state == "privacy"
    assert observation.window_title == "Payment password and OTP"
    assert observation.privacy_reasons
