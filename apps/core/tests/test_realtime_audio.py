from __future__ import annotations

import json
from array import array

import pytest

from robin_core.audio.realtime import LocalTurnDetector, RealtimeTranscriber, pcm16_rms


def pcm_chunk(amplitude: int, duration_ms: int = 100) -> bytes:
    return array("h", [amplitude] * (24 * duration_ms)).tobytes()


def test_local_turn_detector_emits_speech_and_commit_boundaries() -> None:
    detector = LocalTurnDetector(threshold=0.01, silence_ms=500, min_speech_ms=180)

    assert detector.observe(pcm_chunk(0)) == []
    assert detector.observe(pcm_chunk(5000)) == []
    assert detector.observe(pcm_chunk(5000)) == ["speech_started"]
    for _ in range(4):
        assert detector.observe(pcm_chunk(0)) == []
    assert detector.observe(pcm_chunk(0)) == ["commit"]
    assert detector.active is False


def test_pcm16_rms_is_normalized() -> None:
    assert pcm16_rms(pcm_chunk(0)) == 0
    assert pcm16_rms(pcm_chunk(16384)) == pytest.approx(0.5)


def test_transcription_model_is_not_used_as_websocket_session_model() -> None:
    transcriber = RealtimeTranscriber("test-key", model="gpt-realtime-whisper")

    assert transcriber.websocket_url.endswith("?intent=transcription")


@pytest.mark.asyncio
async def test_realtime_event_reader_merges_partial_and_final_callbacks() -> None:
    events = [
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": "turn-1",
            "delta": "Robin, ",
        },
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "turn-1",
            "transcript": "Robin, can you hear me?",
        },
    ]

    class FakeWebsocket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not events:
                raise StopAsyncIteration
            return json.dumps(events.pop(0))

    partials: list[tuple[str, str]] = []
    finals: list[tuple[str, str]] = []

    async def on_partial(item_id: str, text: str) -> None:
        partials.append((item_id, text))

    async def on_final(item_id: str, text: str) -> None:
        finals.append((item_id, text))

    transcriber = RealtimeTranscriber("test-key")
    await transcriber._read_events(FakeWebsocket(), on_partial, on_final)

    assert partials == [("turn-1", "Robin, ")]
    assert finals == [("turn-1", "Robin, can you hear me?")]


@pytest.mark.asyncio
async def test_realtime_event_reader_surfaces_api_errors() -> None:
    events = [{"type": "error", "error": {"message": "bad audio format"}}]

    class FakeWebsocket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not events:
                raise StopAsyncIteration
            return json.dumps(events.pop(0))

    async def ignore(_item_id: str, _text: str) -> None:
        return None

    with pytest.raises(RuntimeError, match="bad audio format"):
        await RealtimeTranscriber("test-key")._read_events(
            FakeWebsocket(), ignore, ignore
        )
