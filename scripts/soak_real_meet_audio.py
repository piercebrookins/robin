from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "core"))
sys.path.insert(0, str(ROOT / "scripts"))

from robin_core.config import load_settings
from robin_core.runtime import RobinRuntime
from smoke_real_meet import wait_for_audio_ready


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def bridge_pid(runtime: RobinRuntime) -> int | None:
    process = getattr(runtime.audio.bridge_client, "_process", None)
    pid = getattr(process, "pid", None)
    return int(pid) if pid is not None else None


def transcription_session_active(runtime: RobinRuntime) -> bool:
    return getattr(runtime, "_audio_session", None) is not None


def bridge_event_loop_running(runtime: RobinRuntime) -> bool:
    handle = getattr(runtime, "_bridge_event_handle", None)
    return bool(handle and not handle.done())


def sample(runtime: RobinRuntime) -> dict[str, Any]:
    state = runtime.audio.runtime_state
    metrics = runtime.metrics()
    return {
        "sampled_at_ms": int(time.time() * 1000),
        "capture_state": state.capture_state,
        "transcription_state": state.transcription_state,
        "playback_state": state.playback_state,
        "last_frame_sequence": state.last_frame_sequence,
        "last_frame_timestamp_ms": state.last_frame_timestamp_ms,
        "received_frame_count": state.received_frame_count,
        "last_rms": state.last_rms,
        "dropped_frame_count": state.dropped_frame_count,
        "audio_frames_received": metrics.audio_frames_received,
        "audio_frames_dropped": metrics.audio_frames_dropped,
        "transcription_reconnect_count": metrics.transcription_reconnect_count,
        "playback_failure_count": metrics.playback_failure_count,
        "bridge_pid": bridge_pid(runtime),
    }


def frame_loss_percent(samples: list[dict[str, Any]]) -> float:
    if not samples:
        return 0.0
    received = max(int(item.get("audio_frames_received") or 0) for item in samples)
    dropped = max(int(item.get("audio_frames_dropped") or 0) for item in samples)
    total = received + dropped
    return (dropped / total) * 100 if total else 0.0


def _int_values(samples: list[dict[str, Any]], key: str) -> list[int]:
    values = []
    for item in samples:
        value = item.get(key)
        if value is not None:
            values.append(int(value))
    return values


def validate_soak_evidence(evidence: dict[str, Any], max_frame_loss_percent: float) -> None:
    samples = evidence.get("samples") or []
    if not samples:
        raise SystemExit("Soak produced no samples.")
    if evidence.get("duration_s", 0) >= evidence.get("sample_interval_s", 1) and len(samples) < 2:
        raise SystemExit("Soak did not collect enough samples to prove continuous capture.")
    bad_samples = [
        item
        for item in samples
        if item.get("capture_state") != "capturing"
        or item.get("transcription_state") != "connected"
        or item.get("last_frame_timestamp_ms") is None
        or item.get("last_frame_sequence") is None
        or item.get("received_frame_count") is None
    ]
    if bad_samples:
        raise SystemExit(f"Soak observed unhealthy audio samples: {bad_samples[:3]}")
    frame_sequences = _int_values(samples, "last_frame_sequence")
    frame_timestamps = _int_values(samples, "last_frame_timestamp_ms")
    if len(frame_sequences) >= 2 and max(frame_sequences) <= min(frame_sequences):
        raise SystemExit("Capture frame sequence did not advance during soak.")
    if len(frame_timestamps) >= 2 and max(frame_timestamps) <= min(frame_timestamps):
        raise SystemExit("Capture frame timestamps did not advance during soak.")
    loss = frame_loss_percent(samples)
    evidence["frame_loss_percent"] = loss
    if loss > max_frame_loss_percent:
        raise SystemExit(f"Frame loss {loss:.3f}% exceeded limit {max_frame_loss_percent:.3f}%.")
    after = evidence.get("audio_after_leave") or {}
    if evidence.get("cleanup_elapsed_ms", 999_999) > 2_000:
        raise SystemExit(f"Cleanup exceeded two seconds: {evidence['cleanup_elapsed_ms']} ms")
    if after.get("capture_state") != "idle" or after.get("transcription_state") != "idle":
        raise SystemExit(f"Audio did not stop cleanly after soak: {after}")
    if after.get("playback_state") != "idle":
        raise SystemExit(f"Playback did not stop cleanly after soak: {after}")
    if evidence.get("transcription_session_active_after_leave"):
        raise SystemExit("Transcription session still active after leave.")
    if evidence.get("bridge_event_loop_running_after_leave"):
        raise SystemExit("Bridge event loop still running after leave.")
    if evidence.get("bridge_process_alive_after_leave"):
        raise SystemExit(f"Bridge process still alive after leave: pid={evidence.get('bridge_pid_before_leave')}")
    if evidence.get("requires_process_bridge") and not evidence.get("bridge_pid_before_leave"):
        raise SystemExit("Process bridge soak did not record a bridge PID before leave.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real Meet audio soak and write JSON evidence.")
    parser.add_argument("--meeting-url", default=os.getenv("ROBIN_REAL_MEET_URL"))
    parser.add_argument("--duration-s", type=float, default=30 * 60)
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--audio-ready-timeout-s", type=float, default=15.0)
    parser.add_argument("--max-frame-loss-percent", type=float, default=0.5)
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=Path("robinworkspace/sessions/real-meet-audio-soak.json"),
    )
    args = parser.parse_args()

    if not args.meeting_url:
        raise SystemExit("Set ROBIN_REAL_MEET_URL or pass --meeting-url with a live Google Meet URL.")
    settings = load_settings()
    if settings.runtime.deployment_mode != "real":
        raise SystemExit("Set runtime.deployment_mode to 'real' in ROBIN_CONFIG_PATH before running this soak.")
    if args.duration_s <= 0:
        raise SystemExit("--duration-s must be positive.")
    if args.sample_interval_s <= 0:
        raise SystemExit("--sample-interval-s must be positive.")

    evidence: dict[str, Any] = {
        "meeting_url": args.meeting_url,
        "duration_s": args.duration_s,
        "sample_interval_s": args.sample_interval_s,
        "requires_process_bridge": settings.audio.bridge.provider == "process",
        "started_at_ms": int(time.time() * 1000),
        "samples": [],
    }
    runtime = RobinRuntime(settings)
    try:
        await runtime.join_meeting(args.meeting_url)
        await wait_for_audio_ready(runtime, args.audio_ready_timeout_s)
        deadline = time.monotonic() + args.duration_s
        while time.monotonic() < deadline:
            evidence["samples"].append(sample(runtime))
            await asyncio.sleep(min(args.sample_interval_s, max(0.0, deadline - time.monotonic())))
        evidence["samples"].append(sample(runtime))
        evidence["audio_before_leave"] = runtime.audio.runtime_state.model_dump(mode="json")
        evidence["bridge_pid_before_leave"] = bridge_pid(runtime)
    finally:
        cleanup_started = time.perf_counter()
        snapshot = await runtime.leave_meeting()
        evidence["cleanup_elapsed_ms"] = int((time.perf_counter() - cleanup_started) * 1000)
        evidence["audio_after_leave"] = snapshot.audio.model_dump(mode="json")
        evidence["meeting_state_after_leave"] = snapshot.meeting_state.value
        evidence["transcription_session_active_after_leave"] = transcription_session_active(runtime)
        evidence["bridge_event_loop_running_after_leave"] = bridge_event_loop_running(runtime)
        pid = evidence.get("bridge_pid_before_leave")
        if pid:
            await asyncio.sleep(0.2)
        evidence["bridge_process_alive_after_leave"] = process_alive(pid)
        evidence["recent_events"] = [event.model_dump(mode="json") for event in runtime.recent_events(50)]
        args.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    validate_soak_evidence(evidence, args.max_frame_loss_percent)
    args.evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"Real Meet audio soak passed. Evidence: {args.evidence_path}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        signal.raise_signal(signal.SIGINT)
