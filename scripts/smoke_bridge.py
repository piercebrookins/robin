from __future__ import annotations

import asyncio
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge_client import ProcessBridgeClient


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["swift", "build", "--package-path", "apps/macos-bridge"], cwd=root, check=True)
    executable = root / "apps/macos-bridge/.build/debug/robin-macos-bridge"
    client = ProcessBridgeClient(executable)
    permissions = await client.permissions_status()
    devices = await client.list_audio_devices()
    audio = root / "RobinWorkspace/sessions/bridge-smoke.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x00" * 1200)
    capture = await client.start_capture("com.google.Chrome")
    play = await client.play_audio(audio)
    stop = await client.stop_capture()
    print(
        "Bridge smoke passed: "
        f"mode={permissions.mode} "
        f"screen_recording={permissions.screen_recording} "
        f"accessibility={permissions.accessibility} "
        f"microphone={permissions.microphone} "
        f"audio_device={permissions.audio_device_available} "
        f"audio_device_name={permissions.audio_device_name!r} "
        f"default_output={permissions.default_output_device!r} "
        f"capture={capture.result['capturing']} "
        f"play={play.result['played']} "
        f"play_output={play.result.get('output_device', '')!r} "
        f"play_route={play.result.get('route', '')!r} "
        f"stop={stop.result['capturing']} "
        f"devices={devices.result.get('devices', '').splitlines()[:3]}"
    )


if __name__ == "__main__":
    asyncio.run(main())
