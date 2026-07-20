from __future__ import annotations

import asyncio
import json
import signal
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
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


class AudioDeviceInfo(BaseModel):
    id: int | str
    uid: str
    name: str
    input_channels: int = 0
    output_channels: int = 0
    sample_rate: int = 0


class PlaybackInterrupted(RuntimeError):
    pass


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

    async def play_pcm_stream(
        self,
        chunks: AsyncIterator[bytes],
        path: Path,
        sample_rate: int = 24_000,
    ) -> BridgeResponse: ...

    async def interrupt_playback(self) -> bool: ...

    async def screen_capture(self, application: str) -> BridgeResponse: ...

    async def list_audio_devices(self) -> BridgeResponse: ...

    async def list_capture_applications(self) -> BridgeResponse: ...

    async def capture_audio_sample(self, bundle_id: str, path: Path, duration_ms: int = 1500) -> BridgeResponse: ...

    async def stream_audio(
        self, bundle_id: str, chunk_bytes: int = 2400
    ) -> AsyncIterator[bytes]: ...


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

    async def play_pcm_stream(
        self,
        chunks: AsyncIterator[bytes],
        path: Path,
        sample_rate: int = 24_000,
    ) -> BridgeResponse:
        path.parent.mkdir(parents=True, exist_ok=True)
        byte_count = 0
        with path.open("wb") as stream_file:
            async for chunk in chunks:
                stream_file.write(chunk)
                byte_count += len(chunk)
        self.played_paths.append(path)
        return BridgeResponse(
            id=str(uuid4()),
            ok=byte_count > 0,
            result={
                "path": str(path),
                "played": byte_count > 0,
                "bytes": byte_count,
                "output_device": "simulated BlackHole 2ch",
                "route": "pcm_stream",
            },
        )

    async def interrupt_playback(self) -> bool:
        return False

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

    async def stream_audio(
        self, bundle_id: str, chunk_bytes: int = 2400
    ) -> AsyncIterator[bytes]:
        if False:
            yield b""


class ProcessBridgeClient(BridgeClient):
    def __init__(self, executable: Path, output_device_name: str = "BlackHole 2ch"):
        self.executable = executable
        self.output_device_name = output_device_name
        self._playback_process: asyncio.subprocess.Process | None = None
        self._playback_interrupted = False

    async def permissions_status(self) -> PermissionStatus:
        response = await self._send("permissions.status", {})
        return PermissionStatus.model_validate(response.result)

    async def start_capture(self, bundle_id: str) -> BridgeResponse:
        return await self._send("audio.capture.start", {"bundle_id": bundle_id})

    async def stop_capture(self) -> BridgeResponse:
        return await self._send("audio.capture.stop", {})

    async def play_audio(self, path: Path) -> BridgeResponse:
        duration = self._wav_duration(path)
        return await self._send(
            "audio.output.play",
            {"path": str(path), "output_device": self.output_device_name},
            timeout_seconds=max(15, duration + 8) if duration is not None else 60,
            track_playback=True,
        )

    async def play_pcm_stream(
        self,
        chunks: AsyncIterator[bytes],
        path: Path,
        sample_rate: int = 24_000,
    ) -> BridgeResponse:
        if not self.executable.exists():
            raise FileNotFoundError(f"Bridge executable not found: {self.executable}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        done_path = path.with_suffix(path.suffix + ".done")
        done_path.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "--play-pcm-stream",
            str(path),
            str(done_path),
            self.output_device_name,
            str(sample_rate),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._playback_process = process
        self._playback_interrupted = False
        try:
            with path.open("ab", buffering=0) as stream_file:
                async for chunk in chunks:
                    if self._playback_interrupted or process.returncode is not None:
                        break
                    if chunk:
                        stream_file.write(chunk)
            done_path.touch()
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise
        except TimeoutError as exc:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise TimeoutError("macOS bridge PCM stream timed out after 120s") from exc
        finally:
            done_path.unlink(missing_ok=True)
            self._playback_process = None
        if self._playback_interrupted:
            self._playback_interrupted = False
            raise PlaybackInterrupted("Speech playback was interrupted by meeting audio.")
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8") or stdout.decode("utf-8"))
        return BridgeResponse.model_validate_json(stdout.decode("utf-8"))

    async def interrupt_playback(self) -> bool:
        process = self._playback_process
        if process is None or process.returncode is not None:
            return False
        self._playback_interrupted = True
        process.send_signal(signal.SIGINT)
        return True

    @staticmethod
    def _wav_duration(path: Path) -> float | None:
        if not path.exists() or path.suffix.lower() != ".wav":
            return None
        try:
            with wave.open(str(path), "rb") as audio:
                if audio.getframerate() <= 0:
                    return None
                return audio.getnframes() / audio.getframerate()
        except (EOFError, wave.Error):
            return None

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
            timeout_seconds=max(duration_ms / 1000 + 10, 15),
        )

    async def stream_audio(
        self, bundle_id: str, chunk_bytes: int = 2400
    ) -> AsyncIterator[bytes]:
        if not self.executable.exists():
            raise FileNotFoundError(f"Bridge executable not found: {self.executable}")
        process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "--stream-audio",
            bundle_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None:
            raise RuntimeError("Bridge audio stream has no stdout pipe.")
        try:
            while chunk := await process.stdout.read(max(chunk_bytes, 480)):
                yield chunk
            stderr = await process.stderr.read() if process.stderr else b""
            await process.wait()
            if process.returncode:
                raise RuntimeError(
                    stderr.decode("utf-8", errors="replace")
                    or f"Bridge audio stream exited with {process.returncode}."
                )
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    async def _send(
        self,
        method: str,
        params: dict[str, Any],
        timeout_seconds: float = 15,
        track_playback: bool = False,
    ) -> BridgeResponse:
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
        if track_playback:
            self._playback_process = process
            self._playback_interrupted = False
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(command).encode("utf-8")),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise TimeoutError(
                f"macOS bridge {method} timed out after {timeout_seconds:.1f}s"
            ) from exc
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        finally:
            if track_playback:
                self._playback_process = None
        if track_playback and self._playback_interrupted:
            self._playback_interrupted = False
            raise PlaybackInterrupted("Speech playback was interrupted by meeting audio.")
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8") or stdout.decode("utf-8"))
        return BridgeResponse.model_validate_json(stdout.decode("utf-8"))


def create_bridge_client(config: AudioConfig) -> BridgeClient:
    if config.bridge_mode == "process":
        if not config.bridge_executable:
            raise ValueError("audio.bridge_executable is required when bridge_mode=process")
        return ProcessBridgeClient(config.bridge_executable, config.output_device_name)
    return SimulatorBridgeClient()
