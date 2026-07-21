from __future__ import annotations

import asyncio
import json
import re
import resource
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .artifacts import ArtifactWorker
from .agent import GeneralTaskAgent
from .audio.bridge import AudioBridge, PreparedSpeech
from .audio.prefetch import NarrationItem, NarrationPrefetchCoordinator, PrefetchResult
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
    PresentationHandoff,
    PresentationHandoffState,
    RobinTask,
    RuntimeSnapshot,
    RuntimeMetrics,
    RuntimeState,
    PresentationSession,
    RehearsalConfirmationRequest,
    RehearsalEvidence,
    SpeechRecord,
    TaskStatus,
    TaskOutcomeState,
    TranscriptSegment,
    ValidationReport,
    WorkspaceSnapshot,
    now_utc,
)
from .security import redact_text, redact_value
from .workspace import Workspace


class RobinRuntime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self._started_monotonic = time.monotonic()
        self._runtime_instance_id = uuid4()
        self.workspace = Workspace(self.settings.workspace)
        self.store = Store(self.settings.database.path)
        self.intent = IntentClassifier(self.settings)
        self.memory_manager = MeetingMemoryManager(self.settings)
        self.artifacts_worker = ArtifactWorker(self.workspace, self.settings.presentation.base_url)
        self.browser = BrowserController(self.settings.browser)
        self.task_agent = GeneralTaskAgent(self.settings, self.workspace)
        self.browser_operator = ControlledBrowserAgent(self.settings, self.browser)
        self.meet = GoogleMeetAdapter(
            self.browser,
            self.settings.browser,
            microphone_device_name=self.settings.audio.output_device_name,
        )
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
        handoffs = self.store.list("presentation_handoff", PresentationHandoff)
        self.presentation_handoff = handoffs[0] if handoffs else PresentationHandoff()
        self.health: list[HealthItem] = []
        self.task_slots = asyncio.Semaphore(self.settings.runtime.max_concurrent_tasks)
        self._task_handles: dict[UUID, asyncio.Task] = {}
        self._subscribers: set[asyncio.Queue[RuntimeSnapshot]] = set()
        self._event_subscribers: set[asyncio.Queue[EventEnvelope]] = set()
        self._listen_handle: asyncio.Task | None = None
        self._caption_watch_handle: asyncio.Task | None = None
        self._join_lock = asyncio.Lock()
        self._speech_lock = asyncio.Lock()
        self._capture_lock = asyncio.Lock()
        self._handoff_lock = asyncio.Lock()
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
        self._realtime_barge_in_items: set[str] = set()
        self._seen_caption_turns: set[tuple[str, str]] = set()
        self._active_spoken_text: str | None = None
        self._meet_recovery_event_count = 0
        self._meet_speech_route_event_count = 0
        self.refresh_health()

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            runtime_state=self.runtime_state,
            meeting_state=self.meeting_state,
            meeting_url=self.meeting_url,
            meeting_id=self.meeting_id,
            listening=self.meeting_state
            in {MeetingState.LISTENING, MeetingState.SPEAKING, MeetingState.PRESENTING},
            presenting=self.meet.presenting,
            capture_loop_running=self._listen_handle is not None and not self._listen_handle.done(),
            calendar_auto_join_running=self._calendar_handle is not None
            and not self._calendar_handle.done(),
            health=self.health,
            transcript=self.transcript[-100:],
            meeting_memory=[
                item for item in self.meeting_memory if item.meeting_id == self.meeting_id
            ][-100:],
            tasks=sorted(self.tasks, key=lambda task: task.created_at),
            artifacts=sorted(self.artifacts, key=lambda artifact: artifact.created_at),
            speech=sorted(self.speech, key=lambda item: item.started_at)[-25:],
            presentations=sorted(self.presentations.values(), key=lambda item: item.updated_at),
            presentation_handoff=self.presentation_handoff,
        )

    def refresh_health(self) -> None:
        bridge_mode = self.settings.audio.bridge_mode
        self.health = [
            HealthItem(
                name="workspace", ok=self.workspace.root.exists(), detail=str(self.workspace.root)
            ),
            HealthItem(
                name="audio_capture",
                ok=self.audio.capture_healthy,
                detail=f"{self.settings.audio.mode}/{bridge_mode} bridge healthy",
            ),
            HealthItem(
                name="virtual_microphone",
                ok=self.audio.virtual_mic_healthy,
                detail=self.settings.audio.output_device_name,
            ),
            HealthItem(
                name="browser_automation",
                ok=True,
                detail=f"{self.settings.browser.automation_mode} adapter ready",
            ),
            HealthItem(
                name="openai",
                ok=bool(self.settings.openai_api_key),
                detail="configured" if self.settings.openai_api_key else "local fallback",
            ),
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
            admitted_states = {
                MeetingState.JOINED,
                MeetingState.LISTENING,
                MeetingState.SPEAKING,
                MeetingState.PRESENTING,
            }
            joining_states = {
                MeetingState.NAVIGATING,
                MeetingState.PREJOIN,
                MeetingState.REQUESTING_ADMISSION,
            }
            if self.meeting_url == meeting_url and self.meeting_state in admitted_states:
                await self.emit_event(
                    "meeting.join.duplicate_suppressed",
                    {"meeting_url": meeting_url},
                    component="meeting",
                )
                if start_listening:
                    await self.start_listening_loop()
                return await self.publish()
            if self.meeting_url == meeting_url and self.meeting_state in joining_states:
                raise RuntimeError("Robin is still waiting for admission to this meeting.")
            if self.meeting_url and self.meeting_state in admitted_states | joining_states:
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
                await self._join_meet_with_progress()
                self.meeting_state = self.meet.state
                await self._emit_meet_recovery_events()
                self.runtime_state = RuntimeState.IN_MEETING
                await self.emit_event(
                    "meeting.joined",
                    {
                        "meeting_url": meeting_url,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "microphone_device": self.meet.selected_microphone_device,
                    },
                    component="meeting",
                )
                await self.publish()
                if start_listening:
                    return await self.start_listening_loop()
                return self.snapshot()
            except Exception as exc:
                with suppress(Exception):
                    await self.meet.leave()
                await self._emit_meet_recovery_events()
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

    async def _join_meet_with_progress(self) -> None:
        handle = asyncio.create_task(self.meet.join())
        total_timeout = (
            self.settings.browser.prejoin_timeout_ms
            + self.settings.browser.admission_timeout_ms
            + 10_000
        ) / 1000
        deadline = time.monotonic() + total_timeout
        last_state = self.meeting_state
        try:
            while True:
                done, _pending = await asyncio.wait({handle}, timeout=0.2)
                if self.meet.state != last_state:
                    last_state = self.meet.state
                    self.meeting_state = last_state
                    await self.emit_event(
                        "meeting.state.changed",
                        {"state": last_state.value},
                        component="meeting",
                    )
                    await self.publish()
                if done:
                    await handle
                    return
                if time.monotonic() >= deadline:
                    handle.cancel()
                    with suppress(asyncio.CancelledError):
                        await handle
                    raise TimeoutError(
                        f"Meet join exceeded the bounded {total_timeout:.1f}s deadline."
                    )
        except asyncio.CancelledError:
            handle.cancel()
            with suppress(asyncio.CancelledError):
                await handle
            raise

    def calendar_snapshot(self) -> CalendarSnapshot:
        snapshot = calendar_snapshot(self.settings.calendar)
        snapshot.auto_join_running = (
            self._calendar_handle is not None and not self._calendar_handle.done()
        )
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
        await self.emit_event(
            "calendar.event.selected", event.model_dump(mode="json"), component="calendar"
        )
        self._calendar_joined_event_ids.add(event.id)
        self._calendar_active_event_id = event.id
        self._calendar_active_event_end = event.end
        return await self.join_meeting(event.meeting_url)

    async def set_calendar_auto_join(
        self, enabled: bool, interval_seconds: float = 15.0
    ) -> RuntimeSnapshot:
        self.settings.calendar.auto_join = enabled
        if enabled:
            if not self.settings.calendar.enabled:
                raise ValueError("Calendar discovery is disabled.")
            if self._calendar_handle and not self._calendar_handle.done():
                return await self.publish()
            self._calendar_handle = asyncio.create_task(
                self._calendar_loop(max(interval_seconds, 1.0))
            )
            await self.emit_event(
                "calendar.auto_join.enabled",
                {"interval_seconds": max(interval_seconds, 1.0)},
                component="calendar",
            )
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
        snapshot.auto_join_running = (
            self._calendar_handle is not None and not self._calendar_handle.done()
        )
        if snapshot.error:
            await self.emit_event(
                "calendar.poll.failed", {"error": snapshot.error}, component="calendar"
            )
            return await self.publish()
        current = now or datetime.now(timezone.utc)
        if (
            self._calendar_active_event_id
            and self._calendar_active_event_end
            and self._calendar_active_event_end <= current
        ):
            await self.emit_event(
                "calendar.event.ended",
                {"event_id": self._calendar_active_event_id},
                component="calendar",
            )
            self._calendar_active_event_id = None
            self._calendar_active_event_end = None
            if self.meeting_url:
                return await self.leave_meeting()
        if not self.settings.calendar.auto_join:
            return await self.publish()
        if snapshot.conflicts:
            await self.emit_event(
                "calendar.auto_join.skipped",
                {"reason": "conflict", "conflicts": snapshot.conflicts},
                component="calendar",
            )
            return await self.publish()
        if self.meeting_state not in {MeetingState.IDLE, MeetingState.ENDED} and self.meeting_url:
            await self.emit_event(
                "calendar.auto_join.skipped",
                {"reason": "already_in_meeting", "meeting_url": self.meeting_url},
                component="calendar",
            )
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
        await self.emit_event(
            "calendar.auto_join.started", event.model_dump(mode="json"), component="calendar"
        )
        return await self._join_calendar_event(event)

    async def _calendar_loop(self, interval_seconds: float) -> None:
        try:
            while True:
                await self.poll_calendar_once()
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.emit_event(
                "calendar.auto_join.failed", {"error": str(exc)}, component="calendar"
            )
            await self.publish()

    async def leave_meeting(self) -> RuntimeSnapshot:
        self.meeting_state = MeetingState.LEAVING
        if self._listen_handle and not self._listen_handle.done():
            await self.stop_listening_loop()
        if self.meet.presenting or any(state.active for state in self.presentations.values()):
            await self.stop_presenting()
        async with self._handoff_lock:
            await self._clear_handoff_for_task(None, "meeting_left")
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
        cleanup_errors: list[str] = []
        try:
            await self.audio.interrupt_speech()
        except Exception as exc:
            cleanup_errors.append(f"interrupt_speech: {exc}")
        await self.stop_listening_loop()
        task_handles = list(self._task_handles.values())
        speech_handles = list(self._speech_handles)
        for handle in [*task_handles, *speech_handles]:
            handle.cancel()
        if task_handles or speech_handles:
            await asyncio.gather(*task_handles, *speech_handles, return_exceptions=True)
        self._task_handles.clear()
        self._speech_handles.clear()
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
        if self.meet.presenting or any(state.active for state in self.presentations.values()):
            try:
                await self.meet.stop_presenting()
            except Exception as exc:
                cleanup_errors.append(f"stop_presenting: {exc}")
        for state in self.presentations.values():
            state.active = False
            state.updated_at = now_utc()
        for task in self.tasks:
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}:
                task.status = TaskStatus.CANCELLED
                task.presentation_ready_at = None
                task.outcome_state = TaskOutcomeState.CANCELLED
                task.outcome_detail = "Cancelled by emergency stop."
                task.updated_at = now_utc()
                self.store.upsert("task", task)
        try:
            async with self._handoff_lock:
                await self._clear_handoff_for_task(None, "emergency_stop")
        except Exception as exc:
            cleanup_errors.append(f"clear_handoff: {exc}")
        try:
            await self.audio.stop()
        except Exception as exc:
            cleanup_errors.append(f"audio_stop: {exc}")
        try:
            await self.meet.leave()
        except Exception as exc:
            cleanup_errors.append(f"meeting_leave: {exc}")
        self.meet.presenting = False
        self.meet.muted = True
        self.meeting_state = MeetingState.ENDED
        self.runtime_state = RuntimeState.READY
        self.refresh_health()
        await self.emit_event(
            "runtime.emergency_stop",
            {"cleanup_errors": cleanup_errors},
            component="runtime",
        )
        return await self.publish()

    async def record_rehearsal_confirmation(
        self, request: RehearsalConfirmationRequest
    ) -> RehearsalEvidence:
        task = self._find_task(request.task_id)
        if task.meeting_id != self.meeting_id:
            raise ValueError("The task does not belong to the current meeting")
        evidence_dir = self.settings.workspace.root / "rehearsals"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        previous = self._rehearsal_evidence(evidence_dir)
        previous_passed = [item for item in previous if item.passed]
        last_passed = previous_passed[-1] if previous_passed else None
        events = [event for event in self.recent_events(500) if event.meeting_id == self.meeting_id]
        task_events = [event for event in events if event.task_id == task.id]
        transcript = [
            segment for segment in self.transcript if segment.meeting_id == self.meeting_id
        ]
        validation = self._latest_artifact(task.id, "validation_json")
        validation_ok = False
        if validation is not None:
            try:
                validation_ok = ValidationReport.model_validate_json(
                    self.workspace.resolve(validation.path).read_text(encoding="utf-8")
                ).ok
            except Exception:
                validation_ok = False
        narration_count = sum(event.type == "presentation.narration" for event in task_events)
        try:
            slide_count = self._deck_slide_count(task.id)
        except Exception:
            slide_count = 0
        outbound_audio = any(
            speech.error is None
            and speech.completed_at is not None
            and "blackhole" in (speech.playback_device or "").casefold()
            for speech in self.speech
        )
        inbound_audio = any(segment.source in {"audio_stt", "merged"} for segment in transcript)
        live_interaction = task.revision > 1 or any(
            event.type == "conversation.addressed" for event in events
        )
        normalized_task = " ".join(task.requested_outcome.casefold().split())
        prior_task = None
        if last_passed:
            try:
                prior_task = str(
                    json.loads(
                        (self.settings.workspace.root / last_passed.evidence_path).read_text(
                            encoding="utf-8"
                        )
                    ).get("task_fingerprint", "")
                )
            except Exception:
                prior_task = None
        automated_checks = {
            "fresh_runtime": last_passed is None
            or last_passed.runtime_instance_id != self._runtime_instance_id,
            "different_task": not prior_task or prior_task != normalized_task,
            "inbound_audio_transcribed": inbound_audio,
            "outbound_audio_routed_to_blackhole": outbound_audio,
            "task_verified": task.outcome_state == TaskOutcomeState.VERIFIED,
            "grounding_validation_passed": validation_ok,
            "presentation_started": any(
                event.type == "presentation.started" for event in task_events
            ),
            "presentation_completed": any(
                event.type == "presentation.completed" for event in task_events
            ),
            "every_slide_narrated": slide_count > 0 and narration_count >= slide_count,
            "live_interaction_observed": live_interaction,
            "meeting_left": any(event.type == "meeting.left" for event in events),
            "state_restored": self.runtime_state == RuntimeState.READY
            and self.meeting_state in {MeetingState.ENDED, MeetingState.IDLE}
            and not self.snapshot().capture_loop_running
            and not self.snapshot().presenting,
        }
        confirmations = {
            "robin_heard_participant": request.robin_heard_participant,
            "correct_understanding": request.correct_understanding,
            "grounded_output": request.grounded_output,
            "correct_shared_surface": request.correct_shared_surface,
            "audible_narration": request.audible_narration,
            "live_qa_or_revision": request.live_qa_or_revision,
            "graceful_leave": request.graceful_leave,
        }
        passed = all(automated_checks.values()) and all(confirmations.values())
        prior_streak = previous[-1].consecutive_passes if previous and previous[-1].passed else 0
        evidence = RehearsalEvidence(
            runtime_instance_id=self._runtime_instance_id,
            meeting_id=self.meeting_id,
            task_id=task.id,
            run_number=len(previous) + 1,
            consecutive_passes=prior_streak + 1 if passed else 0,
            participant_name=redact_text(request.participant_name.strip()),
            notes=redact_text(request.notes.strip()),
            confirmations=confirmations,
            automated_checks=automated_checks,
            passed=passed,
            commit=self._git_commit(),
        )
        filename = (
            f"rehearsal-{evidence.created_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{str(evidence.id)[:8]}.json"
        )
        relative_path = (Path("rehearsals") / filename).as_posix()
        evidence.evidence_path = relative_path
        body = evidence.model_dump(mode="json")
        body["task_fingerprint"] = normalized_task
        (self.settings.workspace.root / relative_path).write_text(
            json.dumps(body, indent=2) + "\n",
            encoding="utf-8",
        )
        await self.emit_event(
            "rehearsal.confirmed",
            {
                "evidence_path": relative_path,
                "passed": passed,
                "consecutive_passes": evidence.consecutive_passes,
                "failed_checks": [
                    name for name, ok in {**automated_checks, **confirmations}.items() if not ok
                ],
            },
            task_id=task.id,
            component="rehearsal",
        )
        return evidence

    @staticmethod
    def _git_commit() -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        value = result.stdout.strip()
        return value or None

    @staticmethod
    def _rehearsal_evidence(evidence_dir: Path) -> list[RehearsalEvidence]:
        evidence: list[RehearsalEvidence] = []
        for path in sorted(evidence_dir.glob("rehearsal-*.json")):
            try:
                evidence.append(RehearsalEvidence.model_validate_json(path.read_text()))
            except Exception:
                continue
        return sorted(evidence, key=lambda item: item.created_at)

    async def ingest_transcript(
        self,
        text: str,
        speaker_name: str | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        source: str = "simulator",
    ) -> RuntimeSnapshot:
        now_ms = int(time.time() * 1000)
        text = redact_text(text)
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

    async def transcribe_audio_file(
        self, relative_path: str, speaker_name: str | None = None
    ) -> RuntimeSnapshot:
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
            transcript = (
                await self.audio.transcribe_file(self.workspace.resolve(result["path"]))
            ).strip()
        event_type = (
            "audio.input.test.passed" if signal and transcript else "audio.input.test.quiet"
        )
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
            await self.emit_event(
                "audio.transcript.empty", {"path": result["path"]}, component="audio"
            )
            return await self.publish()
        if normalized == self._last_audio_text and now_ms - self._last_audio_text_at_ms < 10_000:
            await self.emit_event(
                "audio.transcript.duplicate_suppressed", {"text": text}, component="audio"
            )
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
            self._ensure_caption_watch()
            return self.snapshot()
        self._listen_handle = asyncio.create_task(
            self._realtime_listening_loop(
                bundle_id=bundle_id or self.settings.audio.capture_bundle_id,
                max_iterations=max_iterations,
            )
            if self.settings.audio.realtime_transcription_enabled and self.settings.openai_api_key
            else self._listening_loop(
                bundle_id=bundle_id or self.settings.audio.capture_bundle_id,
                duration_ms=duration_ms
                if duration_ms is not None
                else self.settings.audio.capture_sample_duration_ms,
                interval_ms=interval_ms
                if interval_ms is not None
                else self.settings.audio.capture_loop_interval_ms,
                max_iterations=max_iterations,
            )
        )
        self._seen_caption_turns.clear()
        self._ensure_caption_watch()
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
        if self._caption_watch_handle and not self._caption_watch_handle.done():
            self._caption_watch_handle.cancel()
            with suppress(asyncio.CancelledError):
                await self._caption_watch_handle
        self._caption_watch_handle = None
        if self._listen_handle and not self._listen_handle.done():
            self._listen_handle.cancel()
            with suppress(asyncio.CancelledError):
                await self._listen_handle
        self._listen_handle = None
        await self.emit_event("audio.listen.stopped", {}, component="audio")
        return await self.publish()

    def _ensure_caption_watch(self) -> None:
        if self._caption_watch_handle and not self._caption_watch_handle.done():
            return
        self._caption_watch_handle = asyncio.create_task(self._caption_invitation_loop())

    async def _caption_invitation_loop(self) -> None:
        """Use visible Meet captions when native audio misses a floor invitation."""

        failures = 0
        while True:
            try:
                await self._ingest_caption_invitation_once()
                failures = 0
                await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                if failures == 1 or failures % 20 == 0:
                    await self.emit_event(
                        "audio.caption.watch.failed",
                        {"error": str(exc), "consecutive_failures": failures},
                        component="audio",
                    )
                    await self.publish()
                await asyncio.sleep(1)

    async def _ingest_caption_invitation_once(self) -> bool:
        pending = self._pending_presentation_context()
        if not pending or pending.get("state") != "WAITING_FOR_INVITATION":
            return False
        captions = await asyncio.wait_for(self.meet.recent_captions(), timeout=1.0)
        for caption in captions:
            speaker = " ".join(caption.speaker_name.split())
            text = " ".join(caption.text.split())
            key = (speaker.casefold(), text.casefold())
            if not text or key in self._seen_caption_turns:
                continue
            self._seen_caption_turns.add(key)
            intent = await self.intent.classify(
                text,
                self._active_tasks(),
                pending_presentation=pending,
            )
            if intent.classification != "presentation_invitation":
                continue
            await self.emit_event(
                "audio.caption.invitation_fallback",
                {"speaker_name": speaker, "text": text},
                task_id=self.presentation_handoff.task_id,
                component="audio",
            )
            await self.ingest_transcript(
                text,
                speaker_name=speaker or "Meet participant",
                source="meet_caption",
            )
            return True
        return False

    async def run_browser_operator(
        self,
        request: str,
        page_name: str = "meet",
        approval_token: str | None = None,
    ) -> BrowserOperatorResult:
        request = redact_text(request)
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

    async def _listening_loop(
        self, bundle_id: str, duration_ms: int, interval_ms: int, max_iterations: int | None
    ) -> None:
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
            if item_id not in self._realtime_barge_in_items and self._should_accept_barge_in(text):
                interrupted = await self.audio.interrupt_speech()
                if interrupted:
                    self._realtime_barge_in_items.add(item_id)
                await self.emit_event(
                    "audio.barge_in.accepted",
                    {
                        "item_id": item_id,
                        "text": text,
                        "playback_interrupted": interrupted,
                    },
                    component="audio",
                )
            await self.publish()

        async def on_final(item_id: str, transcript: str) -> None:
            nonlocal completed_turns
            self._realtime_partials.pop(item_id, None)
            self._realtime_barge_in_items.discard(item_id)
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
            speaker_name, source = await self._caption_attribution(text)
            await self.ingest_transcript(
                text,
                speaker_name=speaker_name,
                started_at_ms=now_ms,
                ended_at_ms=now_ms,
                source=source,
            )

        async def on_speech_started() -> None:
            speaking = self.meeting_state == MeetingState.SPEAKING
            await self.emit_event(
                "audio.speech.detected",
                {
                    "while_robin_speaking": speaking,
                    "playback_interrupted": False,
                    "wake_word_required": self.settings.audio.wake_word,
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

    async def _caption_attribution(self, transcript: str) -> tuple[str, str]:
        try:
            captions = await asyncio.wait_for(self.meet.recent_captions(), timeout=0.75)
        except Exception:
            return "Meeting audio", "audio_stt"
        normalized = " ".join(transcript.casefold().split())
        transcript_tokens = set(normalized.split())
        best = None
        best_score = 0.0
        for caption in captions:
            caption_text = " ".join(caption.text.casefold().split())
            caption_tokens = set(caption_text.split())
            overlap = len(transcript_tokens & caption_tokens) / max(
                min(len(transcript_tokens), len(caption_tokens)), 1
            )
            similarity = SequenceMatcher(None, normalized, caption_text).ratio()
            score = max(overlap, similarity)
            if score > best_score:
                best = caption
                best_score = score
        if best is None or best_score < 0.62:
            return "Meeting audio", "audio_stt"
        await self.emit_event(
            "audio.speaker.attributed",
            {
                "speaker_name": best.speaker_name,
                "caption_text": best.text,
                "match_score": round(best_score, 3),
            },
            component="audio",
        )
        return best.speaker_name, "merged"

    def _is_recent_robin_echo(self, text: str) -> bool:
        spoken = self._active_spoken_text or self.audio.last_spoken_text
        if not spoken:
            return False
        if (
            self._active_spoken_text is None
            and int(time.time() * 1000) - self._last_spoken_at_ms > 15_000
        ):
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

    def _contains_wake_word(self, text: str) -> bool:
        wake_word = " ".join(self.settings.audio.wake_word.casefold().split())
        if not wake_word:
            return True
        return bool(re.search(rf"(?<!\w){re.escape(wake_word)}(?!\w)", text.casefold()))

    def _strip_wake_word(self, text: str) -> str:
        wake_word = " ".join(self.settings.audio.wake_word.casefold().split())
        if not wake_word:
            return text.strip()
        cleaned = re.sub(
            rf"(?<!\w){re.escape(wake_word)}(?!\w)",
            " ",
            text,
            flags=re.I,
        )
        return " ".join(cleaned.strip(" ,.:;!?-").split())

    def _should_accept_barge_in(self, text: str) -> bool:
        return (
            (
                self.meeting_state == MeetingState.SPEAKING
                or (
                    self.meeting_state == MeetingState.PRESENTING
                    and self._active_spoken_text is not None
                )
            )
            and self._contains_wake_word(text)
            and not self._is_recent_robin_echo(text)
        )

    def _looks_like_ambiguous_presentation_invitation(self, text: str) -> bool:
        if (
            self.settings.presentation.require_wake_word_for_invitation
            and not self._contains_wake_word(text)
        ):
            return False
        lowered = text.casefold()
        if any(
            phrase in lowered
            for phrase in ("did robin raise", "has robin raised", "robin has a deck")
        ):
            return False
        return any(
            word in lowered
            for word in ("deck", "slides", "present", "share", "hand", "show", "floor")
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
        await self.emit_event(
            "task.created",
            task.model_dump(mode="json"),
            task_id=task.id,
            component="task_orchestrator",
        )
        self._schedule_task(task)
        await self.publish()
        self._schedule_acknowledgement(f"Got it. I’ll work on {task.title}.")
        return task

    async def cancel_task(self, task_id: UUID) -> None:
        task = self._find_task(task_id)
        async with self._handoff_lock:
            await self._clear_handoff_for_task(task.id, "task_cancelled")
        task.status = TaskStatus.CANCELLED
        task.presentation_ready_at = None
        task.updated_at = now_utc()
        task.outcome_state = TaskOutcomeState.CANCELLED
        task.outcome_detail = "Cancelled by a meeting participant or operator."
        handle = self._task_handles.get(task_id)
        if handle:
            handle.cancel()
        self.store.upsert("task", task)
        await self.emit_event(
            "task.cancelled",
            task.model_dump(mode="json"),
            task_id=task.id,
            component="task_orchestrator",
        )
        await self._acknowledge(f"Cancelled {task.title}.")
        await self.publish()

    async def retry_task(self, task_id: UUID) -> RuntimeSnapshot:
        task = self._find_task(task_id)
        active_statuses = {
            TaskStatus.ACCEPTED,
            TaskStatus.QUEUED,
            TaskStatus.EXECUTING,
            TaskStatus.VALIDATING,
            TaskStatus.PRESENTING,
        }
        if task.status in active_statuses:
            raise ValueError(f"Task is already active: {task.status}")
        async with self._handoff_lock:
            await self._clear_handoff_for_task(task.id, "task_retried")
        task.revision += 1
        task.status = TaskStatus.ACCEPTED
        task.error = None
        task.outcome_state = TaskOutcomeState.UNVERIFIED
        task.outcome_detail = None
        task.started_at = None
        task.completed_at = None
        task.presentation_ready_at = None
        task.updated_at = now_utc()
        self.store.upsert("task", task)
        await self.emit_event(
            "task.retry",
            task.model_dump(mode="json"),
            task_id=task.id,
            component="task_orchestrator",
        )
        await self._acknowledge(f"Retrying {task.title}.")
        self._schedule_task(task)
        return await self.publish()

    async def request_presentation_floor(self, task_id: UUID, revision: int) -> RuntimeSnapshot:
        async with self._handoff_lock:
            task = self._find_task(task_id)
            if task.revision != revision or task.status != TaskStatus.READY_TO_PRESENT:
                return await self.publish()
            await self.emit_event(
                "presentation.handoff.queued",
                {"revision": revision},
                task_id=task.id,
                component="presentation",
            )
            await self._advance_presentation_handoff_locked()
            return await self.publish()

    async def lower_presentation_hand(self) -> RuntimeSnapshot:
        async with self._handoff_lock:
            await self._clear_handoff_for_task(None, "operator_lowered")
            return await self.publish()

    async def accept_presentation_invitation(
        self, segment: TranscriptSegment, intent: MeetingIntent
    ) -> bool:
        async with self._handoff_lock:
            handoff = self.presentation_handoff
            if handoff.invitation_segment_id == segment.id:
                return True
            if handoff.state != PresentationHandoffState.WAITING_FOR_INVITATION:
                return False
            if intent.referenced_task_id and intent.referenced_task_id != handoff.task_id:
                await self.emit_event(
                    "presentation.invitation.rejected",
                    {
                        "reason": "wrong_task",
                        "segment_id": str(segment.id),
                        "referenced_task_id": str(intent.referenced_task_id),
                    },
                    task_id=handoff.task_id,
                    component="presentation",
                )
                return True
            task = self._handoff_task()
            if task is None or not self._task_matches_handoff(task):
                await self._reject_stale_invitation(segment, "stale_task_or_revision")
                await self._clear_handoff_for_task(handoff.task_id, "stale_invitation")
                await self._advance_presentation_handoff_locked()
                return True
            start_blocker = self._presentation_start_blocker(task)
            if start_blocker is not None:
                await self._reject_stale_invitation(segment, start_blocker)
                await self._block_handoff(start_blocker.replace("_", " "), task.id)
                return True
            if self._artifact_for_revision(task.id, "deck_json", task.revision) is None:
                await self._reject_stale_invitation(segment, "missing_deck")
                await self._block_handoff("Validated deck artifact is missing.", task.id)
                return True
            handoff.state = PresentationHandoffState.INVITATION_RECEIVED
            handoff.invited_by = segment.speaker_name
            handoff.invitation_segment_id = segment.id
            handoff.updated_at = now_utc()
            self._persist_handoff()
            await self.emit_event(
                "presentation.invitation.detected",
                {
                    "revision": task.revision,
                    "segment_id": str(segment.id),
                    "invited_by": segment.speaker_name,
                },
                task_id=task.id,
                component="presentation",
            )
            if handoff.hand_raised:
                handoff.state = PresentationHandoffState.LOWERING_HAND
                handoff.updated_at = now_utc()
                self._persist_handoff()
                try:
                    await self.meet.lower_hand()
                    handoff.hand_raised = False
                    await self.emit_event(
                        "meeting.hand.lowered",
                        {"revision": task.revision},
                        task_id=task.id,
                        component="meeting",
                    )
                except Exception as exc:
                    await self.emit_event(
                        "meeting.hand.lower.failed",
                        {"error": str(exc), "revision": task.revision},
                        task_id=task.id,
                        component="meeting",
                    )
                finally:
                    await self._emit_meet_recovery_events(task.id)
            handoff.state = PresentationHandoffState.STARTING_PRESENTATION
            handoff.updated_at = now_utc()
            self._persist_handoff()
            await self.emit_event(
                "presentation.handoff.started",
                {"revision": task.revision},
                task_id=task.id,
                component="presentation",
            )
            try:
                handoff.state = PresentationHandoffState.PRESENTING
                handoff.updated_at = now_utc()
                self._persist_handoff()
                await self._present_task(task.id)
            except Exception as exc:
                await self._block_handoff(str(exc), task.id)
                return True
            await self._clear_handoff_for_task(task.id, "presentation_completed")
            await self._advance_presentation_handoff_locked()
            return True

    async def _advance_presentation_handoff_locked(self) -> None:
        if self.presentation_handoff.state != PresentationHandoffState.IDLE:
            return
        task = self._next_ready_presentation_task()
        if task is None:
            self.presentation_handoff = PresentationHandoff()
            self._persist_handoff()
            return
        handoff = PresentationHandoff(
            state=PresentationHandoffState.RAISING_HAND,
            task_id=task.id,
            task_revision=task.revision,
            hand_raised=False,
        )
        self.presentation_handoff = handoff
        self._persist_handoff()
        await self.emit_event(
            "meeting.hand.raise.started",
            {"revision": task.revision},
            task_id=task.id,
            component="meeting",
        )
        try:
            await self.meet.raise_hand()
            handoff.state = PresentationHandoffState.WAITING_FOR_INVITATION
            handoff.hand_raised = True
            handoff.updated_at = now_utc()
            self._persist_handoff()
            await self.emit_event(
                "meeting.hand.raised",
                {"revision": task.revision},
                task_id=task.id,
                component="meeting",
            )
        except Exception as exc:
            await self._block_handoff(
                str(exc),
                task.id,
                event_type="meeting.hand.raise.failed",
                component="meeting",
            )
        finally:
            await self._emit_meet_recovery_events(task.id)

    async def _clear_handoff_for_task(self, task_id: UUID | None, reason: str) -> None:
        handoff = self.presentation_handoff
        if handoff.state == PresentationHandoffState.IDLE:
            return
        if task_id is not None and handoff.task_id not in {None, task_id}:
            return
        original_task_id = handoff.task_id
        if handoff.hand_raised:
            try:
                await self.meet.lower_hand()
                await self.emit_event(
                    "meeting.hand.lowered",
                    {"reason": reason, "revision": handoff.task_revision},
                    task_id=original_task_id,
                    component="meeting",
                )
            except Exception as exc:
                await self.emit_event(
                    "meeting.hand.lower.failed",
                    {"reason": reason, "error": str(exc), "revision": handoff.task_revision},
                    task_id=original_task_id,
                    component="meeting",
                )
            finally:
                await self._emit_meet_recovery_events(original_task_id)
        self.presentation_handoff = PresentationHandoff()
        self._persist_handoff()
        await self.emit_event(
            "presentation.handoff.cleared",
            {"reason": reason},
            task_id=original_task_id,
            component="presentation",
        )

    async def _reject_stale_invitation(self, segment: TranscriptSegment, reason: str) -> None:
        await self.emit_event(
            "presentation.invitation.rejected",
            {"reason": reason, "segment_id": str(segment.id), "speaker_name": segment.speaker_name},
            task_id=self.presentation_handoff.task_id,
            component="presentation",
        )

    async def _block_handoff(
        self,
        error: str,
        task_id: UUID | None,
        event_type: str = "presentation.handoff.blocked",
        component: str = "presentation",
    ) -> None:
        self.presentation_handoff.state = PresentationHandoffState.BLOCKED
        self.presentation_handoff.error = error
        self.presentation_handoff.updated_at = now_utc()
        self._persist_handoff()
        await self.emit_event(
            event_type,
            {"error": error, "revision": self.presentation_handoff.task_revision},
            task_id=task_id,
            component=component,
        )

    def _handoff_task(self) -> RobinTask | None:
        task_id = self.presentation_handoff.task_id
        if task_id is None:
            return None
        try:
            return self._find_task(task_id)
        except KeyError:
            return None

    def _task_matches_handoff(self, task: RobinTask) -> bool:
        return (
            task.status == TaskStatus.READY_TO_PRESENT
            and self.presentation_handoff.task_id == task.id
            and self.presentation_handoff.task_revision == task.revision
        )

    def _next_ready_presentation_task(self) -> RobinTask | None:
        eligible = [
            task
            for task in self.tasks
            if task.status == TaskStatus.READY_TO_PRESENT
            and task.presentation_ready_at is not None
            and self._artifact_for_revision(task.id, "deck_json", task.revision) is not None
        ]
        return min(eligible, key=lambda task: task.presentation_ready_at) if eligible else None

    def _presentation_start_blocker(self, task: RobinTask) -> str | None:
        if task.status != TaskStatus.READY_TO_PRESENT:
            return "task_not_ready_to_present"
        if task.revision != self.presentation_handoff.task_revision:
            return "stale_task_or_revision"
        if self.meeting_state not in {
            MeetingState.JOINED,
            MeetingState.LISTENING,
            MeetingState.SPEAKING,
        }:
            return "not_in_meeting"
        if self.meet.presenting or any(state.active for state in self.presentations.values()):
            return "presentation_already_active"
        return None

    def _persist_handoff(self) -> None:
        self.presentation_handoff.updated_at = now_utc()
        self.store.upsert("presentation_handoff", self.presentation_handoff)

    def _pending_presentation_context(self) -> dict | None:
        handoff = self.presentation_handoff
        if handoff.task_id is None:
            return None
        task = self._handoff_task()
        return {
            "task_id": str(handoff.task_id),
            "title": task.title if task else None,
            "revision": handoff.task_revision,
            "state": handoff.state.value,
            "hand_raised": handoff.hand_raised,
            "require_wake_word": self.settings.presentation.require_wake_word_for_invitation,
        }

    async def _lower_hand_after_share_started(self, task_id: UUID) -> None:
        handoff = self.presentation_handoff
        if handoff.task_id != task_id or not handoff.hand_raised:
            return
        try:
            if not await self.meet.is_hand_raised():
                handoff.hand_raised = False
                self._persist_handoff()
                return
            await self.meet.lower_hand()
            handoff.hand_raised = False
            self._persist_handoff()
            await self.emit_event(
                "meeting.hand.lowered",
                {"reason": "after_share_started", "revision": handoff.task_revision},
                task_id=task_id,
                component="meeting",
            )
        except Exception as exc:
            await self.emit_event(
                "meeting.hand.lower.failed",
                {
                    "reason": "after_share_started",
                    "error": str(exc),
                    "revision": handoff.task_revision,
                },
                task_id=task_id,
                component="meeting",
            )
        finally:
            await self._emit_meet_recovery_events(task_id)

    async def present_task(self, task_id: UUID) -> RuntimeSnapshot:
        async with self._handoff_lock:
            await self._clear_handoff_for_task(None, "manual_override")
            return await self._present_task(task_id)

    async def _present_task(self, task_id: UUID) -> RuntimeSnapshot:
        task = self._find_task(task_id)
        deck = self._artifact_for_revision(task_id, "deck_json", task.revision)
        if not deck or not deck.url:
            raise ValueError("Task has no presentation artifact.")
        deck_spec = self._load_deck(task_id, revision=task.revision)
        narrations = [
            self._slide_narration(deck_spec, index) for index in range(len(deck_spec.slides))
        ]
        prefetch: NarrationPrefetchCoordinator | None = None
        if self.settings.presentation.narration_prefetch_enabled:
            prefetch = NarrationPrefetchCoordinator(
                self.audio,
                [
                    NarrationItem(slide_index=index, text=text)
                    for index, text in enumerate(narrations)
                ],
                concurrency=self.settings.presentation.narration_prefetch_concurrency,
            )
            prefetch.start()
            await asyncio.sleep(0)
            await self.emit_event(
                "presentation.narration.prefetch_started",
                {
                    "slide_count": len(narrations),
                    "concurrency": self.settings.presentation.narration_prefetch_concurrency,
                },
                task_id=task.id,
                component="presentation",
            )
        self.activate_presentation(task_id)
        task.status = TaskStatus.PRESENTING
        task.outcome_state = TaskOutcomeState.WORKING
        task.outcome_detail = "Presenting the verified artifact and narrating its findings."
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
            await self._lower_hand_after_share_started(task.id)
            await self._narrate_deck(task.id, deck_spec, narrations, prefetch)
        except Exception as exc:
            task.error = str(exc)
            task.outcome_state = TaskOutcomeState.BLOCKED
            task.outcome_detail = f"Presentation could not proceed: {exc}"
            await self.emit_event(
                "presentation.failed",
                {"error": str(exc)},
                task_id=task.id,
                component="presentation",
            )
            raise
        finally:
            if prefetch is not None:
                await prefetch.close()
            if self.meet.presenting or self.presentations[task.id].active:
                await self.stop_presenting(task.id)
        task.status = TaskStatus.COMPLETED
        task.error = None
        task.outcome_state = TaskOutcomeState.VERIFIED
        task.outcome_detail = "Artifact validation and live presentation completed."
        task.completed_at = now_utc()
        task.presentation_ready_at = None
        task.updated_at = now_utc()
        self.store.upsert("task", task)
        await self.emit_event(
            "presentation.completed",
            task.model_dump(mode="json"),
            task_id=task.id,
            component="presentation",
        )
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

    async def navigate_presentation(
        self, task_id: UUID, action: str, index: int | None = None
    ) -> PresentationSession:
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
        await self.emit_event(
            "presentation.updated",
            state.model_dump(mode="json"),
            task_id=task_id,
            component="presentation",
        )
        await self.publish()
        return state

    def _deck_slide_count(self, task_id: UUID) -> int:
        return len(self._load_deck(task_id).slides)

    def _load_deck(self, task_id: UUID, revision: int | None = None) -> DeckSpec:
        deck = (
            self._artifact_for_revision(task_id, "deck_json", revision)
            if revision is not None
            else self._latest_artifact(task_id, "deck_json")
        )
        if not deck:
            raise ValueError("Task has no presentation deck.")
        return DeckSpec.model_validate_json(self.workspace.resolve(deck.path).read_text())

    async def _narrate_deck(
        self,
        task_id: UUID,
        deck: DeckSpec,
        narrations: list[str] | None = None,
        prefetch: NarrationPrefetchCoordinator | None = None,
    ) -> None:
        narration_texts = narrations or [
            self._slide_narration(deck, index) for index in range(len(deck.slides))
        ]
        async with self._presentation_speech_session(task_id):
            for index, slide in enumerate(deck.slides):
                prefetch_result: PrefetchResult | None = None
                if prefetch is not None:
                    prefetch_result = await prefetch.get(index)
                slide_started = time.perf_counter()
                await self.emit_event(
                    "presentation.slide.started",
                    {"slide_index": index, "title": slide.title[:160]},
                    task_id=task_id,
                    component="presentation",
                )
                await self.navigate_presentation(task_id, "goto", index=index)
                speech = narration_texts[index]
                await self.emit_event(
                    "presentation.narration",
                    {"slide": index, "speech": speech[:240]},
                    task_id=task_id,
                    component="presentation",
                )
                if (
                    prefetch_result is not None
                    and prefetch_result.prepared is not None
                    and prefetch_result.error is None
                ):
                    record = await self._play_prepared_during_session(
                        prefetch_result.prepared,
                        task_id=task_id,
                        slide_index=index,
                    )
                    prefetch.mark_consumed(index)
                else:
                    if prefetch_result is not None and prefetch_result.error:
                        await self.emit_event(
                            "presentation.narration.prefetch_failed",
                            {"slide_index": index, "error": prefetch_result.error},
                            task_id=task_id,
                            component="presentation",
                        )
                    record = await self._speak_during_session(
                        speech,
                        task_id=task_id,
                        slide_index=index,
                        source="fallback" if prefetch_result is not None else None,
                    )
                await self.emit_event(
                    "presentation.slide.completed",
                    {
                        "slide_index": index,
                        "duration_ms": int((time.perf_counter() - slide_started) * 1000),
                        "interrupted": record.interrupted,
                    },
                    task_id=task_id,
                    component="presentation",
                )
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
                return (
                    "Key metrics: "
                    + "; ".join(f"{label} is {value}" for label, value in metrics)
                    + "."
                )
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
        pending_presentation = self._pending_presentation_context()
        if not self._contains_wake_word(segment.text) and (
            pending_presentation is None
            or self.settings.presentation.require_wake_word_for_invitation
        ):
            await self.emit_event(
                "conversation.ignored",
                {"text": segment.text, "reason": "wake_word_missing"},
                component="conversation",
            )
            return
        if await self._handle_pending_confirmation(segment):
            return
        if pending_presentation is not None:
            invitation = await self.intent.classify(
                segment.text,
                self._active_tasks(),
                pending_presentation=pending_presentation,
            )
            if invitation.classification == "presentation_invitation":
                await self.accept_presentation_invitation(segment, invitation)
                return
            if self._looks_like_ambiguous_presentation_invitation(segment.text):
                await self.emit_event(
                    "presentation.invitation.rejected",
                    {"reason": "ambiguous", "segment_id": str(segment.id)},
                    task_id=self.presentation_handoff.task_id,
                    component="presentation",
                )
                await self._acknowledge("Should I present the ready deck now?")
                return
        if not self._contains_wake_word(segment.text):
            await self.emit_event(
                "conversation.ignored",
                {"text": segment.text, "reason": "wake_word_missing"},
                component="conversation",
            )
            return
        if await self._handle_duplicate_task_request(segment.text):
            return
        active = self._active_tasks()
        intent = await self.intent.classify(segment.text, active)
        if (
            intent.classification in {"direct_request", "confirmed_task"}
            and intent.confidence >= self.settings.model.intent_confidence_accept
        ):
            await self.emit_event(
                "intent.detected", intent.model_dump(mode="json"), component="conversation"
            )
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
            await self.emit_event(
                "task.created",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            self._schedule_task(task)
            await self.publish()
            self._schedule_acknowledgement(
                "Got it. I’ll analyze the files and prepare a short deck."
            )
        elif intent.classification == "task_modification" and intent.referenced_task_id:
            task = self._find_task(intent.referenced_task_id)
            async with self._handoff_lock:
                await self._clear_handoff_for_task(task.id, "task_revised")
            task.revision += 1
            task.presentation_ready_at = None
            task.constraints = sorted(set(task.constraints + intent.constraints + [segment.text]))
            task.outcome_state = TaskOutcomeState.UNVERIFIED
            task.outcome_detail = (
                "Revision requested; prior verification no longer covers the updated task."
            )
            task.source_context_segment_ids.append(segment.id)
            task.updated_at = now_utc()
            self.store.upsert("task", task)
            await self.emit_event(
                "task.updated",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
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
                    self._conversation_artifact_context(),
                )
            )
        elif intent.should_ask_confirmation and intent.clarification_question:
            task = RobinTask(
                meeting_id=self.meeting_id,
                title=intent.task_title or segment.text[:80],
                requester_name=segment.speaker_name,
                status=TaskStatus.AWAITING_CLARIFICATION,
                outcome_state=TaskOutcomeState.AWAITING_CONFIRMATION,
                outcome_detail=intent.clarification_question,
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
            await self.emit_event(
                "task.created",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            await self.emit_event(
                "task.awaiting_clarification",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            await self._acknowledge(intent.clarification_question)

    async def _handle_pending_confirmation(self, segment: TranscriptSegment) -> bool:
        if not self._pending_confirmation:
            return False
        lowered = self._strip_wake_word(segment.text).casefold()
        accepts = {
            "yes",
            "yeah",
            "yep",
            "please do",
            "go ahead",
            "do it",
            "take it",
            "sounds good",
            "correct",
        }
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
            task.outcome_state = TaskOutcomeState.CANCELLED
            task.outcome_detail = "The proposed task was declined."
            task.updated_at = now_utc()
            task.source_context_segment_ids.append(segment.id)
            self.store.upsert("task", task)
            await self.emit_event(
                "clarification.declined",
                {"original_text": original.text, "answer": segment.text},
                task_id=task.id,
                component="conversation",
            )
            await self.emit_event(
                "task.cancelled",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            await self._acknowledge("Okay, I will leave that alone.")
            await self.publish()
            return True
        duplicate = await self._handle_duplicate_task_request(original.text)
        if duplicate:
            task.status = TaskStatus.CANCELLED
            task.outcome_state = TaskOutcomeState.CANCELLED
            task.outcome_detail = "A duplicate confirmed task was suppressed."
            task.updated_at = now_utc()
            task.source_context_segment_ids.append(segment.id)
            self.store.upsert("task", task)
            await self.emit_event(
                "task.cancelled",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            await self.emit_event(
                "clarification.accepted",
                {
                    "original_text": original.text,
                    "answer": segment.text,
                    "duplicate_task_id": str(duplicate.id),
                },
                task_id=duplicate.id,
                component="conversation",
            )
            return True
        task.status = TaskStatus.ACCEPTED
        task.outcome_state = TaskOutcomeState.UNVERIFIED
        task.outcome_detail = None
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
        await self.emit_event(
            "task.accepted",
            task.model_dump(mode="json"),
            task_id=task.id,
            component="task_orchestrator",
        )
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
            if (
                task.status in duplicate_statuses
                and self._normalize_task_text(task.request_text) == normalized
            ):
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
                task.outcome_state = TaskOutcomeState.WORKING
                task.outcome_detail = "Waiting for an available bounded task slot."
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event(
                    "task.queued",
                    task.model_dump(mode="json"),
                    task_id=task.id,
                    component="task_orchestrator",
                )
                await self.publish()
            async with self.task_slots:
                violations = self._resource_budget_violations()
                if violations:
                    raise RuntimeError("Robin resource budget exceeded: " + "; ".join(violations))
                task.status = TaskStatus.EXECUTING
                task.outcome_state = TaskOutcomeState.WORKING
                task.outcome_detail = (
                    "The general agent is inspecting sources and producing the requested output."
                )
                task.started_at = task.started_at or now_utc()
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event(
                    "task.started",
                    task.model_dump(mode="json"),
                    task_id=task.id,
                    component="task_orchestrator",
                )
                await self.publish()
                records = self.workspace.index()
                self.files = records
                self.store.replace_all("file", records)
                if self.task_agent.client:

                    async def report_agent_progress(event_type: str, payload: dict) -> None:
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
                task.outcome_state = TaskOutcomeState.WORKING
                task.outcome_detail = (
                    "Checking citations, structure, calculations, and artifact readiness."
                )
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event(
                    "task.validating",
                    task.model_dump(mode="json"),
                    task_id=task.id,
                    component="task_orchestrator",
                )
                await self.publish()
                for artifact in artifacts:
                    self.artifacts.append(artifact)
                    self.store.upsert("artifact", artifact)
                    await self.emit_event(
                        "artifact.created",
                        artifact.model_dump(mode="json"),
                        task_id=task.id,
                        component="artifact_worker",
                    )
                if not validation.ok:
                    failed_checks = [check.name for check in validation.checks if not check.ok]
                    task.status = TaskStatus.FAILED
                    task.presentation_ready_at = None
                    task.error = f"Validation failed: {', '.join(failed_checks)}"
                    task.outcome_state = TaskOutcomeState.FAILED
                    task.outcome_detail = task.error
                    task.updated_at = now_utc()
                    self.store.upsert("task", task)
                    await self.emit_event(
                        "task.failed",
                        task.model_dump(mode="json"),
                        task_id=task.id,
                        component="task_orchestrator",
                    )
                    await self._acknowledge(
                        "I found a validation issue in the analysis, so I will not present it yet."
                    )
                    await self.publish()
                    return
                task.status = TaskStatus.READY_TO_PRESENT
                task.presentation_ready_at = task.presentation_ready_at or now_utc()
                task.outcome_state = TaskOutcomeState.VERIFIED
                task.outcome_detail = "Grounding, citations, and artifact validation passed."
                task.updated_at = now_utc()
                self.store.upsert("task", task)
                await self.emit_event(
                    "task.completed",
                    task.model_dump(mode="json"),
                    task_id=task.id,
                    component="task_orchestrator",
                )
                await self.publish()
                if self.settings.presentation.hand_raise_handoff_enabled:
                    asyncio.create_task(self.request_presentation_floor(task.id, task.revision))
                else:
                    await self._safe_acknowledge("The analysis and slides are ready.")
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.presentation_ready_at = None
            task.outcome_state = TaskOutcomeState.CANCELLED
            task.outcome_detail = "Execution was cancelled before verification."
            task.updated_at = now_utc()
            self.store.upsert("task", task)
            await self.emit_event(
                "task.cancelled",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            await self.publish()
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.presentation_ready_at = None
            task.error = str(exc)
            if self._is_recoverable_task_blocker(exc):
                task.outcome_state = TaskOutcomeState.BLOCKED
                task.outcome_detail = f"Retryable blocker: {exc}"
            else:
                task.outcome_state = TaskOutcomeState.FAILED
                task.outcome_detail = str(exc)
            task.updated_at = now_utc()
            self.store.upsert("task", task)
            await self.emit_event(
                "task.failed",
                task.model_dump(mode="json"),
                task_id=task.id,
                component="task_orchestrator",
            )
            await self.publish()
            await self._safe_acknowledge(self._task_failure_acknowledgement(task))

    @staticmethod
    def _is_recoverable_task_blocker(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        text = str(exc).casefold()
        return any(
            marker in text
            for marker in (
                "resource budget exceeded",
                "not reachable",
                "connection",
                "temporarily unavailable",
                "timed out",
                "browser",
                "renderer",
                "permission",
            )
        )

    def _meeting_context(self, meeting_id: UUID | None = None) -> list[TranscriptSegment]:
        target = meeting_id or self.meeting_id
        return [segment for segment in self.transcript if segment.meeting_id == target][-30:]

    def _memory_context(self, meeting_id: UUID | None = None) -> list[MeetingMemoryItem]:
        target = meeting_id or self.meeting_id
        return [item for item in self.meeting_memory if item.meeting_id == target][-60:]

    def _conversation_artifact_context(self) -> list[dict]:
        contexts: list[dict] = []
        tasks = sorted(self.tasks, key=lambda task: task.updated_at, reverse=True)
        for task in tasks:
            deck_artifact = self._latest_artifact(task.id, "deck_json")
            if deck_artifact is None:
                continue
            try:
                deck = DeckSpec.model_validate_json(
                    self.workspace.resolve(deck_artifact.path).read_text(encoding="utf-8")
                )
            except Exception:
                continue
            contexts.append(
                {
                    "task_id": str(task.id),
                    "title": task.title[:160],
                    "revision": task.revision,
                    "outcome_state": task.outcome_state,
                    "outcome_detail": (task.outcome_detail or "")[:300],
                    "slides": [
                        {
                            "title": slide.title[:160],
                            "body": [item[:400] for item in slide.body[:2]],
                            "metrics": dict(list(slide.metrics.items())[:6]),
                        }
                        for slide in deck.slides[:8]
                        if slide.type != "sources"
                    ],
                    "sources": [
                        {"label": source.label[:160], "path": source.path[:300]}
                        for source in deck.sources[:12]
                    ],
                }
            )
            if len(contexts) >= 3:
                break
        return contexts

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

    @asynccontextmanager
    async def _presentation_speech_session(self, task_id: UUID | None = None):
        async with self._speech_lock:
            previous = self.meeting_state
            await self._wait_for_speech_floor("presentation narration")
            await self.meet.unmute()
            await self._emit_meet_recovery_events(task_id)
            await self._emit_meet_speech_route_events(task_id)
            self.meeting_state = MeetingState.PRESENTING
            await self.publish()
            try:
                yield
            finally:
                self._last_spoken_at_ms = int(time.time() * 1000)
                try:
                    await self.meet.mute()
                    await self._emit_meet_recovery_events(task_id)
                finally:
                    self._active_spoken_text = None
                    self.meeting_state = (
                        previous if previous != MeetingState.IDLE else self.meet.state
                    )
                    await self.publish()

    @asynccontextmanager
    async def _isolated_speech_session(self, text: str):
        async with self._speech_lock:
            previous = self.meeting_state
            await self._wait_for_speech_floor(text)
            await self.meet.unmute()
            await self._emit_meet_recovery_events()
            await self._emit_meet_speech_route_events()
            self.meeting_state = MeetingState.SPEAKING
            await self.publish()
            try:
                yield
            finally:
                self._last_spoken_at_ms = int(time.time() * 1000)
                try:
                    await self.meet.mute()
                    await self._emit_meet_recovery_events()
                finally:
                    self._active_spoken_text = None
                    self.meeting_state = (
                        previous if previous != MeetingState.IDLE else self.meet.state
                    )
                    await self.publish()

    async def _speak_during_session(
        self,
        text: str,
        *,
        task_id: UUID | None = None,
        slide_index: int | None = None,
        source: str | None = None,
    ) -> SpeechRecord:
        self._active_spoken_text = text
        await self.publish()
        try:
            return await self._speak_and_record(
                text,
                task_id=task_id,
                slide_index=slide_index,
                source=source,
            )
        finally:
            self._active_spoken_text = None
            await self.publish()

    async def _play_prepared_during_session(
        self,
        prepared: PreparedSpeech,
        *,
        task_id: UUID | None = None,
        slide_index: int | None = None,
    ) -> SpeechRecord:
        self._active_spoken_text = prepared.text
        await self.publish()
        try:
            return await self._play_prepared_and_record(
                prepared,
                task_id=task_id,
                slide_index=slide_index,
            )
        finally:
            self._active_spoken_text = None
            await self.publish()

    async def _acknowledge(
        self,
        text: str,
        *,
        task_id: UUID | None = None,
        slide_index: int | None = None,
    ) -> SpeechRecord:
        async with self._isolated_speech_session(text):
            return await self._speak_during_session(
                text,
                task_id=task_id,
                slide_index=slide_index,
            )

    async def _speak_and_record(
        self,
        text: str,
        *,
        task_id: UUID | None = None,
        slide_index: int | None = None,
        source: str | None = None,
    ) -> SpeechRecord:
        await self.emit_event(
            "speech.synthesis.started",
            {"task_id": str(task_id) if task_id else None, "slide_index": slide_index},
            task_id=task_id,
            component="speech",
        )
        speech = await self.audio.speak(text)
        if source:
            speech.source = source  # type: ignore[assignment]
        if speech.time_to_first_audio_ms is not None:
            await self.emit_event(
                "speech.first_audio",
                {
                    "task_id": str(task_id) if task_id else None,
                    "slide_index": slide_index,
                    "time_to_first_audio_ms": speech.time_to_first_audio_ms,
                    "streaming": speech.streaming,
                    "source": speech.source,
                },
                task_id=task_id,
                component="speech",
            )
        await self.emit_event(
            "speech.playback.started",
            {
                "task_id": str(task_id) if task_id else None,
                "slide_index": slide_index,
                "path": speech.path,
                "streaming": speech.streaming,
                "source": speech.source,
                "synthesis_duration_ms": speech.synthesis_duration_ms,
            },
            task_id=task_id,
            component="speech",
        )
        if speech.path:
            speech.path = (
                Path(self.settings.workspace.sessions_dir) / "speech" / speech.path
            ).as_posix()
        self.speech.append(speech)
        self.store.upsert("speech", speech)
        await self.emit_event(
            "speech.interrupted" if speech.interrupted else "speech.completed",
            speech.model_dump(mode="json"),
            task_id=task_id,
            component="speech",
        )
        await self.emit_event(
            "speech.playback.completed",
            {
                "task_id": str(task_id) if task_id else None,
                "slide_index": slide_index,
                "duration_ms": speech.playback_duration_ms,
                "interrupted": speech.interrupted,
                "streaming": speech.streaming,
                "source": speech.source,
                "error": speech.error,
            },
            task_id=task_id,
            component="speech",
        )
        return speech

    async def _play_prepared_and_record(
        self,
        prepared: PreparedSpeech,
        *,
        task_id: UUID | None = None,
        slide_index: int | None = None,
    ) -> SpeechRecord:
        await self.emit_event(
            "speech.synthesis.started",
            {
                "task_id": str(task_id) if task_id else None,
                "slide_index": slide_index,
                "source": "prefetched",
                "prepared": True,
                "synthesis_duration_ms": prepared.synthesis_duration_ms,
            },
            task_id=task_id,
            component="speech",
        )
        speech = await self.audio.play_prepared(prepared)
        await self.emit_event(
            "speech.playback.started",
            {
                "task_id": str(task_id) if task_id else None,
                "slide_index": slide_index,
                "path": speech.path,
                "streaming": speech.streaming,
                "source": speech.source,
                "synthesis_duration_ms": speech.synthesis_duration_ms,
            },
            task_id=task_id,
            component="speech",
        )
        if speech.path:
            speech.path = (
                Path(self.settings.workspace.sessions_dir) / "speech" / speech.path
            ).as_posix()
        self.speech.append(speech)
        self.store.upsert("speech", speech)
        await self.emit_event(
            "speech.interrupted" if speech.interrupted else "speech.completed",
            speech.model_dump(mode="json"),
            task_id=task_id,
            component="speech",
        )
        await self.emit_event(
            "speech.playback.completed",
            {
                "task_id": str(task_id) if task_id else None,
                "slide_index": slide_index,
                "duration_ms": speech.playback_duration_ms,
                "interrupted": speech.interrupted,
                "streaming": speech.streaming,
                "source": speech.source,
                "error": speech.error,
            },
            task_id=task_id,
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

    async def _emit_meet_speech_route_events(self, task_id: UUID | None = None) -> None:
        events = self.meet.speech_route_events or []
        emitted_count = getattr(self, "_meet_speech_route_event_count", 0)
        for event in events[emitted_count:]:
            await self.emit_event(
                event.type,
                {
                    "started_at": event.started_at.isoformat(),
                    "completed_at": event.completed_at.isoformat(),
                    "duration_ms": event.duration_ms,
                    "cache_status": event.cache_status,
                    "selected_device": event.selected_device,
                    "error": event.error,
                },
                task_id=task_id,
                component="speech",
            )
        self._meet_speech_route_event_count = len(events)

    def _status_summary(self) -> str:
        active = self._active_tasks()
        if not active:
            return "I do not have an active task right now."
        return "; ".join(
            f"{task.title}: {task.status.value.lower().replace('_', ' ')}" for task in active[:2]
        )

    def _find_task(self, task_id: UUID) -> RobinTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"Unknown task: {task_id}")

    def _active_tasks(self) -> list[RobinTask]:
        return sorted(
            (
                task
                for task in self.tasks
                if task.status
                not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}
            ),
            key=lambda task: task.updated_at,
            reverse=True,
        )

    def artifact_path(self, relative_path: str) -> Path:
        return self.workspace.resolve(relative_path)

    def _latest_artifact(self, task_id: UUID, artifact_type: str) -> Artifact | None:
        artifacts = [
            artifact
            for artifact in self.artifacts
            if artifact.task_id == task_id and artifact.type == artifact_type
        ]
        if not artifacts:
            return None
        return max(artifacts, key=lambda artifact: (artifact.revision, artifact.created_at))

    def _artifact_for_revision(
        self, task_id: UUID, artifact_type: str, revision: int
    ) -> Artifact | None:
        artifacts = [
            artifact
            for artifact in self.artifacts
            if artifact.task_id == task_id
            and artifact.type == artifact_type
            and artifact.revision == revision
        ]
        if not artifacts:
            return None
        return max(artifacts, key=lambda artifact: artifact.created_at)

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
            payload=redact_value(payload),
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
            path.stat().st_size for path in self.workspace.root.rglob("*") if path.is_file()
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
        violations = self._resource_budget_violations(
            peak_rss_mb=rss_bytes / 1024 / 1024,
            workspace_disk_mb=workspace_bytes / 1024 / 1024,
        )
        return RuntimeMetrics(
            event_count=len(events),
            transcript_count=len(self.transcript),
            task_count=len(self.tasks),
            completed_task_count=sum(
                1 for task in self.tasks if task.status == TaskStatus.COMPLETED
            ),
            failed_task_count=sum(1 for task in self.tasks if task.status == TaskStatus.FAILED),
            active_task_count=sum(1 for task in self.tasks if task.status in active_statuses),
            artifact_count=len(self.artifacts),
            speech_count=len(self.speech),
            presentation_count=len(self.presentations),
            audio_capture_event_count=sum(
                1 for event in events if event.type.startswith("audio.capture")
            ),
            direct_request_count=sum(1 for event in events if event.type == "task.created"),
            agent_tool_call_count=sum(
                1 for event in events if event.type == "agent.tool.completed"
            ),
            recovery_event_count=sum(1 for event in events if ".recovery." in event.type),
            realtime_failure_count=sum(
                1 for event in events if event.type == "audio.realtime.failed"
            ),
            uptime_seconds=round(time.monotonic() - self._started_monotonic, 1),
            process_cpu_seconds=round(usage.ru_utime + usage.ru_stime, 2),
            peak_rss_mb=round(rss_bytes / 1024 / 1024, 1),
            workspace_disk_mb=round(workspace_bytes / 1024 / 1024, 1),
            resource_budget_ok=not violations,
            resource_budget_violations=violations,
        )

    def _resource_budget_violations(
        self,
        peak_rss_mb: float | None = None,
        workspace_disk_mb: float | None = None,
    ) -> list[str]:
        if peak_rss_mb is None:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss_bytes = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
            peak_rss_mb = rss_bytes / 1024 / 1024
        if workspace_disk_mb is None:
            workspace_disk_mb = (
                sum(
                    path.stat().st_size for path in self.workspace.root.rglob("*") if path.is_file()
                )
                / 1024
                / 1024
            )
        violations: list[str] = []
        if peak_rss_mb > self.settings.runtime.max_peak_rss_mb:
            violations.append(
                f"peak memory {peak_rss_mb:.1f} MB exceeds "
                f"{self.settings.runtime.max_peak_rss_mb} MB"
            )
        if workspace_disk_mb > self.settings.runtime.max_workspace_disk_mb:
            violations.append(
                f"workspace {workspace_disk_mb:.1f} MB exceeds "
                f"{self.settings.runtime.max_workspace_disk_mb} MB"
            )
        return violations

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
