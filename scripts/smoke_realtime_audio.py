from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge import AudioBridge
from robin_core.audio.bridge_client import ProcessBridgeClient
from robin_core.audio.realtime import RealtimeTranscriber
from robin_core.config import load_settings

from smoke_audio_workflow import serve_audio


def phrase_coverage(expected: str, actual: str) -> float:
    ignored = {"robin", "is"}
    expected_words = {
        word for word in re.findall(r"[a-z0-9]+", expected.casefold()) if word not in ignored
    }
    actual_words = set(re.findall(r"[a-z0-9]+", actual.casefold()))
    return len(expected_words & actual_words) / max(len(expected_words), 1)


async def main() -> None:
    settings = load_settings()
    if settings.audio.mode != "openai" or settings.audio.bridge_mode != "process":
        raise SystemExit("Run scripts/setup_partner.sh --real-meet before this smoke test.")
    if settings.browser.connection_mode != "cdp":
        raise SystemExit("Realtime audio smoke requires browser.connection_mode=cdp.")
    if not settings.openai_api_key or settings.audio.bridge_executable is None:
        raise SystemExit("OPENAI_API_KEY and audio.bridge_executable are required.")

    output_dir = settings.workspace.root / settings.workspace.sessions_dir / "realtime-audio"
    voice = AudioBridge(settings.audio, output_dir, settings.openai_api_key)
    phrase = "Robin realtime listening check. Streaming transcription is working now."
    print("1/4 Generating a known spoken phrase...")
    record = await voice.speak(phrase)
    if not record.path:
        raise SystemExit("Speech generation did not produce an audio file.")

    server, port = await serve_audio(output_dir / record.path)
    bridge = ProcessBridgeClient(
        settings.audio.bridge_executable,
        settings.audio.output_device_name,
    )
    transcriber = RealtimeTranscriber(
        api_key=settings.openai_api_key,
        model=settings.audio.realtime_transcription_model,
        delay=settings.audio.realtime_transcription_delay,
        threshold=settings.audio.silence_rms_threshold,
        silence_ms=settings.audio.realtime_vad_silence_ms,
        min_speech_ms=settings.audio.realtime_vad_min_speech_ms,
    )
    partials: list[str] = []
    finals: list[str] = []
    final_received = asyncio.Event()

    async def on_partial(_item_id: str, delta: str) -> None:
        partials.append(delta)

    async def on_final(_item_id: str, transcript: str) -> None:
        finals.append(transcript)
        final_received.set()

    playwright = await async_playwright().start()
    page = None
    stream_task = None
    try:
        browser = await playwright.chromium.connect_over_cdp(
            settings.browser.cdp_endpoint,
            no_defaults=True,
        )
        page = await browser.contexts[0].new_page()
        await page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
        print("2/4 Streaming Chrome audio to OpenAI Realtime transcription...")
        stream_task = asyncio.create_task(
            transcriber.run(
                bridge.stream_audio(
                    settings.audio.capture_bundle_id,
                    settings.audio.realtime_chunk_bytes,
                ),
                on_partial,
                on_final,
            )
        )
        await asyncio.sleep(1)
        await page.locator("#voice").evaluate("audio => audio.play()")
        await asyncio.wait_for(final_received.wait(), timeout=20)
        transcript = " ".join(finals)
        coverage = phrase_coverage(phrase, transcript)
        completion_deadline = asyncio.get_running_loop().time() + 8
        while coverage < 0.75 and asyncio.get_running_loop().time() < completion_deadline:
            await asyncio.sleep(0.2)
            transcript = " ".join(finals)
            coverage = phrase_coverage(phrase, transcript)
        normalized = transcript.casefold()
        if coverage < 0.75 or "transcription" not in normalized:
            raise SystemExit(
                f"Realtime transcript was unexpected ({coverage:.0%} phrase coverage): "
                f"{transcript!r}"
            )
        print("3/4 Realtime listening passed.")
        print(
            f"Transcript: {transcript!r}; phrase coverage: {coverage:.0%}; "
            f"partial events: {len(partials)}; "
            f"model: {settings.audio.realtime_transcription_model}"
        )
        before = await bridge.permissions_status()
        interruption = asyncio.create_task(
            voice.speak(
                "I am explaining this result in detail so a participant has time to interrupt me. "
                "The explanation continues with supporting evidence and a final recommendation. "
                "This last sentence should never finish because barge in stops the live stream."
            )
        )
        await asyncio.sleep(1.2)
        if not await voice.interrupt_speech():
            raise SystemExit("Could not interrupt active streaming speech playback.")
        interrupted_record = await interruption
        if not interrupted_record.interrupted or not interrupted_record.streaming:
            raise SystemExit(
                "Interrupted streaming speech completed as if uninterrupted."
            )
        after = await bridge.permissions_status()
        if after.default_output_device != before.default_output_device:
            raise SystemExit(
                "Audio interruption failed to restore the default output device: "
                f"before={before.default_output_device!r}, after={after.default_output_device!r}"
            )
        print("4/4 Barge-in stopped playback and restored the previous output device.")
    finally:
        if stream_task is not None:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
        if page is not None:
            await page.close()
        await playwright.stop()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
