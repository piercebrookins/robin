from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from robin_core.browser.controller import BrowserController
from robin_core.browser.native_dialog import ShareDialogError, ShareDialogEvent
from robin_core.browser.page_driver import PlaywrightPageDriver, SimulatedPageDriver
from robin_core.config import BrowserConfig
from robin_core.meeting.adapters.google_meet import GoogleMeetAdapter
from robin_core.meeting.selectors import MEET_SELECTORS, SelectorCandidate
from robin_core.schemas import MeetingState


@pytest.mark.asyncio
async def test_google_meet_simulator_join_flow_clicks_prejoin_controls() -> None:
    config = BrowserConfig(automation_mode="simulator")
    browser = BrowserController(config)
    adapter = GoogleMeetAdapter(browser, config)

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()

    assert adapter.state == MeetingState.LISTENING
    assert adapter.muted is True
    assert adapter.camera_enabled is False
    assert adapter.meet_page is not None
    assert "join_button" in adapter.meet_page.clicked
    assert "prejoin_mute_button" in adapter.meet_page.clicked
    assert "prejoin_camera_button" in adapter.meet_page.clicked


@pytest.mark.asyncio
async def test_google_meet_microphone_actions_follow_visible_control_state() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)

    await adapter.unmute()
    clicks_after_unmute = page.clicked.count("unmute_button")
    await adapter.unmute()
    await adapter.mute()

    assert clicks_after_unmute == 1
    assert page.clicked.count("unmute_button") == 1
    assert page.clicked.count("mute_button") == 1
    assert adapter.muted is True


@pytest.mark.asyncio
async def test_google_meet_join_recovers_an_existing_joined_tab() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.visible_keys.discard("join_button")
    page.visible_keys.add("leave_button")
    page.visible_keys.add("mute_button")

    await adapter.join()

    assert adapter.state == MeetingState.LISTENING
    assert "join_button" not in page.clicked
    assert "mute_button" in page.clicked


@pytest.mark.asyncio
async def test_google_meet_waiting_room_leave_button_is_not_admission() -> None:
    config = BrowserConfig(automation_mode="simulator", admission_timeout_ms=20)
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.visible_keys.add("leave_button")

    async def waiting_snapshot():
        snapshot = await SimulatedPageDriver.inspect(page)
        return type(snapshot)(
            url=snapshot.url,
            title=snapshot.title,
            text="Please wait until a meeting host brings you into the call",
            elements=snapshot.elements,
        )

    page.inspect = waiting_snapshot  # type: ignore[method-assign]

    with pytest.raises(TimeoutError, match="not admitted"):
        await adapter.join()

    assert adapter.state != MeetingState.LISTENING


@pytest.mark.asyncio
async def test_google_meet_rejected_admission_fails_immediately() -> None:
    config = BrowserConfig(automation_mode="simulator", admission_timeout_ms=1_000)
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)

    async def rejected_snapshot():
        snapshot = await SimulatedPageDriver.inspect(page)
        return type(snapshot)(
            url=snapshot.url,
            title=snapshot.title,
            text="You can't join this video call. No one can join a meeting unless invited or admitted by the host.",
            elements=snapshot.elements,
        )

    page.inspect = rejected_snapshot  # type: ignore[method-assign]

    with pytest.raises(PermissionError, match="rejected admission"):
        await adapter.join()


@pytest.mark.asyncio
async def test_google_meet_leave_closes_the_controlled_meet_tab() -> None:
    config = BrowserConfig(automation_mode="simulator")
    browser = BrowserController(config)
    adapter = GoogleMeetAdapter(browser, config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()

    await adapter.leave()

    assert adapter.state == MeetingState.ENDED
    assert adapter.meet_page is None
    assert "meet" not in browser.pages


def test_microphone_selectors_do_not_match_participant_mute_controls() -> None:
    own_unmute = MEET_SELECTORS["unmute_button"][0].name_regex
    own_mute = MEET_SELECTORS["mute_button"][0].name_regex

    assert own_unmute is not None and own_mute is not None
    assert re.search(own_unmute, "Turn on microphone (⌘ + d)", re.I)
    assert re.search(own_mute, "Turn off microphone (⌘ + d)", re.I)
    assert not re.search(own_unmute, "Mute Pierce Brookins for everyone", re.I)
    assert not re.search(own_mute, "Mute Pierce Brookins for everyone", re.I)


@pytest.mark.asyncio
async def test_playwright_driver_clicks_visible_duplicate_selector_match() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <button aria-label="Stop presenting" style="display:none">Hidden</button>
            <button aria-label="Stop presenting" onclick="this.dataset.clicked='yes'">Visible</button>
            """
        )
        driver = PlaywrightPageDriver(page)

        await driver.click_first(MEET_SELECTORS["stop_presenting_button"], 1_000)

        assert await page.locator("button:visible").get_attribute("data-clicked") == "yes"
        await browser.close()


@pytest.mark.asyncio
async def test_browser_controller_closes_stale_duplicate_presentation_tabs() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("http://example.test/**", lambda route: route.fulfill(body="ok"))
        dashboard = await context.new_page()
        await dashboard.goto("http://example.test/")
        old_deck = await context.new_page()
        await old_deck.goto("http://example.test/present/old?revision=1")
        current_deck = await context.new_page()
        await current_deck.goto("http://example.test/present/current?revision=1")
        duplicate_current = await context.new_page()
        await duplicate_current.goto("http://example.test/present/current?revision=1")
        controller = BrowserController(BrowserConfig(automation_mode="playwright"))
        controller._context = context

        await controller._close_stale_presentation_pages(
            "http://example.test/present/current?revision=1"
        )

        remaining = [page.url for page in context.pages]
        assert remaining == [
            "http://example.test/",
            "http://example.test/present/current?revision=1",
        ]
        await browser.close()


@pytest.mark.asyncio
async def test_google_meet_simulator_present_flow_opens_presentation_page(
    tmp_path: Path,
) -> None:
    config = BrowserConfig(
        automation_mode="simulator",
        recovery_screenshot_dir=tmp_path / "recovery",
    )
    browser = BrowserController(config)
    adapter = GoogleMeetAdapter(browser, config)

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    await adapter.start_presenting("http://127.0.0.1:3000/present/task-1")

    assert adapter.state == MeetingState.PRESENTING
    assert adapter.presenting is True
    assert browser.pages["presentation"].url == "http://127.0.0.1:3000/present/task-1"
    assert adapter.meet_page is not None
    assert "present_button" in adapter.meet_page.clicked
    assert "share_tab_option" in adapter.meet_page.clicked
    assert adapter.share_dialog_result is not None
    assert adapter.share_dialog_result.picker_closed is True
    assert adapter.presentation_evidence_path is not None
    assert Path(adapter.presentation_evidence_path).exists()
    assert adapter.recovery_events is not None
    readiness = next(
        event for event in adapter.recovery_events if event.action == "presentation_ready"
    )
    assert readiness.recovered is True
    assert "task=task-1" in readiness.error


@pytest.mark.asyncio
async def test_google_meet_refuses_to_share_mismatched_presentation(tmp_path: Path) -> None:
    config = BrowserConfig(
        automation_mode="simulator",
        recovery_screenshot_dir=tmp_path / "recovery",
    )
    browser = MismatchedPresentationBrowser(config)
    share_dialog = TrackingShareDialog()
    adapter = GoogleMeetAdapter(browser, config, share_dialog=share_dialog)

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    with pytest.raises(RuntimeError, match="Presentation task mismatch"):
        await adapter.start_presenting("http://127.0.0.1:3000/present/task-1?revision=1")

    assert share_dialog.called is False
    assert adapter.presenting is False
    assert adapter.meet_page is not None
    assert "present_button" not in adapter.meet_page.clicked
    assert adapter.recovery_events is not None
    readiness = adapter.recovery_events[-1]
    assert readiness.action == "presentation_ready"
    assert readiness.recovered is False
    assert readiness.screenshot_path is not None
    assert Path(readiness.screenshot_path).exists()


@pytest.mark.asyncio
async def test_google_meet_does_not_claim_presenting_when_native_picker_fails(
    tmp_path: Path,
) -> None:
    config = BrowserConfig(
        automation_mode="simulator",
        recovery_screenshot_dir=tmp_path / "recovery",
    )
    browser = BrowserController(config)
    adapter = GoogleMeetAdapter(browser, config, share_dialog=FailingShareDialog())

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    with pytest.raises(ShareDialogError, match="picker failed"):
        await adapter.start_presenting("http://127.0.0.1:3000/present/task-1")

    assert adapter.presenting is False
    assert adapter.state == MeetingState.LISTENING
    assert adapter.recovery_events is not None
    assert adapter.recovery_events[-1].action == "share_dialog.attempt_failed"


@pytest.mark.asyncio
async def test_google_meet_recovers_from_transient_join_click_failure(tmp_path: Path) -> None:
    config = BrowserConfig(
        automation_mode="simulator",
        recovery_screenshot_dir=tmp_path / "recovery",
        ui_action_retries=1,
        ui_recovery_pause_ms=0,
    )
    browser = FaultyBrowserController(config, {"join_button": 1})
    adapter = GoogleMeetAdapter(browser, config)

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()

    assert adapter.state == MeetingState.LISTENING
    assert adapter.recovery_events is not None
    assert [event.recovered for event in adapter.recovery_events] == [False, True]
    failed = adapter.recovery_events[0]
    assert failed.action == "join_button"
    assert failed.screenshot_path is not None
    assert Path(failed.screenshot_path).exists()
    assert "join_button" in adapter.meet_page.clicked


@pytest.mark.asyncio
async def test_google_meet_recovers_from_transient_present_click_failure(tmp_path: Path) -> None:
    config = BrowserConfig(
        automation_mode="simulator",
        recovery_screenshot_dir=tmp_path / "recovery",
        ui_action_retries=1,
        ui_recovery_pause_ms=0,
    )
    browser = FaultyBrowserController(config, {"present_button": 1})
    adapter = GoogleMeetAdapter(browser, config)

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    await adapter.start_presenting("http://127.0.0.1:3000/present/task-1")

    assert adapter.state == MeetingState.PRESENTING
    assert adapter.recovery_events is not None
    assert any(
        event.action == "present_button" and event.recovered for event in adapter.recovery_events
    )
    assert browser.pages["presentation"].url == "http://127.0.0.1:3000/present/task-1"


@pytest.mark.asyncio
async def test_google_meet_adapter_rejects_non_meet_hosts() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)

    with pytest.raises(ValueError, match="Only Google Meet URLs"):
        await adapter.navigate("https://example.com/abc-defg-hij")


@pytest.mark.asyncio
async def test_google_meet_adapter_allows_configured_fixture_host() -> None:
    config = BrowserConfig(automation_mode="simulator", allowed_meet_hosts=["127.0.0.1"])
    adapter = GoogleMeetAdapter(BrowserController(config), config)

    await adapter.navigate("http://127.0.0.1:9000/fixture-meet")

    assert adapter.state == MeetingState.PREJOIN
    assert adapter.current_url == "http://127.0.0.1:9000/fixture-meet"


@pytest.mark.asyncio
async def test_playwright_browser_uses_persistent_profile(tmp_path: Path) -> None:
    config = BrowserConfig(
        automation_mode="playwright",
        profile_dir=tmp_path / "chrome-profile",
        headless=True,
    )
    browser = BrowserController(config)

    page = await browser.open_page("blank", "about:blank")

    assert page.url == "about:blank"
    assert config.profile_dir.exists()
    await browser.close()


@pytest.mark.asyncio
async def test_browser_reuses_named_page_instead_of_opening_duplicate_tabs() -> None:
    config = BrowserConfig(automation_mode="simulator")
    browser = BrowserController(config)

    first = await browser.open_page("meet", "https://meet.google.com/abc-defg-hij")
    second = await browser.open_page("meet", "https://meet.google.com/abc-defg-hij")

    assert second is first
    assert list(browser.pages) == ["meet"]


class FaultySimulatedPageDriver(SimulatedPageDriver):
    def __init__(self, failures: dict[str, int]):
        super().__init__()
        self.failures = failures
        self.front_count = 0

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str:
        key = self._key_for(candidates)
        remaining = self.failures.get(key, 0)
        if remaining:
            self.failures[key] = remaining - 1
            raise TimeoutError(f"transient failure clicking {key}")
        return await super().click_first(candidates, timeout_ms)

    async def bring_to_front(self) -> None:
        self.front_count += 1


class FaultyBrowserController(BrowserController):
    def __init__(self, config: BrowserConfig, failures: dict[str, int]):
        super().__init__(config)
        self.failures = failures

    async def open_page(self, name: str, url: str):
        if name == "meet":
            page = FaultySimulatedPageDriver(self.failures)
        else:
            page = SimulatedPageDriver()
        await page.goto(url, self.config.navigation_timeout_ms)
        self.pages[name] = page
        return page


class FailingShareDialog:
    async def select_and_share(self, source_title: str):
        raise ShareDialogError(
            "picker failed",
            [ShareDialogEvent("attempt_failed", 1, False, "source missing")],
        )


class TrackingShareDialog:
    def __init__(self):
        self.called = False

    async def select_and_share(self, source_title: str):
        self.called = True
        raise AssertionError("share dialog must not run for an invalid presentation")


class MismatchedPresentationBrowser(BrowserController):
    async def open_page(self, name: str, url: str):
        page = SimulatedPageDriver(
            presentation_task_id="wrong-task" if name == "presentation" else None,
            presentation_revision="1" if name == "presentation" else None,
        )
        await page.goto(url, self.config.navigation_timeout_ms)
        self.pages[name] = page
        return page
