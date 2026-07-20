from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from .bridge import AudioBridge, PreparedSpeech


@dataclass(frozen=True)
class NarrationItem:
    slide_index: int
    text: str


@dataclass
class PrefetchResult:
    slide_index: int
    text: str
    prepared: PreparedSpeech | None = None
    error: str | None = None
    consumed: bool = False


class NarrationPrefetchCoordinator:
    def __init__(
        self,
        audio: AudioBridge,
        items: list[NarrationItem],
        *,
        concurrency: int = 2,
    ) -> None:
        self.audio = audio
        self.items = list(items)
        self._semaphore = asyncio.Semaphore(max(concurrency, 1))
        self._tasks: dict[int, asyncio.Task[PrefetchResult]] = {}

    def start(self) -> None:
        for item in self.items:
            self._tasks[item.slide_index] = asyncio.create_task(self._prepare(item))

    async def get(self, slide_index: int) -> PrefetchResult:
        try:
            task = self._tasks[slide_index]
        except KeyError as exc:
            raise KeyError(f"No narration prefetch task for slide {slide_index}") from exc
        return await task

    def mark_consumed(self, slide_index: int) -> None:
        task = self._tasks.get(slide_index)
        if task and task.done() and not task.cancelled():
            with contextlib.suppress(Exception):
                task.result().consumed = True

    async def close(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for result in results:
            if isinstance(result, PrefetchResult) and (result.error or not result.consumed):
                self._remove_prepared_file(result.prepared)
            elif isinstance(result, asyncio.CancelledError):
                continue

    async def _prepare(self, item: NarrationItem) -> PrefetchResult:
        prepared: PreparedSpeech | None = None
        try:
            async with self._semaphore:
                prepared = await self.audio.prepare_speech(item.text)
            if prepared.error:
                self._remove_prepared_file(prepared)
                return PrefetchResult(
                    slide_index=item.slide_index,
                    text=item.text,
                    prepared=prepared,
                    error=prepared.error,
                )
            return PrefetchResult(
                slide_index=item.slide_index,
                text=item.text,
                prepared=prepared,
            )
        except asyncio.CancelledError:
            self._remove_prepared_file(prepared)
            raise
        except Exception as exc:
            self._remove_prepared_file(prepared)
            return PrefetchResult(
                slide_index=item.slide_index,
                text=item.text,
                prepared=prepared,
                error=str(exc),
            )

    @staticmethod
    def _remove_prepared_file(prepared: PreparedSpeech | None) -> None:
        if prepared and prepared.path:
            prepared.path.unlink(missing_ok=True)
