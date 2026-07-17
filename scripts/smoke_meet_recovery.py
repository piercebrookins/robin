from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.browser.controller import BrowserController
from robin_core.browser.page_driver import SimulatedPageDriver
from robin_core.config import BrowserConfig
from robin_core.meeting.adapters.google_meet import GoogleMeetAdapter
from robin_core.meeting.selectors import SelectorCandidate
from robin_core.schemas import MeetingState


class FaultySimulatedPageDriver(SimulatedPageDriver):
    def __init__(self, failures: dict[str, int]):
        super().__init__()
        self.failures = failures

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str:
        key = self._key_for(candidates)
        remaining = self.failures.get(key, 0)
        if remaining:
            self.failures[key] = remaining - 1
            raise TimeoutError(f"transient failure clicking {key}")
        return await super().click_first(candidates, timeout_ms)


class FaultyBrowserController(BrowserController):
    def __init__(self, config: BrowserConfig, failures: dict[str, int]):
        super().__init__(config)
        self.failures = failures

    async def open_page(self, name: str, url: str):
        page = FaultySimulatedPageDriver(self.failures) if name == "meet" else SimulatedPageDriver()
        await page.goto(url, self.config.navigation_timeout_ms)
        self.pages[name] = page
        return page


async def main() -> None:
    root = Path("RobinWorkspace").resolve()
    screenshot_dir = root / "sessions" / "smoke-meet-recovery"
    config = BrowserConfig(
        automation_mode="simulator",
        recovery_screenshot_dir=screenshot_dir,
        ui_action_retries=1,
        ui_recovery_pause_ms=0,
    )
    browser = FaultyBrowserController(config, {"join_button": 1, "present_button": 1})
    adapter = GoogleMeetAdapter(browser, config)
    await adapter.navigate("https://meet.google.com/abc-defg-hij")
    await adapter.join()
    await adapter.start_presenting("http://127.0.0.1:3000/present/task-1")
    if adapter.state != MeetingState.PRESENTING or not adapter.presenting:
        raise SystemExit("Meet adapter did not recover into presenting state")
    events = adapter.recovery_events or []
    if not any(event.action == "join_button" and event.recovered for event in events):
        raise SystemExit("Join recovery was not recorded")
    if not any(event.action == "present_button" and event.recovered for event in events):
        raise SystemExit("Present recovery was not recorded")
    if not any(event.screenshot_path and Path(event.screenshot_path).exists() for event in events):
        raise SystemExit("Recovery screenshots were not captured")
    print(f"Meet recovery smoke passed: screenshots={screenshot_dir}")


if __name__ == "__main__":
    asyncio.run(main())
