from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge import AudioBridge
from robin_core.config import load_settings


async def main() -> None:
    settings = load_settings()
    if settings.audio.mode != "openai" or settings.audio.bridge_mode != "process":
        raise SystemExit(
            "Live TTS smoke requires audio.mode=openai and audio.bridge_mode=process. "
            "Run scripts/setup_partner.sh --real-meet first."
        )
    bridge = AudioBridge(
        settings.audio,
        Path("RobinWorkspace/sessions/speech-openai-smoke"),
        settings.openai_api_key,
    )
    record = await bridge.speak("Robin audio smoke test complete.")
    if record.duration_seconds is None or record.duration_seconds < 0.5:
        raise SystemExit(f"TTS produced an implausibly short file: {record.duration_seconds}")
    print(
        "TTS + BlackHole playback passed: "
        f"duration={record.duration_seconds:.2f}s "
        f"bytes={record.byte_count} "
        f"device={record.playback_device} "
        f"route={record.playback_route} "
        f"path={record.path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
