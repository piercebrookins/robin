from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..config import AudioConfig


class PermissionStatus(BaseModel):
    screen_recording: bool
    accessibility: bool
    microphone: bool
    audio_device_available: bool
    mode: str = "simulator"
    audio_device_name: str = ""
    default_output_device: str = ""


class BridgeResponse(BaseModel):
    id: str
    ok: bool
    result: dict[str, Any] = {}
    error: str | None = None


@dataclass
class BridgeCommand:
    method: str
    params: dict[str, Any]
    id: str = ""

    def envelope(self) -> dict[str, Any]:
        return {"id": self.id or str(uuid4()), "method": self.method, "params": self.params}


class BridgeClient:
    async def permissions_status(self) -> PermissionStatus: ...

    async def start_capture(self, bundle_id: str) -> BridgeResponse: ...

    async def stop_capture(self) -> BridgeResponse: ...

    async def play_audio(self, path: Path) -> BridgeResponse: ...

    async def screen_capture(self, application: str) -> BridgeResponse: ...

    async def list_audio_devices(self) -> BridgeResponse: ...

    async def list_capture_applications(self) -> BridgeResponse: ...

    async def capture_audio_sample(self, bundle_id: str, path: Path, duration_ms: int = 1500) -> BridgeResponse: ...


class SimulatorBridgeClient(BridgeClient):
    def __init__(self) -> None:
        self.capture_started = False
        self.played_paths: list[Path] = []

    async def permissions_status(self) -> PermissionStatus:
        return PermissionStatus(
            screen_recording=True,
            accessibility=True,
            microphone=True,
            audio_device_available=True,
            mode="simulator",
            audio_device_name="simulated BlackHole 2ch",
            default_output_device="simulated BlackHole 2ch",
        )

    async def start_capture(self, bundle_id: str) -> BridgeResponse:
        self.capture_started = True
        return BridgeResponse(id=str(uuid4()), ok=True, result={"bundle_id": bundle_id, "capturing": True})

    async def stop_capture(self) -> BridgeResponse:
        self.capture_started = False
        return BridgeResponse(id=str(uuid4()), ok=True, result={"capturing": False})

    async def play_audio(self, path: Path) -> BridgeResponse:
        self.played_paths.append(path)
        return BridgeResponse(id=str(uuid4()), ok=True, result={"path": str(path), "played": path.exists()})

    async def screen_capture(self, application: str) -> BridgeResponse:
        return BridgeResponse(id=str(uuid4()), ok=True, result={"application": application, "image_base64": ""})

    async def list_audio_devices(self) -> BridgeResponse:
        return BridgeResponse(id=str(uuid4()), ok=True, result={"devices": "simulated BlackHole 2ch"})

    async def list_capture_applications(self) -> BridgeResponse:
        return BridgeResponse(id=str(uuid4()), ok=True, result={"applications": "com.google.Chrome:Google Chrome"})

    async def capture_audio_sample(self, bundle_id: str, path: Path, duration_ms: int = 1500) -> BridgeResponse:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"simulated capture")
        return BridgeResponse(
            id=str(uuid4()),
            ok=True,
            result={"bundle_id": bundle_id, "captured": True, "path": str(path), "samples": 0, "bytes": path.stat().st_size},
        )


class ProcessBridgeClient(BridgeClient):
    def __init__(self, executable: Path):
        self.executable = executable

    async def permissions_status(self) -> PermissionStatus:
        response = await self._send("permissions.status", {})
        return PermissionStatus.model_validate(response.result)

    async def start_capture(self, bundle_id: str) -> BridgeResponse:
        return await self._send("audio.capture.start", {"bundle_id": bundle_id})

    async def stop_capture(self) -> BridgeResponse:
        return await self._send("audio.capture.stop", {})

    async def play_audio(self, path: Path) -> BridgeResponse:
        return await self._send("audio.output.play", {"path": str(path)})

    async def screen_capture(self, application: str) -> BridgeResponse:
        return await self._send("screen.capture", {"application": application})

    async def list_audio_devices(self) -> BridgeResponse:
        return await self._send("audio.devices.list", {})

    async def list_capture_applications(self) -> BridgeResponse:
        return await self._send("audio.capture.apps", {})

    async def capture_audio_sample(self, bundle_id: str, path: Path, duration_ms: int = 1500) -> BridgeResponse:
        return await self._send(
            "audio.capture.sample",
            {"bundle_id": bundle_id, "path": str(path), "duration_ms": str(duration_ms)},
        )

    async def _send(self, method: str, params: dict[str, Any]) -> BridgeResponse:
        if not self.executable.exists():
            raise FileNotFoundError(f"Bridge executable not found: {self.executable}")
        command = BridgeCommand(method=method, params=params).envelope()
        process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "--json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(json.dumps(command).encode("utf-8"))
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8") or stdout.decode("utf-8"))
        return BridgeResponse.model_validate_json(stdout.decode("utf-8"))


def create_bridge_client(config: AudioConfig) -> BridgeClient:
    if config.bridge_mode == "process":
        if not config.bridge_executable:
            raise ValueError("audio.bridge_executable is required when bridge_mode=process")
        return ProcessBridgeClient(config.bridge_executable)
    return SimulatorBridgeClient()
