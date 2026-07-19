from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.browser.controller import BrowserController
from robin_core.config import BrowserConfig
from robin_core.meeting.adapters.google_meet import GoogleMeetAdapter
from robin_core.schemas import MeetingState


MEET_FIXTURE = b"""<!doctype html>
<html>
<head><title>Robin Meet Fixture</title></head>
<body>
  <h1>Fake Google Meet</h1>
  <button data-testid="camera-button">Turn off camera</button>
  <button data-testid="mute-button">Turn off microphone</button>
  <button data-testid="unmute-button">Turn on microphone</button>
  <button aria-label="Microphone: BlackHole 2ch (Virtual)" hidden>BlackHole 2ch</button>
  <button data-testid="join-button">Join now</button>
  <button data-testid="leave-button" hidden>Leave call</button>
  <button data-testid="joined-signal" hidden>Leave meeting</button>
  <button data-testid="present-button">Present now</button>
  <button data-testid="share-tab-option" hidden>A tab</button>
  <button data-testid="stop-presenting-button" hidden>Stop presenting</button>
  <span data-testid="presenting-signal" hidden>You are presenting</span>
  <script>
    document.querySelector('[data-testid="join-button"]').addEventListener('click', () => {
      document.querySelector('[data-testid="leave-button"]').hidden = false;
      document.querySelector('[data-testid="joined-signal"]').hidden = false;
    });
    document.querySelector('[data-testid="present-button"]').addEventListener('click', () => {
      document.querySelector('[data-testid="share-tab-option"]').hidden = false;
    });
    document.querySelector('[data-testid="share-tab-option"]').addEventListener('click', () => {
      document.querySelector('[data-testid="share-tab-option"]').hidden = true;
      document.querySelector('[data-testid="stop-presenting-button"]').hidden = false;
      document.querySelector('[data-testid="presenting-signal"]').hidden = false;
    });
  </script>
</body>
</html>
"""


async def serve_fixture() -> tuple[asyncio.AbstractServer, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.read(2048)
        path = request.split(b" ", 2)[1] if request.startswith(b"GET ") else b"/"
        body = (
            b"<main data-robin-presentation-ready='true' data-robin-task-id='task-1' "
            b"data-robin-revision='1'>Ready</main>"
            if path.startswith(b"/present/task-1")
            else MEET_FIXTURE
        )
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"content-type: text/html; charset=utf-8\r\n"
            + f"content-length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    server, port = await serve_fixture()
    config = BrowserConfig(
        automation_mode="playwright",
        allowed_meet_hosts=["127.0.0.1"],
        profile_dir=root / "RobinWorkspace/sessions/chrome-fixture-profile",
        headless=True,
        prejoin_timeout_ms=5_000,
        admission_timeout_ms=5_000,
    )
    browser = BrowserController(config)
    adapter = GoogleMeetAdapter(browser, config)
    try:
        await adapter.navigate(f"http://127.0.0.1:{port}/meet")
        await adapter.join()
        await adapter.start_presenting(f"http://127.0.0.1:{port}/present/task-1?revision=1")
        if adapter.state != MeetingState.PRESENTING or not adapter.presenting:
            raise SystemExit("Meet fixture did not reach presenting state")
        print(f"Meet fixture smoke passed: profile={config.profile_dir}")
    finally:
        await browser.close()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
