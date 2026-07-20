from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from ...browser.controller import BrowserController
from ...browser.native_dialog import (
    ShareDialogController,
    ShareDialogError,
    ShareDialogResult,
    create_share_dialog_controller,
)
from ...browser.page_driver import PageDriver
from ...config import BrowserConfig
from ...schemas import MeetingState
from ..selectors import MEET_SELECTORS


@dataclass
class BrowserRecoveryEvent:
    action: str
    attempt: int
    recovered: bool
    error: str
    page_url: str
    screenshot_path: str | None = None


@dataclass
class SpeechRouteTimingEvent:
    type: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    cache_status: str = "miss"
    selected_device: str | None = None
    error: str | None = None


@dataclass
class GoogleMeetAdapter:
    browser: BrowserController
    config: BrowserConfig
    microphone_device_name: str = "BlackHole 2ch"
    state: MeetingState = MeetingState.IDLE
    current_url: str | None = None
    meet_page: PageDriver | None = None
    presentation_page: PageDriver | None = None
    presenting: bool = False
    muted: bool = True
    camera_enabled: bool = False
    recovery_events: list[BrowserRecoveryEvent] | None = None
    share_dialog: ShareDialogController | None = None
    share_dialog_result: ShareDialogResult | None = None
    presentation_evidence_path: str | None = None
    selected_microphone_device: str | None = None
    speech_route_events: list[SpeechRouteTimingEvent] | None = None
    speech_route_ready: bool = False
    speech_route_device_name: str | None = None

    def __post_init__(self) -> None:
        if self.share_dialog is None:
            self.share_dialog = create_share_dialog_controller(self.config)

    async def navigate(self, meeting_url: str) -> None:
        parsed = urlparse(meeting_url)
        if parsed.hostname not in set(self.config.allowed_meet_hosts):
            raise ValueError("Only Google Meet URLs are supported.")
        self.current_url = meeting_url
        self.selected_microphone_device = None
        self.invalidate_speech_route()
        recovery_count = self.browser.recovery_count
        self.meet_page = await self.browser.open_page("meet", meeting_url)
        if self.browser.recovery_count > recovery_count:
            self._record_recovery(
                "cdp_reconnect",
                self.browser.recovery_count,
                recovered=True,
                error=self.browser.last_recovery_reason or "Reconnected to Robin Chrome.",
                page=self.meet_page,
            )
        self.state = MeetingState.PREJOIN

    async def enter_prejoin(self) -> None:
        page = self._page()
        if not await page.is_visible(MEET_SELECTORS["join_button"], self.config.prejoin_timeout_ms):
            screenshot_path = await self._capture_recovery_screenshot("prejoin_controls", 1, page)
            self._record_recovery(
                "prejoin_controls",
                1,
                recovered=False,
                error="Google Meet prejoin controls did not appear.",
                page=page,
                screenshot_path=screenshot_path,
            )
            raise TimeoutError("Google Meet prejoin controls did not appear.")
        await self._turn_off_prejoin_media()
        self.state = MeetingState.PREJOIN

    async def _turn_off_prejoin_media(self) -> None:
        page = self._page()
        if await page.is_visible(MEET_SELECTORS["prejoin_mute_button"], 1_000):
            await self._click_with_recovery(
                "prejoin_mute_button", MEET_SELECTORS["prejoin_mute_button"], 3_000
            )
        if await page.is_visible(MEET_SELECTORS["prejoin_camera_button"], 1_000):
            await self._click_with_recovery(
                "prejoin_camera_button", MEET_SELECTORS["prejoin_camera_button"], 3_000
            )
        self.muted = True
        self.camera_enabled = False

    async def join(self) -> None:
        if not self.current_url:
            raise ValueError("No meeting URL has been supplied.")
        if await self._is_admitted():
            await self.camera_off()
            await self.mute()
            await self.ensure_microphone_device()
            await self.enable_captions()
            self.state = MeetingState.LISTENING
            return
        await self.enter_prejoin()
        await self.camera_off()
        await self.mute()
        await self.ensure_microphone_device()
        await self._click_with_recovery(
            "join_button", MEET_SELECTORS["join_button"], self.config.prejoin_timeout_ms
        )
        self.state = MeetingState.REQUESTING_ADMISSION
        await self._wait_for_admission()
        await self.ensure_microphone_device()
        await self.enable_captions()
        self.state = MeetingState.LISTENING

    async def enable_captions(self) -> None:
        if not self.config.captions_enabled or not self.meet_page:
            return
        if await self.meet_page.is_visible(MEET_SELECTORS["disable_captions_button"], 500):
            return
        if await self.meet_page.is_visible(MEET_SELECTORS["enable_captions_button"], 500):
            await self._click_with_recovery(
                "enable_captions_button",
                MEET_SELECTORS["enable_captions_button"],
                2_000,
            )

    async def recent_captions(self):
        return await self._page().read_captions()

    async def _wait_for_admission(self) -> None:
        deadline = time.monotonic() + self.config.admission_timeout_ms / 1000
        last_text = ""
        pending_seen = False
        recovery_attempts = 0
        while time.monotonic() < deadline:
            try:
                snapshot = await asyncio.wait_for(self._page().inspect(), timeout=2.0)
            except TimeoutError:
                last_text = "Meet page inspection timed out while waiting for admission."
                await asyncio.sleep(0.1)
                continue
            except Exception as exc:
                last_text = f"Meet page inspection failed while waiting for admission: {exc}"
                if recovery_attempts >= max(self.config.ui_action_retries, 0):
                    raise RuntimeError(last_text) from exc
                recovery_attempts += 1
                admitted = await self._recover_admission_target(recovery_attempts, exc)
                if admitted:
                    return
                continue
            last_text = " ".join(snapshot.text.casefold().split())
            if self._admission_rejected(last_text):
                raise PermissionError("Google Meet rejected admission: " + snapshot.text[:300])
            if self._admission_pending(last_text):
                pending_seen = True
            elif await self._is_admitted():
                if pending_seen:
                    self._record_recovery(
                        "admission_wait",
                        1,
                        recovered=True,
                        error="Robin was admitted after waiting for the host.",
                        page=self._page(),
                    )
                return
            await asyncio.sleep(min(self.config.ui_recovery_pause_ms / 1000, 0.5) or 0.1)
        screenshot_path = await self._capture_recovery_screenshot(
            "admission_timeout", 1, self._page()
        )
        detail = f" Last page text: {last_text[:240]}" if last_text else ""
        self._record_recovery(
            "admission_wait",
            1,
            recovered=False,
            error="Robin was not admitted before the configured deadline." + detail,
            page=self._page(),
            screenshot_path=screenshot_path,
        )
        raise TimeoutError("Robin was not admitted to the meeting before the timeout." + detail)

    async def _recover_admission_target(self, attempt: int, error: Exception) -> bool:
        stale_page = self._page()
        screenshot_path = await self._capture_recovery_screenshot(
            "admission_target", attempt, stale_page
        )
        self._record_recovery(
            "admission_target",
            attempt,
            recovered=False,
            error=str(error),
            page=stale_page,
            screenshot_path=screenshot_path,
        )
        if not self.current_url:
            raise RuntimeError("Cannot recover admission without a meeting URL")
        self.invalidate_speech_route()
        self.meet_page = await self.browser.open_page("meet", self.current_url)
        if await self._is_admitted():
            self._record_recovery(
                "admission_target",
                attempt,
                recovered=True,
                error=str(error),
                page=self.meet_page,
            )
            return True
        snapshot = await self.meet_page.inspect()
        text = " ".join(snapshot.text.casefold().split())
        if not self._admission_pending(text):
            await self.enter_prejoin()
            await self.camera_off()
            await self.mute()
            await self._click_with_recovery(
                "join_button",
                MEET_SELECTORS["join_button"],
                self.config.prejoin_timeout_ms,
            )
            self.state = MeetingState.REQUESTING_ADMISSION
        self._record_recovery(
            "admission_target",
            attempt,
            recovered=True,
            error=str(error),
            page=self.meet_page,
        )
        return False

    async def _is_admitted(self) -> bool:
        page = self._page()
        snapshot = await page.inspect()
        text = " ".join(snapshot.text.casefold().split())
        if self._admission_pending(text) or self._admission_rejected(text):
            return False
        joined = await page.is_visible(MEET_SELECTORS["joined_signal"], 500)
        own_microphone = await page.is_visible(
            MEET_SELECTORS["mute_button"], 250
        ) or await page.is_visible(MEET_SELECTORS["unmute_button"], 250)
        in_call_control = await page.is_visible(MEET_SELECTORS["in_call_signal"], 500)
        return joined and own_microphone and in_call_control

    @staticmethod
    def _admission_pending(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                "please wait until a meeting host brings you into the call",
                "asking to join",
                "waiting for the host",
                "someone in the meeting should let you in soon",
            )
        )

    @staticmethod
    def _admission_rejected(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                "you can't join this video call",
                "you cannot join this video call",
                "your request to join was denied",
                "no one can join a meeting unless invited or admitted",
                "meeting code has expired",
            )
        )

    async def leave(self) -> None:
        if self.meet_page and self.state not in {MeetingState.IDLE, MeetingState.ENDED}:
            try:
                await self._click_with_recovery(
                    "leave_button", MEET_SELECTORS["leave_button"], 3_000
                )
            except Exception:
                pass
        await self.browser.close_page("meet")
        self.meet_page = None
        self.invalidate_speech_route()
        self.state = MeetingState.ENDED
        self.presenting = False

    async def mute(self) -> None:
        try:
            if self.meet_page:
                await self.meet_page.set_microphone_muted(True, 3_000)
            self.muted = True
        except Exception:
            self.invalidate_speech_route()
            raise

    async def unmute(self) -> None:
        if self.meet_page:
            selected = await self.prepare_speech_route()
            unmute_started_at = datetime.now(timezone.utc)
            unmute_started = time.perf_counter()
            self._record_speech_route_event(
                "speech.unmute.started",
                unmute_started_at,
                unmute_started,
                selected_device=selected,
            )
            unmute_error = None
            try:
                await self.meet_page.set_microphone_muted(False, 3_000)
                await asyncio.sleep(max(self.config.microphone_settle_ms, 0) / 1000)
                self.muted = False
            except Exception as exc:
                unmute_error = str(exc)
                self.invalidate_speech_route()
                raise
            finally:
                self._record_speech_route_event(
                    "speech.unmute.completed",
                    unmute_started_at,
                    unmute_started,
                    selected_device=selected,
                    error=unmute_error,
                )
        else:
            self.muted = False

    async def prepare_speech_route(self, force: bool = False) -> str:
        if (
            not force
            and self.speech_route_ready
            and self.selected_microphone_device
            and self.speech_route_device_name == self.microphone_device_name
        ):
            started_at = datetime.now(timezone.utc)
            started = time.perf_counter()
            self._record_speech_route_event(
                "speech.route_prepare.started",
                started_at,
                started,
                selected_device=self.selected_microphone_device,
                cache_status="hit",
            )
            self._record_speech_route_event(
                "speech.route_prepare.completed",
                started_at,
                started,
                selected_device=self.selected_microphone_device,
                cache_status="hit",
            )
            return self.selected_microphone_device
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        self._record_speech_route_event(
            "speech.route_prepare.started",
            started_at,
            started,
            cache_status="refresh" if force else "miss",
        )
        selected = None
        route_error = None
        try:
            selected = await self.ensure_microphone_device()
            self.selected_microphone_device = selected
            self.speech_route_device_name = self.microphone_device_name
            self.speech_route_ready = True
            return selected
        except Exception as exc:
            route_error = str(exc)
            self.invalidate_speech_route()
            raise
        finally:
            self._record_speech_route_event(
                "speech.route_prepare.completed",
                started_at,
                started,
                selected_device=selected,
                cache_status="refresh" if force else "miss",
                error=route_error,
            )

    def invalidate_speech_route(self) -> None:
        self.speech_route_ready = False
        self.speech_route_device_name = None

    async def ensure_microphone_device(self) -> str:
        page = self._page()
        try:
            selected = await page.ensure_microphone_device(
                self.microphone_device_name,
                min(max(self.config.prejoin_timeout_ms, 2_000), 10_000),
            )
        except Exception as exc:
            screenshot_path = await self._capture_recovery_screenshot("microphone_device", 1, page)
            self._record_recovery(
                "microphone_device",
                1,
                recovered=False,
                error=str(exc),
                page=page,
                screenshot_path=screenshot_path,
            )
            raise
        self.selected_microphone_device = selected
        return selected

    def _record_speech_route_event(
        self,
        event_type: str,
        started_at: datetime,
        started: float,
        *,
        selected_device: str | None = None,
        cache_status: str = "miss",
        error: str | None = None,
    ) -> None:
        if self.speech_route_events is None:
            self.speech_route_events = []
        self.speech_route_events.append(
            SpeechRouteTimingEvent(
                type=event_type,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                duration_ms=int((time.perf_counter() - started) * 1000),
                cache_status=cache_status,
                selected_device=selected_device,
                error=error,
            )
        )

    async def camera_off(self) -> None:
        self.camera_enabled = False

    async def raise_hand(self) -> None:
        page = self._page()
        if await self.is_hand_raised():
            return
        if not await page.is_visible(MEET_SELECTORS["raise_hand_button"], 750):
            if await page.is_visible(MEET_SELECTORS["reactions_button"], 750):
                await self._click_with_recovery(
                    "reactions_button", MEET_SELECTORS["reactions_button"], 2_000
                )
        await self._click_with_recovery(
            "raise_hand_button", MEET_SELECTORS["raise_hand_button"], 3_000
        )
        if not await page.is_visible(MEET_SELECTORS["hand_raised_signal"], 3_000):
            raise TimeoutError("Meet did not confirm Robin's raised hand.")

    async def lower_hand(self) -> None:
        page = self._page()
        if not await self.is_hand_raised():
            return
        await self._click_with_recovery(
            "lower_hand_button", MEET_SELECTORS["lower_hand_button"], 3_000
        )
        if await page.is_visible(MEET_SELECTORS["hand_raised_signal"], 1_500):
            raise TimeoutError("Meet did not confirm Robin's hand was lowered.")

    async def is_hand_raised(self) -> bool:
        return await self._page().is_visible(MEET_SELECTORS["hand_raised_signal"], 500)

    async def start_presenting(self, url: str) -> None:
        if not url:
            raise ValueError("Presentation URL is required.")
        self.presentation_page = await self.browser.open_page("presentation", url)
        expected_task_id, expected_revision = self._presentation_identity(url)
        try:
            readiness = await self.presentation_page.wait_for_presentation_ready(
                expected_task_id,
                expected_revision,
                self.config.navigation_timeout_ms,
            )
        except Exception as exc:
            screenshot_path = await self._capture_recovery_screenshot(
                "presentation_not_ready", 1, self.presentation_page
            )
            self._record_recovery(
                "presentation_ready",
                1,
                recovered=False,
                error=str(exc),
                page=self.presentation_page,
                screenshot_path=screenshot_path,
            )
            raise
        self.presentation_evidence_path = await self._capture_recovery_screenshot(
            "presentation_ready", 1, self.presentation_page
        )
        self._record_recovery(
            "presentation_ready",
            1,
            recovered=True,
            error=(
                f"task={readiness.task_id} revision={readiness.revision or 'active'} "
                "renderer ready and no error banner visible"
            ),
            page=self.presentation_page,
            screenshot_path=self.presentation_evidence_path,
        )
        if self.meet_page:
            await self.browser.bring_to_front("meet")
            await self._click_with_recovery(
                "present_button", MEET_SELECTORS["present_button"], 5_000
            )
            if await self.meet_page.is_visible(MEET_SELECTORS["share_tab_option"], 1_500):
                await self._click_with_recovery(
                    "share_tab_option", MEET_SELECTORS["share_tab_option"], 3_000
                )
            try:
                if self.share_dialog is None:
                    raise RuntimeError("Chrome share-dialog controller is unavailable")
                self.share_dialog_result = await self.share_dialog.select_and_share(
                    self.config.share_source_title
                )
                self._record_share_dialog_events(self.share_dialog_result.events)
            except ShareDialogError as exc:
                self._record_share_dialog_events(exc.events)
                raise
            if (
                not self.share_dialog_result.source_selected
                or not self.share_dialog_result.picker_closed
            ):
                raise RuntimeError(
                    "Chrome share picker did not confirm the Robin presentation source"
                )
            if not await self.meet_page.is_visible(MEET_SELECTORS["presenting_signal"], 10_000):
                raise TimeoutError(
                    "Meet did not confirm that Robin is presenting after the picker closed"
                )
        self.presenting = True
        self.state = MeetingState.PRESENTING

    async def stop_presenting(self) -> None:
        if self.meet_page:
            try:
                await self._click_with_recovery(
                    "stop_presenting_button", MEET_SELECTORS["stop_presenting_button"], 3_000
                )
            except Exception:
                pass
        self.presenting = False
        self.state = MeetingState.LISTENING

    async def _click_with_recovery(self, action: str, candidates, timeout_ms: int) -> str:
        page = self._page()
        attempts = max(self.config.ui_action_retries, 0) + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = await page.click_first(candidates, timeout_ms)
                if attempt > 1:
                    self._record_recovery(
                        action, attempt, recovered=True, error=str(last_error or ""), page=page
                    )
                return result
            except Exception as exc:
                last_error = exc
                screenshot_path = await self._capture_recovery_screenshot(action, attempt, page)
                self._record_recovery(
                    action,
                    attempt,
                    recovered=False,
                    error=str(exc),
                    page=page,
                    screenshot_path=screenshot_path,
                )
                if attempt >= attempts:
                    raise
                await page.bring_to_front()
                await asyncio.sleep(max(self.config.ui_recovery_pause_ms, 0) / 1000)
        raise TimeoutError(f"{action} did not recover: {last_error}")

    async def _capture_recovery_screenshot(
        self, action: str, attempt: int, page: PageDriver
    ) -> str | None:
        try:
            self.config.recovery_screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path = (
                self.config.recovery_screenshot_dir / f"{timestamp}_{action}_attempt{attempt}.png"
            )
            path.write_bytes(await page.screenshot())
            return str(path)
        except Exception:
            return None

    def _record_recovery(
        self,
        action: str,
        attempt: int,
        recovered: bool,
        error: str,
        page: PageDriver,
        screenshot_path: str | None = None,
    ) -> None:
        if self.recovery_events is None:
            self.recovery_events = []
        self.recovery_events.append(
            BrowserRecoveryEvent(
                action=action,
                attempt=attempt,
                recovered=recovered,
                error=error,
                page_url=page.url,
                screenshot_path=screenshot_path,
            )
        )

    def _record_share_dialog_events(self, events) -> None:
        page = self._page()
        for event in events:
            self._record_recovery(
                action=f"share_dialog.{event.action}",
                attempt=event.attempt,
                recovered=event.ok,
                error="" if event.ok else event.detail,
                page=page,
                screenshot_path=event.screenshot_path,
            )

    @staticmethod
    def _presentation_identity(url: str) -> tuple[str, str | None]:
        parsed = urlparse(url)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) < 2 or segments[-2] != "present":
            raise ValueError("Presentation URL must end with /present/{task_id}.")
        revision = parse_qs(parsed.query).get("revision", [None])[0]
        return segments[-1], revision

    def _page(self) -> PageDriver:
        if not self.meet_page:
            raise ValueError("Meet page is not initialized.")
        return self.meet_page
