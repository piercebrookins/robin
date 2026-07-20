from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from robin_core.config import AudioConfig, DatabaseConfig, RuntimeConfig, Settings, WorkspaceConfig


def test_legacy_audio_config_is_upgraded_to_explicit_simulator_shape() -> None:
    config = AudioConfig(mode="simulator", bridge_mode="simulator", simulator_transcript="hello")

    assert config.capture.provider == "fixture"
    assert config.transcription.provider == "fixture"
    assert config.transcription.fixture_transcript == "hello"
    assert config.speech.provider == "tone_fixture"
    assert config.bridge.provider == "simulator"


def test_real_deployment_rejects_fixture_audio(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="screen_capture_kit"):
        Settings(
            runtime=RuntimeConfig(deployment_mode="real"),
            workspace=WorkspaceConfig(root=tmp_path),
            database=DatabaseConfig(path=tmp_path / "robin.db"),
            audio=AudioConfig(),
        )


def test_real_deployment_requires_exact_output_uid(tmp_path: Path) -> None:
    executable = tmp_path / "bridge"
    executable.write_text("#!/bin/sh\n")

    with pytest.raises(ValidationError, match="tts_output_device_uid"):
        Settings(
            runtime=RuntimeConfig(deployment_mode="real"),
            workspace=WorkspaceConfig(root=tmp_path),
            database=DatabaseConfig(path=tmp_path / "robin.db"),
            audio={
                "capture": {"provider": "screen_capture_kit"},
                "transcription": {"provider": "openai_realtime"},
                "speech": {"provider": "openai", "response_format": "pcm"},
                "bridge": {"provider": "process", "executable": executable},
                "routing": {"allow_default_output_fallback": False},
            },
        )


def test_real_deployment_accepts_production_audio_contract(tmp_path: Path) -> None:
    executable = tmp_path / "bridge"
    executable.write_text("#!/bin/sh\n")

    settings = Settings(
        runtime=RuntimeConfig(deployment_mode="real"),
        workspace=WorkspaceConfig(root=tmp_path),
        database=DatabaseConfig(path=tmp_path / "robin.db"),
        audio={
            "capture": {"provider": "screen_capture_kit"},
            "transcription": {"provider": "openai_realtime"},
            "speech": {"provider": "openai", "response_format": "pcm"},
            "bridge": {"provider": "process", "executable": executable},
            "routing": {
                "tts_output_device_uid": "com.robin.audio.microphone",
                "meet_microphone_label": "Robin Microphone",
                "meet_speaker_label": "Robin Speaker",
                "allow_default_output_fallback": False,
            },
        },
    )

    assert settings.runtime.deployment_mode == "real"
    assert settings.audio.routing.tts_output_device_uid == "com.robin.audio.microphone"
