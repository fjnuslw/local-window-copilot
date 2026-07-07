from __future__ import annotations

from functools import lru_cache

from app.schemas.observation import ObservationCard
from app.schemas.window import RawWindowCapture


class ObservationBuilder:
    def build_from_capture(self, capture: RawWindowCapture) -> ObservationCard:
        return ObservationCard(
            app_name=capture.app_name,
            process_id=capture.process_id,
            window_title=capture.window_title,
            window_kind_hint="unknown",
            window_bounds=capture.window_bounds,
            screenshot_path=capture.screenshot_path,
            screenshot_hash=capture.screenshot_hash,
            source_signals=["window_title", "process", "screenshot"],
            privacy_state="normal",
            privacy_reasons=[],
            captured_at=capture.captured_at,
        )


@lru_cache
def get_observation_builder() -> ObservationBuilder:
    return ObservationBuilder()
