from __future__ import annotations

from pathlib import Path

import pytest

from robin_core.browser.controller import BrowserController, OperatorApprovalRequired
from robin_core.browser.page_driver import InteractiveElement, SimulatedPageDriver


def controller_with_page(
    *elements: InteractiveElement,
) -> tuple[BrowserController, SimulatedPageDriver]:
    controller = BrowserController()
    page = SimulatedPageDriver(
        url="https://meet.google.com/test-room",
        operator_elements={element.ref: element for element in elements},
    )
    controller.pages["meet"] = page
    return controller, page


@pytest.mark.asyncio
async def test_operator_prefers_semantic_inspection_and_safe_clicks() -> None:
    controller, page = controller_with_page(
        InteractiveElement("e1", "button", "More options", "button")
    )

    snapshot = await controller.inspect_for_operator("meet")
    clicked = await controller.click_for_operator("meet", "e1")

    assert snapshot.url == "https://meet.google.com/test-room"
    assert clicked.name == "More options"
    assert page.clicked == ["e1"]


@pytest.mark.asyncio
async def test_external_meeting_actions_require_explicit_approval() -> None:
    controller, page = controller_with_page(
        InteractiveElement("e1", "button", "Join now", "button")
    )

    with pytest.raises(OperatorApprovalRequired, match="Approval required"):
        await controller.click_for_operator("meet", "e1")

    assert page.clicked == []
    await controller.click_for_operator("meet", "e1", approved=True)
    assert page.clicked == ["e1"]


@pytest.mark.asyncio
async def test_operator_blocks_passwords_and_bounds_text() -> None:
    password_controller, _ = controller_with_page(
        InteractiveElement("e1", "textbox", "Password", "password")
    )
    with pytest.raises(PermissionError, match="password"):
        await password_controller.fill_for_operator("meet", "e1", "secret", approved=True)

    text_controller, page = controller_with_page(
        InteractiveElement("e2", "textbox", "Meeting notes", "textarea")
    )
    await text_controller.fill_for_operator("meet", "e2", "Short note")
    assert page.filled["e2"] == "Short note"
    with pytest.raises(ValueError, match="2000"):
        await text_controller.fill_for_operator("meet", "e2", "x" * 2001)


@pytest.mark.asyncio
async def test_operator_rejects_closed_or_stale_targets() -> None:
    controller, _ = controller_with_page()

    with pytest.raises(KeyError, match="stale"):
        await controller.click_for_operator("meet", "e999")
    with pytest.raises(KeyError, match="not open"):
        await controller.inspect_for_operator("missing")


@pytest.mark.asyncio
async def test_upload_and_download_are_explicitly_approved_and_scoped(tmp_path: Path) -> None:
    source = tmp_path / "source-data" / "report.pdf"
    source.parent.mkdir()
    source.write_bytes(b"report")
    controller, page = controller_with_page(
        InteractiveElement("e1", "input", "Attach file", "file"),
        InteractiveElement("e2", "link", "Export report", "a"),
    )

    with pytest.raises(OperatorApprovalRequired, match="upload"):
        await controller.upload_for_operator("meet", "e1", source)
    await controller.upload_for_operator("meet", "e1", source, approved=True)
    assert page.uploaded["e1"] == str(source)

    download_dir = tmp_path / "generated" / "browser-downloads"
    with pytest.raises(OperatorApprovalRequired, match="download"):
        await controller.download_for_operator("meet", "e2", download_dir)
    downloaded = await controller.download_for_operator("meet", "e2", download_dir, approved=True)
    assert downloaded.is_file()


@pytest.mark.asyncio
async def test_cdp_cleanup_disconnects_without_closing_users_chrome() -> None:
    class Closable:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class Stoppable:
        def __init__(self) -> None:
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    context = Closable()
    browser = Closable()
    playwright = Stoppable()
    controller = BrowserController()
    controller._context = context
    controller._browser = browser
    controller._playwright = playwright
    controller._owns_browser = False

    await controller.close()

    assert context.closed is False
    assert browser.closed is False
    assert playwright.stopped is True
