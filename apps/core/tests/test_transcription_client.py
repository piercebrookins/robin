from __future__ import annotations

import json

import pytest

from robin_core.audio.transcription import AudioFrame, OpenAIRealtimeTranscriber
from robin_core.config import AudioTranscriptionConfig


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.mark.asyncio
async def test_openai_realtime_transcriber_uses_transcription_session_and_manual_commit() -> None:
    websocket = FakeWebSocket()
    transcriber = OpenAIRealtimeTranscriber(
        AudioTranscriptionConfig(provider="openai_realtime", model="gpt-realtime-whisper", language="en"),
        api_key="test-key",
    )
    transcriber._websocket = websocket
    transcriber.connected = True

    await transcriber._send(transcriber.session_update_payload())
    await transcriber.append_frame(AudioFrame(data=b"\x01\x00", sequence=1, captured_at_ms=100))
    await transcriber.commit_audio()

    assert websocket.sent[0]["session"]["type"] == "transcription"
    assert websocket.sent[0]["session"]["audio"]["input"]["turn_detection"] is None
    assert websocket.sent[1]["type"] == "input_audio_buffer.append"
    assert websocket.sent[2] == {"type": "input_audio_buffer.commit"}
