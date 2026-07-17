from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus, ValidationReport


async def main() -> None:
    workspace = Path("RobinWorkspace").resolve()
    db_path = workspace / "sessions" / "smoke-validation" / "robin.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=db_path),
    )
    runtime = RobinRuntime(settings)
    task = await runtime.create_task("Use the finance files to compare actual 2024 quarterly results and make a few slides.")
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Task did not become ready: {task.status} {task.error}")
    validation = next((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "validation_json"), None)
    if not validation:
        raise SystemExit("No validation artifact was created.")
    report = ValidationReport.model_validate_json(runtime.artifact_path(validation.path).read_text())
    if not report.ok:
        failed = ", ".join(check.name for check in report.checks if not check.ok)
        raise SystemExit(f"Validation failed: {failed}")
    print(f"Validation smoke passed: task={task.id} report={workspace / validation.path}")


if __name__ == "__main__":
    asyncio.run(main())
