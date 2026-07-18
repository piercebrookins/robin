from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from robin_core.meeting.selectors import SelectorCandidate


class PageDriver(Protocol):
    url: str

    async def goto(self, url: str, timeout_ms: int) -> None: ...

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str: ...

    async def is_visible(self, candidates: list[SelectorCandidate], timeout_ms: int) -> bool: ...

    async def screenshot(self) -> bytes: ...

    async def bring_to_front(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class SimulatedPageDriver:
    url: str = "about:blank"
    visible_keys: set[str] = field(
        default_factory=lambda: {"join_button", "mute_button", "camera_button", "prejoin_mute_button", "prejoin_camera_button"}
    )
    clicked: list[str] = field(default_factory=list)

    async def goto(self, url: str, timeout_ms: int) -> None:
        self.url = url

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str:
        key = self._key_for(candidates)
        self.clicked.append(key)
        if key == "join_button":
            self.visible_keys.add("joined_signal")
            self.visible_keys.add("leave_button")
        if key in {"prejoin_mute_button", "prejoin_camera_button"}:
            self.visible_keys.discard(key)
        if key == "present_button":
            self.visible_keys.add("stop_presenting_button")
        if key == "stop_presenting_button":
            self.visible_keys.discard("stop_presenting_button")
        return key

    async def is_visible(self, candidates: list[SelectorCandidate], timeout_ms: int) -> bool:
        return self._key_for(candidates) in self.visible_keys

    async def screenshot(self) -> bytes:
        keys = ",".join(sorted(self.visible_keys))
        return f"simulated-page url={self.url} visible={keys}".encode()

    async def bring_to_front(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _key_for(self, candidates: list[SelectorCandidate]) -> str:
        for key, known in {
            "prejoin_mute_button": "Turn off microphone|Mute microphone",
            "prejoin_camera_button": "Turn off camera",
            "join_button": "Join now|Ask to join",
            "leave_button": "Leave call|Leave meeting",
            "mute_button": "Turn off microphone|Mute microphone|Microphone",
            "unmute_button": "Turn on microphone|Unmute microphone|Microphone",
            "camera_button": "Turn off camera|Turn on camera|Camera",
            "present_button": "Present now|Share screen|Present",
            "stop_presenting_button": "Stop presenting|Stop sharing",
            "joined_signal": "Leave call|Leave meeting",
        }.items():
            if any(candidate.name_regex == known for candidate in candidates):
                return key
        return "unknown"


class PlaywrightPageDriver:
    def __init__(self, page):
        self.page = page

    @property
    def url(self) -> str:
        return self.page.url

    async def goto(self, url: str, timeout_ms: int) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str:
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                locator = self._locator(candidate)
                await locator.first.click(timeout=timeout_ms)
                return self._describe(candidate)
            except Exception as exc:
                last_error = exc
        raise TimeoutError(f"No selector candidate was clickable: {last_error}")

    async def is_visible(self, candidates: list[SelectorCandidate], timeout_ms: int) -> bool:
        for candidate in candidates:
            try:
                locator = self._locator(candidate).first
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(full_page=True)

    async def bring_to_front(self) -> None:
        await self.page.bring_to_front()

    async def close(self) -> None:
        await self.page.close()

    def _locator(self, candidate: SelectorCandidate):
        if candidate.role and candidate.name_regex:
            return self.page.get_by_role(candidate.role, name=re.compile(candidate.name_regex, re.I))
        if candidate.test_id:
            return self.page.get_by_test_id(candidate.test_id)
        if candidate.text_regex:
            return self.page.get_by_text(re.compile(candidate.text_regex, re.I))
        raise ValueError(f"Unsupported selector candidate: {candidate}")

    def _describe(self, candidate: SelectorCandidate) -> str:
        if candidate.test_id:
            return f"test_id:{candidate.test_id}"
        if candidate.role:
            return f"role:{candidate.role}/{candidate.name_regex}"
        return candidate.text_regex or "unknown"
