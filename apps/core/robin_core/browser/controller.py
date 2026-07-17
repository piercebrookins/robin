from __future__ import annotations

from dataclasses import dataclass, field

from robin_core.config import BrowserConfig
from robin_core.browser.page_driver import PageDriver, PlaywrightPageDriver, SimulatedPageDriver


@dataclass
class BrowserController:
    config: BrowserConfig = field(default_factory=BrowserConfig)
    pages: dict[str, PageDriver] = field(default_factory=dict)
    _playwright: object | None = None
    _context: object | None = None

    async def open_page(self, name: str, url: str) -> PageDriver:
        if self.config.automation_mode == "playwright":
            page = await self._open_playwright_page(url)
        else:
            page = SimulatedPageDriver()
            await page.goto(url, self.config.navigation_timeout_ms)
        self.pages[name] = page
        return page

    async def bring_to_front(self, name: str) -> PageDriver:
        if name not in self.pages:
            raise KeyError(f"Unknown browser page: {name}")
        page = self.pages[name]
        await page.bring_to_front()
        return page

    async def close_page(self, name: str) -> None:
        page = self.pages.pop(name, None)
        if page:
            await page.close()

    async def close(self) -> None:
        for name in list(self.pages):
            await self.close_page(name)
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _open_playwright_page(self, url: str) -> PageDriver:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        if self._context is None:
            self.config.profile_dir.mkdir(parents=True, exist_ok=True)
            launch_kwargs: dict[str, object] = {
                "headless": self.config.headless,
                "args": [
                    f"--remote-debugging-port={self.config.remote_debugging_port}",
                    "--use-fake-ui-for-media-stream",
                    "--autoplay-policy=no-user-gesture-required",
                ],
            }
            if self.config.executable_path:
                launch_kwargs["executable_path"] = str(self.config.executable_path)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.config.profile_dir),
                **launch_kwargs,
            )
        page = await self._context.new_page()
        driver = PlaywrightPageDriver(page)
        await driver.goto(url, self.config.navigation_timeout_ms)
        return driver
