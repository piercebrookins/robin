from __future__ import annotations

import asyncio
import base64
import json
import math
from array import array
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import websockets


TranscriptCallback = Callable[[str, str], Awaitable[None]]
SpeechCallback = Callable[[], Awaitable[None]]


def pcm16_rms(chunk: bytes) -> float:
    if len(chunk) < 2:
        return 0.0
    samples = array("h")
    samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
    if not samples:
        return 0.0
    square_sum = sum(float(sample) * float(sample) for sample in samples)
    return math.sqrt(square_sum / len(samples)) / 32768.0


@dataclass
class LocalTurnDetector:
    threshold: float
    silence_ms: int
    min_speech_ms: int
    speech_ms: float = 0
    quiet_ms: float = 0
    active: bool = False

    def observe(self, chunk: bytes) -> list[Literal["speech_started", "commit"]]:
        duration_ms = len(chunk) / 48.0  # 24 kHz, mono, signed PCM16.
        events: list[Literal["speech_started", "commit"]] = []
        if pcm16_rms(chunk) >= self.threshold:
            self.speech_ms += duration_ms
            self.quiet_ms = 0
            if not self.active and self.speech_ms >= self.min_speech_ms:
                self.active = True
                events.append("speech_started")
            return events
        if self.active:
            self.quiet_ms += duration_ms
            if self.quiet_ms >= self.silence_ms:
                events.append("commit")
                self.reset()
        else:
            self.speech_ms = max(0, self.speech_ms - duration_ms)
        return events

    def reset(self) -> None:
        self.speech_ms = 0
        self.quiet_ms = 0
        self.active = False


class RealtimeTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-realtime-whisper",
        delay: str = "low",
        threshold: float = 0.002,
        silence_ms: int = 550,
        min_speech_ms: int = 180,
        websocket_url: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.delay = delay
        self.detector = LocalTurnDetector(threshold, silence_ms, min_speech_ms)
        # The handshake selects the transcription session type. The streaming
        # transcription model itself is selected separately in session.update.
        self.websocket_url = websocket_url or (
            "wss://api.openai.com/v1/realtime?intent=transcription"
        )

    async def run(
        self,
        pcm_chunks: AsyncIterator[bytes],
        on_partial: TranscriptCallback,
        on_final: TranscriptCallback,
        on_speech_started: SpeechCallback | None = None,
    ) -> None:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Safety-Identifier": "robin-local-meeting-operator",
        }
        async with websockets.connect(
            self.websocket_url,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            ping_interval=20,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "transcription",
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": 24_000},
                                    "transcription": {
                                        "model": self.model,
                                        "language": "en",
                                        "delay": self.delay,
                                    },
                                    "turn_detection": None,
                                }
                            },
                        },
                    }
                )
            )
            reader = asyncio.create_task(self._read_events(websocket, on_partial, on_final))
            try:
                async for chunk in pcm_chunks:
                    if reader.done():
                        await reader
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(chunk).decode("ascii"),
                            }
                        )
                    )
                    for event in self.detector.observe(chunk):
                        if event == "speech_started" and on_speech_started:
                            await on_speech_started()
                        elif event == "commit":
                            await websocket.send(
                                json.dumps({"type": "input_audio_buffer.commit"})
                            )
                if self.detector.active:
                    await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    self.detector.reset()
            finally:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass

    async def _read_events(
        self,
        websocket,
        on_partial: TranscriptCallback,
        on_final: TranscriptCallback,
    ) -> None:
        async for raw in websocket:
            event = json.loads(raw)
            event_type = event.get("type")
            item_id = str(event.get("item_id", ""))
            if event_type == "conversation.item.input_audio_transcription.delta":
                await on_partial(item_id, str(event.get("delta", "")))
            elif event_type == "conversation.item.input_audio_transcription.completed":
                await on_final(item_id, str(event.get("transcript", "")))
            elif event_type == "error":
                detail = event.get("error", {})
                raise RuntimeError(
                    str(detail.get("message") or detail or "Realtime transcription failed.")
                )
