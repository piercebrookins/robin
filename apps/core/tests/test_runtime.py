from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from uuid import uuid4

import fitz
import pytest

from robin_core.audio.bridge import PreparedSpeech
from robin_core.audio.prefetch import NarrationItem, NarrationPrefetchCoordinator
from robin_core.browser.page_driver import CaptionTurn, SimulatedPageDriver
from robin_core.config import (
    AudioConfig,
    DatabaseConfig,
    PresentationConfig,
    RuntimeConfig,
    Settings,
    WorkspaceConfig,
)
from robin_core.runtime import RobinRuntime
from robin_core.schemas import (
    Artifact,
    DeckSpec,
    MeetingState,
    MeetingIntent,
    PresentationSession,
    RehearsalConfirmationRequest,
    RobinTask,
    SlideSpec,
    SourceCitation,
    RuntimeState,
    SpeechRecord,
    TaskOutcomeState,
    TaskStatus,
    ValidationReport,
    PresentationHandoffState,
    now_utc,
)


@pytest.mark.asyncio
async def test_emergency_stop_halts_all_work_speech_capture_and_presentation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.start_listening_loop(interval_ms=10_000)
    task = RobinTask(
        meeting_id=runtime.meeting_id,
        title="Long-running task",
        status=TaskStatus.EXECUTING,
        request_text="Keep working",
        requested_outcome="A result",
    )
    runtime.tasks.append(task)
    work_handle = asyncio.create_task(asyncio.Event().wait())
    speech_handle = asyncio.create_task(asyncio.Event().wait())
    runtime._task_handles[task.id] = work_handle
    runtime._speech_handles.add(speech_handle)
    runtime.presentations[task.id] = PresentationSession(
        task_id=task.id,
        active=True,
        slide_count=3,
    )
    runtime.meet.presenting = True
    runtime.meeting_state = MeetingState.PRESENTING

    snapshot = await runtime.emergency_stop()

    assert snapshot.runtime_state == RuntimeState.READY
    assert snapshot.meeting_state == MeetingState.ENDED
    assert snapshot.capture_loop_running is False
    assert snapshot.presenting is False
    assert work_handle.cancelled()
    assert speech_handle.cancelled()
    assert runtime._task_handles == {}
    assert runtime._speech_handles == set()
    assert runtime.presentations[task.id].active is False
    assert task.status == TaskStatus.CANCELLED
    assert task.outcome_state == TaskOutcomeState.CANCELLED
    event = next(
        item for item in reversed(runtime.recent_events()) if item.type == "runtime.emergency_stop"
    )
    assert event.payload["cleanup_errors"] == []


@pytest.mark.asyncio
async def test_real_rehearsal_evidence_requires_automated_and_participant_proof(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript(
        "Robin, compare the quarterly finance results and make slides.",
        "Avery",
        source="audio_stt",
    )
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    await runtime.ingest_transcript(
        "Robin, what sources did you use?",
        "Avery",
        source="audio_stt",
    )
    await runtime.present_task(task.id)
    for speech in runtime.speech:
        speech.playback_device = "BlackHole 2ch"
    await runtime.leave_meeting()
    confirmation = RehearsalConfirmationRequest(
        task_id=task.id,
        participant_name="Avery",
        robin_heard_participant=True,
        correct_understanding=True,
        grounded_output=True,
        correct_shared_surface=True,
        audible_narration=True,
        live_qa_or_revision=True,
        graceful_leave=True,
        notes="Verified from the second participant device.",
    )

    first = await runtime.record_rehearsal_confirmation(confirmation)

    assert first.passed is True
    assert first.consecutive_passes == 1
    assert all(first.automated_checks.values())
    assert (workspace / first.evidence_path).is_file()
    second = await runtime.record_rehearsal_confirmation(confirmation)
    assert second.passed is False
    assert second.consecutive_passes == 0
    assert second.automated_checks["fresh_runtime"] is False
    assert second.automated_checks["different_task"] is False


@pytest.mark.asyncio
async def test_demo_task_generates_deck(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
        "2024,Q4,forecast,200,120,80\n"
    )
    _write_pdf(
        source / "finance_context.pdf",
        "Finance context report: 2024 growth improved through Q4 and actuals are preferred for board reporting.",
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript(
        "Robin, use the finance files to compare our 2024 quarterly results and make a few slides.",
        "Avery",
    )
    task = runtime.tasks[-1]
    handle = runtime._task_handles[task.id]
    await handle
    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.outcome_state == TaskOutcomeState.VERIFIED
    assert "validation passed" in (task.outcome_detail or "").casefold()
    assert any(artifact.type == "deck_json" for artifact in runtime.artifacts)
    deck_artifact = next(
        artifact
        for artifact in runtime.artifacts
        if artifact.task_id == task.id and artifact.type == "deck_json"
    )
    pptx_artifact = next(
        artifact
        for artifact in runtime.artifacts
        if artifact.task_id == task.id and artifact.type == "deck_pptx"
    )
    deck_json = runtime.artifact_path(deck_artifact.path).read_text()
    assert "finance_context.pdf" in deck_json
    assert "2024 growth improved through Q4" in deck_json
    assert pptx_artifact.path.endswith("deck_v1.pptx")
    with zipfile.ZipFile(runtime.artifact_path(pptx_artifact.path)) as archive:
        assert "ppt/presentation.xml" in archive.namelist()
    validation = next(
        artifact
        for artifact in runtime.artifacts
        if artifact.task_id == task.id and artifact.type == "validation_json"
    )
    report = ValidationReport.model_validate_json(
        runtime.artifact_path(validation.path).read_text()
    )
    assert report.ok is True
    assert {check.name for check in report.checks} >= {
        "operating_margin_formula",
        "chart_revenue_series",
        "lineage_present",
        "source_citations_present",
    }
    assert "source-data/finance_context.pdf" in report.source_paths
    metrics = runtime.metrics()
    assert metrics.task_count >= 1
    assert metrics.artifact_count >= 3
    assert metrics.speech_count >= 1
    assert metrics.uptime_seconds >= 0
    assert metrics.process_cpu_seconds > 0
    assert metrics.peak_rss_mb > 0
    assert metrics.workspace_disk_mb > 0
    assert metrics.agent_tool_call_count >= 0
    assert any(event.type == "task.completed" for event in runtime.recent_events())
    assert (workspace / "sessions" / "traces" / f"{task.id}.jsonl").exists()


@pytest.mark.asyncio
async def test_validated_task_raises_hand_and_waits_for_invitation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")

    await runtime.ingest_transcript("Robin, make a few slides from the finance files.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    await _wait_for(lambda: runtime.presentation_handoff.state.name == "WAITING_FOR_INVITATION")

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.presentation_ready_at is not None
    assert runtime.presentation_handoff.task_id == task.id
    assert runtime.presentation_handoff.hand_raised is True
    assert runtime.meet.presenting is False
    assert not any(speech.text == "The analysis and slides are ready." for speech in runtime.speech)


@pytest.mark.asyncio
async def test_invitation_lowers_hand_and_presents_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript("Robin, make a few slides from the finance files.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    await _wait_for(lambda: runtime.presentation_handoff.state.name == "WAITING_FOR_INVITATION")

    await runtime.ingest_transcript("Robin, go ahead and share now.", "Avery")

    assert task.status == TaskStatus.COMPLETED
    assert runtime.presentation_handoff.state == PresentationHandoffState.IDLE
    assert runtime.presentation_handoff.hand_raised is False
    assert runtime.meet.presenting is False
    events = runtime.recent_events(300)
    assert sum(1 for event in events if event.type == "presentation.started") == 1
    assert any(event.type == "presentation.invitation.detected" for event in events)
    assert any(event.type == "meeting.hand.lowered" for event in events)


@pytest.mark.asyncio
async def test_visible_meet_caption_recovers_missed_audio_invitation(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)
    page = runtime.meet.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.caption_turns = [
        CaptionTurn("Avery", "Robin, I see your hand is raised. Could you share?")
    ]

    recovered = await runtime._ingest_caption_invitation_once()

    assert recovered is True
    assert task.status == TaskStatus.COMPLETED
    assert runtime.transcript[-1].source == "meet_caption"
    assert runtime.transcript[-1].speaker_name == "Avery"
    assert any(
        event.type == "audio.caption.invitation_fallback" for event in runtime.recent_events(200)
    )


@pytest.mark.asyncio
async def test_meet_caption_fallback_ignores_non_invitation_and_deduplicates(
    tmp_path: Path,
) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)
    page = runtime.meet.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.caption_turns = [CaptionTurn("Avery", "Robin has a deck ready.")]

    first = await runtime._ingest_caption_invitation_once()
    second = await runtime._ingest_caption_invitation_once()

    assert first is False
    assert second is False
    assert runtime.transcript == []
    assert runtime.presentation_handoff.state == PresentationHandoffState.WAITING_FOR_INVITATION


@pytest.mark.asyncio
async def test_stable_meet_caption_recovers_normal_spoken_request(
    tmp_path: Path,
) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    page = runtime.meet.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.caption_turns = [CaptionTurn("Avery", "Older meeting discussion.")]

    baseline = await runtime._ingest_caption_turn_once()
    page.caption_turns.append(CaptionTurn("Avery", "Robin, summarize the finance files."))
    first = await runtime._ingest_caption_turn_once()
    key = ("avery", "robin, summarize the finance files.")
    runtime._caption_candidates[key] -= 1.0
    second = await runtime._ingest_caption_turn_once()

    assert baseline is False
    assert first is False
    assert second is True
    assert runtime.transcript[-1].text == "Robin, summarize the finance files."
    assert runtime.transcript[-1].speaker_name == "Avery"
    assert runtime.transcript[-1].source == "meet_caption"
    assert any(
        event.type == "audio.caption.transcript_fallback" for event in runtime.recent_events(100)
    )


@pytest.mark.asyncio
async def test_stable_meet_caption_ignores_chatter_without_wake_word(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    page = runtime.meet.meet_page
    assert isinstance(page, SimulatedPageDriver)
    page.caption_turns = []
    await runtime._ingest_caption_turn_once()
    page.caption_turns = [CaptionTurn("Avery", "The quarterly review starts tomorrow.")]

    first = await runtime._ingest_caption_turn_once()
    key = ("avery", "the quarterly review starts tomorrow.")
    runtime._caption_candidates[key] -= 1.0
    second = await runtime._ingest_caption_turn_once()

    assert first is False
    assert second is False
    assert runtime.transcript == []


@pytest.mark.asyncio
async def test_non_invitation_turn_leaves_hand_raised(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)

    await runtime.ingest_transcript("Robin has a deck ready.", "Avery")

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert runtime.presentation_handoff.state == PresentationHandoffState.WAITING_FOR_INVITATION
    assert runtime.presentation_handoff.hand_raised is True
    assert runtime.meet.presenting is False


@pytest.mark.asyncio
async def test_runtime_can_relax_wake_word_for_pending_presentation_invitation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(
                base_url="http://127.0.0.1:3000/present",
                require_wake_word_for_invitation=False,
            ),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)

    await runtime.ingest_transcript("Please make another deck later.", "Avery")
    assert len(runtime.tasks) == 1
    assert runtime.presentation_handoff.state == PresentationHandoffState.WAITING_FOR_INVITATION

    await runtime.ingest_transcript("You can share now.", "Avery")

    assert task.status == TaskStatus.COMPLETED
    assert runtime.presentation_handoff.state == PresentationHandoffState.IDLE
    assert not any(
        event.type == "conversation.ignored" and event.payload.get("reason") == "wake_word_missing"
        for event in runtime.recent_events(100)
    )


@pytest.mark.asyncio
async def test_second_ready_task_waits_and_raises_after_first_completes(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    first = _ready_task_with_deck(runtime, "First ready deck")
    second = _ready_task_with_deck(runtime, "Second ready deck")
    assert first.presentation_ready_at and second.presentation_ready_at

    await runtime.request_presentation_floor(first.id, first.revision)
    await runtime.request_presentation_floor(second.id, second.revision)
    assert runtime.presentation_handoff.task_id == first.id

    await runtime.ingest_transcript("Robin, go ahead and share now.", "Avery")

    assert first.status == TaskStatus.COMPLETED
    assert second.status == TaskStatus.READY_TO_PRESENT
    assert runtime.presentation_handoff.state == PresentationHandoffState.WAITING_FOR_INVITATION
    assert runtime.presentation_handoff.task_id == second.id
    assert runtime.presentation_handoff.hand_raised is True


@pytest.mark.asyncio
async def test_duplicate_invitation_segment_cannot_start_twice(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)
    await runtime.ingest_transcript("Robin, go ahead and share now.", "Avery")
    invitation_segment = runtime.transcript[-1]

    await runtime.accept_presentation_invitation(
        invitation_segment,
        MeetingIntent(
            classification="presentation_invitation",
            confidence=1,
            addressed_to_robin=True,
            referenced_task_id=task.id,
        ),
    )

    assert (
        sum(1 for event in runtime.recent_events(200) if event.type == "presentation.started") == 1
    )


@pytest.mark.asyncio
async def test_task_revision_and_cancellation_clear_pending_handoff(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    revised = _ready_task_with_deck(runtime, "Revised deck")
    await runtime.request_presentation_floor(revised.id, revised.revision)

    await runtime.ingest_transcript("Robin, add a sources slide.", "Avery")

    assert revised.revision == 2
    assert revised.presentation_ready_at is None
    assert runtime.presentation_handoff.state == PresentationHandoffState.IDLE

    cancelled = _ready_task_with_deck(runtime, "Cancelled deck")
    await runtime.request_presentation_floor(cancelled.id, cancelled.revision)

    await runtime.cancel_task(cancelled.id)

    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.presentation_ready_at is None
    assert runtime.presentation_handoff.state == PresentationHandoffState.IDLE


@pytest.mark.asyncio
async def test_stale_revision_invitation_is_rejected_without_sharing(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)
    task.revision += 1
    task.presentation_ready_at = None

    await runtime.ingest_transcript("Robin, go ahead and share now.", "Avery")

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert runtime.meet.presenting is False
    assert any(
        event.type == "presentation.invitation.rejected"
        and event.payload.get("reason") == "stale_task_or_revision"
        for event in runtime.recent_events(100)
    )


@pytest.mark.asyncio
async def test_presentation_start_failure_blocks_without_reraising(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task = _ready_task_with_deck(runtime, "Ready deck")
    await runtime.request_presentation_floor(task.id, task.revision)

    async def fail_start(_url: str) -> None:
        raise RuntimeError("share dialog failed")

    runtime.meet.start_presenting = fail_start  # type: ignore[method-assign]

    await runtime.ingest_transcript("Robin, go ahead and share now.", "Avery")

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.outcome_state == TaskOutcomeState.BLOCKED
    assert runtime.presentation_handoff.state == PresentationHandoffState.BLOCKED
    assert runtime.presentation_handoff.error == "share dialog failed"
    assert runtime.presentation_handoff.hand_raised is False


@pytest.mark.asyncio
async def test_duplicate_join_is_idempotent_and_can_enable_listening(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(mode="simulator", capture_loop_interval_ms=10_000),
    )
    runtime = RobinRuntime(settings)
    url = "https://meet.google.com/abc-defg-hij"

    first = await runtime.join_meeting(url)
    second = await runtime.join_meeting(url, start_listening=True)

    assert second.meeting_id == first.meeting_id
    assert second.capture_loop_running is True
    assert sum(1 for event in runtime.recent_events() if event.type == "meeting.join.started") == 1
    assert any(
        event.type == "meeting.join.duplicate_suppressed" for event in runtime.recent_events()
    )
    await runtime.stop_listening_loop()


@pytest.mark.asyncio
async def test_duplicate_join_cannot_promote_waiting_room_to_listening(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    url = "https://meet.google.com/abc-defg-hij"
    runtime.meeting_url = url
    runtime.meeting_state = MeetingState.PREJOIN
    runtime.runtime_state = RuntimeState.JOINING_MEETING

    with pytest.raises(RuntimeError, match="waiting for admission"):
        await runtime.join_meeting(url, start_listening=True)

    assert runtime.meeting_state == MeetingState.PREJOIN
    assert runtime._listen_handle is None


@pytest.mark.asyncio
async def test_caption_text_enriches_realtime_speaker_attribution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    runtime.meet.meet_page = SimulatedPageDriver(
        caption_turns=[
            CaptionTurn("Avery", "Robin, summarize the launch risks and open questions.")
        ]
    )

    speaker, source = await runtime._caption_attribution(
        "Robin summarize the launch risks and open questions"
    )

    assert speaker == "Avery"
    assert source == "merged"
    assert any(event.type == "audio.speaker.attributed" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_unmatched_caption_does_not_invent_speaker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    runtime.meet.meet_page = SimulatedPageDriver(
        caption_turns=[CaptionTurn("Avery", "Completely unrelated discussion")]
    )

    speaker, source = await runtime._caption_attribution("Robin create the quarterly briefing")

    assert speaker == "Meeting audio"
    assert source == "audio_stt"


@pytest.mark.asyncio
async def test_addressed_voice_check_gets_an_audible_reply_without_creating_task(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            audio=AudioConfig(mode="simulator"),
        )
    )

    await runtime.ingest_transcript(
        "Robin, can you hear me?",
        speaker_name="Meeting audio",
        source="audio_stt",
    )

    assert runtime.tasks == []
    assert runtime.speech[-1].text.startswith("Yes, I can hear you.")
    assert any(event.type == "conversation.addressed" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_unaddressed_meeting_turn_is_transcribed_but_never_answered(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            audio=AudioConfig(mode="simulator"),
        )
    )

    await runtime.ingest_transcript(
        "Please make slides from the finance files.",
        speaker_name="Avery",
        source="audio_stt",
    )

    assert runtime.transcript[-1].text == "Please make slides from the finance files."
    assert runtime.tasks == []
    assert runtime.speech == []
    ignored = [event for event in runtime.recent_events() if event.type == "conversation.ignored"]
    assert ignored[-1].payload["reason"] == "wake_word_missing"


def test_barge_in_requires_wake_word_and_rejects_robin_echo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    runtime.meeting_state = MeetingState.SPEAKING
    runtime._active_spoken_text = "Robin voice check. If you can hear this, audio is working."

    assert runtime._should_accept_barge_in("Robin voice check") is False
    assert runtime._should_accept_barge_in("Please stop") is False

    runtime._active_spoken_text = "The analysis and slides are ready."
    assert runtime._should_accept_barge_in("Robin, stop") is True


@pytest.mark.asyncio
async def test_grounded_question_uses_validated_artifact_sources(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.ingest_transcript(
        "Robin, compare the quarterly finance results and make slides.", "Avery"
    )
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    await runtime.ingest_transcript("Robin, what sources did you use?", "Avery")

    assert len(runtime.tasks) == 1
    assert any(
        "finance_2024_quarterly_results" in speech.text or "finance.csv" in speech.text
        for speech in runtime.speech
    )
    assert any(event.type == "conversation.addressed" for event in runtime.recent_events())


def test_slide_narration_is_bounded_for_meeting_delivery(tmp_path: Path) -> None:
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=tmp_path / "workspace"),
            database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"),
        )
    )

    narration = runtime._spoken_excerpt("A complete sentence. " + "detail " * 100)

    assert len(narration) <= 260
    assert narration == "A complete sentence."


@pytest.mark.asyncio
async def test_task_work_starts_without_waiting_for_acknowledgement(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        runtime=RuntimeConfig(max_concurrent_tasks=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    speech_started = asyncio.Event()
    release_speech = asyncio.Event()

    async def slow_speech(_text: str) -> None:
        speech_started.set()
        await release_speech.wait()

    runtime._safe_acknowledge = slow_speech  # type: ignore[method-assign]
    await runtime.task_slots.acquire()

    task = await asyncio.wait_for(runtime.create_task("Make finance slides."), timeout=0.2)
    await asyncio.wait_for(speech_started.wait(), timeout=0.2)
    for _ in range(20):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.01)

    assert task.status == TaskStatus.QUEUED
    release_speech.set()
    handle = runtime._task_handles[task.id]
    handle.cancel()
    runtime.task_slots.release()
    await handle


@pytest.mark.asyncio
async def test_presentation_navigation_state_is_clamped(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    await runtime.ingest_transcript("Robin, make a few slides from the finance files.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    state = runtime.activate_presentation(task.id)
    assert state.active is True
    assert state.slide_count >= 3

    await runtime.navigate_presentation(task.id, "next")
    moved = await runtime.navigate_presentation(task.id, "goto", index=999)

    assert moved.active_slide == moved.slide_count - 1
    assert runtime.snapshot().presentations[-1].task_id == task.id


@pytest.mark.asyncio
async def test_open_stale_presentation_tab_cannot_reactivate_or_advance(tmp_path: Path) -> None:
    runtime = _runtime_for_handoff_fixture(tmp_path)
    task = _ready_task_with_deck(runtime, "Ready deck")

    state = runtime.presentation_state(task.id)

    assert state.active is False
    with pytest.raises(RuntimeError, match="not active"):
        await runtime.navigate_presentation(task.id, "next")
    assert state.active_slide == 0


@pytest.mark.asyncio
async def test_stop_presenting_deactivates_presentation_session(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript("Robin, make a few slides from the finance files.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    slide_count = runtime._deck_slide_count(task.id)
    speech_before = len(runtime.speech)
    assert isinstance(runtime.meet.meet_page, SimulatedPageDriver)
    page = runtime.meet.meet_page
    unmute_before = page.clicked.count("unmute_button")
    mute_before = page.clicked.count("mute_button")
    route_events_before = len(runtime.meet.speech_route_events or [])
    prefetch_started = asyncio.Event()
    original_prepare = runtime.audio.prepare_speech
    original_start_presenting = runtime.meet.start_presenting

    async def prepare_and_signal(text: str):
        prefetch_started.set()
        return await original_prepare(text)

    async def start_presenting_after_prefetch_started(url: str) -> None:
        assert prefetch_started.is_set()
        await original_start_presenting(url)

    runtime.audio.prepare_speech = prepare_and_signal  # type: ignore[method-assign]
    runtime.meet.start_presenting = start_presenting_after_prefetch_started  # type: ignore[method-assign]

    await runtime.present_task(task.id)
    stopped = await runtime.stop_presenting(task.id)

    assert stopped.presenting is False
    assert runtime.presentations[task.id].active is False
    assert len(runtime.speech) >= speech_before + slide_count
    presented_speech = runtime.speech[speech_before:]
    assert len(presented_speech) == slide_count
    assert {speech.source for speech in presented_speech} == {"prefetched"}
    assert page.clicked.count("unmute_button") == unmute_before + 1
    assert page.clicked.count("mute_button") == mute_before + 1
    route_events = runtime.meet.speech_route_events or []
    presentation_route_completions = [
        event
        for event in route_events[route_events_before:]
        if event.type == "speech.route_prepare.completed"
    ]
    assert len(presentation_route_completions) == 1
    assert presentation_route_completions[0].cache_status == "hit"
    assert runtime.meet.muted is True
    assert any("Revenue increased" in speech.text for speech in runtime.speech)
    assert any("Key metrics:" in speech.text for speech in runtime.speech)
    assert (
        sum(1 for event in runtime.recent_events(200) if event.type == "presentation.narration")
        >= slide_count
    )
    trace_types = {event.type for event in runtime.recent_events(400)}
    assert "speech.route_prepare.started" in trace_types
    assert "speech.route_prepare.completed" in trace_types
    assert "speech.unmute.started" in trace_types
    assert "speech.unmute.completed" in trace_types
    assert "speech.synthesis.started" in trace_types
    assert "speech.playback.started" in trace_types
    assert "speech.playback.completed" in trace_types
    assert "presentation.slide.started" in trace_types
    assert "presentation.slide.completed" in trace_types
    assert "presentation.narration.prefetch_started" in trace_types
    assert any(event.type == "presentation.stopped" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_prefetch_failure_falls_back_and_later_slides_stay_prepared(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task_id = uuid4()
    runtime.presentations[task_id] = PresentationSession(
        task_id=task_id,
        active=True,
        slide_count=2,
    )
    deck = DeckSpec(
        task_id=task_id,
        revision=1,
        title="Fallback deck",
        slides=[
            SlideSpec(type="title", title="One", body=["First"]),
            SlideSpec(type="executive_summary", title="Two", body=["Second"]),
        ],
        sources=[],
    )
    original_prepare = runtime.audio.prepare_speech

    async def prepare_with_one_failure(text: str):
        if text == "first narration":
            partial = runtime.audio.output_dir / "partial.wav"
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(b"partial")
            return PreparedSpeech(
                text=text,
                path=partial,
                model="model",
                voice="voice",
                format="wav",
                mode="simulator",
                error="synthetic failure",
            )
        return await original_prepare(text)

    runtime.audio.prepare_speech = prepare_with_one_failure  # type: ignore[method-assign]
    narrations = ["first narration", "second narration"]
    prefetch = NarrationPrefetchCoordinator(
        runtime.audio,
        [NarrationItem(index, text) for index, text in enumerate(narrations)],
        concurrency=2,
    )
    prefetch.start()

    try:
        await runtime._narrate_deck(task_id, deck, narrations, prefetch)
    finally:
        await prefetch.close()

    assert [speech.source for speech in runtime.speech[-2:]] == ["fallback", "prefetched"]
    assert any(
        event.type == "presentation.narration.prefetch_failed"
        for event in runtime.recent_events(200)
    )
    assert not (runtime.audio.output_dir / "partial.wav").exists()


@pytest.mark.asyncio
async def test_disabled_prefetch_uses_streaming_narration_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(
                base_url="http://127.0.0.1:3000/present",
                narration_prefetch_enabled=False,
            ),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task_id = uuid4()
    runtime.presentations[task_id] = PresentationSession(
        task_id=task_id,
        active=True,
        slide_count=1,
    )
    deck = DeckSpec(
        task_id=task_id,
        revision=1,
        title="Streaming deck",
        slides=[SlideSpec(type="title", title="One", body=["First"])],
        sources=[],
    )

    await runtime._narrate_deck(task_id, deck, ["streaming narration"], prefetch=None)

    assert runtime.speech[-1].source == "streamed"
    assert not any(
        event.type == "presentation.narration.prefetch_started"
        for event in runtime.recent_events(100)
    )


@pytest.mark.asyncio
async def test_deck_narration_failure_mutes_microphone(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task_id = uuid4()
    runtime.presentations[task_id] = PresentationSession(
        task_id=task_id,
        active=True,
        slide_count=1,
    )
    deck = DeckSpec(
        task_id=task_id,
        revision=1,
        title="Failure deck",
        slides=[SlideSpec(type="title", title="One", body=["First"])],
        sources=[],
    )

    async def fail_speech(_text: str) -> SpeechRecord:
        raise RuntimeError("playback failed")

    runtime.audio.speak = fail_speech  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="playback failed"):
        await runtime._narrate_deck(task_id, deck, ["failing narration"], prefetch=None)

    assert runtime.meet.muted is True


@pytest.mark.asyncio
async def test_deck_narration_cancellation_mutes_microphone(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task_id = uuid4()
    runtime.presentations[task_id] = PresentationSession(
        task_id=task_id,
        active=True,
        slide_count=1,
    )
    deck = DeckSpec(
        task_id=task_id,
        revision=1,
        title="Cancellation deck",
        slides=[SlideSpec(type="title", title="One", body=["First"])],
        sources=[],
    )
    speech_started = asyncio.Event()

    async def hang_speech(_text: str) -> SpeechRecord:
        speech_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    runtime.audio.speak = hang_speech  # type: ignore[method-assign]
    handle = asyncio.create_task(
        runtime._narrate_deck(task_id, deck, ["cancellable narration"], prefetch=None)
    )
    await speech_started.wait()

    handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle

    assert runtime.meet.muted is True


@pytest.mark.asyncio
async def test_interrupted_deck_narration_stops_subsequent_slides(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    task_id = uuid4()
    runtime.presentations[task_id] = PresentationSession(
        task_id=task_id,
        active=True,
        slide_count=2,
    )
    deck = DeckSpec(
        task_id=task_id,
        revision=1,
        title="Interrupted deck",
        slides=[
            SlideSpec(type="title", title="One", body=["First"]),
            SlideSpec(type="executive_summary", title="Two", body=["Second"]),
        ],
        sources=[],
    )

    async def interrupted_speech(text: str) -> SpeechRecord:
        return SpeechRecord(
            text=text,
            mode="simulator",
            voice="alloy",
            model="simulator",
            format="wav",
            interrupted=True,
        )

    runtime.audio.speak = interrupted_speech  # type: ignore[method-assign]

    await runtime._narrate_deck(
        task_id,
        deck,
        ["interrupted narration", "should not play"],
        prefetch=None,
    )

    assert [speech.text for speech in runtime.speech[-1:]] == ["interrupted narration"]
    assert runtime.presentations[task_id].active_slide == 0
    assert any(
        event.type == "presentation.narration.interrupted" for event in runtime.recent_events(100)
    )
    assert runtime.meet.muted is True


@pytest.mark.asyncio
async def test_failed_narration_cleans_up_presentation_and_restores_ready_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript("Robin, make a few slides from the finance files.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    async def fail_narration(*_args: object) -> None:
        raise RuntimeError("narration failed")

    runtime._narrate_deck = fail_narration  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="narration failed"):
        await runtime.present_task(task.id)

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.error == "narration failed"
    assert task.outcome_state == TaskOutcomeState.BLOCKED
    assert runtime.meet.presenting is False
    assert runtime.presentations[task.id].active is False
    assert any(event.type == "presentation.failed" for event in runtime.recent_events())
    assert any(event.type == "presentation.stopped" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_leave_meeting_clears_stale_presenting_task_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript("Robin, make a few slides from the finance files.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    runtime.activate_presentation(task.id)
    task.status = TaskStatus.PRESENTING
    runtime.meet.presenting = True

    await runtime.leave_meeting()

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert runtime.presentations[task.id].active is False
    assert runtime.meet.presenting is False


@pytest.mark.asyncio
async def test_retry_failed_task_reschedules_work(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    task = await runtime.create_task("Use the finance files to make slides.")
    await runtime._task_handles[task.id]
    assert task.status == TaskStatus.FAILED
    assert any(
        speech.text.startswith("I could not complete Use the finance files")
        for speech in runtime.speech
    )
    assert any("No CSV or XLSX finance data" in speech.text for speech in runtime.speech)

    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    await runtime.retry_task(task.id)
    await runtime._task_handles[task.id]

    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.revision == 2
    assert task.presentation_ready_at is not None
    assert any(event.type == "task.retry" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_task_waiting_for_concurrency_slot_is_marked_queued(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    settings = Settings(
        runtime=RuntimeConfig(max_concurrent_tasks=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    await runtime.task_slots.acquire()
    task = await runtime.create_task("Use the finance files to make queued slides.")

    for _ in range(20):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.01)

    assert task.status == TaskStatus.QUEUED
    assert any(event.type == "task.queued" for event in runtime.recent_events())
    runtime.task_slots.release()
    await runtime._task_handles[task.id]
    assert task.status == TaskStatus.READY_TO_PRESENT


@pytest.mark.asyncio
async def test_queued_task_can_be_cancelled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n2024,Q1,actual,100,70,30\n"
    )
    settings = Settings(
        runtime=RuntimeConfig(max_concurrent_tasks=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    await runtime.task_slots.acquire()
    task = await runtime.create_task("Use the finance files to make queued slides.")
    for _ in range(20):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.01)

    await runtime.cancel_task(task.id)
    runtime.task_slots.release()
    await runtime._task_handles[task.id]

    assert task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_duplicate_direct_task_request_returns_existing_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    settings = Settings(
        runtime=RuntimeConfig(max_concurrent_tasks=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    await runtime.task_slots.acquire()

    task = await runtime.create_task("Use the finance files to make duplicate slides.")
    for _ in range(20):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.01)
    duplicate = await runtime.create_task("  use   the finance files to make duplicate slides.  ")

    assert duplicate.id == task.id
    assert len(runtime.tasks) == 1
    assert list(runtime._task_handles) == [task.id]
    assert any(event.type == "task.duplicate_suppressed" for event in runtime.recent_events())

    runtime.task_slots.release()
    await runtime._task_handles[task.id]
    assert task.status == TaskStatus.READY_TO_PRESENT


@pytest.mark.asyncio
async def test_duplicate_transcript_request_does_not_create_second_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    settings = Settings(
        runtime=RuntimeConfig(max_concurrent_tasks=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    await runtime.task_slots.acquire()
    text = "Robin, use the finance files to make duplicate transcript slides."

    await runtime.ingest_transcript(text, "Avery")
    task = runtime.tasks[-1]
    for _ in range(20):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.01)
    await runtime.ingest_transcript(f"  {text}  ", "Avery")

    assert len(runtime.tasks) == 1
    assert runtime.tasks[0].id == task.id
    assert any(event.type == "task.duplicate_suppressed" for event in runtime.recent_events())

    runtime.task_slots.release()
    await runtime._task_handles[task.id]
    assert task.status == TaskStatus.READY_TO_PRESENT


@pytest.mark.asyncio
async def test_follow_up_preserves_revisioned_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
        "2024,Q4,forecast,200,120,80\n"
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    await runtime.ingest_transcript(
        "Robin, use the finance files to compare our 2024 quarterly results and make a few slides.",
        "Avery",
    )
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    await runtime.ingest_transcript(
        "Robin, add operating margin and use actuals instead of forecasts.", "Blair"
    )
    await runtime._task_handles[task.id]

    assert task.revision == 2
    assert len(task.source_context_segment_ids) == 2
    assert task.source_context_segment_ids[-1] == runtime.transcript[-1].id
    decks = sorted(
        (
            artifact
            for artifact in runtime.artifacts
            if artifact.task_id == task.id and artifact.type == "deck_json"
        ),
        key=lambda artifact: artifact.revision,
    )
    pptx_decks = sorted(
        (
            artifact
            for artifact in runtime.artifacts
            if artifact.task_id == task.id and artifact.type == "deck_pptx"
        ),
        key=lambda artifact: artifact.revision,
    )
    validations = sorted(
        (
            artifact
            for artifact in runtime.artifacts
            if artifact.task_id == task.id and artifact.type == "validation_json"
        ),
        key=lambda artifact: artifact.revision,
    )
    assert [artifact.revision for artifact in decks] == [1, 2]
    assert [artifact.revision for artifact in pptx_decks] == [1, 2]
    assert [artifact.revision for artifact in validations] == [1, 2]
    assert decks[0].path.endswith("deck_v1.json")
    assert decks[1].path.endswith("deck_v2.json")
    assert pptx_decks[0].path.endswith("deck_v1.pptx")
    assert pptx_decks[1].path.endswith("deck_v2.pptx")
    assert decks[1].url == f"http://127.0.0.1:3000/present/{task.id}?revision=2"
    assert runtime._deck_slide_count(task.id) >= 3


@pytest.mark.asyncio
async def test_follow_up_targets_most_recent_active_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
        "2024,Q4,forecast,200,120,80\n"
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    older = await runtime.create_task("Use the finance files to make an older deck.")
    await runtime._task_handles[older.id]
    newer = await runtime.create_task("Use the finance files to make the current deck.")
    await runtime._task_handles[newer.id]

    await runtime.ingest_transcript(
        "Robin, add operating margin and use actuals instead of forecasts.", "Blair"
    )
    await runtime._task_handles[newer.id]

    assert older.revision == 1
    assert newer.revision == 2
    assert any(
        artifact.task_id == newer.id
        and artifact.revision == 2
        and artifact.path.endswith("deck_v2.json")
        for artifact in runtime.artifacts
    )


@pytest.mark.asyncio
async def test_ambiguous_request_requires_confirmation_before_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)

    async def ambiguous_intent(_text: str, _active: list[RobinTask]) -> MeetingIntent:
        return MeetingIntent(
            classification="possible_task",
            confidence=0.65,
            addressed_to_robin=True,
            task_title="Compare finance files",
            requested_outcome="Compare the finance files and make slides",
            should_ask_confirmation=True,
            clarification_question="Should I take that on?",
        )

    runtime.intent.classify = ambiguous_intent  # type: ignore[method-assign]

    await runtime.ingest_transcript(
        "Robin, could someone compare the finance files and make slides?", "Avery"
    )

    assert len(runtime.tasks) == 1
    pending = runtime.tasks[-1]
    assert pending.status == TaskStatus.AWAITING_CLARIFICATION
    assert pending.outcome_state == TaskOutcomeState.AWAITING_CONFIRMATION
    assert pending.request_text == "Robin, could someone compare the finance files and make slides?"
    assert runtime.speech[-1].text == "Should I take that on?"
    assert any(event.type == "clarification.requested" for event in runtime.recent_events())
    assert any(event.type == "task.awaiting_clarification" for event in runtime.recent_events())

    await runtime.ingest_transcript("Robin, yes, please do.", "Blair")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    assert task.id == pending.id
    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.request_text == "Robin, could someone compare the finance files and make slides?"
    assert task.source_context_segment_ids[-1] == runtime.transcript[-1].id
    assert any(event.type == "clarification.accepted" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_declined_ambiguous_request_cancels_pending_task(tmp_path: Path) -> None:
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path / "workspace"),
        database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"),
    )
    runtime = RobinRuntime(settings)

    async def ambiguous_intent(_text: str, _active: list[RobinTask]) -> MeetingIntent:
        return MeetingIntent(
            classification="possible_task",
            confidence=0.65,
            addressed_to_robin=True,
            task_title="Finance slide",
            requested_outcome="Make a finance slide",
            should_ask_confirmation=True,
            clarification_question="Should I take that on?",
        )

    runtime.intent.classify = ambiguous_intent  # type: ignore[method-assign]

    await runtime.ingest_transcript("Robin, could someone make a finance slide?", "Avery")
    task = runtime.tasks[-1]
    assert task.status == TaskStatus.AWAITING_CLARIFICATION
    await runtime.ingest_transcript("Robin, no, ignore that.", "Blair")

    assert len(runtime.tasks) == 1
    assert task.status == TaskStatus.CANCELLED
    assert task.outcome_state == TaskOutcomeState.CANCELLED
    assert task.source_context_segment_ids[-1] == runtime.transcript[-1].id
    assert runtime.speech[-1].text == "Okay, I will leave that alone."
    assert any(event.type == "clarification.declined" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_workspace_rejects_bad_meet_url(tmp_path: Path) -> None:
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path / "workspace"),
        database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    with pytest.raises(ValueError):
        await runtime.join_meeting("https://example.com/not-meet")


@pytest.mark.asyncio
async def test_workspace_reindex_persists_files_and_emits_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "finance.csv").write_text("quarter,revenue,operating_income\nQ1,100,25\n")
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
    )
    runtime = RobinRuntime(settings)

    snapshot = await runtime.reindex_workspace()
    record = runtime.workspace_file(snapshot.files[0].id)

    assert snapshot.file_count == 1
    assert record.relative_path == "source-data/finance.csv"
    assert any(event.type == "workspace.reindexed" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_validation_failure_blocks_presentation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "sessions").mkdir()
    (workspace / "cache").mkdir()
    (source / "finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income,operating_margin\n"
        "2024,Q1,actual,100,70,30,0.99\n"
        "2024,Q2,actual,120,80,40,0.99\n"
        "2024,Q3,actual,150,95,55,0.99\n"
        "2024,Q4,actual,180,110,70,0.99\n"
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    task = await runtime.create_task("Use the finance files to make a short presentation.")
    await runtime._task_handles[task.id]

    assert task.status == TaskStatus.FAILED
    assert task.error == "Validation failed: operating_margin_formula"
    assert task.outcome_state == TaskOutcomeState.FAILED
    assert (
        runtime.speech[-1].text
        == "I found a validation issue in the analysis, so I will not present it yet."
    )
    validation = next(
        artifact
        for artifact in runtime.artifacts
        if artifact.task_id == task.id and artifact.type == "validation_json"
    )
    report = ValidationReport.model_validate_json(
        runtime.artifact_path(validation.path).read_text()
    )
    assert report.ok is False
    assert any(check.name == "operating_margin_formula" and not check.ok for check in report.checks)


def _write_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def _runtime_for_handoff_fixture(tmp_path: Path) -> RobinRuntime:
    workspace = tmp_path / "workspace"
    return RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )


def _ready_task_with_deck(runtime: RobinRuntime, title: str) -> RobinTask:
    task = RobinTask(
        meeting_id=runtime.meeting_id,
        title=title,
        status=TaskStatus.READY_TO_PRESENT,
        request_text=title,
        requested_outcome=title,
        outcome_state=TaskOutcomeState.VERIFIED,
        outcome_detail="Grounding, citations, and artifact validation passed.",
        presentation_ready_at=now_utc(),
    )
    runtime.tasks.append(task)
    runtime.store.upsert("task", task)
    deck = DeckSpec(
        task_id=task.id,
        revision=task.revision,
        title=title,
        slides=[
            SlideSpec(type="title", title=title, body=["Ready to present."]),
            SlideSpec(type="sources", title="Sources"),
        ],
        sources=[SourceCitation(label="fixture", path="source-data/fixture.csv", note="fixture")],
    )
    relative_path = Path("generated") / str(task.id) / f"deck_v{task.revision}.json"
    path = runtime.workspace.resolve(relative_path.as_posix())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(deck.model_dump_json(), encoding="utf-8")
    artifact = Artifact(
        task_id=task.id,
        revision=task.revision,
        type="deck_json",
        path=relative_path.as_posix(),
        url=f"{runtime.settings.presentation.base_url}/{task.id}?revision={task.revision}",
    )
    runtime.artifacts.append(artifact)
    runtime.store.upsert("artifact", artifact)
    return task


async def _wait_for(predicate, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()
