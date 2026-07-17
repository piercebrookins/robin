from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import AudioConfig, DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workspace = root / "RobinWorkspace"
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        audio=AudioConfig(
            mode="simulator",
            bridge_mode="simulator",
            simulator_transcript="Robin listening loop smoke discussion.",
        ),
    )
    runtime = RobinRuntime(settings)
    await runtime.start_listening_loop(max_iterations=1, interval_ms=0)
    if runtime._listen_handle is None:
        raise SystemExit("Listening loop did not start")
    await runtime._listen_handle
    snapshot = runtime.snapshot()
    if snapshot.capture_loop_running:
        raise SystemExit("Listening loop did not stop after bounded iteration")
    if not snapshot.transcript or snapshot.transcript[-1].source != "audio_stt":
        raise SystemExit("Listening loop did not ingest an audio_stt transcript")
    print(f"Listen loop smoke passed: {snapshot.transcript[-1].text}")


if __name__ == "__main__":
    asyncio.run(main())
