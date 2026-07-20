from __future__ import annotations

import math
import time
import wave
from pathlib import Path

from openai import AsyncOpenAI

from ..config import AudioConfig
from ..schemas import SpeechRecord, now_utc
from .bridge_client import BridgeClient, PlaybackInterrupted, create_bridge_client


class AudioBridge:
    def __init__(self, config: AudioConfig | None = None, output_dir: Path | None = None, openai_api_key: str | None = None, bridge_client: BridgeClient | None = None) -> None:
        self.config = config or AudioConfig()
        self.output_dir = output_dir
        self.openai_api_key = openai_api_key
        self.openai_client = (
            AsyncOpenAI(
                api_key=openai_api_key,
                timeout=self.config.openai_timeout_seconds,
                max_retries=self.config.openai_max_retries,
            )
            if openai_api_key
            else None
        )
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
            synthesis_started = time.perf_counter()
            record.synthesis_started_at = now_utc()
            playback = None
            if self.config.mode == "openai" and self.config.streaming_speech_enabled:
                playback = await self._synthesize_and_stream_openai(record)
            elif self.config.mode == "openai":
                await self._synthesize_openai(record)
            else:
                self._synthesize_simulator(record)
            record.synthesis_completed_at = now_utc()
            record.synthesis_duration_ms = int((time.perf_counter() - synthesis_started) * 1000)
            if playback is None and record.path and self.output_dir:
                path = self.output_dir / record.path
                record.duration_seconds = self._wav_duration(path)
                playback_started = time.perf_counter()
                record.playback_started_at = now_utc()
                playback = await self.bridge_client.play_audio(path)
                record.playback_completed_at = now_utc()
                record.playback_duration_ms = int((time.perf_counter() - playback_started) * 1000)
            elif playback is not None:
                record.playback_completed_at = now_utc()
            if playback is not None:
                played = playback.result.get("played", False)
                if not playback.ok or str(played).lower() not in {"true", "1"}:
                    raise RuntimeError(playback.error or "Audio playback did not start.")
                record.playback_device = str(playback.result.get("output_device", "")) or None
                record.playback_route = str(playback.result.get("route", "")) or None
            record.completed_at = now_utc()
        except PlaybackInterrupted:
            record.interrupted = True
            record.completed_at = now_utc()
        except Exception as exc:
            record.error = str(exc)
            record.completed_at = now_utc()
            raise
        finally:
            self.last_record = record
        return record

    async def interrupt_speech(self) -> bool:
        return await self.bridge_client.interrupt_playback()

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
        if self.openai_client is None:
            raise ValueError("OPENAI_API_KEY is required for audio.mode=openai.")
        response = await self.openai_client.audio.speech.create(
            model=self.config.speech_model,
            voice=self.config.speech_voice,
            input=record.text,
            response_format=self.config.speech_format,
        )
        content = await _read_binary_response(response)
        path.write_bytes(content)
        if record.format == "wav":
            self._normalize_streaming_wav_header(path)
        record.path = path.name
        record.byte_count = path.stat().st_size

    async def _synthesize_and_stream_openai(self, record: SpeechRecord):
        if not self.openai_api_key or self.openai_client is None:
            raise ValueError("OPENAI_API_KEY is required for audio.mode=openai.")
        path = self._speech_path(record)
        pcm_path = path.with_suffix(".pcm")
        started = time.perf_counter()
        playback_started = time.perf_counter()
        record.streaming = True
        record.playback_started_at = now_utc()

        async def chunks():
            async with self.openai_client.audio.speech.with_streaming_response.create(
                model=self.config.speech_model,
                voice=self.config.speech_voice,
                input=record.text,
                response_format="pcm",
            ) as response:
                async for chunk in response.iter_bytes(
                    chunk_size=max(self.config.streaming_speech_chunk_bytes, 960)
                ):
                    if record.time_to_first_audio_ms is None and chunk:
                        record.time_to_first_audio_ms = int(
                            (time.perf_counter() - started) * 1000
                        )
                    yield chunk

        try:
            playback = await self.bridge_client.play_pcm_stream(
                chunks(),
                pcm_path,
                self.config.streaming_speech_sample_rate,
            )
            record.playback_completed_at = now_utc()
            record.playback_duration_ms = int((time.perf_counter() - playback_started) * 1000)
            return playback
        finally:
            if pcm_path.exists() and pcm_path.stat().st_size:
                self._pcm_to_wav(
                    pcm_path,
                    path,
                    self.config.streaming_speech_sample_rate,
                )
                record.path = path.name
                record.byte_count = path.stat().st_size
                record.duration_seconds = self._wav_duration(path)
            pcm_path.unlink(missing_ok=True)

    @staticmethod
    def _pcm_to_wav(pcm_path: Path, wav_path: Path, sample_rate: int) -> None:
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            with pcm_path.open("rb") as pcm:
                while chunk := pcm.read(64 * 1024):
                    wav.writeframesraw(chunk)

    async def _transcribe_openai(self, path: Path) -> str:
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for audio.mode=openai.")
        if self.openai_client is None:
            raise ValueError("OPENAI_API_KEY is required for audio.mode=openai.")
        with path.open("rb") as audio_file:
            response = await self.openai_client.audio.transcriptions.create(
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

    @staticmethod
    def _wav_duration(path: Path) -> float | None:
        if path.suffix.lower() != ".wav":
            return None
        try:
            with wave.open(str(path), "rb") as wav:
                if wav.getframerate() <= 0 or wav.getnframes() <= 0:
                    raise ValueError("Synthesized WAV contains no audio frames.")
                frame_count = wav.getnframes()
                bytes_per_frame = wav.getnchannels() * wav.getsampwidth()
                # Streaming WAV responses may leave RIFF/data sizes at 0xFFFFFFFF.
                # Derive the real frame count from the bytes after the data header.
                header = path.read_bytes()[:4096]
                data_marker = header.find(b"data")
                if data_marker >= 0 and bytes_per_frame > 0:
                    actual_frames = (path.stat().st_size - data_marker - 8) // bytes_per_frame
                    frame_count = min(frame_count, actual_frames)
                if frame_count <= 0:
                    raise ValueError("Synthesized WAV contains no PCM payload.")
                return frame_count / wav.getframerate()
        except wave.Error as exc:
            raise ValueError(f"Synthesized WAV is invalid: {exc}") from exc

    @staticmethod
    def _normalize_streaming_wav_header(path: Path) -> None:
        """Replace streaming-size sentinels so native audio engines read the PCM correctly."""
        content = bytearray(path.read_bytes())
        if len(content) < 44 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
            raise ValueError("Synthesized WAV has an invalid RIFF header.")
        data_marker = content.find(b"data", 12, min(len(content), 4096))
        if data_marker < 0 or data_marker + 8 > len(content):
            raise ValueError("Synthesized WAV has no data chunk.")
        riff_size = len(content) - 8
        data_size = len(content) - data_marker - 8
        if riff_size > 0xFFFFFFFF or data_size > 0xFFFFFFFF:
            raise ValueError("Synthesized WAV is too large for a RIFF container.")
        content[4:8] = riff_size.to_bytes(4, "little")
        content[data_marker + 4 : data_marker + 8] = data_size.to_bytes(4, "little")
        path.write_bytes(content)


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
