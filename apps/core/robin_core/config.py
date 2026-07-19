from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field


load_dotenv()


class RuntimeConfig(BaseModel):
    environment: str = "development"
    log_level: str = "INFO"
    max_concurrent_tasks: int = 2
    acknowledgement_deadline_ms: int = 4000
    speech_floor_silence_ms: int = 400
    speech_floor_max_wait_ms: int = 3000
    min_free_disk_mb: int = 512
    max_peak_rss_mb: int = 1024
    max_workspace_disk_mb: int = 2048


class ModelConfig(BaseModel):
    primary: str = "gpt-5.6"
    agent_max_iterations: int = 8
    agent_max_source_chars: int = 24_000
    intent_confidence_accept: float = 0.75
    intent_confidence_confirm: float = 0.60
    intent_timeout_seconds: float = 4.0


class BrowserConfig(BaseModel):
    meet_base_url: str = "https://meet.google.com"
    automation_mode: str = "simulator"
    connection_mode: str = "launch"
    cdp_endpoint: str = "http://127.0.0.1:9222"
    allowed_meet_hosts: list[str] = Field(default_factory=lambda: ["meet.google.com"])
    executable_path: Path | None = None
    profile_dir: Path = Path("~/Library/Application Support/Robin/Chrome")
    recovery_screenshot_dir: Path = Path("./RobinWorkspace/sessions/browser-recovery")
    headless: bool = False
    use_fake_media_ui: bool = True
    remote_debugging_port: int = 9222
    navigation_timeout_ms: int = 30_000
    prejoin_timeout_ms: int = 20_000
    admission_timeout_ms: int = 120_000
    ui_action_retries: int = 1
    ui_recovery_pause_ms: int = 250
    share_dialog_mode: str = "simulator"
    share_source_title: str = "Robin Presentation"
    share_dialog_timeout_ms: int = 10_000
    share_dialog_retries: int = 1
    share_dialog_poll_interval_ms: int = 250
    computer_use_command: str = "cua-driver"


class AudioConfig(BaseModel):
    mode: str = "simulator"
    bridge_mode: str = "simulator"
    bridge_executable: Path | None = None
    capture_bundle_id: str = "com.google.Chrome"
    capture_sample_duration_ms: int = 1500
    capture_loop_interval_ms: int = 500
    silence_rms_threshold: float = 0.002
    openai_timeout_seconds: float = 20.0
    openai_max_retries: int = 1
    speech_model: str = "gpt-4o-mini-tts"
    transcription_model: str = "gpt-4o-mini-transcribe"
    realtime_transcription_enabled: bool = False
    realtime_transcription_model: str = "gpt-realtime-whisper"
    realtime_transcription_delay: str = "low"
    realtime_vad_silence_ms: int = 550
    realtime_vad_min_speech_ms: int = 180
    realtime_chunk_bytes: int = 2400
    speech_voice: str = "alloy"
    speech_format: str = "wav"
    output_device_name: str = "BlackHole 2ch"
    post_speech_cooldown_ms: int = 700
    simulator_transcript: str = (
        "Robin, use the finance files to compare the quarterly results and make slides."
    )


class WorkspaceConfig(BaseModel):
    root: Path = Path("./RobinWorkspace")
    source_dir: str = "source-data"
    generated_dir: str = "generated"
    sessions_dir: str = "sessions"
    max_file_size_mb: int = 50
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".csv", ".xlsx", ".pdf", ".pptx", ".txt", ".md"]
    )


class PresentationConfig(BaseModel):
    base_url: str = "http://127.0.0.1:3000/present"
    default_slide_count: int = 4


class DatabaseConfig(BaseModel):
    path: Path = Path("./RobinWorkspace/robin.db")


class CalendarConfig(BaseModel):
    enabled: bool = False
    provider: str = "local"
    file_path: Path | None = None
    lookahead_hours: int = 12
    auto_join: bool = False
    join_early_seconds: int = 60


class Settings(BaseModel):
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    presentation: PresentationConfig = Field(default_factory=PresentationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    openai_api_key: str | None = None


def load_settings() -> Settings:
    config_path = Path(os.getenv("ROBIN_CONFIG_PATH", "config/robin.example.yaml"))
    data: dict[str, Any] = {}
    if config_path.exists():
        import yaml

        data = yaml.safe_load(config_path.read_text()) or {}
    settings = Settings(**data)
    settings.openai_api_key = os.getenv("OPENAI_API_KEY")
    settings.workspace.root = settings.workspace.root.expanduser().resolve()
    settings.database.path = settings.database.path.expanduser().resolve()
    settings.browser.profile_dir = settings.browser.profile_dir.expanduser()
    settings.browser.recovery_screenshot_dir = (
        settings.browser.recovery_screenshot_dir.expanduser().resolve()
    )
    if settings.browser.executable_path:
        settings.browser.executable_path = settings.browser.executable_path.expanduser()
    if settings.audio.bridge_executable:
        settings.audio.bridge_executable = settings.audio.bridge_executable.expanduser()
    if settings.calendar.file_path:
        settings.calendar.file_path = settings.calendar.file_path.expanduser().resolve()
    return settings
