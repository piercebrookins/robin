from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.memory import MeetingMemoryManager
from robin_core.runtime import RobinRuntime
from robin_core.schemas import MeetingMemoryItem, TranscriptSegment


def segment(text: str, speaker: str = "Avery") -> TranscriptSegment:
    return TranscriptSegment(
        meeting_id=uuid4(),
        speaker_name=speaker,
        text=text,
        started_at_ms=0,
        ended_at_ms=100,
    )


@pytest.mark.asyncio
async def test_local_memory_extracts_decision_owner_deadline_and_provenance() -> None:
    turn = segment("We decided to ship the accessibility fix, assigned to Morgan by Friday.")
    additions, resolutions = await MeetingMemoryManager(Settings()).extract(turn, [])

    assert resolutions == []
    decision = next(item for item in additions if item.kind == "decision")
    assert decision.owner == "Morgan"
    assert decision.deadline.casefold() == "friday"
    assert decision.source_segment_ids == [turn.id]


def test_memory_merge_deduplicates_and_resolves_existing_items() -> None:
    meeting_id = uuid4()
    original = MeetingMemoryItem(
        meeting_id=meeting_id,
        kind="question",
        text="Who owns launch approval?",
    )
    duplicate = MeetingMemoryItem(
        meeting_id=meeting_id,
        kind="question",
        text="  who OWNS launch approval? ",
    )

    merged = MeetingMemoryManager.merge([original], [duplicate], [str(original.id)])

    assert len(merged) == 1
    assert merged[0].status == "resolved"


@pytest.mark.asyncio
async def test_explicit_correction_deterministically_resolves_overlapping_decision() -> None:
    meeting_id = uuid4()
    original_turn = TranscriptSegment(
        meeting_id=meeting_id,
        speaker_name="Avery",
        text="We decided Morgan owns the launch checklist and it is due Friday.",
        started_at_ms=0,
        ended_at_ms=100,
    )
    manager = MeetingMemoryManager(Settings())
    existing, _ = await manager.extract(original_turn, [])
    decision = next(item for item in existing if item.kind == "decision")
    correction = TranscriptSegment(
        meeting_id=meeting_id,
        speaker_name="Avery",
        text="Correction: cancel that prior ownership decision. Taylor now owns the launch checklist, due Monday.",
        started_at_ms=200,
        ended_at_ms=300,
    )

    additions, resolved = await manager.extract(correction, existing)
    manager.merge(existing, additions, resolved)

    assert str(decision.id) in resolved
    assert decision.status == "resolved"


@pytest.mark.asyncio
async def test_unrelated_correction_does_not_resolve_existing_decision() -> None:
    meeting_id = uuid4()
    decision = MeetingMemoryItem(
        meeting_id=meeting_id,
        kind="decision",
        text="Morgan owns the launch checklist.",
    )
    correction = TranscriptSegment(
        meeting_id=meeting_id,
        speaker_name="Avery",
        text="Correction: the catering headcount is twenty people.",
        started_at_ms=200,
        ended_at_ms=300,
    )

    _additions, resolved = await MeetingMemoryManager(Settings()).extract(
        correction, [decision]
    )

    assert resolved == []


@pytest.mark.asyncio
async def test_runtime_persists_memory_across_restart(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=root),
        database=DatabaseConfig(path=root / "robin.db"),
    )
    runtime = RobinRuntime(settings)

    await runtime.ingest_transcript(
        "We decided to use the revised launch plan.", speaker_name="Casey"
    )
    await asyncio.gather(*runtime._memory_handles)

    assert any(item.kind == "decision" for item in runtime.meeting_memory)
    restarted = RobinRuntime(settings)
    saved = next(item for item in restarted.meeting_memory if item.kind == "decision")
    assert saved.speaker_name == "Casey"
    assert saved.source_segment_ids
