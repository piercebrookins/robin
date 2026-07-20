from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
import time
from uuid import UUID

from ..schemas import TranscriptSegment, now_utc
from .transcription import AudioFrame, TranscriptionClient, TranscriptionEvent


TranscriptSink = Callable[[str, str | None, int | None, int | None, str], Awaitable[object]]
TranscriberFactory = Callable[[], TranscriptionClient]


class AudioSession:
    def __init__(
        self,
        meeting_id: UUID,
        transcriber: TranscriptionClient,
        ingest_final: TranscriptSink,
        frame_queue_seconds: float = 2.0,
        frame_duration_ms: int = 100,
        transcriber_factory: TranscriberFactory | None = None,
        reconnect_attempts: int = 3,
        reconnect_initial_backoff_s: float = 0.25,
        reconnect_max_backoff_s: float = 2.0,
        vad_threshold: float = 0.5,
        silence_duration_ms: int = 500,
    ) -> None:
        self.meeting_id = meeting_id
        self.transcriber = transcriber
        self.transcriber_factory = transcriber_factory
        self.ingest_final = ingest_final
        self.frame_queue_maxsize = max(1, int((frame_queue_seconds * 1000) / frame_duration_ms))
        self.frame_queue: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=self.frame_queue_maxsize)
        self.recent_frames: deque[AudioFrame] = deque(maxlen=self.frame_queue_maxsize)
        self.dropped_frames = 0
        self.forwarded_frames = 0
        self.replayed_frames = 0
        self.reconnect_count = 0
        self.partial_event_count = 0
        self.final_event_count = 0
        self.last_final_latency_ms: int | None = None
        self.reconnect_attempts = max(0, reconnect_attempts)
        self.reconnect_initial_backoff_s = max(0.0, reconnect_initial_backoff_s)
        self.reconnect_max_backoff_s = max(
            self.reconnect_initial_backoff_s,
            reconnect_max_backoff_s,
        )
        self.vad_threshold = max(0.0, vad_threshold)
        self.silence_duration_ms = max(0, silence_duration_ms)
        self.commit_count = 0
        self.final_segments: list[TranscriptSegment] = []
        self.partial_text = ""
        self.connected = False
        self.last_error: str | None = None
        self._closed = False
        self._forwarder: asyncio.Task | None = None
        self._event_reader: asyncio.Task | None = None
        self._reconnect_lock = asyncio.Lock()
        self._seen_finals: set[tuple[str, str]] = set()
        self._speech_open = False
        self._last_voice_at_ms: int | None = None

    async def start(self) -> None:
        await self.transcriber.connect(self.meeting_id)
        self._closed = False
        self.connected = True
        self._forwarder = asyncio.create_task(self._forward_frames())
        self._event_reader = asyncio.create_task(self._read_transcription_events())

    async def append_frame(self, frame: AudioFrame) -> None:
        self.recent_frames.append(frame)
        try:
            self.frame_queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.dropped_frames += 1

    async def close(self) -> None:
        self._closed = True
        for task in (self._forwarder, self._event_reader):
            if task:
                task.cancel()
        await self.transcriber.close()
        for task in (self._forwarder, self._event_reader):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.connected = False

    async def _forward_frames(self) -> None:
        try:
            while True:
                frame = await self.frame_queue.get()
                try:
                    await self.transcriber.append_frame(frame)
                    self.forwarded_frames += 1
                    await self._maybe_commit_after_silence(frame)
                except Exception as exc:
                    self.last_error = str(exc)
                    await self._reconnect(f"append failed: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)

    async def _read_transcription_events(self) -> None:
        try:
            while not self._closed:
                try:
                    async for event in self.transcriber.events():
                        await self._handle_event(event)
                    if not self._closed:
                        await self._reconnect("transcription event stream closed")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = str(exc)
                    await self._reconnect(f"event stream failed: {exc}")
        except asyncio.CancelledError:
            raise

    async def _handle_event(self, event: TranscriptionEvent) -> None:
        if event.kind == "partial":
            self.partial_event_count += 1
            self.partial_text += event.text
            return
        if event.kind == "error":
            self.last_error = event.error or "transcription error"
            await self._reconnect(self.last_error)
            return
        if event.kind != "final":
            return
        text = " ".join(event.text.split())
        if not text:
            return
        key = (event.item_id or "", text.casefold())
        if key in self._seen_finals:
            return
        self._seen_finals.add(key)
        self.final_event_count += 1
        if event.ended_at_ms is not None:
            self.last_final_latency_ms = max(0, int(time.time() * 1000) - event.ended_at_ms)
        await self.ingest_final(
            text,
            "Meeting audio",
            event.started_at_ms,
            event.ended_at_ms,
            "audio_stt",
        )
        self.final_segments.append(
            TranscriptSegment(
                meeting_id=self.meeting_id,
                speaker_name="Meeting audio",
                text=text,
                started_at_ms=event.started_at_ms or 0,
                ended_at_ms=event.ended_at_ms or event.started_at_ms or 0,
                source="audio_stt",
                created_at=now_utc(),
            )
        )

    async def _reconnect(self, reason: str) -> None:
        if self._closed:
            return
        async with self._reconnect_lock:
            if self._closed:
                return
            self.connected = False
            self.last_error = reason
            retained_frames = list(self.recent_frames)
            for attempt in range(1, self.reconnect_attempts + 1):
                delay = min(
                    self.reconnect_initial_backoff_s * (2 ** (attempt - 1)),
                    self.reconnect_max_backoff_s,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                if self.transcriber_factory:
                    with suppress(Exception):
                        await self.transcriber.close()
                    self.transcriber = self.transcriber_factory()
                try:
                    await self.transcriber.connect(self.meeting_id)
                    for frame in retained_frames:
                        await self.transcriber.append_frame(frame)
                        self.replayed_frames += 1
                    self.connected = True
                    self.reconnect_count += 1
                    return
                except Exception as exc:
                    self.last_error = f"reconnect attempt {attempt} failed: {exc}"
                    self.connected = False
            self.last_error = f"transcription reconnect exhausted after {self.reconnect_attempts} attempts: {reason}"

    async def _maybe_commit_after_silence(self, frame: AudioFrame) -> None:
        if frame.rms is None:
            return
        if frame.rms >= self.vad_threshold:
            self._speech_open = True
            self._last_voice_at_ms = frame.captured_at_ms
            return
        if not self._speech_open or self._last_voice_at_ms is None:
            return
        if frame.captured_at_ms - self._last_voice_at_ms < self.silence_duration_ms:
            return
        await self.transcriber.commit_audio()
        self.commit_count += 1
        self._speech_open = False
        self._last_voice_at_ms = None
