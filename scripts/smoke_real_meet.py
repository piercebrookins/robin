from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import load_settings
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus


async def main() -> None:
    meeting_url = os.getenv("ROBIN_REAL_MEET_URL")
    if not meeting_url:
        raise SystemExit("Set ROBIN_REAL_MEET_URL to a live Google Meet URL before running this smoke.")
    settings = load_settings()
    if settings.browser.automation_mode != "playwright":
        raise SystemExit("Set browser.automation_mode to 'playwright' in ROBIN_CONFIG_PATH before running this smoke.")
    if not settings.browser.executable_path:
        raise SystemExit("Set browser.executable_path to Google Chrome in ROBIN_CONFIG_PATH before running this smoke.")
    runtime = RobinRuntime(settings)
    await runtime.join_meeting(meeting_url)
    task = await runtime.create_task("Use the finance files to compare actual 2024 quarterly results and make a few slides.")
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Task did not become ready: {task.status} {task.error}")
    if not any(artifact.task_id == task.id and artifact.type == "validation_json" for artifact in runtime.artifacts):
        raise SystemExit("Task became ready without a validation artifact.")
    await runtime.present_task(task.id)
    await runtime.stop_presenting(task.id)
    await runtime.leave_meeting()
    print(f"Real Meet smoke passed: task={task.id}")


if __name__ == "__main__":
    asyncio.run(main())
