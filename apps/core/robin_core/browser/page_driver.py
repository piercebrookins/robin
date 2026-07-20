from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from robin_core.meeting.selectors import MEET_SELECTORS, SelectorCandidate


@dataclass(frozen=True)
class PresentationReadiness:
    task_id: str
    revision: str
    screenshot_safe: bool = True


@dataclass(frozen=True)
class InteractiveElement:
    ref: str
    role: str
    name: str
    kind: str
    disabled: bool = False


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    title: str
    text: str
    elements: list[InteractiveElement]


@dataclass(frozen=True)
class CaptionTurn:
    speaker_name: str
    text: str


class PageDriver(Protocol):
    url: str

    async def goto(self, url: str, timeout_ms: int) -> None: ...

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str: ...

    async def is_visible(self, candidates: list[SelectorCandidate], timeout_ms: int) -> bool: ...

    async def ensure_microphone_device(self, device_name: str, timeout_ms: int) -> str: ...

    async def set_microphone_muted(self, muted: bool, timeout_ms: int) -> str: ...

    async def wait_for_presentation_ready(
        self,
        expected_task_id: str,
        expected_revision: str | None,
        timeout_ms: int,
    ) -> PresentationReadiness: ...

    async def screenshot(self) -> bytes: ...

    async def inspect(self) -> PageSnapshot: ...

    async def click_ref(self, ref: str) -> InteractiveElement: ...

    async def fill_ref(self, ref: str, text: str) -> InteractiveElement: ...

    async def upload_ref(self, ref: str, path: Path) -> InteractiveElement: ...

    async def download_ref(self, ref: str, destination_dir: Path) -> Path: ...

    async def read_captions(self) -> list[CaptionTurn]: ...

    async def bring_to_front(self) -> None: ...

    async def close(self) -> None: ...

    def is_closed(self) -> bool: ...


@dataclass
class SimulatedPageDriver:
    url: str = "about:blank"
    visible_keys: set[str] = field(
        default_factory=lambda: {
            "join_button",
            "mute_button",
            "camera_button",
            "prejoin_mute_button",
            "prejoin_camera_button",
        }
    )
    clicked: list[str] = field(default_factory=list)
    presentation_error: str | None = None
    presentation_task_id: str | None = None
    presentation_revision: str | None = None
    title: str = "Simulated page"
    operator_elements: dict[str, InteractiveElement] = field(default_factory=dict)
    filled: dict[str, str] = field(default_factory=dict)
    uploaded: dict[str, str] = field(default_factory=dict)
    download_names: dict[str, str] = field(default_factory=dict)
    caption_turns: list[CaptionTurn] = field(default_factory=list)
    microphone_device: str = "BlackHole 2ch (Virtual)"
    available_microphone_devices: list[str] = field(
        default_factory=lambda: ["BlackHole 2ch (Virtual)", "MacBook Microphone (Built-in)"]
    )

    async def goto(self, url: str, timeout_ms: int) -> None:
        self.url = url

    async def click_first(self, candidates: list[SelectorCandidate], timeout_ms: int) -> str:
        key = self._key_for(candidates)
        self.clicked.append(key)
        if key == "join_button":
            self.visible_keys.add("joined_signal")
            self.visible_keys.add("leave_button")
            self.visible_keys.add("present_button")
            self.visible_keys.add("enable_captions_button")
        if key in {"prejoin_mute_button", "prejoin_camera_button"}:
            self.visible_keys.discard(key)
        if key == "present_button":
            self.visible_keys.add("share_tab_option")
        if key == "share_tab_option":
            self.visible_keys.discard("share_tab_option")
            self.visible_keys.add("stop_presenting_button")
            self.visible_keys.add("presenting_signal")
        if key == "stop_presenting_button":
            self.visible_keys.discard("stop_presenting_button")
        if key == "mute_button":
            self.visible_keys.discard("mute_button")
            self.visible_keys.add("unmute_button")
        if key == "unmute_button":
            self.visible_keys.discard("unmute_button")
            self.visible_keys.add("mute_button")
        if key == "prejoin_mute_button":
            self.visible_keys.discard("mute_button")
            self.visible_keys.add("unmute_button")
        return key

    async def is_visible(self, candidates: list[SelectorCandidate], timeout_ms: int) -> bool:
        return self._key_for(candidates) in self.visible_keys

    async def ensure_microphone_device(self, device_name: str, timeout_ms: int) -> str:
        matching = next(
            (
                device
                for device in self.available_microphone_devices
                if device_name.casefold() in device.casefold()
            ),
            None,
        )
        if matching is None:
            raise RuntimeError(f"Meet microphone device is unavailable: {device_name}")
        self.microphone_device = matching
        return matching

    async def set_microphone_muted(self, muted: bool, timeout_ms: int) -> str:
        currently_muted = "unmute_button" in self.visible_keys
        if muted != currently_muted:
            await self.click_first(
                MEET_SELECTORS["mute_button" if muted else "unmute_button"],
                timeout_ms,
            )
        return "muted" if muted else "unmuted"

    async def screenshot(self) -> bytes:
        keys = ",".join(sorted(self.visible_keys))
        return f"simulated-page url={self.url} visible={keys}".encode()

    async def inspect(self) -> PageSnapshot:
        return PageSnapshot(
            url=self.url,
            title=self.title,
            text=",".join(sorted(self.visible_keys)),
            elements=list(self.operator_elements.values()),
        )

    async def click_ref(self, ref: str) -> InteractiveElement:
        element = self._operator_element(ref)
        if element.disabled:
            raise RuntimeError(f"Element {ref} is disabled")
        self.clicked.append(ref)
        return element

    async def fill_ref(self, ref: str, text: str) -> InteractiveElement:
        element = self._operator_element(ref)
        if element.kind not in {"input", "textarea", "contenteditable"}:
            raise RuntimeError(f"Element {ref} is not editable")
        self.filled[ref] = text
        return element

    async def upload_ref(self, ref: str, path: Path) -> InteractiveElement:
        element = self._operator_element(ref)
        if element.kind != "file":
            raise RuntimeError(f"Element {ref} is not a file input")
        if not path.is_file():
            raise FileNotFoundError(path)
        self.uploaded[ref] = str(path)
        return element

    async def download_ref(self, ref: str, destination_dir: Path) -> Path:
        element = self._operator_element(ref)
        if element.disabled:
            raise RuntimeError(f"Element {ref} is disabled")
        destination_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(self.download_names.get(ref, "download.bin")).name
        destination = destination_dir / filename
        destination.write_bytes(b"simulated download")
        self.clicked.append(ref)
        return destination

    async def read_captions(self) -> list[CaptionTurn]:
        return list(self.caption_turns)

    def _operator_element(self, ref: str) -> InteractiveElement:
        try:
            return self.operator_elements[ref]
        except KeyError as exc:
            raise KeyError(f"Unknown or stale page element: {ref}") from exc

    async def wait_for_presentation_ready(
        self,
        expected_task_id: str,
        expected_revision: str | None,
        timeout_ms: int,
    ) -> PresentationReadiness:
        if self.presentation_error:
            raise RuntimeError(
                f"Presentation renderer reported an error: {self.presentation_error}"
            )
        parsed = urlparse(self.url)
        actual_task_id = self.presentation_task_id or parsed.path.rstrip("/").split("/")[-1]
        actual_revision = (
            self.presentation_revision
            or parse_qs(parsed.query).get("revision", [expected_revision or ""])[0]
        )
        self._validate_presentation_identity(
            actual_task_id,
            actual_revision,
            expected_task_id,
            expected_revision,
        )
        return PresentationReadiness(task_id=actual_task_id, revision=actual_revision)

    async def bring_to_front(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def is_closed(self) -> bool:
        return False

    def _key_for(self, candidates: list[SelectorCandidate]) -> str:
        for key, known in {
            "prejoin_mute_button": "Turn off microphone|Mute microphone",
            "prejoin_camera_button": "Turn off camera",
            "join_button": "Join now|Ask to join",
            "leave_button": "Leave call|Leave meeting",
            "mute_button": r"^(?:Turn off microphone|Mute microphone)(?:\b.*)?$",
            "unmute_button": r"^(?:Turn on microphone|Unmute microphone)(?:\b.*)?$",
            "camera_button": "Turn off camera|Turn on camera|Camera",
            "present_button": "Present now|Share screen|Present",
            "share_tab_option": "A tab|Chrome tab|Share a tab",
            "stop_presenting_button": r"^(?:Stop presenting|Stop sharing)$",
            "presenting_signal": r"^(?:Stop presenting|Stop sharing)$",
            "joined_signal": "Leave call|Leave meeting",
            "in_call_signal": "Present now|Share screen|Present",
            "enable_captions_button": "Turn on captions|Show captions|Enable captions",
            "disable_captions_button": "Turn off captions|Hide captions|Disable captions",
        }.items():
            if any(candidate.name_regex == known for candidate in candidates):
                return key
        return "unknown"

    @staticmethod
    def _validate_presentation_identity(
        actual_task_id: str,
        actual_revision: str,
        expected_task_id: str,
        expected_revision: str | None,
    ) -> None:
        if actual_task_id != expected_task_id:
            raise RuntimeError(
                f"Presentation task mismatch: expected {expected_task_id}, got {actual_task_id or 'missing'}"
            )
        if expected_revision is not None and actual_revision != expected_revision:
            raise RuntimeError(
                f"Presentation revision mismatch: expected {expected_revision}, "
                f"got {actual_revision or 'missing'}"
            )


class PlaywrightPageDriver:
    def __init__(self, page):
        self.page = page
        self._operator_refs: dict[str, object] = {}
        self._operator_elements: dict[str, InteractiveElement] = {}

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
                await locator.first.wait_for(state="attached", timeout=timeout_ms)
                for index in range(await locator.count()):
                    match = locator.nth(index)
                    if await match.is_visible():
                        await match.click(timeout=timeout_ms)
                        return self._describe(candidate)
                last_error = TimeoutError(
                    f"Selector matched no visible elements: {self._describe(candidate)}"
                )
            except Exception as exc:
                last_error = exc
        raise TimeoutError(f"No selector candidate was clickable: {last_error}")

    async def is_visible(self, candidates: list[SelectorCandidate], timeout_ms: int) -> bool:
        for candidate in candidates:
            try:
                locator = self._locator(candidate)
                await locator.first.wait_for(state="attached", timeout=timeout_ms)
                for index in range(await locator.count()):
                    if await locator.nth(index).is_visible():
                        return True
            except Exception:
                continue
        return False

    async def ensure_microphone_device(self, device_name: str, timeout_ms: int) -> str:
        """Select and verify Meet's microphone without relying on hit-test coordinates.

        Meet renders the device picker as custom buttons rather than a native
        ``select``.  Programmatic DOM clicks avoid the tiny arrow button
        occasionally landing on the adjacent mute control in compact layouts.
        """

        target = " ".join(device_name.split()).casefold()
        if not target:
            raise ValueError("Microphone device name is required")

        async def selected_label() -> str:
            locator = self.page.locator('button[aria-label^="Microphone:"]')
            for index in range(await locator.count()):
                label = (await locator.nth(index).get_attribute("aria-label") or "").strip()
                if label:
                    return label.removeprefix("Microphone:").strip()
            return ""

        current = await selected_label()
        if target in current.casefold():
            if "blackhole" in target:
                await self._disable_virtual_microphone_processing(timeout_ms)
            return current

        audio_settings = self.page.locator('button[aria-label="Audio settings"]')
        try:
            await audio_settings.first.wait_for(state="attached", timeout=timeout_ms)
        except Exception as exc:
            raise RuntimeError("Meet audio settings control is unavailable") from exc
        if (await audio_settings.first.get_attribute("aria-expanded")) != "true":
            await audio_settings.first.evaluate("element => element.click()")

        device_button = self.page.locator('button[aria-label^="Microphone:"]')
        try:
            await device_button.first.wait_for(state="visible", timeout=timeout_ms)
        except Exception as exc:
            raise RuntimeError("Meet microphone picker is unavailable") from exc
        current = await selected_label()
        if target not in current.casefold():
            await device_button.first.evaluate("element => element.click()")
            options = self.page.locator(
                '[role="option"], [role="menuitemradio"], [role="menuitem"], [role="radio"]'
            )
            match = None
            try:
                await options.first.wait_for(state="attached", timeout=timeout_ms)
                for index in range(await options.count()):
                    option = options.nth(index)
                    label = " ".join(
                        (
                            await option.get_attribute("aria-label")
                            or await option.inner_text()
                            or ""
                        ).split()
                    )
                    if target in label.casefold() and await option.is_visible():
                        match = option
                        break
            except Exception:
                match = None
            if match is None:
                raise RuntimeError(f"Meet microphone device is unavailable: {device_name}")
            await match.evaluate("element => element.click()")

        deadline_ms = max(timeout_ms, 250)
        try:
            await self.page.wait_for_function(
                """([needle]) => [...document.querySelectorAll('button[aria-label^="Microphone:"]')]
                    .some(button => (button.getAttribute('aria-label') || '').toLowerCase().includes(needle))""",
                arg=[target],
                timeout=deadline_ms,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Meet did not retain microphone device selection: {device_name}"
            ) from exc
        verified = await selected_label()
        if target not in verified.casefold():
            raise RuntimeError(
                f"Meet selected microphone {verified or 'unknown'}, expected {device_name}"
            )
        if "blackhole" in target:
            await self._disable_virtual_microphone_processing(timeout_ms)
        return verified

    async def set_microphone_muted(self, muted: bool, timeout_ms: int) -> str:
        """Set and verify Meet microphone state, with a keyboard fallback.

        Meet auto-hides the in-call toolbar and occasionally removes its mic
        button from the visible accessibility tree. The documented Command-D
        shortcut still toggles the local microphone in that state.
        """

        desired = MEET_SELECTORS["unmute_button" if muted else "mute_button"]
        action = MEET_SELECTORS["mute_button" if muted else "unmute_button"]

        async def attached(candidates: list[SelectorCandidate]) -> bool:
            for candidate in candidates:
                try:
                    if candidate.role and candidate.name_regex:
                        locator = self.page.locator(f'{candidate.role},[role="{candidate.role}"]')
                        pattern = re.compile(candidate.name_regex, re.I)
                        for index in range(await locator.count()):
                            element = locator.nth(index)
                            name = (
                                await element.get_attribute("aria-label")
                                or await element.get_attribute("title")
                                or await element.text_content()
                                or ""
                            ).strip()
                            if pattern.search(name):
                                return True
                        continue
                    locator = self._locator(candidate)
                    for index in range(await locator.count()):
                        if await locator.nth(index).is_attached():
                            return True
                except Exception:
                    continue
            return False

        if await attached(desired):
            return "muted" if muted else "unmuted"

        clicked = False
        try:
            if await self.is_visible(action, min(timeout_ms, 1_000)):
                await self.click_first(action, timeout_ms)
                clicked = True
        except Exception:
            clicked = False
        if not clicked:
            await self.page.bring_to_front()
            await self.page.keyboard.press("Meta+d")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_ms, 250) / 1000
        while loop.time() < deadline:
            if await attached(desired):
                return "muted" if muted else "unmuted"
            await self.page.wait_for_timeout(50)
        state = "mute" if muted else "unmute"
        raise RuntimeError(f"Meet did not confirm {state} after button and shortcut recovery")

    async def _disable_virtual_microphone_processing(self, timeout_ms: int) -> None:
        """Keep Meet from filtering or merging an injected virtual-mic signal."""

        switches = self.page.locator(
            'button[role="switch"][aria-label="Studio sound"], '
            'button[role="switch"][aria-label="Adaptive audio"]'
        )
        switches_visible = any(
            [await switches.nth(index).is_visible() for index in range(await switches.count())]
        )
        if not switches_visible:
            audio_settings = self.page.locator('button[aria-label="Audio settings"]')
            if (
                await audio_settings.count()
                and (await audio_settings.first.get_attribute("aria-expanded")) != "true"
            ):
                await audio_settings.first.evaluate("element => element.click()")
            settings = self.page.locator('button[aria-label="Settings"]')
            visible_settings = None
            for index in range(await settings.count()):
                if await settings.nth(index).is_visible():
                    visible_settings = settings.nth(index)
                    break
            # Some Meet editions do not expose these processing controls. In
            # that case device verification is still valid and there is no
            # processing toggle to enforce.
            if visible_settings is None:
                return
            await visible_settings.evaluate("element => element.click()")
            try:
                await switches.first.wait_for(state="visible", timeout=timeout_ms)
            except Exception as exc:
                raise RuntimeError("Meet audio processing settings did not open") from exc

        for label in ("Studio sound", "Adaptive audio"):
            control = self.page.locator(f'button[role="switch"][aria-label="{label}"]')
            if not await control.count():
                continue
            if (await control.first.get_attribute("aria-checked")) == "true":
                # Meet's switch controller ignores synthetic DOM clicks but
                # accepts a trusted pointer event at the control itself.
                await control.first.click(force=True, timeout=timeout_ms)
            try:
                await self.page.wait_for_function(
                    """([name]) => {
                        const control = document.querySelector(
                          `button[role="switch"][aria-label="${name}"]`
                        );
                        return !control || control.getAttribute('aria-checked') === 'false';
                    }""",
                    arg=[label],
                    timeout=max(timeout_ms, 250),
                )
            except Exception as exc:
                raise RuntimeError(f"Meet kept {label} enabled for BlackHole") from exc

        close = self.page.locator('button[aria-label="Close dialogue"]')
        if await close.count() and await close.first.is_visible():
            await close.first.evaluate("element => element.click()")

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(full_page=True)

    async def inspect(self) -> PageSnapshot:
        self._operator_refs = {}
        self._operator_elements = {}
        candidates = self.page.locator(
            "button,a,input,textarea,select,[role=button],[role=link],[contenteditable=true]"
        )
        elements: list[InteractiveElement] = []
        for index in range(min(await candidates.count(), 60)):
            locator = candidates.nth(index)
            if not await locator.is_visible():
                continue
            tag = await locator.evaluate("element => element.tagName.toLowerCase()")
            input_type = (await locator.get_attribute("type") or "").casefold()
            kind = input_type if input_type in {"password", "file"} else str(tag)
            if await locator.get_attribute("contenteditable") == "true":
                kind = "contenteditable"
            name = (
                await locator.get_attribute("aria-label")
                or await locator.get_attribute("title")
                or await locator.inner_text()
                or await locator.get_attribute("placeholder")
                or ""
            ).strip()[:180]
            role = (await locator.get_attribute("role") or str(tag)).casefold()
            ref = f"e{len(elements) + 1}"
            disabled = await locator.is_disabled()
            element = InteractiveElement(ref, role, name, kind, disabled)
            self._operator_refs[ref] = locator
            self._operator_elements[ref] = element
            elements.append(element)
        body_text = " ".join((await self.page.locator("body").inner_text()).split())[:6000]
        return PageSnapshot(self.url, await self.page.title(), body_text, elements)

    async def click_ref(self, ref: str) -> InteractiveElement:
        element, locator = self._resolve_operator_ref(ref)
        if element.disabled:
            raise RuntimeError(f"Element {ref} is disabled")
        await locator.click()
        return element

    async def fill_ref(self, ref: str, text: str) -> InteractiveElement:
        element, locator = self._resolve_operator_ref(ref)
        if element.kind == "password":
            raise PermissionError("Robin cannot fill password fields")
        if element.kind not in {"input", "textarea", "contenteditable"}:
            raise RuntimeError(f"Element {ref} is not editable")
        await locator.fill(text)
        return element

    async def upload_ref(self, ref: str, path: Path) -> InteractiveElement:
        element, locator = self._resolve_operator_ref(ref)
        if element.kind != "file":
            raise RuntimeError(f"Element {ref} is not a file input")
        await locator.set_input_files(str(path))
        return element

    async def download_ref(self, ref: str, destination_dir: Path) -> Path:
        element, locator = self._resolve_operator_ref(ref)
        if element.disabled:
            raise RuntimeError(f"Element {ref} is disabled")
        async with self.page.expect_download() as download_info:
            await locator.click()
        download = await download_info.value
        filename = Path(download.suggested_filename or "download.bin").name
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / filename
        if destination.exists():
            destination = (
                destination_dir / f"{destination.stem}-{uuid4().hex[:8]}{destination.suffix}"
            )
        await download.save_as(str(destination))
        return destination

    async def read_captions(self) -> list[CaptionTurn]:
        raw = await self.page.evaluate(
            r"""
            () => {
              const selectors = [
                '[data-robin-caption]',
                '[aria-live="polite"]',
                '[aria-live="assertive"]',
                '.iTTPOb',
                '[jsname="tgaKEf"]'
              ];
              const nodes = [...new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]))];
              const visible = node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              const clean = value => (value || '').replace(/\s+/g, ' ').trim();
              const ignored = /^(you are presenting|someone joined|someone left|microphone|camera|captions?)$/i;
              const results = [];
              for (const node of nodes) {
                if (!visible(node)) continue;
                const speakerNode = node.querySelector(
                  '[data-speaker-name], .zs7s8d, .KcIKyf, [aria-label^="Caption from "]'
                );
                const textNode = node.querySelector(
                  '[data-caption-text], .CNusmb, .ygicle, [jsname="YSxPC"]'
                );
                let speaker = clean(
                  speakerNode?.getAttribute('data-speaker-name') ||
                  speakerNode?.getAttribute('aria-label')?.replace(/^Caption from\s+/i, '') ||
                  speakerNode?.textContent
                );
                let text = clean(textNode?.textContent);
                if (!speaker || !text) {
                  const parts = [...node.children].map(child => clean(child.textContent)).filter(Boolean);
                  if (parts.length >= 2 && parts[0].length <= 80) {
                    speaker ||= parts[0];
                    text ||= parts.slice(1).join(' ');
                  }
                }
                if (speaker && text && !ignored.test(speaker) && speaker !== text && text.length <= 1000) {
                  results.push({speaker_name: speaker.slice(0, 120), text: text.slice(0, 1000)});
                }
              }
              return results.slice(-12);
            }
            """
        )
        seen: set[tuple[str, str]] = set()
        turns: list[CaptionTurn] = []
        for item in raw:
            speaker = " ".join(str(item.get("speaker_name", "")).split())[:120]
            text = " ".join(str(item.get("text", "")).split())[:1000]
            key = (speaker.casefold(), text.casefold())
            if speaker and text and key not in seen:
                seen.add(key)
                turns.append(CaptionTurn(speaker, text))
        return turns

    def _resolve_operator_ref(self, ref: str):
        if ref not in self._operator_refs or ref not in self._operator_elements:
            raise KeyError(f"Unknown or stale page element: {ref}; inspect the page again")
        return self._operator_elements[ref], self._operator_refs[ref]

    async def wait_for_presentation_ready(
        self,
        expected_task_id: str,
        expected_revision: str | None,
        timeout_ms: int,
    ) -> PresentationReadiness:
        error = self.page.locator('[data-robin-presentation-error="true"]')
        ready = self.page.locator('[data-robin-presentation-ready="true"]')
        try:
            await ready.wait_for(state="visible", timeout=timeout_ms)
        except Exception as exc:
            if await error.count() and await error.first.is_visible():
                detail = (await error.first.text_content() or "unknown renderer error").strip()
                raise RuntimeError(f"Presentation renderer reported an error: {detail}") from exc
            raise TimeoutError(
                "Presentation renderer did not become ready before the timeout"
            ) from exc
        if await error.count() and await error.first.is_visible():
            detail = (await error.first.text_content() or "unknown renderer error").strip()
            raise RuntimeError(f"Presentation renderer reported an error: {detail}")
        actual_task_id = (await ready.get_attribute("data-robin-task-id") or "").strip()
        actual_revision = (await ready.get_attribute("data-robin-revision") or "").strip()
        SimulatedPageDriver._validate_presentation_identity(
            actual_task_id,
            actual_revision,
            expected_task_id,
            expected_revision,
        )
        return PresentationReadiness(task_id=actual_task_id, revision=actual_revision)

    async def bring_to_front(self) -> None:
        await self.page.bring_to_front()

    async def close(self) -> None:
        await self.page.close()

    def is_closed(self) -> bool:
        return self.page.is_closed()

    def _locator(self, candidate: SelectorCandidate):
        if candidate.role and candidate.name_regex:
            return self.page.get_by_role(
                candidate.role, name=re.compile(candidate.name_regex, re.I)
            )
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
