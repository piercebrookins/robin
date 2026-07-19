from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.browser.controller import BrowserController
from robin_core.browser.operator_agent import ControlledBrowserAgent
from robin_core.config import load_settings


async def main() -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for the model browser smoke test.")
    if settings.browser.automation_mode != "playwright":
        raise SystemExit("Run scripts/setup_partner.sh --real-meet first.")

    browser = BrowserController(settings.browser)
    page_name = "operator-smoke"
    try:
        await browser.open_page(page_name, "http://localhost:3000")
        snapshot = await browser.inspect_for_operator(page_name)
        if "robin" not in f"{snapshot.title} {snapshot.text}".casefold():
            raise SystemExit(
                "Dashboard identity was not observable before the model run: "
                f"title={snapshot.title!r}, text={snapshot.text[:160]!r}"
            )
        agent = ControlledBrowserAgent(settings, browser)
        result = await agent.execute(
            "Inspect this local Robin dashboard and report whether the page identifies itself as Robin. Do not click or type anything.",
            page_name,
        )
        if result.status != "completed":
            raise SystemExit(f"Browser operator unexpectedly paused: {result.model_dump()}")
        tools = [item.get("tool") for item in result.tool_calls]
        if "inspect_page" not in tools or "finish_browser_task" not in tools:
            raise SystemExit(f"Browser operator did not inspect and finish: {tools}")
        print(f"Model browser operator passed: {result.summary}")
        print(f"Tool trace: {tools}")
    finally:
        await browser.close_page(page_name)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
