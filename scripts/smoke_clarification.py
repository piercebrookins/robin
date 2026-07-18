from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus


async def main() -> None:
    workspace = Path("RobinWorkspace").resolve()
    db_path = workspace / "sessions" / "smoke-clarification" / "robin.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=db_path),
    )
    runtime = RobinRuntime(settings)
    await runtime.ingest_transcript("Could someone compare the finance files and make slides?", "Demo")
    if len(runtime.tasks) != 1 or runtime.tasks[-1].status != TaskStatus.AWAITING_CLARIFICATION:
        raise SystemExit("Ambiguous request did not create an awaiting-clarification task.")
    pending_id = runtime.tasks[-1].id
    if not runtime.speech or runtime.speech[-1].text != "Should I take that on?":
        raise SystemExit("Robin did not ask for confirmation.")
    await runtime.ingest_transcript("Yes, please do.", "Demo")
    task = runtime.tasks[-1]
    if task.id != pending_id:
        raise SystemExit("Clarification accepted a different task record.")
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Confirmed task did not become ready: {task.status} {task.error}")
    if not any(event.type == "clarification.accepted" for event in runtime.recent_events()):
        raise SystemExit("Clarification acceptance event was not recorded.")
    print(f"Clarification smoke passed: task={task.id}")


if __name__ == "__main__":
    asyncio.run(main())
