from __future__ import annotations

import asyncio
import time
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.schemas.analyze import WindowAnalysisResult
from app.schemas.window import RawWindowCapture, WindowWatchStatus
from app.services.assistant_state import get_assistant_state_service
from app.services.runtime_log import get_runtime_log_service
from app.services.window_analysis import ObservationAgent, get_window_analysis_service
from app.services.window_capture import WindowCaptureService, get_window_capture_service


class WindowWatcherService:
    def __init__(
        self,
        *,
        capture_service: WindowCaptureService,
        analysis_service: ObservationAgent | None = None,
        state_service=None,
        interval_seconds: float,
        capture_min_interval_seconds: float,
        analysis_min_interval_seconds: float,
    ) -> None:
        self.capture_service = capture_service
        self.analysis_service = analysis_service
        self.state_service = state_service
        self.interval_seconds = interval_seconds
        self.capture_min_interval_seconds = capture_min_interval_seconds
        self.analysis_min_interval_seconds = analysis_min_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._last_window_signature: tuple[object, ...] | None = None
        self._last_capture_hash: str | None = None
        self._last_capture_monotonic = 0.0
        self._last_analysis_monotonic = 0.0
        self._last_capture: RawWindowCapture | None = None
        self._last_analysis: dict[str, object] | None = None
        self._last_error: str | None = None
        self._captures_count = 0
        self._analyses_count = 0
        self._analysis_running = False
        self._manual_task: asyncio.Task[None] | None = None

    def start(self) -> WindowWatchStatus:
        if self._task is None or self._task.done():
            get_runtime_log_service().info(
                "window_watcher",
                "start",
                "Window watcher loop started.",
                interval_seconds=self.interval_seconds,
            )
            self._task = asyncio.create_task(self._run(), name="local-window-watcher")
        return self.status()

    async def stop(self) -> WindowWatchStatus:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        return self.status()

    async def pause(self, *, reason: str = "window-watch-paused") -> WindowWatchStatus:
        status = await self.stop()
        state_service = self.state_service or get_assistant_state_service()
        await state_service.set_state("idle", reason=reason)
        return status

    def request_observe_once(self, *, resume_after: bool = True) -> WindowWatchStatus:
        if self._manual_task is None or self._manual_task.done():
            self._manual_task = asyncio.create_task(
                self._observe_once_background(resume_after=resume_after),
                name="local-window-manual-observe",
            )
        return self.status()

    async def _observe_once_background(self, *, resume_after: bool) -> None:
        try:
            await self.observe_once_now(resume_after=resume_after)
        except Exception:
            # observe_once_now already records state/error; background callers read status.
            pass

    async def observe_once_now(self, *, resume_after: bool = False) -> WindowAnalysisResult | None:
        await self.stop()
        try:
            return await self._observe_once(manual=True)
        finally:
            if resume_after and get_settings().auto_start_window_watch:
                self.start()

    def status(self) -> WindowWatchStatus:
        return WindowWatchStatus(
            running=self._task is not None and not self._task.done(),
            interval_seconds=self.interval_seconds,
            capture_min_interval_seconds=self.capture_min_interval_seconds,
            analysis_min_interval_seconds=self.analysis_min_interval_seconds,
            last_capture=self._last_capture,
            last_analysis=self._last_analysis,
            last_error=self._last_error,
            captures_count=self._captures_count,
            analyses_count=self._analyses_count,
        )

    @staticmethod
    def _capture_fields(capture: RawWindowCapture) -> dict[str, Any]:
        return {
            "app_name": capture.app_name,
            "window_title": capture.window_title,
            "process_id": capture.process_id,
            "screenshot_path": str(capture.screenshot_path),
            "screenshot_hash": capture.screenshot_hash,
            "captured_at": capture.captured_at.isoformat(),
        }

    async def _observe_once(self, *, manual: bool) -> WindowAnalysisResult | None:
        state_service = self.state_service or get_assistant_state_service()
        log = get_runtime_log_service()
        prefix = "manual" if manual else "auto"
        if self._analysis_running:
            self._last_error = "Window analysis is already running."
            log.error(
                "window_watcher",
                f"{prefix}_analysis_rejected",
                self._last_error,
            )
            raise RuntimeError(self._last_error)

        try:
            await state_service.set_state("observing", reason=f"window-watch-{prefix}-capture-started")
            log.info("window_watcher", f"{prefix}_capture_start", "Window capture started.")
            capture = await asyncio.to_thread(self.capture_service.capture_foreground_window)
            self._record_capture(capture)
            log.info(
                "window_watcher",
                f"{prefix}_capture_success",
                "Window capture completed.",
                **self._capture_fields(capture),
            )

            if self.analysis_service is None:
                await state_service.set_state("idle", reason=f"window-watch-{prefix}-capture-finished")
                return None

            self._analysis_running = True
            await state_service.set_state("analyzing", reason=f"window-watch-{prefix}-analysis-started")
            log.info(
                "window_watcher",
                f"{prefix}_analysis_start",
                "Window analysis started.",
                **self._capture_fields(capture),
            )
            result = await asyncio.to_thread(self.analysis_service.analyze_capture, capture)
            self._record_analysis(result)
            log.info(
                "window_watcher",
                f"{prefix}_analysis_success",
                "Window analysis completed.",
                **self._capture_fields(capture),
                window_type=result.analysis.window_type,
                summary=result.analysis.summary,
            )
            await state_service.set_state("idle", reason=f"window-watch-{prefix}-analysis-finished")
            return result
        except Exception as exc:
            self._last_error = str(exc)
            log.exception(
                "window_watcher",
                f"{prefix}_failure",
                "Window watcher step failed.",
                exc,
                last_capture_hash=self._last_capture_hash,
            )
            await state_service.set_state(
                "error",
                reason=f"window-watch-{prefix}-failed",
                error=str(exc),
            )
            raise
        finally:
            self._analysis_running = False

    def _record_capture(self, capture: RawWindowCapture) -> None:
        self._last_capture_monotonic = time.monotonic()
        self._last_error = None
        self._last_capture = capture
        self._last_capture_hash = capture.screenshot_hash
        self._last_window_signature = (
            capture.process_id,
            capture.app_name,
            capture.window_title,
            capture.window_bounds.left,
            capture.window_bounds.top,
            capture.window_bounds.right,
            capture.window_bounds.bottom,
        )
        self._captures_count += 1

    def _record_analysis(self, result: WindowAnalysisResult) -> None:
        self._last_analysis_monotonic = time.monotonic()
        self._last_analysis = result.model_dump(mode="json")
        self._analyses_count += 1

    async def tick(self) -> None:
        state_service = self.state_service or get_assistant_state_service()
        info = await asyncio.to_thread(self.capture_service.get_foreground_window_info)
        signature = info.signature()
        signature_changed = signature != self._last_window_signature
        enough_capture_time_passed = (
            time.monotonic() - self._last_capture_monotonic
            >= self.capture_min_interval_seconds
        )
        if not signature_changed and not enough_capture_time_passed:
            return

        await state_service.set_state("observing", reason="window-watch-capture-started")
        capture = await asyncio.to_thread(self.capture_service.capture_window_info, info)
        self._last_window_signature = signature
        self._last_capture_monotonic = time.monotonic()
        self._last_error = None

        if capture.screenshot_hash == self._last_capture_hash:
            await state_service.set_state("idle", reason="window-watch-capture-finished")
            return

        self._last_capture = capture
        self._last_capture_hash = capture.screenshot_hash
        self._captures_count += 1

        enough_analysis_time_passed = (
            time.monotonic() - self._last_analysis_monotonic
            >= self.analysis_min_interval_seconds
        )
        if (
            self.analysis_service is None
            or self._analysis_running
            or (not signature_changed and not enough_analysis_time_passed)
        ):
            await state_service.set_state("idle", reason="window-watch-capture-finished")
            return

        self._analysis_running = True
        log = get_runtime_log_service()
        try:
            await state_service.set_state("analyzing", reason="window-watch-analysis-started")
            log.info(
                "window_watcher",
                "auto_analysis_start",
                "Window analysis started.",
                **self._capture_fields(capture),
            )
            result = await asyncio.to_thread(self.analysis_service.analyze_capture, capture)
            self._record_analysis(result)
            log.info(
                "window_watcher",
                "auto_analysis_success",
                "Window analysis completed.",
                **self._capture_fields(capture),
                window_type=result.analysis.window_type,
                summary=result.analysis.summary,
            )
            await state_service.set_state("idle", reason="window-watch-analysis-finished")
        except Exception as exc:
            self._last_analysis_monotonic = time.monotonic()
            self._last_error = str(exc)
            log.exception(
                "window_watcher",
                "auto_analysis_failure",
                "Window analysis failed.",
                exc,
                **self._capture_fields(capture),
            )
            await state_service.set_state(
                "error",
                reason="window-watch-failed",
                error=str(exc),
            )
            raise
        finally:
            self._analysis_running = False

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                state_service = self.state_service or get_assistant_state_service()
                try:
                    await state_service.set_state(
                        "error",
                        reason="window-watch-failed",
                        error=str(exc),
                    )
                except Exception as state_exc:
                    self._last_error = (
                        f"{self._last_error}; state update failed: {state_exc}"
                    )

            await asyncio.sleep(self.interval_seconds)


@lru_cache
def get_window_watcher_service() -> WindowWatcherService:
    settings = get_settings()
    return WindowWatcherService(
        capture_service=get_window_capture_service(),
        analysis_service=get_window_analysis_service(),
        interval_seconds=settings.window_watch_interval_seconds,
        capture_min_interval_seconds=settings.window_capture_min_interval_seconds,
        analysis_min_interval_seconds=settings.window_analysis_min_interval_seconds,
    )