from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from robin_core.schemas import TranscriptSegment


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "smoke_real_meet.py"
SPEC = importlib.util.spec_from_file_location("smoke_real_meet", SCRIPT_PATH)
assert SPEC and SPEC.loader
smoke_real_meet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_real_meet)


class FakeRuntime:
    def __init__(self, transcript: list[TranscriptSegment]) -> None:
        self.transcript = transcript


@pytest.mark.asyncio
async def test_wait_for_phrase_requires_audio_stt_source() -> None:
    meeting_id = uuid4()
    runtime = FakeRuntime(
        [
            TranscriptSegment(
                meeting_id=meeting_id,
                speaker_name="Dashboard",
                text="Robin audio verification phrase abc123",
                started_at_ms=0,
                ended_at_ms=1,
                source="simulator",
            ),
            TranscriptSegment(
                meeting_id=meeting_id,
                speaker_name="Meeting audio",
                text="Robin audio verification phrase abc123",
                started_at_ms=2,
                ended_at_ms=3,
                source="audio_stt",
            ),
        ]
    )

    segment = await smoke_real_meet.wait_for_phrase(
        runtime,
        "Robin audio verification phrase abc123",
        timeout_s=0.01,
    )

    assert segment.source == "audio_stt"


def test_confirm_reply_heard_accepts_environment_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("ROBIN_REAL_MEET_REPLY_CONFIRMED", "yes")

    assert smoke_real_meet.confirm_reply_heard("Robin audio reply abc123 complete.", False)


def test_normalize_ignores_case_and_punctuation() -> None:
    assert smoke_real_meet.normalize("Robin, AUDIO!") == "robin audio"


def healthy_evidence() -> dict:
    return {
        "participant_transcript": {
            "source": "audio_stt",
            "suppressed": False,
            "text": "Robin audio verification phrase abc123",
        },
        "cleanup_action": "emergency-stop",
        "reply_confirmed_by_second_participant": True,
        "audio_before_cleanup": {
            "capture_state": "capturing",
            "transcription_state": "connected",
            "last_frame_sequence": 42,
            "last_frame_timestamp_ms": 12_345,
            "received_frame_count": 42,
        },
        "cleanup_elapsed_ms": 150,
        "audio_after_cleanup": {
            "capture_state": "idle",
            "transcription_state": "idle",
            "playback_state": "idle",
        },
        "muted_after_cleanup": True,
        "transcription_session_active_after_cleanup": False,
        "bridge_process_alive_after_cleanup": False,
        "recent_events": [{"type": "runtime.emergency_stop"}],
    }


def test_validate_smoke_evidence_accepts_real_audio_cleanup() -> None:
    smoke_real_meet.validate_smoke_evidence(healthy_evidence())


def test_validate_smoke_evidence_accepts_leave_cleanup() -> None:
    evidence = healthy_evidence()
    evidence["cleanup_action"] = "leave"
    evidence["recent_events"] = [{"type": "meeting.leave.cleanup"}]

    smoke_real_meet.validate_smoke_evidence(evidence)


def test_validate_smoke_evidence_rejects_non_audio_transcript() -> None:
    evidence = healthy_evidence()
    evidence["participant_transcript"]["source"] = "simulator"

    with pytest.raises(SystemExit, match="audio STT"):
        smoke_real_meet.validate_smoke_evidence(evidence)


def test_validate_smoke_evidence_rejects_missing_live_frame() -> None:
    evidence = healthy_evidence()
    evidence["audio_before_cleanup"]["last_frame_timestamp_ms"] = None

    with pytest.raises(SystemExit, match="not live"):
        smoke_real_meet.validate_smoke_evidence(evidence)


def test_validate_smoke_evidence_rejects_orphan_bridge_process() -> None:
    evidence = healthy_evidence()
    evidence["bridge_process_alive_after_cleanup"] = True
    evidence["bridge_pid_before_cleanup"] = 123

    with pytest.raises(SystemExit, match="Bridge process still alive"):
        smoke_real_meet.validate_smoke_evidence(evidence)


def test_validate_smoke_evidence_rejects_active_transcription_session_after_leave() -> None:
    evidence = healthy_evidence()
    evidence["transcription_session_active_after_cleanup"] = True

    with pytest.raises(SystemExit, match="Transcription session still active"):
        smoke_real_meet.validate_smoke_evidence(evidence)


def test_validate_smoke_evidence_rejects_missing_cleanup_event() -> None:
    evidence = healthy_evidence()
    evidence["recent_events"] = []

    with pytest.raises(SystemExit, match="Missing cleanup event"):
        smoke_real_meet.validate_smoke_evidence(evidence)
