from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from robin_core.audio.bridge import AudioBridge
from robin_core.audio.bridge_client import (
    BridgeResponse,
    PlaybackInterrupted,
    SimulatorBridgeClient,
)
from robin_core.config import AudioConfig, DatabaseConfig, RuntimeConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


class SilentBridgeClient(SimulatorBridgeClient):
    async def capture_audio_sample(
        self,
        bundle_id: str,
        path: Path,
        duration_ms: int = 1500,
    ) -> BridgeResponse:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"silent audio")
        return BridgeResponse(
            id="silent",
            ok=True,
            result={"bundle_id": bundle_id, "rms": "0.0001"},
        )


class FailedPlaybackBridgeClient(SimulatorBridgeClient):
    async def play_audio(self, path: Path) -> BridgeResponse:
        return BridgeResponse(id="failed", ok=False, error="BlackHole route failed")


class InterruptedPlaybackBridgeClient(SimulatorBridgeClient):
    async def play_audio(self, path: Path) -> BridgeResponse:
        raise PlaybackInterrupted("participant spoke")

    async def interrupt_playback(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_audio_bridge_records_participant_interruption(tmp_path: Path) -> None:
    audio = AudioBridge(
        AudioConfig(),
        tmp_path,
        bridge_client=InterruptedPlaybackBridgeClient(),
    )

    record = await audio.speak("This is a longer explanation.")

    assert record.interrupted is True
    assert record.error is None
    assert await audio.interrupt_speech() is True


class SignalBridgeClient(SimulatorBridgeClient):
    async def capture_audio_sample(
        self,
        bundle_id: str,
        path: Path,
        duration_ms: int = 1500,
    ) -> BridgeResponse:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"speech audio")
        return BridgeResponse(
            id="signal",
            ok=True,
            result={"bundle_id": bundle_id, "rms": "0.05", "peak": "0.2"},
        )


class SerializedCaptureBridgeClient(SignalBridgeClient):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def capture_audio_sample(
        self,
        bundle_id: str,
        path: Path,
        duration_ms: int = 1500,
    ) -> BridgeResponse:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        try:
            return await super().capture_audio_sample(bundle_id, path, duration_ms)
        finally:
            self.active -= 1


class FailedCaptureBridgeClient(SimulatorBridgeClient):
    async def capture_audio_sample(
        self,
        bundle_id: str,
        path: Path,
        duration_ms: int = 1500,
    ) -> BridgeResponse:
        return BridgeResponse(id="failed", ok=False, error="capture unavailable")


@pytest.mark.asyncio
async def test_simulator_speech_writes_wav(tmp_path: Path) -> None:
    bridge = AudioBridge(AudioConfig(mode="simulator"), tmp_path)

    record = await bridge.speak("Got it. I will prepare the deck.")

    assert record.path is not None
    assert record.byte_count > 44
    with wave.open(str(tmp_path / record.path), "rb") as wav:
        assert wav.getframerate() == 24_000
        assert wav.getnchannels() == 1
    assert record.duration_seconds == pytest.approx(0.18)


@pytest.mark.asyncio
async def test_prepare_speech_writes_valid_wav_without_record(tmp_path: Path) -> None:
    bridge = AudioBridge(AudioConfig(mode="simulator"), tmp_path)

    prepared = await bridge.prepare_speech("Prepare this narration.")

    assert prepared.error is None
    assert prepared.path is not None
    assert prepared.byte_count > 44
    assert prepared.duration_seconds == pytest.approx(0.18)
    assert bridge.last_record is None
    with wave.open(str(prepared.path), "rb") as wav:
        assert wav.getframerate() == 24_000
        assert wav.getnchannels() == 1


@pytest.mark.asyncio
async def test_play_prepared_creates_one_speech_record(tmp_path: Path) -> None:
    bridge = AudioBridge(AudioConfig(mode="simulator"), tmp_path)
    prepared = await bridge.prepare_speech("Play the prepared narration.")

    record = await bridge.play_prepared(prepared)

    assert record.text == prepared.text
    assert record.source == "prefetched"
    assert prepared.path is not None
    assert record.path == prepared.path.name
    assert record.completed_at is not None
    assert bridge.last_record == record


@pytest.mark.asyncio
async def test_openai_speech_streams_pcm_before_preserving_wav(tmp_path: Path) -> None:
    class FakeStreamingResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def iter_bytes(self, chunk_size: int):
            assert chunk_size == 4_800
            yield b"\x01\x00" * 2_400
            await asyncio.sleep(0)
            yield b"\x02\x00" * 2_400

    class FakeStreamingFactory:
        def create(self, **kwargs):
            assert kwargs["response_format"] == "pcm"
            return FakeStreamingResponse()

    audio = AudioBridge(
        AudioConfig(
            mode="openai",
            streaming_speech_enabled=True,
            streaming_speech_chunk_bytes=4_800,
        ),
        tmp_path,
        openai_api_key="test-key",
        bridge_client=SimulatorBridgeClient(),
    )
    audio.openai_client = SimpleNamespace(
        audio=SimpleNamespace(
            speech=SimpleNamespace(
                with_streaming_response=FakeStreamingFactory()
            )
        )
    )

    record = await audio.speak("Stream this response.")

    assert record.streaming is True
    assert record.time_to_first_audio_ms is not None
    assert record.playback_route == "pcm_stream"
    assert record.path is not None
    assert not list(tmp_path.glob("*.pcm"))
    with wave.open(str(tmp_path / record.path), "rb") as wav:
        assert wav.getframerate() == 24_000
        assert wav.getnframes() == 4_800


@pytest.mark.asyncio
async def test_speech_fails_when_native_playback_fails(tmp_path: Path) -> None:
    bridge = AudioBridge(
        AudioConfig(mode="simulator"),
        tmp_path,
        bridge_client=FailedPlaybackBridgeClient(),
    )

    with pytest.raises(RuntimeError, match="BlackHole route failed"):
        await bridge.speak("This must not be reported as spoken.")


@pytest.mark.asyncio
async def test_streaming_wav_placeholder_sizes_use_actual_payload_duration(tmp_path: Path) -> None:
    bridge = AudioBridge(AudioConfig(mode="simulator"), tmp_path)
    record = await bridge.speak("Streaming WAV duration test.")
    path = tmp_path / str(record.path)
    content = bytearray(path.read_bytes())
    content[4:8] = b"\xff\xff\xff\xff"
    content[40:44] = b"\xff\xff\xff\xff"
    path.write_bytes(content)

    assert bridge._wav_duration(path) == pytest.approx(0.18)


def test_streaming_wav_header_is_normalized_for_native_playback(tmp_path: Path) -> None:
    path = tmp_path / "streaming.wav"
    bridge = AudioBridge(AudioConfig(mode="simulator"), tmp_path)
    bridge._write_tone_wav(path)
    content = bytearray(path.read_bytes())
    content[4:8] = b"\xff\xff\xff\xff"
    content[40:44] = b"\xff\xff\xff\xff"
    path.write_bytes(content)

    bridge._normalize_streaming_wav_header(path)

    normalized = path.read_bytes()
    assert int.from_bytes(normalized[4:8], "little") == len(normalized) - 8
    assert int.from_bytes(normalized[40:44], "little") == len(normalized) - 44
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() == int(24_000 * 0.18)


@pytest.mark.asyncio
async def test_runtime_persists_speech_records(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator"),
    )
    runtime = RobinRuntime(settings)

    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime._acknowledge("Got it. I will prepare the deck.")

    assert runtime.speech
    speech = runtime.speech[-1]
    assert speech.path is not None
    assert (workspace / speech.path).exists()
    assert runtime.snapshot().speech[-1].id == speech.id


@pytest.mark.asyncio
async def test_runtime_waits_for_speech_floor_after_participant_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        runtime=RuntimeConfig(speech_floor_silence_ms=60, speech_floor_max_wait_ms=500),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator"),
    )
    runtime = RobinRuntime(settings)
    await runtime.ingest_transcript("This is regular meeting discussion.", "Avery")

    start = asyncio.get_running_loop().time()
    await runtime._acknowledge("I will wait for the floor.")
    elapsed_ms = int((asyncio.get_running_loop().time() - start) * 1000)

    assert elapsed_ms >= 45
    floor_events = [event for event in runtime.recent_events() if event.type == "speech.floor_wait"]
    assert floor_events
    assert floor_events[-1].payload["wait_ms"] > 0


@pytest.mark.asyncio
async def test_runtime_does_not_wait_on_robin_transcript_echo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        runtime=RuntimeConfig(speech_floor_silence_ms=500, speech_floor_max_wait_ms=500),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator"),
    )
    runtime = RobinRuntime(settings)
    await runtime.ingest_transcript("The analysis is ready.", "Robin")

    start = asyncio.get_running_loop().time()
    await runtime._acknowledge("Continuing without echo wait.")
    elapsed_ms = int((asyncio.get_running_loop().time() - start) * 1000)

    assert elapsed_ms < 300
    assert not any(event.type == "speech.floor_wait" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_runtime_transcribes_workspace_audio_with_simulator(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    audio_path = source / "request.wav"
    audio_path.write_bytes(b"fake audio fixture")
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", simulator_transcript="Robin, make slides from the finance files."),
    )
    runtime = RobinRuntime(settings)

    snapshot = await runtime.transcribe_audio_file("source-data/request.wav", speaker_name="Avery")

    assert snapshot.transcript[-1].text == "Robin, make slides from the finance files."
    assert snapshot.transcript[-1].speaker_name == "Avery"


@pytest.mark.asyncio
async def test_runtime_captures_audio_sample_with_simulator(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", bridge_mode="simulator"),
    )
    runtime = RobinRuntime(settings)

    result = await runtime.capture_audio_sample(output_name="sample.wav")

    assert result["ok"] is True
    assert result["path"] == "sessions/captures/sample.wav"
    assert (workspace / result["path"]).exists()


@pytest.mark.asyncio
async def test_runtime_capture_and_transcribe_once_ingests_audio_stt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", bridge_mode="simulator", simulator_transcript="This is ordinary meeting discussion."),
    )
    runtime = RobinRuntime(settings)

    snapshot = await runtime.capture_and_transcribe_once()

    assert snapshot.transcript[-1].text == "This is ordinary meeting discussion."
    assert snapshot.transcript[-1].source == "audio_stt"


@pytest.mark.asyncio
async def test_runtime_listening_loop_runs_bounded_iteration(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", bridge_mode="simulator", simulator_transcript="Loop captured this discussion."),
    )
    runtime = RobinRuntime(settings)

    started = await runtime.start_listening_loop(max_iterations=1, interval_ms=0)
    assert started.capture_loop_running is True
    assert runtime._listen_handle is not None
    await runtime._listen_handle
    stopped = runtime.snapshot()

    assert stopped.capture_loop_running is False
    assert stopped.transcript[-1].text == "Loop captured this discussion."


@pytest.mark.asyncio
async def test_leave_meeting_stops_active_listening_loop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", bridge_mode="simulator", simulator_transcript="Loop captured this discussion."),
    )
    runtime = RobinRuntime(settings)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.start_listening_loop(interval_ms=10_000)
    assert runtime.snapshot().capture_loop_running is True

    snapshot = await runtime.leave_meeting()

    assert snapshot.capture_loop_running is False
    assert runtime._listen_handle is None
    assert snapshot.meeting_state.value == "ENDED"
    assert any(event.type == "audio.listen.stopped" for event in runtime.recent_events())
    assert any(event.type == "meeting.leave.cleanup" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_live_listener_skips_silence_without_transcription_or_file_buildup(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", silence_rms_threshold=0.002),
    )
    runtime = RobinRuntime(settings)
    runtime.audio = AudioBridge(
        settings.audio,
        workspace / "sessions" / "speech",
        bridge_client=SilentBridgeClient(),
    )

    await runtime.capture_and_transcribe_once(retain_capture=False)

    assert not list((workspace / "sessions" / "captures").glob("*.wav"))
    assert any(event.type == "audio.silence.skipped" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_audio_input_check_requires_signal_and_transcription(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(
            mode="simulator",
            silence_rms_threshold=0.002,
            simulator_transcript="Audio check heard clearly.",
        ),
    )
    runtime = RobinRuntime(settings)
    runtime.audio = AudioBridge(
        settings.audio,
        workspace / "sessions" / "speech",
        bridge_client=SignalBridgeClient(),
    )

    result = await runtime.test_audio_input(duration_ms=100)

    assert result["ok"] is True
    assert result["transcript"] == "Audio check heard clearly."
    assert any(event.type == "audio.input.test.passed" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_runtime_serializes_native_audio_captures(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator"),
    )
    client = SerializedCaptureBridgeClient()
    runtime = RobinRuntime(settings)
    runtime.audio = AudioBridge(
        settings.audio,
        workspace / "sessions" / "speech",
        bridge_client=client,
    )

    await asyncio.gather(
        runtime.capture_audio_sample(output_name="listener.wav"),
        runtime.capture_audio_sample(output_name="diagnostic.wav"),
    )

    assert client.max_active == 1


@pytest.mark.asyncio
async def test_failed_audio_input_check_returns_dashboard_safe_metrics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator"),
    )
    runtime = RobinRuntime(settings)
    runtime.audio = AudioBridge(
        settings.audio,
        workspace / "sessions" / "speech",
        bridge_client=FailedCaptureBridgeClient(),
    )

    result = await runtime.test_audio_input(duration_ms=100)

    assert result["ok"] is False
    assert result["rms"] == 0.0
    assert result["peak"] == 0.0
    assert result["transcript"] == ""
    assert result["error"] == "capture unavailable"
