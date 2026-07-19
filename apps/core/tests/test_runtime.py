from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import fitz
import pytest

from robin_core.config import (
    AudioConfig,
    DatabaseConfig,
    PresentationConfig,
    RuntimeConfig,
    Settings,
    WorkspaceConfig,
)
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus, ValidationReport


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
    _write_pdf(source / "finance_context.pdf", "Finance context report: 2024 growth improved through Q4 and actuals are preferred for board reporting.")
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
    )
    runtime = RobinRuntime(settings)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript("Robin, use the finance files to compare our 2024 quarterly results and make a few slides.", "Avery")
    task = runtime.tasks[-1]
    handle = runtime._task_handles[task.id]
    await handle
    assert task.status == TaskStatus.READY_TO_PRESENT
    assert any(artifact.type == "deck_json" for artifact in runtime.artifacts)
    deck_artifact = next(artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "deck_json")
    pptx_artifact = next(artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "deck_pptx")
    deck_json = runtime.artifact_path(deck_artifact.path).read_text()
    assert "finance_context.pdf" in deck_json
    assert "2024 growth improved through Q4" in deck_json
    assert pptx_artifact.path.endswith("deck_v1.pptx")
    with zipfile.ZipFile(runtime.artifact_path(pptx_artifact.path)) as archive:
        assert "ppt/presentation.xml" in archive.namelist()
    validation = next(artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "validation_json")
    report = ValidationReport.model_validate_json(runtime.artifact_path(validation.path).read_text())
    assert report.ok is True
    assert {check.name for check in report.checks} >= {"operating_margin_formula", "chart_revenue_series", "lineage_present", "source_citations_present"}
    assert "source-data/finance_context.pdf" in report.source_paths
    metrics = runtime.metrics()
    assert metrics.task_count >= 1
    assert metrics.artifact_count >= 3
    assert metrics.speech_count >= 1
    assert any(event.type == "task.completed" for event in runtime.recent_events())
    assert (workspace / "sessions" / "traces" / f"{task.id}.jsonl").exists()


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
        event.type == "meeting.join.duplicate_suppressed"
        for event in runtime.recent_events()
    )
    await runtime.stop_listening_loop()


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

    await runtime.present_task(task.id)
    stopped = await runtime.stop_presenting(task.id)

    assert stopped.presenting is False
    assert runtime.presentations[task.id].active is False
    assert len(runtime.speech) >= speech_before + slide_count
    assert any("Revenue increased" in speech.text for speech in runtime.speech)
    assert any("Key metrics:" in speech.text for speech in runtime.speech)
    assert sum(1 for event in runtime.recent_events(200) if event.type == "presentation.narration") >= slide_count
    assert any(event.type == "presentation.stopped" for event in runtime.recent_events())


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
    assert any(speech.text.startswith("I could not complete Use the finance files") for speech in runtime.speech)
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
    assert any(event.type == "task.retry" for event in runtime.recent_events())
    assert any(speech.text == "The analysis and slides are ready." for speech in runtime.speech)


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
    (source / "finance.csv").write_text("year,quarter,scenario,revenue,expenses,operating_income\n2024,Q1,actual,100,70,30\n")
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
    await runtime.ingest_transcript("Robin, use the finance files to compare our 2024 quarterly results and make a few slides.", "Avery")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    await runtime.ingest_transcript("Robin, add operating margin and use actuals instead of forecasts.", "Blair")
    await runtime._task_handles[task.id]

    assert task.revision == 2
    assert len(task.source_context_segment_ids) == 2
    assert task.source_context_segment_ids[-1] == runtime.transcript[-1].id
    decks = sorted((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "deck_json"), key=lambda artifact: artifact.revision)
    pptx_decks = sorted((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "deck_pptx"), key=lambda artifact: artifact.revision)
    validations = sorted((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "validation_json"), key=lambda artifact: artifact.revision)
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

    await runtime.ingest_transcript("Robin, add operating margin and use actuals instead of forecasts.", "Blair")
    await runtime._task_handles[newer.id]

    assert older.revision == 1
    assert newer.revision == 2
    assert any(artifact.task_id == newer.id and artifact.revision == 2 and artifact.path.endswith("deck_v2.json") for artifact in runtime.artifacts)


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

    await runtime.ingest_transcript("Could someone compare the finance files and make slides?", "Avery")

    assert len(runtime.tasks) == 1
    pending = runtime.tasks[-1]
    assert pending.status == TaskStatus.AWAITING_CLARIFICATION
    assert pending.request_text == "Could someone compare the finance files and make slides?"
    assert runtime.speech[-1].text == "Should I take that on?"
    assert any(event.type == "clarification.requested" for event in runtime.recent_events())
    assert any(event.type == "task.awaiting_clarification" for event in runtime.recent_events())

    await runtime.ingest_transcript("Yes, please do.", "Blair")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]

    assert task.id == pending.id
    assert task.status == TaskStatus.READY_TO_PRESENT
    assert task.request_text == "Could someone compare the finance files and make slides?"
    assert task.source_context_segment_ids[-1] == runtime.transcript[-1].id
    assert any(event.type == "clarification.accepted" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_declined_ambiguous_request_cancels_pending_task(tmp_path: Path) -> None:
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path / "workspace"),
        database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"),
    )
    runtime = RobinRuntime(settings)

    await runtime.ingest_transcript("Could someone make a finance slide?", "Avery")
    task = runtime.tasks[-1]
    assert task.status == TaskStatus.AWAITING_CLARIFICATION
    await runtime.ingest_transcript("No, ignore that.", "Blair")

    assert len(runtime.tasks) == 1
    assert task.status == TaskStatus.CANCELLED
    assert task.source_context_segment_ids[-1] == runtime.transcript[-1].id
    assert runtime.speech[-1].text == "Okay, I will leave that alone."
    assert any(event.type == "clarification.declined" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_workspace_rejects_bad_meet_url(tmp_path: Path) -> None:
    settings = Settings(workspace=WorkspaceConfig(root=tmp_path / "workspace"), database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"))
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
    assert runtime.speech[-1].text == "I found a validation issue in the analysis, so I will not present it yet."
    validation = next(artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "validation_json")
    report = ValidationReport.model_validate_json(runtime.artifact_path(validation.path).read_text())
    assert report.ok is False
    assert any(check.name == "operating_margin_formula" and not check.ok for check in report.checks)


def _write_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
