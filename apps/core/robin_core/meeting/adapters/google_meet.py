from __future__ import annotations

import asyncio
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
class GoogleMeetAdapter:
    browser: BrowserController
    config: BrowserConfig
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

    def __post_init__(self) -> None:
        if self.share_dialog is None:
            self.share_dialog = create_share_dialog_controller(self.config)

    async def navigate(self, meeting_url: str) -> None:
        parsed = urlparse(meeting_url)
        if parsed.hostname not in set(self.config.allowed_meet_hosts):
            raise ValueError("Only Google Meet URLs are supported.")
        self.current_url = meeting_url
        self.meet_page = await self.browser.open_page("meet", meeting_url)
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
            await self._click_with_recovery("prejoin_mute_button", MEET_SELECTORS["prejoin_mute_button"], 3_000)
        if await page.is_visible(MEET_SELECTORS["prejoin_camera_button"], 1_000):
            await self._click_with_recovery("prejoin_camera_button", MEET_SELECTORS["prejoin_camera_button"], 3_000)
        self.muted = True
        self.camera_enabled = False

    async def join(self) -> None:
        if not self.current_url:
            raise ValueError("No meeting URL has been supplied.")
        if await self._page().is_visible(MEET_SELECTORS["joined_signal"], 1_000):
            await self.camera_off()
            await self.mute()
            self.state = MeetingState.LISTENING
            return
        await self.enter_prejoin()
        await self.camera_off()
        await self.mute()
        await self._click_with_recovery(
            "join_button", MEET_SELECTORS["join_button"], self.config.prejoin_timeout_ms
        )
        if not await self._page().is_visible(
            MEET_SELECTORS["joined_signal"], self.config.admission_timeout_ms
        ):
            raise TimeoutError("Robin was not admitted to the meeting before the timeout.")
        self.state = MeetingState.LISTENING

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
        self.state = MeetingState.ENDED
        self.presenting = False

    async def mute(self) -> None:
        if self.meet_page:
            if await self.meet_page.is_visible(MEET_SELECTORS["mute_button"], 750):
                await self._click_with_recovery(
                    "mute_button", MEET_SELECTORS["mute_button"], 3_000
                )
            elif not await self.meet_page.is_visible(
                MEET_SELECTORS["unmute_button"], 750
            ):
                raise RuntimeError("Meet microphone control is unavailable; could not mute Robin.")
        self.muted = True

    async def unmute(self) -> None:
        if self.meet_page:
            if await self.meet_page.is_visible(MEET_SELECTORS["unmute_button"], 750):
                await self._click_with_recovery(
                    "unmute_button", MEET_SELECTORS["unmute_button"], 3_000
                )
            elif not await self.meet_page.is_visible(MEET_SELECTORS["mute_button"], 750):
                raise RuntimeError(
                    "Meet microphone control is unavailable; could not unmute Robin."
                )
        self.muted = False

    async def camera_off(self) -> None:
        self.camera_enabled = False

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
