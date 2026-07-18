from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

from robin_core.audio.bridge import AudioBridge
from robin_core.config import AudioConfig, DatabaseConfig, RuntimeConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


@pytest.mark.asyncio
async def test_simulator_speech_writes_wav(tmp_path: Path) -> None:
    bridge = AudioBridge(AudioConfig(mode="simulator"), tmp_path)

    record = await bridge.speak("Got it. I will prepare the deck.")

    assert record.path is not None
    assert record.byte_count > 44
    with wave.open(str(tmp_path / record.path), "rb") as wav:
        assert wav.getframerate() == 24_000
        assert wav.getnchannels() == 1


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
