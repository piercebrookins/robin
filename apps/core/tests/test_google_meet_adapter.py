from __future__ import annotations

from pathlib import Path

import pytest

from robin_core.browser.controller import BrowserController
from robin_core.browser.page_driver import SimulatedPageDriver
from robin_core.config import BrowserConfig
from robin_core.meeting.adapters.google_meet import GoogleMeetAdapter
from robin_core.meeting.selectors import SelectorCandidate
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


@pytest.mark.asyncio
async def test_google_meet_simulator_present_flow_opens_presentation_page() -> None:
    config = BrowserConfig(automation_mode="simulator")
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
    assert any(event.action == "present_button" and event.recovered for event in adapter.recovery_events)
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
