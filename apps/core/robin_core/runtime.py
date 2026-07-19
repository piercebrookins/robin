from __future__ import annotations

import asyncio
import resource
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from contextlib import suppress
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .artifacts import ArtifactWorker
from .agent import GeneralTaskAgent
from .audio.bridge import AudioBridge
from .audio.realtime import RealtimeTranscriber
from .calendar import calendar_snapshot
from .config import Settings, load_settings
from .intent import IntentClassifier
from .browser.controller import BrowserController
from .browser.operator_agent import BrowserOperatorResult, ControlledBrowserAgent
from .meeting.adapters.google_meet import GoogleMeetAdapter
from .memory import MeetingMemoryManager
from .persistence import Store
from .schemas import (
    Artifact,
    DeckSpec,
    EventEnvelope,
    FileIndexRecord,
    HealthItem,
    MeetingIntent,
    MeetingMemoryItem,
    MeetingState,
    CalendarSnapshot,
    RobinTask,
    RuntimeSnapshot,
    RuntimeMetrics,
    RuntimeState,
    PresentationSession,
    SpeechRecord,
    TaskStatus,
    TranscriptSegment,
    WorkspaceSnapshot,
    now_utc,
)
from .workspace import Workspace


class RobinRuntime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self._started_monotonic = time.monotonic()
        self.workspace = Workspace(self.settings.workspace)
        self.store = Store(self.settings.database.path)
        self.intent = IntentClassifier(self.settings)
        self.memory_manager = MeetingMemoryManager(self.settings)
        self.artifacts_worker = ArtifactWorker(self.workspace, self.settings.presentation.base_url)
        self.browser = BrowserController(self.settings.browser)
        self.task_agent = GeneralTaskAgent(self.settings, self.workspace)
        self.browser_operator = ControlledBrowserAgent(self.settings, self.browser)
        self.meet = GoogleMeetAdapter(self.browser, self.settings.browser)
        self.audio = AudioBridge(
            self.settings.audio,
            self.workspace.sessions / "speech",
            self.settings.openai_api_key,
        )
        self.runtime_state = RuntimeState.READY
        self.meeting_state = MeetingState.IDLE
        self.meeting_url: str | None = None
        self.meeting_id = uuid4()
        self.transcript: list[TranscriptSegment] = self.store.list("transcript", TranscriptSegment)
        self.meeting_memory: list[MeetingMemoryItem] = self.store.list(
            "meeting_memory", MeetingMemoryItem
        )
        self.tasks: list[RobinTask] = self.store.list("task", RobinTask)
        self.artifacts: list[Artifact] = self.store.list("artifact", Artifact)
        self.files: list[FileIndexRecord] = self.store.list("file", FileIndexRecord)
        self.speech: list[SpeechRecord] = self.store.list("speech", SpeechRecord)
        self.presentations: dict[UUID, PresentationSession] = {}
        self.health: list[HealthItem] = []
        self.task_slots = asyncio.Semaphore(self.settings.runtime.max_concurrent_tasks)
        self._task_handles: dict[UUID, asyncio.Task] = {}
        self._subscribers: set[asyncio.Queue[RuntimeSnapshot]] = set()
        self._event_subscribers: set[asyncio.Queue[EventEnvelope]] = set()
        self._listen_handle: asyncio.Task | None = None
        self._join_lock = asyncio.Lock()
        self._speech_lock = asyncio.Lock()
        self._capture_lock = asyncio.Lock()
        self._speech_handles: set[asyncio.Task] = set()
        self._memory_handles: set[asyncio.Task] = set()
        self._last_spoken_at_ms = 0
        self._calendar_handle: asyncio.Task | None = None
        self._calendar_joined_event_ids: set[str] = set()
        self._calendar_active_event_id: str | None = None
        self._calendar_active_event_end: datetime | None = None
        self._pending_confirmation: tuple[MeetingIntent, TranscriptSegment, UUID] | None = None
        self._last_audio_text: str | None = None
        self._last_audio_text_at_ms: int = 0
        self._last_silence_event_at_ms: int = 0
        self._realtime_partials: dict[str, str] = {}
        self._meet_recovery_event_count = 0
        self.refresh_health()

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            runtime_state=self.runtime_state,
            meeting_state=self.meeting_state,
            meeting_url=self.meeting_url,
            meeting_id=self.meeting_id,
            listening=self.meeting_state in {MeetingState.LISTENING, MeetingState.SPEAKING, MeetingState.PRESENTING},
            presenting=self.meet.presenting,
            capture_loop_running=self._listen_handle is not None and not self._listen_handle.done(),
            calendar_auto_join_running=self._calendar_handle is not None and not self._calendar_handle.done(),
            health=self.health,
            transcript=self.transcript[-100:],
            meeting_memory=[
                item for item in self.meeting_memory if item.meeting_id == self.meeting_id
            ][-100:],
            tasks=sorted(self.tasks, key=lambda task: task.created_at),
            artifacts=sorted(self.artifacts, key=lambda artifact: artifact.created_at),
            speech=sorted(self.speech, key=lambda item: item.started_at)[-25:],
            presentations=sorted(self.presentations.values(), key=lambda item: item.updated_at),
        )

    def refresh_health(self) -> None:
        bridge_mode = self.settings.audio.bridge_mode
        self.health = [
            HealthItem(name="workspace", ok=self.workspace.root.exists(), detail=str(self.workspace.root)),
            HealthItem(name="audio_capture", ok=self.audio.capture_healthy, detail=f"{self.settings.audio.mode}/{bridge_mode} bridge healthy"),
            HealthItem(name="virtual_microphone", ok=self.audio.virtual_mic_healthy, detail=self.settings.audio.output_device_name),
            HealthItem(name="browser_automation", ok=True, detail=f"{self.settings.browser.automation_mode} adapter ready"),
            HealthItem(name="openai", ok=bool(self.settings.openai_api_key), detail="configured" if self.settings.openai_api_key else "local fallback"),
        ]

    async def refresh_bridge_health(self) -> None:
        permissions = await self.audio.permissions_status()
        self.audio.capture_healthy = permissions.microphone or permissions.mode == "simulator"
        self.audio.virtual_mic_healthy = permissions.audio_device_available
        self.refresh_health()

    async def join_meeting(
        self,
        meeting_url: str,
        start_listening: bool = False,
    ) -> RuntimeSnapshot:
        async with self._join_lock:
            active_states = {
                MeetingState.NAVIGATING,
                MeetingState.PREJOIN,
                MeetingState.REQUESTING_ADMISSION,
                MeetingState.JOINED,
                MeetingState.LISTENING,
                MeetingState.SPEAKING,
                MeetingState.PRESENTING,
            }
            if self.meeting_url == meeting_url and self.meeting_state in active_states:
                await self.emit_event(
                    "meeting.join.duplicate_suppressed",
                    {"meeting_url": meeting_url},
                    component="meeting",
                )
                if start_listening:
                    await self.start_listening_loop()
                return await self.publish()
            if self.meeting_url and self.meeting_state in active_states:
                raise ValueError(
                    "Robin is already in a meeting. Leave it before joining another link."
                )

            started = time.perf_counter()
            self.meeting_id = uuid4()
            self.runtime_state = RuntimeState.JOINING_MEETING
            self.meeting_state = MeetingState.NAVIGATING
            self.meeting_url = meeting_url
            await self.emit_event(
                "meeting.join.started", {"meeting_url": meeting_url}, component="meeting"
            )
            await self.publish()
            try:
                await self.meet.navigate(meeting_url)
                self.meeting_state = self.meet.state
                await self.publish()
                await self.meet.join()
                self.meeting_state = self.meet.state
                await self._emit_meet_recovery_events()
                self.runtime_state = RuntimeState.IN_MEETING
                await self.emit_event(
                    "meeting.joined",
                    {
                        "meeting_url": meeting_url,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    },
                    component="meeting",
                )
                await self.publish()
                if start_listening:
                    return await self.start_listening_loop()
                return self.snapshot()
            except Exception as exc:
                self.runtime_state = RuntimeState.READY
                self.meeting_state = MeetingState.IDLE
                self.meeting_url = None
                await self.emit_event(
                    "meeting.join.failed",
                    {"meeting_url": meeting_url, "error": str(exc)},
                    component="meeting",
                )
                await self.publish()
                raise

    def calendar_snapshot(self) -> CalendarSnapshot:
        snapshot = calendar_snapshot(self.settings.calendar)
        snapshot.auto_join_running = self._calendar_handle is not None and not self._calendar_handle.done()
        return snapshot

    async def reindex_workspace(self) -> WorkspaceSnapshot:
        self.files = self.workspace.index()
        self.store.replace_all("file", self.files)
        await self.emit_event(
            "workspace.reindexed",
            {"file_count": len(self.files), "root": str(self.workspace.root)},
            component="workspace",
        )
        return self.workspace_snapshot()

    def workspace_snapshot(self) -> WorkspaceSnapshot:
        if not self.files:
            self.files = self.store.list("file", FileIndexRecord)
        return WorkspaceSnapshot(
            root=str(self.workspace.root),
            source_dir=self.settings.workspace.source_dir,
            generated_dir=self.settings.workspace.generated_dir,
            sessions_dir=self.settings.workspace.sessions_dir,
            file_count=len(self.files),
            files=sorted(self.files, key=lambda item: item.relative_path),
        )

    def workspace_file(self, file_id: UUID) -> FileIndexRecord:
        snapshot = self.workspace_snapshot()
        for record in snapshot.files:
            if record.id == file_id:
                return record
        raise KeyError(f"Unknown workspace file: {file_id}")

    async def join_calendar_event(self, event_id: str) -> RuntimeSnapshot:
        snapshot = self.calendar_snapshot()
        if snapshot.error:
            raise ValueError(snapshot.error)
        event = next((item for item in snapshot.events if item.id == event_id), None)
        if not event:
            raise KeyError(f"Unknown calendar event: {event_id}")
        return await self._join_calendar_event(event)

    async def _join_calendar_event(self, event) -> RuntimeSnapshot:
        await self.emit_event("calendar.event.selected", event.model_dump(mode="json"), component="calendar")
        self._calendar_joined_event_ids.add(event.id)
        self._calendar_active_event_id = event.id
        self._calendar_active_event_end = event.end
        return await self.join_meeting(event.meeting_url)

    async def set_calendar_auto_join(self, enabled: bool, interval_seconds: float = 15.0) -> RuntimeSnapshot:
        self.settings.calendar.auto_join = enabled
        if enabled:
            if not self.settings.calendar.enabled:
                raise ValueError("Calendar discovery is disabled.")
            if self._calendar_handle and not self._calendar_handle.done():
                return await self.publish()
            self._calendar_handle = asyncio.create_task(self._calendar_loop(max(interval_seconds, 1.0)))
            await self.emit_event("calendar.auto_join.enabled", {"interval_seconds": max(interval_seconds, 1.0)}, component="calendar")
        else:
            if self._calendar_handle and not self._calendar_handle.done():
                self._calendar_handle.cancel()
                with suppress(asyncio.CancelledError):
                    await self._calendar_handle
            self._calendar_handle = None
            await self.emit_event("calendar.auto_join.disabled", {}, component="calendar")
        return await self.publish()

    async def poll_calendar_once(self, now: datetime | None = None) -> RuntimeSnapshot:
        snapshot = calendar_snapshot(self.settings.calendar, now=now)
        snapshot.auto_join_running = self._calendar_handle is not None and not self._calendar_handle.done()
        if snapshot.error:
            await self.emit_event("calendar.poll.failed", {"error": snapshot.error}, component="calendar")
            return await self.publish()
        current = now or datetime.now(timezone.utc)
        if self._calendar_active_event_id and self._calendar_active_event_end and self._calendar_active_event_end <= current:
            await self.emit_event("calendar.event.ended", {"event_id": self._calendar_active_event_id}, component="calendar")
            self._calendar_active_event_id = None
            self._calendar_active_event_end = None
            if self.meeting_url:
                return await self.leave_meeting()
        if not self.settings.calendar.auto_join:
            return await self.publish()
        if snapshot.conflicts:
            await self.emit_event("calendar.auto_join.skipped", {"reason": "conflict", "conflicts": snapshot.conflicts}, component="calendar")
            return await self.publish()
        if self.meeting_state not in {MeetingState.IDLE, MeetingState.ENDED} and self.meeting_url:
            await self.emit_event("calendar.auto_join.skipped", {"reason": "already_in_meeting", "meeting_url": self.meeting_url}, component="calendar")
            return await self.publish()
        join_before = current.timestamp() + self.settings.calendar.join_early_seconds
        candidates = [
            event
            for event in snapshot.events
            if not event.conflicted
            and event.id not in self._calendar_joined_event_ids
            and event.end >= current
            and event.start.timestamp() <= join_before
        ]
        if not candidates:
            return await self.publish()
        event = candidates[0]
        await self.emit_event("calendar.auto_join.started", event.model_dump(mode="json"), component="calendar")
        return await self._join_calendar_event(event)

    async def _calendar_loop(self, interval_seconds: float) -> None:
        try:
            while True:
                await self.poll_calendar_once()
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.emit_event("calendar.auto_join.failed", {"error": str(exc)}, component="calendar")
            await self.publish()

    async def leave_meeting(self) -> RuntimeSnapshot:
        self.meeting_state = MeetingState.LEAVING
        if self._listen_handle and not self._listen_handle.done():
            await self.stop_listening_loop()
        if self.meet.presenting or any(state.active for state in self.presentations.values()):
            await self.stop_presenting()
        await self.meet.leave()
        self.meeting_state = self.meet.state
        await self._emit_meet_recovery_events()
        self.runtime_state = RuntimeState.READY
        await self.emit_event(
            "meeting.leave.cleanup",
            {"capture_loop_running": False, "presenting": False},
            component="meeting",
        )
        await self.emit_event("meeting.left", {}, component="meeting")
        return await self.publish()

    async def emergency_stop(self) -> RuntimeSnapshot:
        self.runtime_state = RuntimeState.STOPPING
        for handle in self._task_handles.values():
            handle.cancel()
        for handle in list(self._memory_handles):
            handle.cancel()
        if self._memory_handles:
            await asyncio.gather(*self._memory_handles, return_exceptions=True)
            self._memory_handles.clear()
        if self._calendar_handle and not self._calendar_handle.done():
            self._calendar_handle.cancel()
            with suppress(asyncio.CancelledError):
                await self._calendar_handle
            self._calendar_handle = None
            self.settings.calendar.auto_join = False
        await self.stop_listening_loop()
        for task in self.tasks:
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}:
                task.status = TaskStatus.CANCELLED
                task.updated_at = now_utc()
                self.store.upsert("task", task)
        await self.audio.stop()
        await self.meet.leave()
        self.meeting_state = MeetingState.ENDED
        self.runtime_state = RuntimeState.READY
        self.refresh_health()
        await self.emit_event("runtime.emergency_stop", {}, component="runtime")
        return await self.publish()

    async def ingest_transcript(
        self,
        text: str,
        speaker_name: str | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        source: str = "simulator",
    ) -> RuntimeSnapshot:
        now_ms = int(time.time() * 1000)
        segment = TranscriptSegment(
            meeting_id=self.meeting_id,
            speaker_name=speaker_name,
            text=text,
            started_at_ms=started_at_ms or now_ms,
            ended_at_ms=ended_at_ms or now_ms,
            source=source,
        )
        self.transcript.append(segment)
        self.store.upsert("transcript", segment)
        self._schedule_memory_update(segment)
        await self.emit_event(
            "transcript.final",
            {"text": segment.text, "speaker_name": segment.speaker_name, "source": segment.source},
            component="transcript",
        )
        await self._handle_intent(segment)
        return await self.publish()

    def _schedule_memory_update(self, segment: TranscriptSegment) -> None:
        handle = asyncio.create_task(self._update_meeting_memory(segment))
        self._memory_handles.add(handle)
        handle.add_done_callback(self._memory_handles.discard)

    async def _update_meeting_memory(self, segment: TranscriptSegment) -> None:
        try:
            current = [
                item for item in self.meeting_memory if item.meeting_id == segment.meeting_id
            ]
            additions, resolve_ids = await self.memory_manager.extract(segment, current)
            before = {item.id: item.status for item in self.meeting_memory}
            MeetingMemoryManager.merge(self.meeting_memory, additions, resolve_ids)
            for item in self.meeting_memory:
                if item.id not in before or before[item.id] != item.status:
                    self.store.upsert("meeting_memory", item)
            if additions or resolve_ids:
                await self.emit_event(
                    "memory.updated",
                    {
                        "added": [item.model_dump(mode="json") for item in additions],
                        "resolved_ids": resolve_ids,
                    },
                    component="meeting_memory",
                )
                await self.publish()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.emit_event(
                "memory.update.failed", {"error": str(exc)}, component="meeting_memory"
            )
            await self.publish()

    async def transcribe_audio_file(self, relative_path: str, speaker_name: str | None = None) -> RuntimeSnapshot:
        path = self.workspace.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {relative_path}")
        text = await self.audio.transcribe_file(path)
        return await self.ingest_transcript(text, speaker_name=speaker_name, source="audio_stt")

    async def capture_audio_sample(
        self,
        bundle_id: str = "com.google.Chrome",
        duration_ms: int = 1500,
        output_name: str | None = None,
        emit_capture_event: bool = True,
    ) -> dict:
        capture_dir = self.workspace.sessions / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        safe_name = output_name or f"capture_{int(time.time() * 1000)}.wav"
        if "/" in safe_name or ".." in safe_name:
            raise ValueError("output_name must be a simple filename")
        output_path = capture_dir / safe_name
        async with self._capture_lock:
            response = await self.audio.bridge_client.capture_audio_sample(
                bundle_id,
                output_path,
                duration_ms=duration_ms,
            )
        if emit_capture_event:
            await self.emit_event(
                "audio.capture.sample",
                {
                    "bundle_id": bundle_id,
                    "duration_ms": duration_ms,
                    "path": str(output_path.relative_to(self.workspace.root)),
                    "ok": response.ok,
                    "result": response.result,
                    "error": response.error,
                },
                component="audio",
            )
        return {
            "ok": response.ok,
            "path": output_path.relative_to(self.workspace.root).as_posix(),
            "result": response.result,
            "error": response.error,
        }

    async def test_audio_output(self) -> RuntimeSnapshot:
        text = "Robin voice check. If you can hear this, my meeting audio is working."
        await self.emit_event(
            "audio.output.test.started",
            {"device": self.settings.audio.output_device_name},
            component="audio",
        )
        active_meeting = self.meeting_url is not None and self.meeting_state not in {
            MeetingState.IDLE,
            MeetingState.ENDED,
        }
        if active_meeting:
            await self._acknowledge(text)
        else:
            await self._speak_and_record(text)
            await self.publish()
        speech = self.speech[-1]
        await self.emit_event(
            "audio.output.test.passed",
            {
                "device": speech.playback_device or self.settings.audio.output_device_name,
                "duration_seconds": speech.duration_seconds,
                "mode": speech.mode,
            },
            component="audio",
        )
        return await self.publish()

    async def test_audio_input(self, duration_ms: int | None = None) -> dict:
        sample_ms = duration_ms or self.settings.audio.capture_sample_duration_ms
        await self.emit_event(
            "audio.input.test.started",
            {
                "bundle_id": self.settings.audio.capture_bundle_id,
                "duration_ms": sample_ms,
            },
            component="audio",
        )
        result = await self.capture_audio_sample(
            bundle_id=self.settings.audio.capture_bundle_id,
            duration_ms=sample_ms,
            output_name="audio_input_test.wav",
            emit_capture_event=False,
        )
        if not result["ok"]:
            payload = {
                **result,
                "rms": 0.0,
                "peak": 0.0,
                "threshold": self.settings.audio.silence_rms_threshold,
                "transcript": "",
            }
            await self.emit_event("audio.input.test.failed", payload, component="audio")
            await self.publish()
            return payload
        metrics = result.get("result", {})
        rms = float(metrics.get("rms", 0.0))
        peak = float(metrics.get("peak", 0.0))
        signal = rms >= self.settings.audio.silence_rms_threshold
        transcript = ""
        if signal:
            transcript = (await self.audio.transcribe_file(self.workspace.resolve(result["path"]))).strip()
        event_type = "audio.input.test.passed" if signal and transcript else "audio.input.test.quiet"
        payload = {
            "rms": rms,
            "peak": peak,
            "threshold": self.settings.audio.silence_rms_threshold,
            "transcript": transcript,
            "path": result["path"],
        }
        await self.emit_event(event_type, payload, component="audio")
        await self.publish()
        return {"ok": event_type.endswith("passed"), **payload}

    async def capture_and_transcribe_once(
        self,
        bundle_id: str | None = None,
        duration_ms: int | None = None,
        speaker_name: str = "Meeting audio",
        retain_capture: bool = True,
    ) -> RuntimeSnapshot:
        started_ms = int(time.time() * 1000)
        result = await self.capture_audio_sample(
            bundle_id=bundle_id or self.settings.audio.capture_bundle_id,
            duration_ms=duration_ms or self.settings.audio.capture_sample_duration_ms,
            emit_capture_event=retain_capture,
        )
        if not result["ok"]:
            await self.emit_event("audio.capture.skipped", result, component="audio")
            return await self.publish()
        capture_path = self.workspace.resolve(result["path"])
        rms = float(result.get("result", {}).get("rms", 1.0))
        if rms < self.settings.audio.silence_rms_threshold:
            now_ms = int(time.time() * 1000)
            if now_ms - self._last_silence_event_at_ms >= 30_000:
                await self.emit_event(
                    "audio.silence.skipped",
                    {"rms": rms},
                    component="audio",
                )
                self._last_silence_event_at_ms = now_ms
            if not retain_capture:
                capture_path.unlink(missing_ok=True)
            return await self.publish()
        try:
            text = await self.audio.transcribe_file(capture_path)
        finally:
            if not retain_capture:
                capture_path.unlink(missing_ok=True)
        normalized = " ".join(text.lower().split())
        now_ms = int(time.time() * 1000)
        if not normalized:
            await self.emit_event("audio.transcript.empty", {"path": result["path"]}, component="audio")
            return await self.publish()
        if normalized == self._last_audio_text and now_ms - self._last_audio_text_at_ms < 10_000:
            await self.emit_event("audio.transcript.duplicate_suppressed", {"text": text}, component="audio")
            return await self.publish()
        self._last_audio_text = normalized
        self._last_audio_text_at_ms = now_ms
        return await self.ingest_transcript(
            text,
            speaker_name=speaker_name,
            started_at_ms=started_ms,
            ended_at_ms=now_ms,
            source="audio_stt",
        )

    async def start_listening_loop(
        self,
        bundle_id: str | None = None,
        duration_ms: int | None = None,
        interval_ms: int | None = None,
        max_iterations: int | None = None,
    ) -> RuntimeSnapshot:
        if self._listen_handle and not self._listen_handle.done():
            return self.snapshot()
        self._listen_handle = asyncio.create_task(
            self._realtime_listening_loop(
                bundle_id=bundle_id or self.settings.audio.capture_bundle_id,
                max_iterations=max_iterations,
            )
            if self.settings.audio.realtime_transcription_enabled
            and self.settings.openai_api_key
            else self._listening_loop(
                bundle_id=bundle_id or self.settings.audio.capture_bundle_id,
                duration_ms=duration_ms if duration_ms is not None else self.settings.audio.capture_sample_duration_ms,
                interval_ms=interval_ms if interval_ms is not None else self.settings.audio.capture_loop_interval_ms,
                max_iterations=max_iterations,
            )
        )
        if self.meeting_state == MeetingState.IDLE:
            self.meeting_state = MeetingState.LISTENING
        await self.emit_event(
            "audio.listen.started",
            {
                "bundle_id": bundle_id or self.settings.audio.capture_bundle_id,
                "mode": "realtime"
                if self.settings.audio.realtime_transcription_enabled
                and self.settings.openai_api_key
                else "bounded",
            },
            component="audio",
        )
        return await self.publish()

    async def stop_listening_loop(self) -> RuntimeSnapshot:
        if self._listen_handle and not self._listen_handle.done():
            self._listen_handle.cancel()
            with suppress(asyncio.CancelledError):
                await self._listen_handle
        self._listen_handle = None
        await self.emit_event("audio.listen.stopped", {}, component="audio")
        return await self.publish()

    async def run_browser_operator(
        self,
        request: str,
        page_name: str = "meet",
        approval_token: str | None = None,
    ) -> BrowserOperatorResult:
        await self.emit_event(
            "browser.operator.started",
            {"request": request[:500], "page": page_name},
            component="browser_operator",
        )
        result = await self.browser_operator.execute(
            request, page_name, approval_token=approval_token
        )
        for call in result.tool_calls:
            await self.emit_event(
                "browser.operator.tool",
                call,
                component="browser_operator",
            )
        await self.emit_event(
            "browser.operator.awaiting_confirmation"
            if result.status == "awaiting_confirmation"
            else "browser.operator.completed",
            result.model_dump(mode="json"),
            component="browser_operator",
        )
        await self.publish()
        return result

    async def _listening_loop(self, bundle_id: str, duration_ms: int, interval_ms: int, max_iterations: int | None) -> None:
        iterations = 0
        consecutive_failures = 0
        try:
            while max_iterations is None or iterations < max_iterations:
                now_ms = int(time.time() * 1000)
                speech_cooldown = max(self.settings.audio.post_speech_cooldown_ms, 0)
                if (
                    self.meeting_state == MeetingState.SPEAKING
                    or now_ms - self._last_spoken_at_ms < speech_cooldown
                ):
                    await asyncio.sleep(0.1)
                    continue
                try:
                    await self.capture_and_transcribe_once(
                        bundle_id=bundle_id,
                        duration_ms=duration_ms,
                        retain_capture=False,
                    )
                    consecutive_failures = 0
                except Exception as exc:
                    consecutive_failures += 1
                    await self.emit_event(
                        "audio.listen.iteration_failed",
                        {"error": str(exc), "consecutive_failures": consecutive_failures},
                        component="audio",
                    )
                    await self.publish()
                    await asyncio.sleep(min(2**consecutive_failures, 10))
                iterations += 1
                await asyncio.sleep(max(interval_ms, 0) / 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.emit_event("audio.listen.failed", {"error": str(exc)}, component="audio")
            self.audio.capture_healthy = False
            self.refresh_health()
            await self.publish()

    async def _realtime_listening_loop(
        self, bundle_id: str, max_iterations: int | None = None
    ) -> None:
        failures = 0
        completed_turns = 0

        async def on_partial(item_id: str, delta: str) -> None:
            if not delta:
                return
            text = self._realtime_partials.get(item_id, "") + delta
            self._realtime_partials[item_id] = text
            await self.emit_event(
                "audio.transcript.partial",
                {"item_id": item_id, "text": text},
                component="audio",
            )
            await self.publish()

        async def on_final(item_id: str, transcript: str) -> None:
            nonlocal completed_turns
            self._realtime_partials.pop(item_id, None)
            text = transcript.strip()
            if not text:
                return
            if self._is_recent_robin_echo(text):
                await self.emit_event(
                    "audio.transcript.echo_suppressed",
                    {"item_id": item_id, "text": text},
                    component="audio",
                )
                return
            normalized = " ".join(text.casefold().split())
            now_ms = int(time.time() * 1000)
            if (
                normalized == self._last_audio_text
                and now_ms - self._last_audio_text_at_ms < 10_000
            ):
                await self.emit_event(
                    "audio.transcript.duplicate_suppressed",
                    {"item_id": item_id, "text": text},
                    component="audio",
                )
                return
            self._last_audio_text = normalized
            self._last_audio_text_at_ms = now_ms
            completed_turns += 1
            await self.ingest_transcript(
                text,
                speaker_name="Meeting audio",
                started_at_ms=now_ms,
                ended_at_ms=now_ms,
                source="audio_stt",
            )

        async def on_speech_started() -> None:
            speaking = self.meeting_state == MeetingState.SPEAKING
            interrupted = await self.audio.interrupt_speech() if speaking else False
            await self.emit_event(
                "audio.speech.detected",
                {
                    "while_robin_speaking": speaking,
                    "playback_interrupted": interrupted,
                },
                component="audio",
            )
            await self.publish()

        while max_iterations is None or completed_turns < max_iterations:
            transcriber = RealtimeTranscriber(
                api_key=self.settings.openai_api_key or "",
                model=self.settings.audio.realtime_transcription_model,
                delay=self.settings.audio.realtime_transcription_delay,
                threshold=self.settings.audio.silence_rms_threshold,
                silence_ms=self.settings.audio.realtime_vad_silence_ms,
                min_speech_ms=self.settings.audio.realtime_vad_min_speech_ms,
            )
            try:
                await self.emit_event(
                    "audio.realtime.starting",
                    {"model": self.settings.audio.realtime_transcription_model},
                    component="audio",
                )
                await transcriber.run(
                    self.audio.bridge_client.stream_audio(
                        bundle_id, self.settings.audio.realtime_chunk_bytes
                    ),
                    on_partial,
                    on_final,
                    on_speech_started,
                )
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                await self.emit_event(
                    "audio.realtime.failed",
                    {"error": str(exc), "consecutive_failures": failures},
                    component="audio",
                )
                await self.publish()
                if failures >= 3:
                    await self.emit_event(
                        "audio.realtime.fallback",
                        {"mode": "bounded"},
                        component="audio",
                    )
                    await self._listening_loop(
                        bundle_id=bundle_id,
                        duration_ms=self.settings.audio.capture_sample_duration_ms,
                        interval_ms=self.settings.audio.capture_loop_interval_ms,
                        max_iterations=max_iterations,
                    )
                    return
                await asyncio.sleep(min(2**failures, 5))

    def _is_recent_robin_echo(self, text: str) -> bool:
        spoken = self.audio.last_spoken_text
        if not spoken or int(time.time() * 1000) - self._last_spoken_at_ms > 15_000:
            return False
        normalized = " ".join(text.casefold().split())
        expected = " ".join(spoken.casefold().split())
        if not normalized or not expected:
            return False
        return (
            normalized in expected
            or expected in normalized
            or SequenceMatcher(None, normalized, expected).ratio() >= 0.78
        )

    async def create_task(self, text: str, requester_name: str | None = None) -> RobinTask:
        duplicate = await self._handle_duplicate_task_request(text)
        if duplicate:
            return duplicate
        task = RobinTask(
            meeting_id=self.meeting_id,
            title=text[:80],
            requester_name=requester_name,
            status=TaskStatus.ACCEPTED,
            request_text=text,
            requested_outcome=text,
            constraints=[],
        )
        self.tasks.append(task)
        self.store.upsert("task", task)
        await self.emit_event("task.created", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
        self._schedule_task(task)
        await self.publish()
        self._schedule_acknowledgement(f"Got it. I’ll work on {task.title}.")
        return task

    async def cancel_task(self, task_id: UUID) -> None:
        task = self._find_task(task_id)
        task.status = TaskStatus.CANCELLED
        task.updated_at = now_utc()
        handle = self._task_handles.get(task_id)
        if handle:
            handle.cancel()
        self.store.upsert("task", task)
        await self.emit_event("task.cancelled", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
        await self._acknowledge(f"Cancelled {task.title}.")
        await self.publish()

    async def retry_task(self, task_id: UUID) -> RuntimeSnapshot:
        task = self._find_task(task_id)
        active_statuses = {TaskStatus.ACCEPTED, TaskStatus.QUEUED, TaskStatus.EXECUTING, TaskStatus.VALIDATING, TaskStatus.PRESENTING}
        if task.status in active_statuses:
            raise ValueError(f"Task is already active: {task.status}")
        task.revision += 1
        task.status = TaskStatus.ACCEPTED
        task.error = None
        task.started_at = None
        task.completed_at = None
        task.updated_at = now_utc()
        self.store.upsert("task", task)
        await self.emit_event("task.retry", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
        await self._acknowledge(f"Retrying {task.title}.")
        self._schedule_task(task)
        return await self.publish()

    async def present_task(self, task_id: UUID) -> RuntimeSnapshot:
        task = self._find_task(task_id)
        deck = self._latest_artifact(task_id, "deck_json")
        if not deck or not deck.url:
            raise ValueError("Task has no presentation artifact.")
        deck_spec = self._load_deck(task_id)
        self.activate_presentation(task_id)
        task.status = TaskStatus.PRESENTING
        task.updated_at = now_utc()
        self.store.upsert("task", task)
        await self.emit_event(
            "presentation.started",
            {"url": deck.url},
            task_id=task.id,
            component="presentation",
        )
        try:
            await self.meet.start_presenting(deck.url)
            self.meeting_state = self.meet.state
            await self._emit_meet_recovery_events(task.id)
            await self._narrate_deck(task.id, deck_spec)
        except Exception as exc:
            task.error = str(exc)
            await self.emit_event(
                "presentation.failed",
                {"error": str(exc)},
                task_id=task.id,
                component="presentation",
            )
            raise
        finally:
            if self.meet.presenting or self.presentations[task.id].active:
                await self.stop_presenting(task.id)
        task.status = TaskStatus.COMPLETED
        task.error = None
        task.completed_at = now_utc()
        task.updated_at = now_utc()
        self.store.upsert("task", task)
        await self.emit_event("presentation.completed", task.model_dump(mode="json"), task_id=task.id, component="presentation")
        return await self.publish()

    async def stop_presenting(self, task_id: UUID | None = None) -> RuntimeSnapshot:
        await self.meet.stop_presenting()
        self.meeting_state = self.meet.state
        await self._emit_meet_recovery_events(task_id)
        target_ids = (
            [task_id]
            if task_id
            else list(
                dict.fromkeys(
                    [
                        *self.presentations,
                        *(task.id for task in self.tasks if task.status == TaskStatus.PRESENTING),
                    ]
                )
            )
        )
        for current_id in target_ids:
            state = self.presentations.get(current_id)
            if state:
                state.active = False
                state.updated_at = now_utc()
            try:
                task = self._find_task(current_id)
            except KeyError:
                continue
            if task.status == TaskStatus.PRESENTING:
                task.status = TaskStatus.READY_TO_PRESENT
                task.updated_at = now_utc()
                self.store.upsert("task", task)
        await self.emit_event(
            "presentation.stopped",
            {"task_id": str(task_id) if task_id else None},
            task_id=task_id,
            component="presentation",
        )
        return await self.publish()

    def presentation_state(self, task_id: UUID) -> PresentationSession:
        return self.presentations.get(task_id) or self.activate_presentation(task_id)

    def activate_presentation(self, task_id: UUID) -> PresentationSession:
        slide_count = self._deck_slide_count(task_id)
        existing = self.presentations.get(task_id)
        if existing:
            existing.slide_count = slide_count
            existing.active = True
            existing.active_slide = min(existing.active_slide, max(slide_count - 1, 0))
            existing.updated_at = now_utc()
            return existing
        state = PresentationSession(task_id=task_id, active=True, slide_count=slide_count)
        self.presentations[task_id] = state
        return state

    async def navigate_presentation(self, task_id: UUID, action: str, index: int | None = None) -> PresentationSession:
        state = self.presentation_state(task_id)
        if action == "next":
            state.active_slide += 1
        elif action == "previous":
            state.active_slide -= 1
        elif action == "goto":
            if index is None:
                raise ValueError("index is required for goto")
            state.active_slide = index
        else:
            raise ValueError(f"Unknown presentation navigation action: {action}")
        state.active_slide = max(0, min(state.active_slide, max(state.slide_count - 1, 0)))
        state.updated_at = now_utc()
        await self.emit_event("presentation.updated", state.model_dump(mode="json"), task_id=task_id, component="presentation")
        await self.publish()
        return state

    def _deck_slide_count(self, task_id: UUID) -> int:
        return len(self._load_deck(task_id).slides)

    def _load_deck(self, task_id: UUID) -> DeckSpec:
        deck = self._latest_artifact(task_id, "deck_json")
        if not deck:
            raise ValueError("Task has no presentation deck.")
        return DeckSpec.model_validate_json(self.workspace.resolve(deck.path).read_text())

    async def _narrate_deck(self, task_id: UUID, deck: DeckSpec) -> None:
        for index, slide in enumerate(deck.slides):
            await self.navigate_presentation(task_id, "goto", index=index)
            speech = self._slide_narration(deck, index)
            await self.emit_event(
                "presentation.narration",
                {"slide": index, "speech": speech},
                task_id=task_id,
                component="presentation",
            )
            record = await self._acknowledge(speech)
            if record.interrupted:
                await self.emit_event(
                    "presentation.narration.interrupted",
                    {"slide": index},
                    task_id=task_id,
                    component="presentation",
                )
                break

    def _slide_narration(self, deck: DeckSpec, index: int) -> str:
        slide = deck.slides[index]
        if slide.type == "title":
            return f"I’ll walk through {deck.title}. {slide.body[0] if slide.body else ''}".strip()
        if slide.type == "executive_summary":
            return self._spoken_excerpt(" ".join(slide.body[:2]))
        if slide.type == "chart":
            return self._spoken_excerpt(
                f"This chart shows {slide.title.lower()}. {slide.body[0] if slide.body else ''}".strip()
            )
        if slide.type == "key_metrics":
            metrics = list(slide.metrics.items())[:3]
            if metrics:
                return "Key metrics: " + "; ".join(f"{label} is {value}" for label, value in metrics) + "."
            return f"Here are the key metrics for {deck.title}."
        if slide.type == "sources":
            source_names = ", ".join(source.label for source in deck.sources[:3])
            return f"I used {source_names} and validated the derived figures before presenting."
        return self._spoken_excerpt(" ".join(slide.body[:2]) or slide.title)

    @staticmethod
    def _spoken_excerpt(text: str, max_chars: int = 260) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        candidate = compact[: max_chars + 1]
        boundary = max(candidate.rfind(". "), candidate.rfind("; "))
        if boundary >= 15:
            return candidate[: boundary + 1]
        return candidate[: max_chars - 1].rstrip(" ,;:") + "."

    async def _handle_intent(self, segment: TranscriptSegment) -> None:
        if await self._handle_pending_confirmation(segment):
            return
        if await self._handle_duplicate_task_request(segment.text):
            return
        active = self._active_tasks()
        intent = await self.intent.classify(segment.text, active)
        if intent.classification in {"direct_request", "confirmed_task"} and intent.confidence >= self.settings.model.intent_confidence_accept:
            await self.emit_event("intent.detected", intent.model_dump(mode="json"), component="conversation")
            task = RobinTask(
                meeting_id=self.meeting_id,
                title=intent.task_title or segment.text[:80],
                requester_name=segment.speaker_name,
                status=TaskStatus.ACCEPTED,
                request_text=segment.text,
                requested_outcome=intent.requested_outcome or segment.text,
                constraints=intent.constraints,
                source_context_segment_ids=[segment.id],
            )
            self.tasks.append(task)
            self.store.upsert("task", task)
            await self.emit_event("task.created", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            self._schedule_task(task)
            await self.publish()
            self._schedule_acknowledgement(
                "Got it. I’ll analyze the files and prepare a short deck."
            )
        elif intent.classification == "task_modification" and intent.referenced_task_id:
            task = self._find_task(intent.referenced_task_id)
            task.revision += 1
            task.constraints = sorted(set(task.constraints + intent.constraints + [segment.text]))
            task.source_context_segment_ids.append(segment.id)
            task.updated_at = now_utc()
            self.store.upsert("task", task)
            await self.emit_event("task.updated", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self._acknowledge("Understood. I’ll apply that update to the active task.")
            handle = self._task_handles.get(task.id)
            if handle and not handle.done():
                handle.cancel()
            self._schedule_task(task)
            await self.publish()
        elif intent.classification == "task_cancellation" and intent.referenced_task_id:
            await self.cancel_task(intent.referenced_task_id)
        elif intent.classification == "status_request":
            summary = self._status_summary()
            await self._acknowledge(summary)
        elif intent.classification == "conversation_request":
            await self.emit_event(
                "conversation.addressed",
                {"text": segment.text},
                component="conversation",
            )
            await self._acknowledge(
                await self.intent.respond(
                    segment.text,
                    self._active_tasks(),
                    self._meeting_context(),
                    self._memory_context(),
                )
            )
        elif intent.should_ask_confirmation and intent.clarification_question:
            task = RobinTask(
                meeting_id=self.meeting_id,
                title=intent.task_title or segment.text[:80],
                requester_name=segment.speaker_name,
                status=TaskStatus.AWAITING_CLARIFICATION,
                request_text=segment.text,
                requested_outcome=intent.requested_outcome or segment.text,
                constraints=intent.constraints,
                source_context_segment_ids=[segment.id],
            )
            self.tasks.append(task)
            self.store.upsert("task", task)
            self._pending_confirmation = (intent, segment, task.id)
            await self.emit_event(
                "clarification.requested",
                {"question": intent.clarification_question, "text": segment.text},
                task_id=task.id,
                component="conversation",
            )
            await self.emit_event("task.created", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self.emit_event("task.awaiting_clarification", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self._acknowledge(intent.clarification_question)

    async def _handle_pending_confirmation(self, segment: TranscriptSegment) -> bool:
        if not self._pending_confirmation:
            return False
        lowered = segment.text.strip().lower()
        accepts = {"yes", "yeah", "yep", "please do", "go ahead", "do it", "take it", "sounds good", "correct"}
        declines = {"no", "nope", "never mind", "cancel", "don't", "do not", "ignore that"}
        is_accept = any(phrase in lowered for phrase in accepts)
        is_decline = any(phrase in lowered for phrase in declines)
        if not is_accept and not is_decline:
            return False
        intent, original, task_id = self._pending_confirmation
        self._pending_confirmation = None
        task = self._find_task(task_id)
        if is_decline:
            task.status = TaskStatus.CANCELLED
            task.updated_at = now_utc()
            task.source_context_segment_ids.append(segment.id)
            self.store.upsert("task", task)
            await self.emit_event(
                "clarification.declined",
                {"original_text": original.text, "answer": segment.text},
                task_id=task.id,
                component="conversation",
            )
            await self.emit_event("task.cancelled", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self._acknowledge("Okay, I will leave that alone.")
            await self.publish()
            return True
        duplicate = await self._handle_duplicate_task_request(original.text)
        if duplicate:
            task.status = TaskStatus.CANCELLED
            task.updated_at = now_utc()
            task.source_context_segment_ids.append(segment.id)
            self.store.upsert("task", task)
            await self.emit_event("task.cancelled", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self.emit_event(
                "clarification.accepted",
                {"original_text": original.text, "answer": segment.text, "duplicate_task_id": str(duplicate.id)},
                task_id=duplicate.id,
                component="conversation",
            )
            return True
        task.status = TaskStatus.ACCEPTED
        task.requested_outcome = intent.requested_outcome or original.text
        task.constraints = intent.constraints
        task.updated_at = now_utc()
        task.source_context_segment_ids.append(segment.id)
        self.store.upsert("task", task)
        await self.emit_event(
            "clarification.accepted",
            {"original_text": original.text, "answer": segment.text},
            task_id=task.id,
            component="conversation",
        )
        await self.emit_event("task.accepted", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
        await self._acknowledge("Got it. I’ll take that on.")
        self._schedule_task(task)
        await self.publish()
        return True

    async def _handle_duplicate_task_request(self, text: str) -> RobinTask | None:
        task = self._find_duplicate_task(text)
        if not task:
            return None
        await self.emit_event(
            "task.duplicate_suppressed",
            {"existing_task_id": str(task.id), "status": task.status.value, "text": text},
            task_id=task.id,
            component="task_orchestrator",
        )
        await self._acknowledge(self._duplicate_acknowledgement(task))
        await self.publish()
        return task

    def _find_duplicate_task(self, text: str) -> RobinTask | None:
        normalized = self._normalize_task_text(text)
        if not normalized:
            return None
        duplicate_statuses = {
            TaskStatus.ACCEPTED,
            TaskStatus.QUEUED,
            TaskStatus.EXECUTING,
            TaskStatus.VALIDATING,
            TaskStatus.READY_TO_PRESENT,
            TaskStatus.PRESENTING,
        }
        for task in sorted(self.tasks, key=lambda item: item.updated_at, reverse=True):
            if task.status in duplicate_statuses and self._normalize_task_text(task.request_text) == normalized:
                return task
        return None

    def _normalize_task_text(self, text: str) -> str:
        return " ".join(text.casefold().split())

    def _duplicate_acknowledgement(self, task: RobinTask) -> str:
        if task.status == TaskStatus.READY_TO_PRESENT:
            return f"I already have {task.title} ready."
        if task.status == TaskStatus.PRESENTING:
            return f"I’m already presenting {task.title}."
        return f"I’m already working on {task.title}."

    def _task_failure_acknowledgement(self, task: RobinTask) -> str:
        detail = (task.error or "an unexpected error").strip()
        if len(detail) > 140:
            detail = detail[:137].rstrip() + "..."
        return f"I could not complete {task.title}. The blocker is: {detail}."

    def _schedule_task(self, task: RobinTask) -> None:
        handle = asyncio.create_task(self._execute_task(task.id))
        self._task_handles[task.id] = handle

    async def _execute_task(self, task_id: UUID) -> None:
        task = self._find_task(task_id)
        try:
            if self.task_slots.locked():
                task.status = TaskStatus.QUEUED
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event("task.queued", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
                await self.publish()
            async with self.task_slots:
                task.status = TaskStatus.EXECUTING
                task.started_at = task.started_at or now_utc()
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event("task.started", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
                await self.publish()
                records = self.workspace.index()
                self.files = records
                self.store.replace_all("file", records)
                if self.task_agent.client:
                    async def report_agent_progress(
                        event_type: str, payload: dict
                    ) -> None:
                        await self.emit_event(
                            event_type,
                            payload,
                            task_id=task.id,
                            component="general_agent",
                        )
                        await self.publish()

                    await self.emit_event(
                        "agent.started",
                        {"model": self.settings.model.primary, "file_count": len(records)},
                        task_id=task.id,
                        component="general_agent",
                    )
                    result = await self.task_agent.execute(
                        task,
                        records,
                        meeting_context=self._meeting_context(task.meeting_id),
                        memory_context=self._memory_context(task.meeting_id),
                        progress=report_agent_progress,
                    )
                    artifacts, _deck, validation = await asyncio.to_thread(
                        self.artifacts_worker.write_agent_result, task, result
                    )
                else:
                    # Simulator/offline tests retain the deterministic fixture worker. Real partner
                    # mode always has OPENAI_API_KEY and therefore uses the general tool loop above.
                    files = [
                        self.workspace.resolve(record.relative_path)
                        for record in self.workspace.search(task.requested_outcome, records)
                    ]
                    artifacts, _chart, _deck, validation = await asyncio.to_thread(
                        self.artifacts_worker.run_finance_analysis, task, files
                    )
                task.status = TaskStatus.VALIDATING
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event("task.validating", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
                await self.publish()
                for artifact in artifacts:
                    self.artifacts.append(artifact)
                    self.store.upsert("artifact", artifact)
                    await self.emit_event("artifact.created", artifact.model_dump(mode="json"), task_id=task.id, component="artifact_worker")
                if not validation.ok:
                    failed_checks = [check.name for check in validation.checks if not check.ok]
                    task.status = TaskStatus.FAILED
                    task.error = f"Validation failed: {', '.join(failed_checks)}"
                    task.updated_at = now_utc()
                    self.store.upsert("task", task)
                    await self.emit_event("task.failed", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
                    await self._acknowledge("I found a validation issue in the analysis, so I will not present it yet.")
                    await self.publish()
                    return
                task.status = TaskStatus.READY_TO_PRESENT
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event("task.completed", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
                await self.publish()
                await self._safe_acknowledge("The analysis and slides are ready.")
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.updated_at = now_utc()
            self.store.upsert("task", task)
            await self.emit_event("task.cancelled", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self.publish()
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.updated_at = now_utc()
            self.store.upsert("task", task)
            await self.emit_event("task.failed", task.model_dump(mode="json"), task_id=task.id, component="task_orchestrator")
            await self.publish()
            await self._safe_acknowledge(self._task_failure_acknowledgement(task))

    def _meeting_context(self, meeting_id: UUID | None = None) -> list[TranscriptSegment]:
        target = meeting_id or self.meeting_id
        return [segment for segment in self.transcript if segment.meeting_id == target][-30:]

    def _memory_context(self, meeting_id: UUID | None = None) -> list[MeetingMemoryItem]:
        target = meeting_id or self.meeting_id
        return [item for item in self.meeting_memory if item.meeting_id == target][-60:]

    def _schedule_acknowledgement(self, text: str) -> None:
        handle = asyncio.create_task(self._safe_acknowledge(text))
        self._speech_handles.add(handle)
        handle.add_done_callback(self._speech_handles.discard)

    async def _safe_acknowledge(self, text: str) -> None:
        try:
            await self._acknowledge(text)
        except Exception as exc:
            await self.emit_event(
                "speech.failed",
                {"text": text[:120], "error": str(exc)},
                component="speech",
            )
            await self.publish()

    async def _acknowledge(self, text: str) -> SpeechRecord:
        async with self._speech_lock:
            previous = self.meeting_state
            await self._wait_for_speech_floor(text)
            self.meeting_state = MeetingState.SPEAKING
            await self.publish()
            try:
                await self.meet.unmute()
                await self._emit_meet_recovery_events()
                return await self._speak_and_record(text)
            finally:
                self._last_spoken_at_ms = int(time.time() * 1000)
                await self.meet.mute()
                await self._emit_meet_recovery_events()
                self.meeting_state = previous if previous != MeetingState.IDLE else self.meet.state
                await self.publish()

    async def _speak_and_record(self, text: str) -> SpeechRecord:
        speech = await self.audio.speak(text)
        if speech.path:
            speech.path = (
                Path(self.settings.workspace.sessions_dir) / "speech" / speech.path
            ).as_posix()
        self.speech.append(speech)
        self.store.upsert("speech", speech)
        await self.emit_event(
            "speech.interrupted" if speech.interrupted else "speech.completed",
            speech.model_dump(mode="json"),
            component="speech",
        )
        return speech

    async def _wait_for_speech_floor(self, text: str) -> None:
        wait_ms = self._speech_floor_wait_ms()
        if wait_ms <= 0:
            return
        await self.emit_event(
            "speech.floor_wait",
            {"wait_ms": wait_ms, "text": text[:120]},
            component="speech",
        )
        await asyncio.sleep(wait_ms / 1000)

    def _speech_floor_wait_ms(self) -> int:
        if not self.transcript:
            return 0
        last = self.transcript[-1]
        if last.speaker_name and last.speaker_name.strip().lower() == "robin":
            return 0
        now_ms = int(time.time() * 1000)
        elapsed_ms = max(0, now_ms - last.ended_at_ms)
        required_ms = max(self.settings.runtime.speech_floor_silence_ms, 0)
        max_wait_ms = max(self.settings.runtime.speech_floor_max_wait_ms, 0)
        return min(max(required_ms - elapsed_ms, 0), max_wait_ms)

    async def _emit_meet_recovery_events(self, task_id: UUID | None = None) -> None:
        events = self.meet.recovery_events or []
        for event in events[self._meet_recovery_event_count :]:
            await self.emit_event(
                "browser.recovery",
                {
                    "action": event.action,
                    "attempt": event.attempt,
                    "recovered": event.recovered,
                    "error": event.error,
                    "page_url": event.page_url,
                    "screenshot_path": event.screenshot_path,
                },
                task_id=task_id,
                component="browser",
            )
        self._meet_recovery_event_count = len(events)

    def _status_summary(self) -> str:
        active = self._active_tasks()
        if not active:
            return "I do not have an active task right now."
        return "; ".join(f"{task.title}: {task.status.value.lower().replace('_', ' ')}" for task in active[:2])

    def _find_task(self, task_id: UUID) -> RobinTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"Unknown task: {task_id}")

    def _active_tasks(self) -> list[RobinTask]:
        return sorted(
            (task for task in self.tasks if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}),
            key=lambda task: task.updated_at,
            reverse=True,
        )

    def artifact_path(self, relative_path: str) -> Path:
        return self.workspace.resolve(relative_path)

    def _latest_artifact(self, task_id: UUID, artifact_type: str) -> Artifact | None:
        artifacts = [artifact for artifact in self.artifacts if artifact.task_id == task_id and artifact.type == artifact_type]
        if not artifacts:
            return None
        return max(artifacts, key=lambda artifact: (artifact.revision, artifact.created_at))

    async def emit_event(
        self,
        event_type: str,
        payload: dict,
        task_id: UUID | None = None,
        component: str = "runtime",
    ) -> EventEnvelope:
        event = EventEnvelope(
            type=event_type,
            meeting_id=self.meeting_id,
            task_id=task_id,
            component=component,
            payload=payload,
        )
        event.id = self.store.append_event_body(event.type, event.model_dump(mode="json"))
        self._write_trace(event)
        stale: list[asyncio.Queue[EventEnvelope]] = []
        for queue in self._event_subscribers:
            if queue.full():
                stale.append(queue)
                continue
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        for queue in stale:
            self._event_subscribers.discard(queue)
        return event

    def recent_events(self, limit: int = 100) -> list[EventEnvelope]:
        events = []
        for row in self.store.list_events(limit):
            body = dict(row["body"])
            if "type" in body:
                body["id"] = row["id"]
                events.append(EventEnvelope.model_validate(body))
            else:
                events.append(
                    EventEnvelope(
                        id=row["id"],
                        type=row["kind"],
                        timestamp=row["created_at"],
                        meeting_id=self.meeting_id,
                        payload=body,
                    )
                )
        return events

    def metrics(self) -> RuntimeMetrics:
        events = self.recent_events(500)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_bytes = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
        workspace_bytes = sum(
            path.stat().st_size
            for path in self.workspace.root.rglob("*")
            if path.is_file()
        )
        active_statuses = {
            TaskStatus.AWAITING_CLARIFICATION,
            TaskStatus.ACCEPTED,
            TaskStatus.QUEUED,
            TaskStatus.EXECUTING,
            TaskStatus.VALIDATING,
            TaskStatus.READY_TO_PRESENT,
            TaskStatus.PRESENTING,
        }
        return RuntimeMetrics(
            event_count=len(events),
            transcript_count=len(self.transcript),
            task_count=len(self.tasks),
            completed_task_count=sum(1 for task in self.tasks if task.status == TaskStatus.COMPLETED),
            failed_task_count=sum(1 for task in self.tasks if task.status == TaskStatus.FAILED),
            active_task_count=sum(1 for task in self.tasks if task.status in active_statuses),
            artifact_count=len(self.artifacts),
            speech_count=len(self.speech),
            presentation_count=len(self.presentations),
            audio_capture_event_count=sum(1 for event in events if event.type.startswith("audio.capture")),
            direct_request_count=sum(1 for event in events if event.type == "task.created"),
            agent_tool_call_count=sum(
                1 for event in events if event.type == "agent.tool.completed"
            ),
            recovery_event_count=sum(
                1 for event in events if ".recovery." in event.type
            ),
            realtime_failure_count=sum(
                1 for event in events if event.type == "audio.realtime.failed"
            ),
            uptime_seconds=round(time.monotonic() - self._started_monotonic, 1),
            process_cpu_seconds=round(usage.ru_utime + usage.ru_stime, 2),
            peak_rss_mb=round(rss_bytes / 1024 / 1024, 1),
            workspace_disk_mb=round(workspace_bytes / 1024 / 1024, 1),
        )

    def _write_trace(self, event: EventEnvelope) -> None:
        trace_dir = self.workspace.sessions / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        name = event.task_id or event.meeting_id or "runtime"
        path = trace_dir / f"{name}.jsonl"
        with path.open("a") as handle:
            handle.write(event.model_dump_json() + "\n")

    async def publish(self) -> RuntimeSnapshot:
        snapshot = self.snapshot()
        stale: list[asyncio.Queue[RuntimeSnapshot]] = []
        for queue in self._subscribers:
            if queue.full():
                stale.append(queue)
                continue
            with suppress(asyncio.QueueFull):
                queue.put_nowait(snapshot)
        for queue in stale:
            self._subscribers.discard(queue)
        return snapshot

    async def subscribe(self):
        queue: asyncio.Queue[RuntimeSnapshot] = asyncio.Queue(maxsize=16)
        self._subscribers.add(queue)
        await queue.put(self.snapshot())
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def subscribe_events(self):
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=64)
        self._event_subscribers.add(queue)
        for event in self.recent_events(20):
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        try:
            while True:
                yield await queue.get()
        finally:
            self._event_subscribers.discard(queue)
