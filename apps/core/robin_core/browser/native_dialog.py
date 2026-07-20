from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Awaitable, Callable, Protocol

from robin_core.config import BrowserConfig


CommandRunner = Callable[[list[str]], Awaitable[str]]


def computer_use_permissions_granted(output: str) -> bool:
    """Accept current JSON and legacy human-readable cua-driver output."""

    start = output.find("{")
    if start >= 0:
        try:
            payload = json.loads(output[start:])
        except json.JSONDecodeError:
            pass
        else:
            return payload.get("accessibility") is True and payload.get("screen_recording") is True
    lowered = output.casefold()
    return bool(
        re.search(r"accessibility\s*:\s*(?:granted|true)\b", lowered)
        and re.search(r"screen recording\s*:\s*(?:granted|true)\b", lowered)
    )


@dataclass
class ShareDialogEvent:
    action: str
    attempt: int
    ok: bool
    detail: str
    screenshot_path: str | None = None


@dataclass
class ShareDialogResult:
    method: str
    source_title: str
    source_selected: bool
    picker_closed: bool
    events: list[ShareDialogEvent] = field(default_factory=list)


class ShareDialogError(RuntimeError):
    def __init__(self, message: str, events: list[ShareDialogEvent]):
        super().__init__(message)
        self.events = events


class ShareDialogController(Protocol):
    async def select_and_share(self, source_title: str) -> ShareDialogResult: ...


class SimulatedShareDialogController:
    async def select_and_share(self, source_title: str) -> ShareDialogResult:
        return ShareDialogResult(
            method="simulator",
            source_title=source_title,
            source_selected=True,
            picker_closed=True,
            events=[ShareDialogEvent("share_confirmed", 1, True, "simulated picker closed")],
        )


class CuaDriverShareDialogController:
    """Controls only Chrome's native share picker through macOS Accessibility.

    Web-page controls remain owned by Playwright. Commands are passed as argument
    arrays (never through a shell), are pinned to the Chrome PID listening on the
    configured loopback debugging port, and are verified after every action.
    """

    picker_markers = ("Choose what to share", "Share your screen")

    def __init__(self, config: BrowserConfig, runner: CommandRunner | None = None):
        self.config = config
        self.runner = runner or self._run_command
        self.events: list[ShareDialogEvent] = []
        self.trace_path = config.recovery_screenshot_dir / "share-dialog-trace.jsonl"

    async def select_and_share(self, source_title: str) -> ShareDialogResult:
        self.events = []
        attempts = max(self.config.share_dialog_retries, 0) + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            pid: int | None = None
            window_id: int | None = None
            try:
                await self._ensure_daemon(attempt)
                await self._ensure_permissions(attempt)
                pid = await self._resolve_chrome_pid(attempt)
                window_id, tree = await self._wait_for_picker(pid, attempt)
                picker_tree = self._picker_window_subtree(tree)
                source_indices = self._find_indices(
                    picker_tree, source_title, actionable_only=True
                )
                if not source_indices:
                    raise RuntimeError(f"share source titled {source_title!r} was not found")
                if len(source_indices) != 1:
                    raise RuntimeError(
                        f"share source title {source_title!r} was ambiguous; "
                        f"found {len(source_indices)} actionable matches"
                    )
                source_index = source_indices[0]
                await self._click(pid, window_id, source_index)
                self._record("select_source", attempt, True, f"selected {source_title!r}")

                selected_tree, selected_shot = await self._snapshot(
                    pid, window_id, attempt, "selected"
                )
                share_index = self._find_share_button(
                    self._picker_window_subtree(selected_tree)
                )
                if share_index is None:
                    raise RuntimeError("enabled Share button was not found after source selection")
                await self._click(pid, window_id, share_index)
                self._record(
                    "confirm_share", attempt, True, "clicked enabled Share button", selected_shot
                )

                picker_closed = await self._verify_picker_closed(pid, window_id)
                self._record(
                    "verify_picker_closed",
                    attempt,
                    picker_closed,
                    "picker closed" if picker_closed else "picker remained visible",
                    None,
                )
                if not picker_closed:
                    raise RuntimeError("Chrome share picker remained visible after confirmation")
                return ShareDialogResult(
                    method="cua_driver_accessibility",
                    source_title=source_title,
                    source_selected=True,
                    picker_closed=True,
                    events=list(self.events),
                )
            except Exception as exc:
                last_error = exc
                self._record("attempt_failed", attempt, False, str(exc))
                if attempt < attempts:
                    await asyncio.sleep(max(self.config.ui_recovery_pause_ms, 0) / 1000)
                elif pid is not None and window_id is not None:
                    await self._cancel_picker(pid, window_id, attempt)
        raise ShareDialogError(
            f"Chrome share picker automation failed: {last_error}", list(self.events)
        )

    async def _ensure_daemon(self, attempt: int) -> None:
        try:
            await self.runner([self.config.computer_use_command, "status"])
        except RuntimeError:
            await self.runner(["open", "-n", "-g", "-a", "CuaDriver", "--args", "serve"])
            deadline = monotonic() + 5
            while monotonic() < deadline:
                try:
                    await self.runner([self.config.computer_use_command, "status"])
                    break
                except RuntimeError:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Codex/macOS Computer Use daemon did not start")
        self._record("computer_use_daemon", attempt, True, "computer-use daemon is running")

    async def _ensure_permissions(self, attempt: int) -> None:
        output = await self.runner(
            [self.config.computer_use_command, "check_permissions", '{"prompt":false}']
        )
        if not computer_use_permissions_granted(output):
            raise RuntimeError(
                "computer-use Accessibility or Screen Recording permission is not granted"
            )
        self._record("permissions", attempt, True, "Accessibility and Screen Recording granted")

    async def _resolve_chrome_pid(self, attempt: int) -> int:
        output = await self.runner(
            ["lsof", "-nP", f"-iTCP:{self.config.remote_debugging_port}", "-sTCP:LISTEN", "-t"]
        )
        pids = [int(value) for value in output.split() if value.isdigit()]
        if len(set(pids)) != 1:
            raise RuntimeError(
                f"expected one Robin Chrome PID on 127.0.0.1:{self.config.remote_debugging_port}; found {sorted(set(pids))}"
            )
        pid = pids[0]
        self._record("resolve_chrome", attempt, True, f"pinned computer use to pid {pid}")
        return pid

    async def _wait_for_picker(self, pid: int, attempt: int) -> tuple[int, str]:
        deadline = monotonic() + max(self.config.share_dialog_timeout_ms, 1) / 1000
        while monotonic() < deadline:
            windows = await self._list_windows(pid)
            for window in windows:
                if not window.get("is_on_screen", True):
                    continue
                window_id = int(window["window_id"])
                tree, screenshot = await self._snapshot(pid, window_id, attempt, "picker")
                if self._picker_present(tree):
                    self._record(
                        "detect_picker",
                        attempt,
                        True,
                        f"picker found in window {window_id}",
                        screenshot,
                    )
                    return window_id, tree
            await asyncio.sleep(max(self.config.share_dialog_poll_interval_ms, 10) / 1000)
        raise TimeoutError("Chrome share picker did not appear before the timeout")

    async def _list_windows(self, pid: int) -> list[dict[str, object]]:
        output = await self._cua("list_windows", {"pid": pid})
        payload = json.loads(output)
        return list(payload.get("windows", []))

    async def _verify_picker_closed(self, pid: int, picker_window_id: int) -> bool:
        previous_mode = await self._capture_mode()
        await self._set_capture_mode("ax")
        try:
            deadline = monotonic() + max(self.config.share_dialog_timeout_ms, 1) / 1000
            while monotonic() < deadline:
                windows = await self._list_windows(pid)
                candidates = [
                    window
                    for window in windows
                    if int(window["window_id"]) == picker_window_id
                    or self._picker_present(str(window.get("title", "")))
                ]
                if not candidates:
                    return True
                picker_remains = False
                for window in candidates:
                    try:
                        tree = await self._ax_tree(pid, int(window["window_id"]))
                    except RuntimeError as exc:
                        if "No window with window_id" in str(exc):
                            continue
                        raise
                    if self._picker_present(tree):
                        picker_remains = True
                        break
                if not picker_remains:
                    return True
                await asyncio.sleep(max(self.config.share_dialog_poll_interval_ms, 10) / 1000)
            return False
        finally:
            await self._set_capture_mode(previous_mode)

    async def _capture_mode(self) -> str:
        output = await self.runner([self.config.computer_use_command, "get_config"])
        start = output.find("{")
        if start < 0:
            raise RuntimeError("computer-use configuration output was not JSON")
        payload = json.loads(output[start:])
        return str(payload.get("capture_mode", "som"))

    async def _set_capture_mode(self, mode: str) -> None:
        await self._cua("set_config", {"key": "capture_mode", "value": mode})

    async def _ax_tree(self, pid: int, window_id: int) -> str:
        output = await self._cua("get_window_state", {"pid": pid, "window_id": window_id})
        payload = json.loads(output)
        return str(payload.get("tree_markdown", ""))

    async def _snapshot(
        self, pid: int, window_id: int, attempt: int, label: str
    ) -> tuple[str, str]:
        self.config.recovery_screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = (
            self.config.recovery_screenshot_dir
            / f"{timestamp}_share_dialog_{label}_attempt{attempt}.png"
        )
        output = await self._cua(
            "get_window_state",
            {"pid": pid, "window_id": window_id, "screenshot_out_file": str(path)},
        )
        payload = json.loads(output)
        tree = str(payload.get("tree_markdown", ""))
        path.with_suffix(".ax.txt").write_text(tree, encoding="utf-8")
        return tree, str(path)

    async def _click(self, pid: int, window_id: int, element_index: int) -> None:
        await self._cua(
            "click",
            {"pid": pid, "window_id": window_id, "element_index": element_index},
        )

    async def _cancel_picker(self, pid: int, window_id: int, attempt: int) -> None:
        try:
            tree = await self._ax_tree(pid, window_id)
            cancel_index = self._find_cancel_button(self._picker_window_subtree(tree))
            if cancel_index is None:
                self._record("cancel_picker", attempt, False, "enabled Cancel button not found")
                return
            await self._click(pid, window_id, cancel_index)
            closed = await self._verify_picker_closed(pid, window_id)
            self._record(
                "cancel_picker",
                attempt,
                closed,
                "picker cancelled" if closed else "Cancel clicked but picker remained visible",
            )
        except Exception as exc:
            self._record("cancel_picker", attempt, False, f"cleanup failed: {exc}")

    async def _cua(self, tool: str, arguments: dict[str, object]) -> str:
        return await self.runner([self.config.computer_use_command, tool, json.dumps(arguments)])

    def _record(
        self,
        action: str,
        attempt: int,
        ok: bool,
        detail: str,
        screenshot_path: str | None = None,
    ) -> None:
        event = ShareDialogEvent(action, attempt, ok, detail, screenshot_path)
        self.events.append(event)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as trace:
            trace.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    @classmethod
    def _picker_present(cls, tree: str) -> bool:
        lowered = tree.lower()
        return any(marker.lower() in lowered for marker in cls.picker_markers)

    @classmethod
    def _picker_window_subtree(cls, tree: str) -> str:
        """Return one picker window when CuaDriver mirrors it in the app tree."""

        lines = tree.splitlines()
        top_level = re.compile(r"^- \[\d+\] AX(?:Window|MenuBar)\b")
        for start, line in enumerate(lines):
            if not re.match(r"^- \[\d+\] AXWindow\b", line):
                continue
            end = start + 1
            while end < len(lines) and not top_level.match(lines[end]):
                end += 1
            candidate = "\n".join(lines[start:end])
            if cls._picker_present(candidate):
                return candidate
        return tree

    @staticmethod
    def _find_indices(tree: str, label: str, actionable_only: bool = False) -> list[int]:
        matches: list[tuple[int, str]] = []
        label_pattern = re.compile(rf'(?:["=(]\s*){re.escape(label)}(?:["\s)]|$)', re.IGNORECASE)
        roles = ("AXRow", "AXCell", "AXRadioButton", "AXButton", "AXGroup")
        for line in tree.splitlines():
            if not label_pattern.search(line):
                continue
            match = re.search(r"\[(\d+)\]", line)
            if match:
                role = next((candidate for candidate in roles if candidate in line), "")
                if not actionable_only or role:
                    matches.append((int(match.group(1)), role))
        if actionable_only:
            for preferred_role in roles:
                preferred = [index for index, role in matches if role == preferred_role]
                if preferred:
                    return preferred
        return [index for index, _ in matches]

    @staticmethod
    def _find_share_button(tree: str) -> int | None:
        for line in tree.splitlines():
            if "AXButton" not in line or "DISABLED" in line:
                continue
            if re.search(r'["=(]\s*Share(?:["\s)]|$)', line, re.IGNORECASE):
                match = re.search(r"\[(\d+)\]", line)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _find_cancel_button(tree: str) -> int | None:
        for line in tree.splitlines():
            if "AXButton" not in line or "DISABLED" in line:
                continue
            if re.search(r'["=(]\s*Cancel(?:["\s)]|$)', line, re.IGNORECASE):
                match = re.search(r"\[(\d+)\]", line)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    async def _run_command(command: list[str]) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"required computer-use command is unavailable: {command[0]}"
            ) from exc
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = (
                stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
            )
            raise RuntimeError(f"command failed ({command[0]} {command[1]}): {detail}")
        return stdout.decode(errors="replace")


def create_share_dialog_controller(config: BrowserConfig) -> ShareDialogController:
    if config.share_dialog_mode == "simulator":
        return SimulatedShareDialogController()
    if config.share_dialog_mode == "cua_driver":
        return CuaDriverShareDialogController(config)
    raise ValueError(f"Unsupported browser.share_dialog_mode: {config.share_dialog_mode}")
