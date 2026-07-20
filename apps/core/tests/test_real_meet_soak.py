from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "soak_real_meet_audio.py"
SPEC = importlib.util.spec_from_file_location("soak_real_meet_audio", SCRIPT_PATH)
assert SPEC and SPEC.loader
soak_real_meet_audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(soak_real_meet_audio)


def healthy_evidence() -> dict:
    return {
        "samples": [
            {
                "capture_state": "capturing",
                "transcription_state": "connected",
                "audio_frames_received": 1000,
                "audio_frames_dropped": 1,
                "last_frame_sequence": 1000,
                "last_frame_timestamp_ms": 10_000,
                "received_frame_count": 1000,
            },
            {
                "capture_state": "capturing",
                "transcription_state": "connected",
                "audio_frames_received": 1200,
                "audio_frames_dropped": 1,
                "last_frame_sequence": 1200,
                "last_frame_timestamp_ms": 12_000,
                "received_frame_count": 1200,
            }
        ],
        "duration_s": 10,
        "sample_interval_s": 5,
        "cleanup_elapsed_ms": 120,
        "audio_after_leave": {
            "capture_state": "idle",
            "transcription_state": "idle",
            "playback_state": "idle",
        },
        "bridge_process_alive_after_leave": False,
        "transcription_session_active_after_leave": False,
        "bridge_event_loop_running_after_leave": False,
    }


def test_frame_loss_percent_uses_max_observed_counts() -> None:
    samples = [
        {"audio_frames_received": 100, "audio_frames_dropped": 1},
        {"audio_frames_received": 200, "audio_frames_dropped": 2},
    ]

    assert soak_real_meet_audio.frame_loss_percent(samples) == pytest.approx(2 / 202 * 100)


def test_validate_soak_evidence_accepts_healthy_cleanup() -> None:
    evidence = healthy_evidence()

    soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)

    assert evidence["frame_loss_percent"] < 0.5


def test_validate_soak_evidence_rejects_orphan_bridge_process() -> None:
    evidence = healthy_evidence()
    evidence["bridge_process_alive_after_leave"] = True
    evidence["bridge_pid_before_leave"] = 123

    with pytest.raises(SystemExit, match="Bridge process still alive"):
        soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)


def test_validate_soak_evidence_rejects_unhealthy_audio_sample() -> None:
    evidence = healthy_evidence()
    evidence["samples"][0]["capture_state"] = "failed"

    with pytest.raises(SystemExit, match="unhealthy audio samples"):
        soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)


def test_validate_soak_evidence_rejects_stagnant_capture_frames() -> None:
    evidence = healthy_evidence()
    evidence["samples"][1]["last_frame_sequence"] = evidence["samples"][0]["last_frame_sequence"]
    evidence["samples"][1]["last_frame_timestamp_ms"] = evidence["samples"][0]["last_frame_timestamp_ms"]

    with pytest.raises(SystemExit, match="frame sequence did not advance"):
        soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)


def test_validate_soak_evidence_rejects_active_transcription_after_leave() -> None:
    evidence = healthy_evidence()
    evidence["transcription_session_active_after_leave"] = True

    with pytest.raises(SystemExit, match="Transcription session still active"):
        soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)


def test_validate_soak_evidence_rejects_running_bridge_event_loop_after_leave() -> None:
    evidence = healthy_evidence()
    evidence["bridge_event_loop_running_after_leave"] = True

    with pytest.raises(SystemExit, match="Bridge event loop still running"):
        soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)


def test_validate_soak_evidence_requires_process_bridge_pid() -> None:
    evidence = healthy_evidence()
    evidence["requires_process_bridge"] = True

    with pytest.raises(SystemExit, match="bridge PID"):
        soak_real_meet_audio.validate_soak_evidence(evidence, max_frame_loss_percent=0.5)
