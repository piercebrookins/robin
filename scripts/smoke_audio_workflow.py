from __future__ import annotations

import asyncio
import shutil
import signal
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
    output_dir.mkdir(parents=True, exist_ok=True)
    voice = AudioBridge(settings.audio, output_dir, settings.openai_api_key)
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise SystemExit("ffmpeg is required for the BlackHole loopback proof.")
    loopback_path = output_dir / "blackhole-loopback.wav"
    loopback_path.unlink(missing_ok=True)
    print(f"1/4 Generating real speech and proving output through {settings.audio.output_device_name}…")
    loopback = await asyncio.create_subprocess_exec(
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "avfoundation",
        "-i",
        f":{settings.audio.output_device_name}",
        "-ac",
        "1",
        "-ar",
        "24000",
        str(loopback_path),
    )
    try:
        await asyncio.sleep(2.0)
        record = await voice.speak(
            "Robin end to end audio check. Speech generation, virtual microphone, and playback are working."
        )
        await asyncio.sleep(0.5)
    finally:
        if loopback.returncode is None:
            loopback.send_signal(signal.SIGINT)
        await loopback.wait()
    if loopback.returncode not in {0, 255}:
        raise SystemExit(f"BlackHole loopback recorder failed with exit code {loopback.returncode}.")
    if not record.path or not record.duration_seconds:
        raise SystemExit("Speech generation did not produce a valid WAV.")
    if settings.audio.streaming_speech_enabled:
        if not record.streaming or record.playback_route != "pcm_stream":
            raise SystemExit(
                f"Speech did not use the streaming PCM route: {record.playback_route!r}"
            )
        if record.time_to_first_audio_ms is None or record.time_to_first_audio_ms >= int(
            record.duration_seconds * 1000
        ):
            raise SystemExit(
                "Streaming speech did not begin before the complete utterance duration."
            )
    voice_path = output_dir / record.path
    loopback_transcript = await voice.transcribe_file(loopback_path)
    if "robin" not in loopback_transcript.lower() or "audio" not in loopback_transcript.lower():
        raise SystemExit(
            f"BlackHole loopback transcription was unexpected: {loopback_transcript!r}"
        )

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
        print(f"2/4 Playing the phrase in Chrome and capturing {capture_ms / 1000:.1f}s…")
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

        print(f"3/4 Transcribing the captured Chrome audio (rms={rms:.4f}, peak={peak:.4f})…")
        transcript = await voice.transcribe_file(capture_path)
        if "robin" not in transcript.lower() or "audio" not in transcript.lower():
            raise SystemExit(f"Capture transcription was unexpected: {transcript!r}")
        print("4/4 Both directions passed: Chrome → Robin transcription and Robin → BlackHole speech.")
        print(
            "Live audio workflow passed: "
            f"voice={record.duration_seconds:.2f}s, "
            f"device={record.playback_device}, "
            f"route={record.playback_route}, "
            f"first_audio={record.time_to_first_audio_ms}ms, "
            f"loopback={loopback_transcript!r}, "
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
