from __future__ import annotations

import json
from pathlib import Path

import pytest

from robin_core.browser.native_dialog import CuaDriverShareDialogController, ShareDialogError
from robin_core.config import BrowserConfig


class ScriptedRunner:
    def __init__(self, tmp_path: Path, trees: list[str], lsof_output: str = "4242\n"):
        self.tmp_path = tmp_path
        self.trees = list(trees)
        self.lsof_output = lsof_output
        self.clicks: list[dict[str, object]] = []
        self.capture_mode_changes: list[str] = []

    async def __call__(self, command: list[str]) -> str:
        if command[0] == "lsof":
            return self.lsof_output
        if command[1] == "status":
            return "cua-driver daemon is running"
        if command[1] == "check_permissions":
            return "Accessibility: granted\nScreen Recording: granted\n"
        if command[1] == "get_config":
            return json.dumps({"capture_mode": "som"})
        arguments = json.loads(command[2])
        if command[1] == "set_config":
            self.capture_mode_changes.append(str(arguments["value"]))
            return json.dumps({"ok": True})
        if command[1] == "list_windows":
            if len(self.clicks) >= 2:
                return json.dumps(
                    {
                        "windows": [
                            {
                                "window_id": 92,
                                "title": "Robin Share Request Fixture",
                                "is_on_screen": True,
                            }
                        ]
                    }
                )
            return json.dumps(
                {
                    "windows": [
                        {
                            "window_id": 91,
                            "title": "Meet",
                            "is_on_screen": True,
                        }
                    ]
                }
            )
        if command[1] == "get_window_state":
            if "screenshot_out_file" in arguments:
                screenshot = Path(str(arguments["screenshot_out_file"]))
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                screenshot.write_bytes(b"png")
            tree = self.trees.pop(0) if self.trees else '[1] AXWindow "Meet"'
            return json.dumps({"tree_markdown": tree})
        if command[1] == "click":
            self.clicks.append(arguments)
            return json.dumps({"ok": True})
        raise AssertionError(f"unexpected command: {command}")


def picker_tree(*, share_disabled: bool) -> str:
    disabled = " DISABLED" if share_disabled else ""
    return "\n".join(
        [
            '[1] AXWindow "Meet"',
            '[2] AXStaticText "Choose what to share with meet.google.com"',
            "[17] AXRow (Robin Presentation)",
            f'[22] AXButton "Share"{disabled}',
            '[23] AXButton "Cancel"',
        ]
    )


@pytest.mark.asyncio
async def test_cua_driver_selects_only_named_source_and_verifies_picker_closed(
    tmp_path: Path,
) -> None:
    runner = ScriptedRunner(
        tmp_path,
        [
            picker_tree(share_disabled=True),
            picker_tree(share_disabled=False),
            '[1] AXWindow "Meet"',
        ],
    )
    config = BrowserConfig(
        share_dialog_mode="cua_driver",
        recovery_screenshot_dir=tmp_path / "recovery",
        share_dialog_retries=0,
        share_dialog_timeout_ms=100,
        share_dialog_poll_interval_ms=10,
    )
    controller = CuaDriverShareDialogController(config, runner)

    result = await controller.select_and_share("Robin Presentation")

    assert result.source_selected is True
    assert result.picker_closed is True
    assert result.method == "cua_driver_accessibility"
    assert [click["element_index"] for click in runner.clicks] == [17, 22]
    assert all(click["pid"] == 4242 and click["window_id"] == 91 for click in runner.clicks)
    assert runner.capture_mode_changes == ["ax", "som"]
    screenshots = [Path(event.screenshot_path) for event in result.events if event.screenshot_path]
    assert screenshots and all(path.exists() for path in screenshots)
    trace = config.recovery_screenshot_dir / "share-dialog-trace.jsonl"
    assert trace.exists()
    assert "verify_picker_closed" in trace.read_text()


@pytest.mark.asyncio
async def test_cua_driver_retries_and_returns_diagnostics_when_source_is_missing(
    tmp_path: Path,
) -> None:
    missing_source = picker_tree(share_disabled=True).replace("Robin Presentation", "Unrelated Tab")
    runner = ScriptedRunner(tmp_path, [missing_source, missing_source, missing_source])
    config = BrowserConfig(
        share_dialog_mode="cua_driver",
        recovery_screenshot_dir=tmp_path / "recovery",
        share_dialog_retries=1,
        share_dialog_timeout_ms=50,
        share_dialog_poll_interval_ms=10,
        ui_recovery_pause_ms=0,
    )
    controller = CuaDriverShareDialogController(config, runner)

    with pytest.raises(ShareDialogError, match="was not found") as error:
        await controller.select_and_share("Robin Presentation")

    failures = [event for event in error.value.events if event.action == "attempt_failed"]
    assert [event.attempt for event in failures] == [1, 2]
    assert [click["element_index"] for click in runner.clicks] == [23]
    assert any(event.action == "cancel_picker" for event in error.value.events)
    assert (config.recovery_screenshot_dir / "share-dialog-trace.jsonl").exists()


@pytest.mark.asyncio
async def test_cua_driver_refuses_ambiguous_remote_debugging_processes(tmp_path: Path) -> None:
    runner = ScriptedRunner(tmp_path, [], lsof_output="4242\n4343\n")
    config = BrowserConfig(
        share_dialog_mode="cua_driver",
        recovery_screenshot_dir=tmp_path / "recovery",
        share_dialog_retries=0,
    )
    controller = CuaDriverShareDialogController(config, runner)

    with pytest.raises(ShareDialogError, match="expected one Robin Chrome PID"):
        await controller.select_and_share("Robin Presentation")

    assert runner.clicks == []


@pytest.mark.asyncio
async def test_cua_driver_refuses_ambiguous_presentation_titles(tmp_path: Path) -> None:
    ambiguous = picker_tree(share_disabled=True).replace(
        "[17] AXRow (Robin Presentation)",
        "[17] AXRow (Robin Presentation)\n[18] AXRow (Robin Presentation)",
    )
    runner = ScriptedRunner(tmp_path, [ambiguous, ambiguous])
    config = BrowserConfig(
        share_dialog_mode="cua_driver",
        recovery_screenshot_dir=tmp_path / "recovery",
        share_dialog_retries=0,
    )
    controller = CuaDriverShareDialogController(config, runner)

    with pytest.raises(ShareDialogError, match="ambiguous"):
        await controller.select_and_share("Robin Presentation")

    assert [click["element_index"] for click in runner.clicks] == [23]
