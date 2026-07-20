from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from robin_core.browser.controller import BrowserController
from robin_core.browser.native_dialog import ShareDialogError, ShareDialogEvent
from robin_core.browser.page_driver import CaptionTurn, PlaywrightPageDriver, SimulatedPageDriver
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
    assert adapter.selected_microphone_device == "BlackHole 2ch (Virtual)"


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
async def test_google_meet_speech_route_is_cached_until_invalidated() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)

    selected = await adapter.prepare_speech_route()
    await adapter.prepare_speech_route()
    adapter.invalidate_speech_route()
    await adapter.prepare_speech_route()

    assert selected == "BlackHole 2ch (Virtual)"
    assert adapter.speech_route_ready is True
    completed = [
        event
        for event in adapter.speech_route_events or []
        if event.type == "speech.route_prepare.completed"
    ]
    assert [event.cache_status for event in completed[-3:]] == ["miss", "hit", "miss"]


@pytest.mark.asyncio
async def test_google_meet_failed_route_prepare_is_not_cached() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    adapter.microphone_device_name = "Missing Virtual Microphone"
    adapter.invalidate_speech_route()

    with pytest.raises(RuntimeError, match="microphone device is unavailable"):
        await adapter.prepare_speech_route()

    assert adapter.speech_route_ready is False


@pytest.mark.asyncio
async def test_google_meet_page_replacement_invalidates_speech_route() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    await adapter.prepare_speech_route()
    assert adapter.speech_route_ready is True

    await adapter._recover_admission_target(1, RuntimeError("Target closed"))
    await adapter.prepare_speech_route()

    completed = [
        event
        for event in adapter.speech_route_events or []
        if event.type == "speech.route_prepare.completed"
    ]
    assert completed[-1].cache_status == "miss"


@pytest.mark.asyncio
async def test_google_meet_enables_captions_after_admission() -> None:
    config = BrowserConfig(automation_mode="simulator", captions_enabled=True)
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.visible_keys.add("enable_captions_button")

    await adapter.join()

    assert "enable_captions_button" in page.clicked


@pytest.mark.asyncio
async def test_playwright_driver_reads_visible_speaker_labeled_captions() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <div aria-live="polite" data-robin-caption>
              <div data-speaker-name="Avery">Avery</div>
              <div data-caption-text>Robin, summarize the launch risks.</div>
            </div>
            """
        )

        captions = await PlaywrightPageDriver(page).read_captions()

        assert captions == [CaptionTurn("Avery", "Robin, summarize the launch risks.")]
        await browser.close()


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
    page.visible_keys.add("present_button")

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
async def test_google_meet_waiting_room_transient_blank_text_is_not_admission() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(BrowserController(config), config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    page = adapter.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.visible_keys.discard("join_button")
    page.visible_keys.add("leave_button")
    page.visible_keys.add("mute_button")

    assert await adapter._is_admitted() is False


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
async def test_playwright_driver_selects_and_verifies_meet_microphone() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <button aria-label="Audio settings" aria-expanded="false"
              onclick="this.setAttribute('aria-expanded','true'); document.querySelector('#panel').hidden=false">
              Audio settings
            </button>
            <div id="panel" hidden>
              <button aria-label="Microphone: MacBook Microphone (Built-in)"
                onclick="document.querySelector('#choices').hidden=false">Microphone</button>
              <div id="choices" hidden>
                <button role="menuitemradio"
                  onclick="document.querySelector('[aria-label^=&quot;Microphone:&quot;]').setAttribute('aria-label','Microphone: BlackHole 2ch (Virtual)')">
                  BlackHole 2ch (Virtual)
                </button>
              </div>
            </div>
            """
        )

        selected = await PlaywrightPageDriver(page).ensure_microphone_device("BlackHole 2ch", 1_000)

        assert selected == "BlackHole 2ch (Virtual)"
        await browser.close()


@pytest.mark.asyncio
async def test_playwright_driver_uses_shortcut_when_meet_toolbar_is_hidden() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <button id="mic" aria-label="Turn on microphone" style="display:none"></button>
            """
        )
        driver = PlaywrightPageDriver(page)

        async def meet_shortcut(key: str) -> None:
            assert key == "Meta+d"
            await page.locator("#mic").evaluate(
                """mic => mic.setAttribute(
                    'aria-label',
                    mic.getAttribute('aria-label').startsWith('Turn on')
                      ? 'Turn off microphone'
                      : 'Turn on microphone'
                )"""
            )

        page.keyboard.press = meet_shortcut  # type: ignore[method-assign]

        assert await driver.set_microphone_muted(False, 1_000) == "unmuted"
        assert await page.locator("#mic").get_attribute("aria-label") == "Turn off microphone"
        assert await driver.set_microphone_muted(True, 1_000) == "muted"
        assert await page.locator("#mic").get_attribute("aria-label") == "Turn on microphone"
        await browser.close()


@pytest.mark.asyncio
async def test_playwright_driver_disables_processing_for_blackhole() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <button aria-label="Audio settings" aria-expanded="false"
              onclick="this.setAttribute('aria-expanded','true'); document.querySelector('#quick').hidden=false">
              Audio settings
            </button>
            <div id="quick" hidden>
              <button aria-label="Microphone: BlackHole 2ch (Virtual)">Microphone</button>
              <button aria-label="Settings"
                onclick="document.querySelector('#dialog').hidden=false">Settings</button>
            </div>
            <div id="dialog" hidden>
              <button role="switch" aria-label="Studio sound" aria-checked="true"
                onclick="this.setAttribute('aria-checked','false')"></button>
              <button role="switch" aria-label="Adaptive audio" aria-checked="true"
                onclick="this.setAttribute('aria-checked','false')"></button>
              <button aria-label="Close dialogue" onclick="this.parentElement.hidden=true"></button>
            </div>
            """
        )

        selected = await PlaywrightPageDriver(page).ensure_microphone_device("BlackHole 2ch", 1_000)

        assert selected == "BlackHole 2ch (Virtual)"
        assert (
            await page.locator('[aria-label="Studio sound"]').get_attribute("aria-checked")
            == "false"
        )
        assert (
            await page.locator('[aria-label="Adaptive audio"]').get_attribute("aria-checked")
            == "false"
        )
        await browser.close()


@pytest.mark.asyncio
async def test_google_meet_refuses_to_unmute_without_configured_microphone() -> None:
    config = BrowserConfig(automation_mode="simulator")
    adapter = GoogleMeetAdapter(
        BrowserController(config),
        config,
        microphone_device_name="Missing Virtual Microphone",
    )
    await adapter.navigate("https://meet.google.com/abc-defg-hij")

    with pytest.raises(RuntimeError, match="microphone device is unavailable"):
        await adapter.join()


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


@pytest.mark.asyncio
async def test_browser_reconnects_once_after_lost_cdp_connection() -> None:
    class RecoveringController(BrowserController):
        attempts = 0

        async def _open_playwright_page_once(self, name: str, url: str):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("CDP transport closed")
            page = SimulatedPageDriver()
            await page.goto(url, self.config.navigation_timeout_ms)
            return page

    config = BrowserConfig(
        automation_mode="playwright",
        connection_mode="cdp",
    )
    browser = RecoveringController(config)

    page = await browser.open_page("meet", "https://meet.google.com/abc-defg-hij")

    assert page.url == "https://meet.google.com/abc-defg-hij"
    assert browser.attempts == 2
    assert browser.recovery_count == 1
    assert "CDP transport closed" in (browser.last_recovery_reason or "")


@pytest.mark.asyncio
async def test_google_meet_reopens_closed_target_while_waiting_for_admission() -> None:
    class ClosingAfterJoinPage(SimulatedPageDriver):
        failed = False

        async def inspect(self):
            if "join_button" in self.clicked and not self.failed:
                self.failed = True
                raise RuntimeError("Target page, context or browser has been closed")
            return await super().inspect()

        def is_closed(self) -> bool:
            return self.failed

    class ReopeningBrowser(BrowserController):
        open_count = 0

        async def open_page(self, name: str, url: str):
            self.open_count += 1
            page = ClosingAfterJoinPage() if self.open_count == 1 else SimulatedPageDriver()
            await page.goto(url, self.config.navigation_timeout_ms)
            self.pages[name] = page
            return page

    config = BrowserConfig(
        automation_mode="simulator",
        admission_timeout_ms=1_000,
        ui_action_retries=1,
        ui_recovery_pause_ms=0,
    )
    browser = ReopeningBrowser(config)
    adapter = GoogleMeetAdapter(browser, config)

    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()

    assert adapter.state == MeetingState.LISTENING
    assert browser.open_count == 2
    assert any(
        event.action == "admission_target" and event.recovered
        for event in adapter.recovery_events or []
    )


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
