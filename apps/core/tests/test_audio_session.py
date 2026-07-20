from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from robin_core.audio.audio_session import AudioSession
from robin_core.audio.transcription import AudioFrame, FixtureTranscriber, TranscriptionEvent


@pytest.mark.asyncio
async def test_audio_session_ignores_partials_and_ingests_final_transcripts() -> None:
    meeting_id = uuid4()
    ingested: list[tuple[str, str]] = []

    async def ingest(text, speaker_name, started_at_ms, ended_at_ms, source):
        ingested.append((text, source))

    transcriber = FixtureTranscriber(emit_final_on_first_frame=False)
    session = AudioSession(meeting_id, transcriber, ingest)
    await session.start()

    await transcriber.inject_event(TranscriptionEvent(kind="partial", text="Robin"))
    await asyncio.sleep(0)
    assert ingested == []

    await transcriber.inject_event(
        TranscriptionEvent(
            kind="final",
            text=" Robin, make slides. ",
            item_id="item_1",
            started_at_ms=10,
            ended_at_ms=90,
        )
    )
    for _ in range(20):
        if ingested:
            break
        await asyncio.sleep(0.01)

    await session.close()

    assert ingested == [("Robin, make slides.", "audio_stt")]
    assert session.partial_text == "Robin"
    assert session.partial_event_count == 1
    assert session.final_event_count == 1
    assert session.last_final_latency_ms is not None
    assert session.final_segments[-1].text == "Robin, make slides."


@pytest.mark.asyncio
async def test_audio_session_deduplicates_finals_by_item_and_normalized_text() -> None:
    meeting_id = uuid4()
    ingested: list[str] = []

    async def ingest(text, speaker_name, started_at_ms, ended_at_ms, source):
        ingested.append(text)

    transcriber = FixtureTranscriber(emit_final_on_first_frame=False)
    session = AudioSession(meeting_id, transcriber, ingest)
    await session.start()

    event = TranscriptionEvent(kind="final", text="Robin, make slides.", item_id="item_1")
    await transcriber.inject_event(event)
    await transcriber.inject_event(event)
    for _ in range(20):
        if len(ingested) == 1:
            break
        await asyncio.sleep(0.01)

    await session.close()

    assert ingested == ["Robin, make slides."]


@pytest.mark.asyncio
async def test_audio_session_bounds_frame_queue_and_counts_drops() -> None:
    meeting_id = uuid4()

    async def ingest(text, speaker_name, started_at_ms, ended_at_ms, source):
        return None

    transcriber = FixtureTranscriber(emit_final_on_first_frame=False)
    session = AudioSession(
        meeting_id,
        transcriber,
        ingest,
        frame_queue_seconds=0.2,
        frame_duration_ms=100,
    )

    for index in range(5):
        await session.append_frame(
            AudioFrame(data=b"\x00\x00" * 120, sequence=index, captured_at_ms=index * 100)
        )

    assert session.frame_queue.qsize() == 2
    assert session.dropped_frames == 3


@pytest.mark.asyncio
async def test_audio_session_reconnects_and_replays_recent_frames() -> None:
    meeting_id = uuid4()
    created: list[ReconnectableTranscriber] = []

    async def ingest(text, speaker_name, started_at_ms, ended_at_ms, source):
        return None

    def factory() -> ReconnectableTranscriber:
        transcriber = ReconnectableTranscriber(fail_first_append=not created)
        created.append(transcriber)
        return transcriber

    first = factory()
    session = AudioSession(
        meeting_id,
        first,
        ingest,
        transcriber_factory=factory,
        reconnect_attempts=2,
        reconnect_initial_backoff_s=0,
    )
    await session.start()

    await session.append_frame(AudioFrame(data=b"\x01\x00", sequence=1, captured_at_ms=100))
    for _ in range(20):
        if len(created) == 2 and created[-1].appended_frames:
            break
        await asyncio.sleep(0.01)

    await session.close()

    assert len(created) == 2
    assert session.connected is False
    assert session.reconnect_count == 1
    assert session.replayed_frames == 1
    assert [frame.sequence for frame in created[-1].appended_frames] == [1]


@pytest.mark.asyncio
async def test_audio_session_replay_keeps_only_bounded_recent_frames() -> None:
    meeting_id = uuid4()
    created: list[ReconnectableTranscriber] = []

    async def ingest(text, speaker_name, started_at_ms, ended_at_ms, source):
        return None

    def factory() -> ReconnectableTranscriber:
        transcriber = ReconnectableTranscriber(fail_first_append=not created)
        created.append(transcriber)
        return transcriber

    first = factory()
    session = AudioSession(
        meeting_id,
        first,
        ingest,
        frame_queue_seconds=0.2,
        frame_duration_ms=100,
        transcriber_factory=factory,
        reconnect_attempts=2,
        reconnect_initial_backoff_s=0,
    )
    for index in range(5):
        await session.append_frame(
            AudioFrame(data=b"\x01\x00", sequence=index, captured_at_ms=index * 100)
        )
    await session.start()
    for _ in range(20):
        if len(created) == 2 and len(created[-1].appended_frames) >= 2:
            break
        await asyncio.sleep(0.01)

    await session.close()

    assert len(created) == 2
    assert [frame.sequence for frame in created[-1].appended_frames[:2]] == [3, 4]


@pytest.mark.asyncio
async def test_audio_session_commits_after_configured_silence() -> None:
    meeting_id = uuid4()

    async def ingest(text, speaker_name, started_at_ms, ended_at_ms, source):
        return None

    transcriber = FixtureTranscriber(emit_final_on_first_frame=False)
    session = AudioSession(
        meeting_id,
        transcriber,
        ingest,
        vad_threshold=0.5,
        silence_duration_ms=200,
    )
    await session.start()

    for frame in [
        AudioFrame(data=b"\x01\x00", sequence=1, captured_at_ms=100, rms=0.7),
        AudioFrame(data=b"\x01\x00", sequence=2, captured_at_ms=200, rms=0.1),
        AudioFrame(data=b"\x01\x00", sequence=3, captured_at_ms=400, rms=0.1),
    ]:
        await session.append_frame(frame)
    for _ in range(20):
        if transcriber.commit_count:
            break
        await asyncio.sleep(0.01)

    await session.close()

    assert transcriber.commit_count == 1
    assert session.commit_count == 1


class ReconnectableTranscriber(FixtureTranscriber):
    def __init__(self, fail_first_append: bool = False) -> None:
        super().__init__(emit_final_on_first_frame=False)
        self.fail_first_append = fail_first_append

    async def append_frame(self, frame: AudioFrame) -> None:
        if self.fail_first_append:
            self.fail_first_append = False
            raise RuntimeError("socket dropped")
        await super().append_frame(frame)
