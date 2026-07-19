from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from robin_core.config import BrowserConfig
from robin_core.browser.page_driver import (
    InteractiveElement,
    PageDriver,
    PageSnapshot,
    PlaywrightPageDriver,
    SimulatedPageDriver,
)


class OperatorApprovalRequired(PermissionError):
    def __init__(self, action: str, element: InteractiveElement):
        super().__init__(f"Approval required to {action} {element.name or element.ref!r}")
        self.action = action
        self.element = element


@dataclass
class BrowserController:
    config: BrowserConfig = field(default_factory=BrowserConfig)
    pages: dict[str, PageDriver] = field(default_factory=dict)
    _playwright: object | None = None
    _browser: object | None = None
    _context: object | None = None
    _owns_browser: bool = False
    recovery_count: int = 0
    last_recovery_reason: str | None = None

    async def open_page(self, name: str, url: str) -> PageDriver:
        existing = self.pages.get(name)
        if existing and not existing.is_closed():
            try:
                if existing.url != url:
                    await existing.goto(url, self.config.navigation_timeout_ms)
                await existing.bring_to_front()
                return existing
            except Exception as exc:
                if self.config.automation_mode != "playwright" or self.config.connection_mode != "cdp":
                    raise
                self._reset_cdp_connection(f"stale page {name}: {exc}")
        self.pages.pop(name, None)
        if self.config.automation_mode == "playwright":
            page = await self._open_playwright_page(name, url)
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

    async def inspect_for_operator(self, name: str) -> PageSnapshot:
        return await self._operator_page(name).inspect()

    async def click_for_operator(self, name: str, ref: str, approved: bool = False) -> InteractiveElement:
        page = self._operator_page(name)
        snapshot = await page.inspect()
        element = next((item for item in snapshot.elements if item.ref == ref), None)
        if element is None:
            raise KeyError(f"Unknown or stale page element: {ref}")
        if self._requires_approval("click", element) and not approved:
            raise OperatorApprovalRequired("click", element)
        return await page.click_ref(ref)

    async def fill_for_operator(
        self, name: str, ref: str, text: str, approved: bool = False
    ) -> InteractiveElement:
        if len(text) > 2000:
            raise ValueError("Browser input is limited to 2000 characters")
        page = self._operator_page(name)
        snapshot = await page.inspect()
        element = next((item for item in snapshot.elements if item.ref == ref), None)
        if element is None:
            raise KeyError(f"Unknown or stale page element: {ref}")
        if element.kind == "password":
            raise PermissionError("Robin cannot fill password fields")
        if self._requires_approval("fill", element) and not approved:
            raise OperatorApprovalRequired("fill", element)
        return await page.fill_ref(ref, text)

    def _operator_page(self, name: str) -> PageDriver:
        page = self.pages.get(name)
        if page is None or page.is_closed():
            raise KeyError(f"Operator page is not open: {name}")
        return page

    @staticmethod
    def _requires_approval(action: str, element: InteractiveElement) -> bool:
        label = f"{element.name} {element.role} {element.kind}".casefold()
        risky = (
            "join",
            "leave",
            "share",
            "present",
            "send",
            "submit",
            "upload",
            "download",
            "allow",
            "permission",
            "delete",
            "remove",
        )
        return any(word in label for word in risky) or (
            action == "fill" and element.kind in {"file", "password"}
        )

    async def close(self) -> None:
        for name in list(self.pages):
            await self.close_page(name)
        if self._context and self._owns_browser:
            await self._context.close()
        self._context = None
        if self._browser and self._owns_browser:
            await self._browser.close()
        self._browser = None
        self._owns_browser = False
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _open_playwright_page(self, name: str, url: str) -> PageDriver:
        try:
            return await self._open_playwright_page_once(name, url)
        except Exception as exc:
            if self.config.connection_mode != "cdp":
                raise
            self._reset_cdp_connection(f"CDP connection failed: {exc}")
            return await self._open_playwright_page_once(name, url)

    async def _open_playwright_page_once(self, name: str, url: str) -> PageDriver:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        if self._context is None:
            if self.config.connection_mode == "cdp":
                self._owns_browser = False
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.config.cdp_endpoint,
                    no_defaults=True,
                )
                contexts = self._browser.contexts
                self._context = contexts[0] if contexts else await self._browser.new_context()
            else:
                self._owns_browser = True
                self._context = await self._launch_persistent_context()
        if name == "presentation":
            await self._close_stale_presentation_pages(url)
        page = self._matching_context_page(name, url)
        if page is None:
            page = await self._context.new_page()
        driver = PlaywrightPageDriver(page)
        if driver.url != url:
            await driver.goto(url, self.config.navigation_timeout_ms)
        return driver

    def _reset_cdp_connection(self, reason: str) -> None:
        self.pages.clear()
        self._context = None
        self._browser = None
        self._owns_browser = False
        self.recovery_count += 1
        self.last_recovery_reason = reason[:1000]

    async def _close_stale_presentation_pages(self, url: str) -> None:
        """Keep one current renderer tab so Chrome's share picker has one clear source."""
        if self._context is None:
            return
        target = urlsplit(url)
        presentation_prefix = f"{target.scheme}://{target.netloc}/present/"
        kept_current = False
        for page in list(self._context.pages):
            if page.is_closed() or not page.url.startswith(presentation_prefix):
                continue
            if page.url == url and not kept_current:
                kept_current = True
                continue
            await page.close()

    def _matching_context_page(self, name: str, url: str):
        if self._context is None:
            return None
        pages = [page for page in self._context.pages if not page.is_closed()]
        exact = next((page for page in pages if page.url == url), None)
        if exact is not None:
            return exact
        if name == "meet":
            return next(
                (page for page in pages if page.url.startswith(self.config.meet_base_url)),
                None,
            )
        return None

    async def _launch_persistent_context(self):
        if self._playwright is None:
            raise RuntimeError("Playwright is not initialized.")
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            f"--remote-debugging-port={self.config.remote_debugging_port}",
            "--autoplay-policy=no-user-gesture-required",
        ]
        if self.config.use_fake_media_ui:
            args.append("--use-fake-ui-for-media-stream")
        launch_kwargs: dict[str, object] = {
            "headless": self.config.headless,
            "args": args,
        }
        if self.config.executable_path:
            launch_kwargs["executable_path"] = str(self.config.executable_path)
        return await self._playwright.chromium.launch_persistent_context(
            str(self.config.profile_dir),
            **launch_kwargs,
        )
