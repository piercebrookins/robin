from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeState(StrEnum):
    BOOTING = "BOOTING"
    READY = "READY"
    JOINING_MEETING = "JOINING_MEETING"
    IN_MEETING = "IN_MEETING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"


class MeetingState(StrEnum):
    IDLE = "IDLE"
    NAVIGATING = "NAVIGATING"
    PREJOIN = "PREJOIN"
    REQUESTING_ADMISSION = "REQUESTING_ADMISSION"
    JOINED = "JOINED"
    LISTENING = "LISTENING"
    SPEAKING = "SPEAKING"
    PRESENTING = "PRESENTING"
    LEAVING = "LEAVING"
    ENDED = "ENDED"


class TaskStatus(StrEnum):
    PROPOSED = "PROPOSED"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    ACKNOWLEDGING = "ACKNOWLEDGING"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    READY_TO_PRESENT = "READY_TO_PRESENT"
    PRESENTING = "PRESENTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HealthItem(BaseModel):
    name: str
    ok: bool
    detail: str
    checked_at: datetime = Field(default_factory=now_utc)


class TranscriptSegment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    speaker_name: str | None = None
    text: str
    started_at_ms: int
    ended_at_ms: int
    is_final: bool = True
    confidence: float | None = None
    source: Literal["audio_stt", "meet_caption", "merged", "simulator"] = "simulator"
    created_at: datetime = Field(default_factory=now_utc)


class MeetingMemoryItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    kind: Literal[
        "topic",
        "reference",
        "decision",
        "objection",
        "question",
        "commitment",
        "correction",
    ]
    text: str
    speaker_name: str | None = None
    owner: str | None = None
    deadline: str | None = None
    status: Literal["active", "resolved", "superseded", "cancelled"] = "active"
    source_segment_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class MeetingIntent(BaseModel):
    classification: Literal[
        "non_task",
        "possible_task",
        "direct_request",
        "confirmed_task",
        "clarification_answer",
        "task_modification",
        "task_cancellation",
        "status_request",
        "conversation_request",
    ]
    confidence: float
    addressed_to_robin: bool
    requester_name: str | None = None
    task_title: str | None = None
    requested_outcome: str | None = None
    constraints: list[str] = Field(default_factory=list)
    referenced_task_id: UUID | None = None
    should_acknowledge: bool = False
    should_ask_confirmation: bool = False
    clarification_question: str | None = None


class RobinTask(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    title: str
    requester_name: str | None = None
    status: TaskStatus = TaskStatus.PROPOSED
    revision: int = 1
    request_text: str
    requested_outcome: str
    constraints: list[str] = Field(default_factory=list)
    source_context_segment_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_task_id: UUID | None = None
    error: str | None = None


class FileIndexRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    relative_path: str
    file_type: str
    sha256: str
    size_bytes: int
    summary: str
    columns: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)


class WorkspaceSnapshot(BaseModel):
    root: str
    source_dir: str
    generated_dir: str
    sessions_dir: str
    file_count: int
    files: list[FileIndexRecord] = Field(default_factory=list)


class ChartSeries(BaseModel):
    name: str
    x: list[str]
    y: list[float]


class ChartSpec(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    subtitle: str | None = None
    chart_type: Literal["bar", "line", "grouped_bar", "stacked_bar", "area"] = "grouped_bar"
    x_label: str | None = None
    y_label: str | None = None
    y_unit: str | None = None
    series: list[ChartSeries]
    annotations: list[str] = Field(default_factory=list)
    source_note: str
    lineage: list[dict] = Field(default_factory=list)


class SourceCitation(BaseModel):
    label: str
    path: str
    note: str


class SlideSpec(BaseModel):
    type: Literal["title", "executive_summary", "chart", "key_metrics", "findings", "methodology", "sources"]
    title: str
    body: list[str] = Field(default_factory=list)
    chart_id: UUID | None = None
    metrics: dict[str, str] = Field(default_factory=dict)


class DeckSpec(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    revision: int
    title: str
    theme: str = "default"
    slides: list[SlideSpec]
    sources: list[SourceCitation]
    generated_at: datetime = Field(default_factory=now_utc)


class ValidationCheck(BaseModel):
    name: str
    ok: bool
    detail: str
    source: str | None = None
    expected: Any | None = None
    actual: Any | None = None


class ValidationReport(BaseModel):
    task_id: UUID
    ok: bool
    checks: list[ValidationCheck]
    source_paths: list[str]
    generated_at: datetime = Field(default_factory=now_utc)


class AgentDeliverable(BaseModel):
    title: str
    summary: str
    slides: list[SlideSpec]
    sources: list[SourceCitation]


class AgentExecutionResult(BaseModel):
    deliverable: AgentDeliverable
    model: str
    iterations: int
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    source_paths: list[str] = Field(default_factory=list)


class Artifact(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    revision: int = 1
    type: Literal[
        "chart_json",
        "chart_png",
        "deck_json",
        "deck_pptx",
        "validation_json",
        "report_markdown",
        "agent_result_json",
    ]
    path: str
    url: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class SpeechRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    text: str
    mode: Literal["simulator", "openai"]
    voice: str
    model: str
    format: str
    path: str | None = None
    byte_count: int = 0
    duration_seconds: float | None = None
    playback_device: str | None = None
    playback_route: str | None = None
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime | None = None
    error: str | None = None
    interrupted: bool = False


class PresentationSession(BaseModel):
    task_id: UUID
    active_slide: int = 0
    slide_count: int = 0
    active: bool = False
    updated_at: datetime = Field(default_factory=now_utc)


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    meeting_url: str
    source: str = "local"
    conflicted: bool = False


class CalendarSnapshot(BaseModel):
    enabled: bool
    provider: str
    auto_join: bool = False
    auto_join_running: bool = False
    events: list[CalendarEvent] = Field(default_factory=list)
    conflicts: list[list[str]] = Field(default_factory=list)
    error: str | None = None


class EventEnvelope(BaseModel):
    id: int | None = None
    type: str
    timestamp: datetime = Field(default_factory=now_utc)
    meeting_id: UUID | None = None
    task_id: UUID | None = None
    component: str = "runtime"
    payload: dict = Field(default_factory=dict)


class RuntimeMetrics(BaseModel):
    event_count: int = 0
    transcript_count: int = 0
    task_count: int = 0
    completed_task_count: int = 0
    failed_task_count: int = 0
    active_task_count: int = 0
    artifact_count: int = 0
    speech_count: int = 0
    presentation_count: int = 0
    audio_capture_event_count: int = 0
    direct_request_count: int = 0
    agent_tool_call_count: int = 0
    recovery_event_count: int = 0
    realtime_failure_count: int = 0
    uptime_seconds: float = 0
    process_cpu_seconds: float = 0
    peak_rss_mb: float = 0
    workspace_disk_mb: float = 0
    resource_budget_ok: bool = True
    resource_budget_violations: list[str] = Field(default_factory=list)


class RuntimeSnapshot(BaseModel):
    runtime_state: RuntimeState
    meeting_state: MeetingState
    meeting_url: str | None
    meeting_id: UUID
    listening: bool
    presenting: bool
    capture_loop_running: bool = False
    calendar_auto_join_running: bool = False
    health: list[HealthItem]
    transcript: list[TranscriptSegment]
    meeting_memory: list[MeetingMemoryItem] = Field(default_factory=list)
    tasks: list[RobinTask]
    artifacts: list[Artifact]
    speech: list[SpeechRecord] = Field(default_factory=list)
    presentations: list[PresentationSession] = Field(default_factory=list)


class JoinMeetingRequest(BaseModel):
    meeting_url: str
    start_listening: bool = False


class TranscriptIngestRequest(BaseModel):
    speaker_name: str | None = None
    text: str
    started_at_ms: int | None = None
    ended_at_ms: int | None = None


class AudioTranscribeRequest(BaseModel):
    path: str
    speaker_name: str | None = None


class AudioCaptureSampleRequest(BaseModel):
    bundle_id: str = "com.google.Chrome"
    duration_ms: int = 1500
    output_name: str | None = None


class AudioListenLoopRequest(BaseModel):
    bundle_id: str | None = None
    duration_ms: int | None = None
    interval_ms: int | None = None
    max_iterations: int | None = None


class BrowserOperatorRequest(BaseModel):
    request: str
    page_name: str = "meet"
    approval_token: str | None = None


class CalendarAutoJoinRequest(BaseModel):
    enabled: bool
    interval_seconds: float = 15.0


class TaskCreateRequest(BaseModel):
    text: str
    requester_name: str | None = None


class PresentationGotoRequest(BaseModel):
    index: int


class WorkspacePath(BaseModel):
    path: Path
