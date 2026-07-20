from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from robin_core.audio.bridge_client import AudioDeviceInfo


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "smoke_audio_routing.py"
SPEC = importlib.util.spec_from_file_location("smoke_audio_routing", SCRIPT_PATH)
assert SPEC and SPEC.loader
smoke_audio_routing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_audio_routing)


def devices() -> list[AudioDeviceInfo]:
    return [
        AudioDeviceInfo(
            id=1,
            uid="robin-output-uid",
            name="Robin Microphone",
            output_channels=2,
            sample_rate=48_000,
        ),
        AudioDeviceInfo(
            id=2,
            uid="robin-speaker-uid",
            name="Robin Speaker",
            input_channels=2,
            output_channels=2,
            sample_rate=48_000,
        ),
    ]


def test_routing_evidence_accepts_exact_output_uid_and_meet_labels() -> None:
    evidence = smoke_audio_routing.routing_evidence(
        devices(),
        "robin-output-uid",
        "Robin Microphone",
        "Robin Speaker",
        allow_default_output_fallback=False,
    )

    smoke_audio_routing.validate_routing_evidence(evidence)

    assert evidence["tts_output_device"]["uid"] == "robin-output-uid"
    assert evidence["meet_microphone_label_ok"] is True
    assert evidence["meet_speaker_label_ok"] is True


def test_routing_evidence_rejects_placeholder_uid() -> None:
    evidence = smoke_audio_routing.routing_evidence(
        devices(),
        smoke_audio_routing.PLACEHOLDER_UID,
        "Robin Microphone",
        "Robin Speaker",
        allow_default_output_fallback=False,
    )

    with pytest.raises(SystemExit, match="placeholder"):
        smoke_audio_routing.validate_routing_evidence(evidence)


def test_routing_evidence_rejects_default_output_fallback() -> None:
    evidence = smoke_audio_routing.routing_evidence(
        devices(),
        "robin-output-uid",
        "Robin Microphone",
        "Robin Speaker",
        allow_default_output_fallback=True,
    )

    with pytest.raises(SystemExit, match="fallback"):
        smoke_audio_routing.validate_routing_evidence(evidence)


def test_routing_evidence_rejects_missing_meet_label() -> None:
    evidence = smoke_audio_routing.routing_evidence(
        devices(),
        "robin-output-uid",
        "Missing Microphone",
        "Robin Speaker",
        allow_default_output_fallback=False,
    )

    with pytest.raises(SystemExit, match="microphone label"):
        smoke_audio_routing.validate_routing_evidence(evidence)
