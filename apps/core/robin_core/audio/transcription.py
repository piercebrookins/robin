from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from ..config import AudioTranscriptionConfig


@dataclass(frozen=True)
class AudioFrame:
    data: bytes
    sequence: int
    captured_at_ms: int
    sample_rate: int = 24_000
    channels: int = 1
    encoding: str = "pcm_s16le"
    rms: float | None = None


@dataclass(frozen=True)
class TranscriptionEvent:
    kind: Literal["partial", "final", "error", "connected", "closed"]
    text: str = ""
    item_id: str | None = None
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    error: str | None = None


class TranscriptionClient(Protocol):
    connected: bool

    async def connect(self, meeting_id: UUID) -> None: ...

    async def append_frame(self, frame: AudioFrame) -> None: ...

    async def commit_audio(self) -> None: ...

    def events(self) -> AsyncIterator[TranscriptionEvent]: ...

    async def close(self) -> None: ...


class FixtureTranscriber:
    def __init__(self, transcript: str = "", emit_final_on_first_frame: bool = True) -> None:
        self.transcript = transcript
        self.emit_final_on_first_frame = emit_final_on_first_frame
        self.connected = False
        self.appended_frames: list[AudioFrame] = []
        self.commit_count = 0
        self._events: asyncio.Queue[TranscriptionEvent | None] = asyncio.Queue()
        self._emitted_final = False

    async def connect(self, meeting_id: UUID) -> None:
        self.connected = True
        await self._events.put(TranscriptionEvent(kind="connected"))

    async def append_frame(self, frame: AudioFrame) -> None:
        if not self.connected:
            raise RuntimeError("transcriber is not connected")
        self.appended_frames.append(frame)
        if self.emit_final_on_first_frame and self.transcript and not self._emitted_final:
            self._emitted_final = True
            await self._events.put(
                TranscriptionEvent(
                    kind="final",
                    text=self.transcript,
                    item_id=f"fixture-{frame.sequence}",
                    started_at_ms=frame.captured_at_ms,
                    ended_at_ms=frame.captured_at_ms + 100,
                )
            )

    async def commit_audio(self) -> None:
        self.commit_count += 1

    async def inject_event(self, event: TranscriptionEvent) -> None:
        await self._events.put(event)

    async def close(self) -> None:
        self.connected = False
        await self._events.put(TranscriptionEvent(kind="closed"))
        await self._events.put(None)

    async def events(self) -> AsyncIterator[TranscriptionEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            yield event


class OpenAIRealtimeTranscriber:
    def __init__(self, config: AudioTranscriptionConfig, api_key: str) -> None:
        self.config = config
        self.api_key = api_key
        self.connected = False
        self._events: asyncio.Queue[TranscriptionEvent | None] = asyncio.Queue(maxsize=256)
        self._websocket = None
        self._reader: asyncio.Task | None = None

    async def connect(self, meeting_id: UUID) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets is required for realtime transcription") from exc
        self._websocket = await websockets.connect(
            f"wss://api.openai.com/v1/realtime?model={self.config.model}",
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
        )
        await self._send(self.session_update_payload())
        self.connected = True
        await self._events.put(TranscriptionEvent(kind="connected"))
        self._reader = asyncio.create_task(self._read_events())

    async def append_frame(self, frame: AudioFrame) -> None:
        if not self.connected:
            raise RuntimeError("transcriber is not connected")
        if frame.sample_rate != 24_000 or frame.channels != 1 or frame.encoding != "pcm_s16le":
            raise ValueError("realtime transcription requires 24 kHz mono pcm_s16le frames")
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(frame.data).decode("ascii"),
            }
        )

    def session_update_payload(self) -> dict:
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24_000},
                        "transcription": {
                            "model": self.config.model,
                            "language": self.config.language,
                        },
                        "turn_detection": None,
                    }
                },
            },
        }

    async def commit_audio(self) -> None:
        if not self.connected:
            raise RuntimeError("transcriber is not connected")
        await self._send({"type": "input_audio_buffer.commit"})

    async def close(self) -> None:
        self.connected = False
        if self._reader:
            self._reader.cancel()
        if self._websocket:
            await self._websocket.close()
        await self._events.put(TranscriptionEvent(kind="closed"))
        await self._events.put(None)

    async def events(self) -> AsyncIterator[TranscriptionEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            yield event

    async def _send(self, payload: dict) -> None:
        if not self._websocket:
            raise RuntimeError("realtime websocket is not connected")
        await self._websocket.send(json.dumps(payload))

    async def _read_events(self) -> None:
        try:
            async for raw in self._websocket:
                event = json.loads(raw)
                event_type = event.get("type")
                if event_type == "conversation.item.input_audio_transcription.delta":
                    await self._events.put(
                        TranscriptionEvent(
                            kind="partial",
                            text=event.get("delta", ""),
                            item_id=event.get("item_id"),
                        )
                    )
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    await self._events.put(
                        TranscriptionEvent(
                            kind="final",
                            text=event.get("transcript", ""),
                            item_id=event.get("item_id"),
                        )
                    )
                elif event_type == "error":
                    await self._events.put(
                        TranscriptionEvent(kind="error", error=json.dumps(event.get("error", event)))
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._events.put(TranscriptionEvent(kind="error", error=str(exc)))


def create_transcriber(config: AudioTranscriptionConfig, api_key: str | None) -> TranscriptionClient:
    if config.provider == "openai_realtime":
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for realtime transcription")
        return OpenAIRealtimeTranscriber(config, api_key)
    return FixtureTranscriber(config.fixture_transcript)
