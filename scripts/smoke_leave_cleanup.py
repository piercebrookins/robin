from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import AudioConfig, DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


async def main() -> None:
    workspace = Path("RobinWorkspace").resolve()
    db_path = workspace / "sessions" / "smoke-leave-cleanup" / "robin.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=db_path),
            audio=AudioConfig(mode="simulator", bridge_mode="simulator", simulator_transcript="Leave cleanup smoke."),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.start_listening_loop(interval_ms=10_000)
    if not runtime.snapshot().capture_loop_running:
        raise SystemExit("Listening loop did not start.")
    snapshot = await runtime.leave_meeting()
    if snapshot.capture_loop_running:
        raise SystemExit("Listening loop was still running after leaving.")
    if snapshot.presenting:
        raise SystemExit("Presentation state was still active after leaving.")
    if not any(event.type == "meeting.leave.cleanup" for event in runtime.recent_events()):
        raise SystemExit("Meeting leave cleanup event was not recorded.")
    print("Leave cleanup smoke passed.")


if __name__ == "__main__":
    asyncio.run(main())
