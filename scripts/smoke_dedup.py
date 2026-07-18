from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, RuntimeConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus


async def main() -> None:
    workspace = Path("RobinWorkspace").resolve()
    db_path = workspace / "sessions" / "smoke-dedup" / "robin.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    settings = Settings(
        runtime=RuntimeConfig(max_concurrent_tasks=1),
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=db_path),
    )
    runtime = RobinRuntime(settings)
    await runtime.task_slots.acquire()
    task = await runtime.create_task("Use the finance files to make duplicate smoke slides.")
    for _ in range(50):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.02)
    duplicate = await runtime.create_task("  use   the finance files to make duplicate smoke slides.  ")
    if duplicate.id != task.id:
        raise SystemExit(f"Duplicate request created a second task: {task.id} != {duplicate.id}")
    if len(runtime.tasks) != 1:
        raise SystemExit(f"Expected one task after duplicate suppression, found {len(runtime.tasks)}")
    if not any(event.type == "task.duplicate_suppressed" for event in runtime.recent_events()):
        raise SystemExit("Missing task.duplicate_suppressed event")
    runtime.task_slots.release()
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Deduplicated task did not complete: {task.status} {task.error}")
    print(f"Dedup smoke passed: task={task.id}")


if __name__ == "__main__":
    asyncio.run(main())
