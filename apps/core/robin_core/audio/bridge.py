from __future__ import annotations

import math
import wave
from pathlib import Path

from openai import AsyncOpenAI

from ..config import AudioConfig
from ..schemas import SpeechRecord, now_utc
from .bridge_client import BridgeClient, create_bridge_client


class AudioBridge:
    def __init__(self, config: AudioConfig | None = None, output_dir: Path | None = None, openai_api_key: str | None = None, bridge_client: BridgeClient | None = None) -> None:
        self.config = config or AudioConfig()
        self.output_dir = output_dir
        self.openai_api_key = openai_api_key
        self.bridge_client = bridge_client or create_bridge_client(self.config)
        self.capture_healthy = True
        self.virtual_mic_healthy = True
        self.last_spoken_text: str | None = None
        self.last_record: SpeechRecord | None = None

    async def speak(self, text: str) -> SpeechRecord:
        self.last_spoken_text = text
        record = SpeechRecord(
            text=text,
            mode="openai" if self.config.mode == "openai" else "simulator",
            voice=self.config.speech_voice,
            model=self.config.speech_model,
            format=self.config.speech_format,
        )
        try:
            if self.config.mode == "openai":
                await self._synthesize_openai(record)
            else:
                self._synthesize_simulator(record)
            if record.path and self.output_dir:
                await self.bridge_client.play_audio(self.output_dir / record.path)
            record.completed_at = now_utc()
        except Exception as exc:
            record.error = str(exc)
            record.completed_at = now_utc()
            raise
        finally:
            self.last_record = record
        return record

    async def stop(self) -> None:
        await self.bridge_client.stop_capture()
        self.capture_healthy = False

    async def start_capture(self, bundle_id: str = "com.google.Chrome") -> None:
        await self.bridge_client.start_capture(bundle_id)
        self.capture_healthy = True

    async def permissions_status(self):
        return await self.bridge_client.permissions_status()

    async def transcribe_file(self, path: Path) -> str:
        if self.config.mode == "openai":
            return await self._transcribe_openai(path)
        return self.config.simulator_transcript

    def _speech_path(self, record: SpeechRecord) -> Path:
        if not self.output_dir:
            raise ValueError("AudioBridge output directory is not configured.")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir / f"speech_{record.id}.{record.format}"

    def _synthesize_simulator(self, record: SpeechRecord) -> None:
        path = self._speech_path(record)
        if record.format != "wav":
            path.write_bytes(b"")
        else:
            self._write_tone_wav(path)
        record.path = path.name
        record.byte_count = path.stat().st_size

    async def _synthesize_openai(self, record: SpeechRecord) -> None:
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for audio.mode=openai.")
        path = self._speech_path(record)
        client = AsyncOpenAI(api_key=self.openai_api_key)
        response = await client.audio.speech.create(
            model=self.config.speech_model,
            voice=self.config.speech_voice,
            input=record.text,
            response_format=self.config.speech_format,
        )
        content = await _read_binary_response(response)
        path.write_bytes(content)
        record.path = path.name
        record.byte_count = len(content)

    async def _transcribe_openai(self, path: Path) -> str:
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for audio.mode=openai.")
        client = AsyncOpenAI(api_key=self.openai_api_key)
        with path.open("rb") as audio_file:
            response = await client.audio.transcriptions.create(
                model=self.config.transcription_model,
                file=audio_file,
                response_format="json",
            )
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        if isinstance(response, str):
            return response
        raise TypeError(f"Unsupported transcription response type: {type(response)!r}")

    def _write_tone_wav(self, path: Path) -> None:
        sample_rate = 24_000
        duration_seconds = 0.18
        frames = int(sample_rate * duration_seconds)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            for index in range(frames):
                value = int(9000 * math.sin(2 * math.pi * 440 * (index / sample_rate)))
                wav.writeframesraw(value.to_bytes(2, "little", signed=True))


async def _read_binary_response(response) -> bytes:
    if hasattr(response, "aread"):
        return await response.aread()
    if hasattr(response, "read"):
        data = response.read()
        if hasattr(data, "__await__"):
            return await data
        return data
    if hasattr(response, "content"):
        return response.content
    raise TypeError(f"Unsupported speech response type: {type(response)!r}")
