from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge_client import AudioDeviceInfo, ProcessBridgeClient
from robin_core.config import load_settings


PLACEHOLDER_UID = "REPLACE_WITH_ROBIN_LOOPBACK_OUTPUT_UID"


def find_device_by_uid(devices: list[AudioDeviceInfo], uid: str | None) -> AudioDeviceInfo | None:
    if not uid:
        return None
    return next((device for device in devices if device.uid == uid), None)


def devices_matching_label(devices: list[AudioDeviceInfo], label: str) -> list[AudioDeviceInfo]:
    normalized = label.casefold().strip()
    return [device for device in devices if normalized and normalized in device.name.casefold()]


def routing_evidence(
    devices: list[AudioDeviceInfo],
    tts_output_device_uid: str | None,
    meet_microphone_label: str,
    meet_speaker_label: str,
    allow_default_output_fallback: bool,
) -> dict[str, Any]:
    target = find_device_by_uid(devices, tts_output_device_uid)
    microphone_matches = devices_matching_label(devices, meet_microphone_label)
    speaker_matches = devices_matching_label(devices, meet_speaker_label)
    return {
        "tts_output_device_uid": tts_output_device_uid,
        "tts_output_device": target.model_dump(mode="json") if target else None,
        "tts_output_device_ok": bool(target and target.output_channels > 0),
        "meet_microphone_label": meet_microphone_label,
        "meet_microphone_matches": [device.model_dump(mode="json") for device in microphone_matches],
        "meet_microphone_label_ok": bool(microphone_matches),
        "meet_speaker_label": meet_speaker_label,
        "meet_speaker_matches": [device.model_dump(mode="json") for device in speaker_matches],
        "meet_speaker_label_ok": bool(speaker_matches),
        "allow_default_output_fallback": allow_default_output_fallback,
        "default_output_fallback_ok": not allow_default_output_fallback,
        "devices": [device.model_dump(mode="json") for device in devices],
    }


def validate_routing_evidence(evidence: dict[str, Any]) -> None:
    uid = evidence.get("tts_output_device_uid")
    if not uid or uid == PLACEHOLDER_UID:
        raise SystemExit("Configured TTS output UID is missing or still set to the placeholder.")
    if not evidence.get("tts_output_device_ok"):
        raise SystemExit(f"Configured TTS output UID is not an output-capable device: {uid}")
    if not evidence.get("meet_microphone_label_ok"):
        raise SystemExit(f"Meet microphone label was not found in CoreAudio devices: {evidence.get('meet_microphone_label')}")
    if not evidence.get("meet_speaker_label_ok"):
        raise SystemExit(f"Meet speaker label was not found in CoreAudio devices: {evidence.get('meet_speaker_label')}")
    if not evidence.get("default_output_fallback_ok"):
        raise SystemExit("Default output fallback must be disabled for real audio routing.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Robin's exact real audio routing devices.")
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=Path("robinworkspace/sessions/audio-routing-smoke.json"),
    )
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    settings = load_settings()
    if settings.audio.bridge.provider != "process" or not settings.audio.bridge.executable:
        raise SystemExit("Set audio.bridge.provider=process and audio.bridge.executable before running routing smoke.")
    if settings.runtime.deployment_mode != "real":
        raise SystemExit("Set runtime.deployment_mode=real before running routing smoke.")
    if not args.skip_build:
        subprocess.run(["swift", "build", "--package-path", "apps/macos-bridge"], cwd=root, check=True)
    client = ProcessBridgeClient(settings.audio.bridge.executable)
    try:
        version = await client.version()
        devices = await client.audio_devices()
        evidence = routing_evidence(
            devices,
            settings.audio.routing.tts_output_device_uid,
            settings.audio.routing.meet_microphone_label,
            settings.audio.routing.meet_speaker_label,
            settings.audio.routing.allow_default_output_fallback,
        )
        evidence["bridge_version"] = version.result
        validate_routing_evidence(evidence)
    finally:
        await client.close()

    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"Audio routing smoke passed. Evidence: {args.evidence_path}")


if __name__ == "__main__":
    asyncio.run(main())
