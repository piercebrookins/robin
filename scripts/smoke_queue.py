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
    db_path = workspace / "sessions" / "smoke-queue" / "robin.db"
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
    task = await runtime.create_task("Use the finance files to make queued slides.")
    for _ in range(50):
        if task.status == TaskStatus.QUEUED:
            break
        await asyncio.sleep(0.02)
    if task.status != TaskStatus.QUEUED:
        raise SystemExit(f"Task did not enter queued state: {task.status}")
    runtime.task_slots.release()
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Queued task did not complete: {task.status} {task.error}")
    print(f"Queue smoke passed: task={task.id}")


if __name__ == "__main__":
    asyncio.run(main())
