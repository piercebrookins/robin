from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, PresentationConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workspace = root / "RobinWorkspace/sessions/retry-present-smoke"
    source = workspace / "source-data"
    if workspace.exists():
        import shutil

        shutil.rmtree(workspace)
    source.mkdir(parents=True, exist_ok=True)
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    task = await runtime.create_task("Use retry_smoke finance files to make slides.")
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.FAILED:
        raise SystemExit(f"Expected initial failure, saw {task.status}")
    if not any("No CSV or XLSX finance data" in speech.text for speech in runtime.speech):
        raise SystemExit("Robin did not speak the initial task blocker.")

    (source / "retry_smoke_finance.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,70,30\n"
        "2024,Q2,actual,120,80,40\n"
        "2024,Q3,actual,150,95,55\n"
        "2024,Q4,actual,180,110,70\n"
    )
    await runtime.retry_task(task.id)
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Retry did not complete, saw {task.status}: {task.error}")
    await runtime.present_task(task.id)
    narration_count = sum(1 for event in runtime.recent_events(200) if event.type == "presentation.narration")
    if narration_count < runtime._deck_slide_count(task.id):
        raise SystemExit("Presentation did not narrate every slide")
    if not any("Key metrics:" in speech.text for speech in runtime.speech):
        raise SystemExit("Presentation narration did not include key metrics")
    stopped = await runtime.stop_presenting(task.id)
    if stopped.presenting:
        raise SystemExit("Presentation did not stop")
    print(f"Retry/presentation smoke passed: task={task.id} revision={task.revision}")


if __name__ == "__main__":
    asyncio.run(main())
