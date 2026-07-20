from __future__ import annotations

from pathlib import Path


BRIDGE_SOURCE = Path(__file__).resolve().parents[3] / "apps/macos-bridge/Sources/RobinBridge/main.swift"


def test_streaming_playback_drain_uses_buffer_completion_tracking() -> None:
    source = BRIDGE_SOURCE.read_text()
    manager = source.split("final class StreamingPlaybackManager", 1)[1].split(
        "let streamingPlayback",
        1,
    )[0]

    assert "Thread.sleep" not in manager
    assert "scheduledBufferCount" in manager
    assert "completedBufferCount" in manager
    assert "audio.output.drained" in manager
    assert "scheduled_buffers" in manager
    assert "completed_buffers" in manager


def test_continuous_capture_restarts_once_after_unexpected_stop() -> None:
    source = BRIDGE_SOURCE.read_text()
    manager = source.split("final class ContinuousCaptureManager", 1)[1].split(
        "let continuousCapture",
        1,
    )[0]

    assert "restartAttempted" in manager
    assert "intentionalStop" in manager
    assert "restartAfterFailure" in manager
    assert "audio.capture.restarted" in manager
    assert "capture restart produced no audio frame" in manager


def test_continuous_capture_frame_events_include_live_drop_count() -> None:
    source = BRIDGE_SOURCE.read_text()
    manager = source.split("final class ContinuousCaptureManager", 1)[1].split(
        "let continuousCapture",
        1,
    )[0]

    assert "lock.withLock {\n                droppedFrames += 1\n            }" in manager
    assert 'let currentDroppedFrames = droppedFrames' in manager
    assert '"dropped_frames": "\\(currentDroppedFrames)"' in manager
