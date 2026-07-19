from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

from robin_core.audio.bridge_client import ProcessBridgeClient, SimulatorBridgeClient


def write_tiny_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x00" * 1200)


@pytest.mark.asyncio
async def test_simulator_bridge_client_tracks_capture_and_playback(tmp_path: Path) -> None:
    client = SimulatorBridgeClient()
    audio = tmp_path / "speech.wav"
    write_tiny_wav(audio)

    permissions = await client.permissions_status()
    start = await client.start_capture("com.google.Chrome")
    play = await client.play_audio(audio)
    apps = await client.list_capture_applications()
    sample = await client.capture_audio_sample("com.google.Chrome", tmp_path / "capture.wav")
    stop = await client.stop_capture()

    assert permissions.audio_device_available is True
    assert start.result["capturing"] is True
    assert play.result["played"] is True
    assert "com.google.Chrome" in apps.result["applications"]
    assert sample.ok is True
    assert Path(sample.result["path"]).exists()
    assert stop.result["capturing"] is False


@pytest.mark.asyncio
async def test_process_bridge_client_health_after_swift_build(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    subprocess.run(["swift", "build", "--package-path", "apps/macos-bridge"], cwd=root, check=True)
    executable = root / "apps/macos-bridge/.build/debug/robin-macos-bridge"
    client = ProcessBridgeClient(executable)

    permissions = await client.permissions_status()
    assert permissions.mode == "process"
    apps = await client.list_capture_applications()
    assert apps.ok is True
    assert "applications" in apps.result

    audio = tmp_path / "speech.wav"
    write_tiny_wav(audio)
    response = await client.play_audio(audio)
    assert response.ok is True
    assert response.result["played"] == "true"
    if permissions.audio_device_available:
        assert "BlackHole" in response.result["output_device"]
        assert response.result["route"] == "default_device_swap"
        after_playback = await client.permissions_status()
        assert after_playback.default_output_device == permissions.default_output_device


@pytest.mark.asyncio
async def test_process_bridge_passes_configured_output_device(tmp_path: Path) -> None:
    client = ProcessBridgeClient(tmp_path / "bridge", "Exact Virtual Device")
    sent: dict[str, object] = {}

    async def fake_send(
        method: str, params: dict[str, object], timeout_seconds: float = 15
    ):
        sent.update({"method": method, "params": params, "timeout": timeout_seconds})
        from robin_core.audio.bridge_client import BridgeResponse

        return BridgeResponse(id="test", ok=True, result={"played": "true"})

    client._send = fake_send  # type: ignore[method-assign]
    await client.play_audio(tmp_path / "voice.wav")

    assert sent["method"] == "audio.output.play"
    assert sent["params"] == {
        "path": str(tmp_path / "voice.wav"),
        "output_device": "Exact Virtual Device",
    }
    assert sent["timeout"] == 60


@pytest.mark.asyncio
async def test_process_bridge_playback_timeout_includes_wav_duration(tmp_path: Path) -> None:
    client = ProcessBridgeClient(tmp_path / "bridge")
    audio = tmp_path / "long.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x00" * 24_000 * 20)
    sent: dict[str, float] = {}

    async def fake_send(
        _method: str, _params: dict[str, object], timeout_seconds: float = 15
    ):
        sent["timeout"] = timeout_seconds
        from robin_core.audio.bridge_client import BridgeResponse

        return BridgeResponse(id="test", ok=True, result={"played": "true"})

    client._send = fake_send  # type: ignore[method-assign]
    await client.play_audio(audio)

    assert sent["timeout"] == pytest.approx(28)
