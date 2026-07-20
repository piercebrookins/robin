from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from robin_core.audio.bridge import AudioBridge, PreparedSpeech
from robin_core.audio.prefetch import NarrationItem, NarrationPrefetchCoordinator
from robin_core.config import AudioConfig


class TrackingAudioBridge(AudioBridge):
    def __init__(self, tmp_path: Path, delays: dict[int, float] | None = None) -> None:
        super().__init__(AudioConfig(mode="simulator"), tmp_path)
        self.delays = delays or {}
        self.active = 0
        self.max_active = 0
        self.started: list[int] = []

    async def prepare_speech(self, text: str) -> PreparedSpeech:
        index = int(text.rsplit(" ", 1)[-1])
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.append(index)
        try:
            await asyncio.sleep(self.delays.get(index, 0))
            return await super().prepare_speech(text)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_prefetch_limits_concurrent_synthesis(tmp_path: Path) -> None:
    audio = TrackingAudioBridge(tmp_path, delays={0: 0.02, 1: 0.02, 2: 0.02})
    coordinator = NarrationPrefetchCoordinator(
        audio,
        [NarrationItem(index, f"slide {index}") for index in range(5)],
        concurrency=2,
    )

    coordinator.start()
    results = [await coordinator.get(index) for index in range(5)]

    assert audio.max_active == 2
    assert [result.slide_index for result in results] == [0, 1, 2, 3, 4]
    for index in range(5):
        coordinator.mark_consumed(index)
    await coordinator.close()


@pytest.mark.asyncio
async def test_prefetch_preserves_get_order_when_completion_order_differs(tmp_path: Path) -> None:
    audio = TrackingAudioBridge(tmp_path, delays={0: 0.03, 1: 0.0})
    coordinator = NarrationPrefetchCoordinator(
        audio,
        [NarrationItem(0, "slide 0"), NarrationItem(1, "slide 1")],
        concurrency=2,
    )

    coordinator.start()
    second = await coordinator.get(1)
    first = await coordinator.get(0)

    assert second.slide_index == 1
    assert first.slide_index == 0
    coordinator.mark_consumed(0)
    coordinator.mark_consumed(1)
    await coordinator.close()


@pytest.mark.asyncio
async def test_prefetch_failure_removes_partial_file(tmp_path: Path) -> None:
    class FailingAudio(TrackingAudioBridge):
        async def prepare_speech(self, text: str) -> PreparedSpeech:
            partial = tmp_path / "partial.wav"
            partial.write_bytes(b"partial")
            return PreparedSpeech(
                text=text,
                path=partial,
                model="model",
                voice="voice",
                format="wav",
                mode="simulator",
                error="synthesis failed",
            )

    coordinator = NarrationPrefetchCoordinator(
        FailingAudio(tmp_path),
        [NarrationItem(0, "slide 0")],
    )

    coordinator.start()
    result = await coordinator.get(0)
    await coordinator.close()

    assert result.error == "synthesis failed"
    assert result.prepared is not None
    assert result.prepared.path is not None
    assert not result.prepared.path.exists()


@pytest.mark.asyncio
async def test_prefetch_cancellation_removes_unconsumed_files(tmp_path: Path) -> None:
    audio = TrackingAudioBridge(tmp_path, delays={0: 0.0, 1: 0.05})
    coordinator = NarrationPrefetchCoordinator(
        audio,
        [NarrationItem(0, "slide 0"), NarrationItem(1, "slide 1")],
        concurrency=1,
    )

    coordinator.start()
    result = await coordinator.get(0)
    await coordinator.close()

    assert result.prepared is not None
    assert result.prepared.path is not None
    assert not result.prepared.path.exists()
