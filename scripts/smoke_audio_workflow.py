from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge import AudioBridge
from robin_core.audio.bridge_client import ProcessBridgeClient
from robin_core.config import load_settings


async def serve_audio(audio_path: Path) -> tuple[asyncio.AbstractServer, int]:
    audio = audio_path.read_bytes()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            writer.close()
            await writer.wait_closed()
            return
        target = request.split(b" ", 2)[1]
        if target == b"/voice.wav":
            body = audio
            content_type = "audio/wav"
        else:
            body = b"""<!doctype html><html><body>
            <h1>Robin Chrome audio capture check</h1>
            <audio id="voice" src="/voice.wav" preload="auto"></audio>
            </body></html>"""
            content_type = "text/html; charset=utf-8"
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def main() -> None:
    settings = load_settings()
    if settings.audio.mode != "openai" or settings.audio.bridge_mode != "process":
        raise SystemExit("Run scripts/setup_partner.sh --real-meet before the live audio workflow.")
    if settings.browser.connection_mode != "cdp":
        raise SystemExit("Live audio workflow requires browser.connection_mode=cdp.")

    if settings.audio.bridge_executable is None:
        raise SystemExit("audio.bridge_executable is required for the live audio workflow.")
    output_dir = settings.workspace.root / settings.workspace.sessions_dir / "audio-workflow"
    voice = AudioBridge(settings.audio, output_dir, settings.openai_api_key)
    print(f"1/3 Generating real speech and routing it to {settings.audio.output_device_name}…")
    record = await voice.speak(
        "Robin end to end audio check. Speech generation, virtual microphone, and playback are working."
    )
    if not record.path or not record.duration_seconds:
        raise SystemExit("Speech generation did not produce a valid WAV.")
    voice_path = output_dir / record.path

    server, port = await serve_audio(voice_path)
    capture_path = output_dir / "chrome-capture.wav"
    bridge = ProcessBridgeClient(
        settings.audio.bridge_executable,
        settings.audio.output_device_name,
    )
    playwright = await async_playwright().start()
    page = None
    try:
        browser = await playwright.chromium.connect_over_cdp(
            settings.browser.cdp_endpoint,
            no_defaults=True,
        )
        context = browser.contexts[0]
        page = await context.new_page()
        await page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
        capture_ms = min(max(int(record.duration_seconds * 1000) + 2_000, 4_000), 15_000)
        print(f"2/3 Playing the phrase in Chrome and capturing {capture_ms / 1000:.1f}s…")
        capture = asyncio.create_task(
            bridge.capture_audio_sample(
                settings.audio.capture_bundle_id,
                capture_path,
                duration_ms=capture_ms,
            )
        )
        await asyncio.sleep(0.8)
        await page.locator("#voice").evaluate("audio => audio.play()")
        result = await capture
        if not result.ok:
            raise SystemExit(f"Native Chrome capture failed: {result.error}")
        rms = float(result.result.get("rms", 0.0))
        peak = float(result.result.get("peak", 0.0))
        if rms < settings.audio.silence_rms_threshold:
            raise SystemExit(
                f"Chrome capture was silent: rms={rms:.6f}, peak={peak:.6f}, "
                f"threshold={settings.audio.silence_rms_threshold:.6f}"
            )

        print(f"3/3 Transcribing the captured Chrome audio (rms={rms:.4f}, peak={peak:.4f})…")
        transcript = await voice.transcribe_file(capture_path)
        if "robin" not in transcript.lower() or "audio" not in transcript.lower():
            raise SystemExit(f"Capture transcription was unexpected: {transcript!r}")
        print(
            "Live audio workflow passed: "
            f"voice={record.duration_seconds:.2f}s, "
            f"device={record.playback_device}, "
            f"captured={transcript!r}"
        )
    finally:
        if page is not None:
            await page.close()
        await playwright.stop()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
