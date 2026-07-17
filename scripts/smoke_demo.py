from __future__ import annotations

import asyncio
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime
from robin_core.schemas import TaskStatus, ValidationReport


async def main() -> None:
    workspace = Path("RobinWorkspace").resolve()
    db_path = workspace / "sessions" / "smoke-demo" / "robin.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=db_path),
    )
    runtime = RobinRuntime(settings)
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript(
        "Robin, use the finance files to compare our 2024 quarterly results and make a few slides.",
        "Demo",
    )
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Task did not become ready: {task.status} {task.error}")
    await runtime.ingest_transcript("Robin, add operating margin and use actuals instead of forecasts.", "Demo")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Revision did not become ready: {task.status} {task.error}")
    deck = max((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "deck_json"), key=lambda artifact: artifact.revision)
    pptx = max((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "deck_pptx"), key=lambda artifact: artifact.revision)
    validation = max((artifact for artifact in runtime.artifacts if artifact.task_id == task.id and artifact.type == "validation_json"), key=lambda artifact: artifact.revision)
    report = ValidationReport.model_validate_json(runtime.artifact_path(validation.path).read_text())
    if not report.ok:
        failed = ", ".join(check.name for check in report.checks if not check.ok)
        raise SystemExit(f"Validation failed: {failed}")
    deck_text = runtime.artifact_path(deck.path).read_text()
    if "finance_context_report.pdf" not in deck_text or "2024 growth improved through Q4" not in deck_text:
        raise SystemExit("Deck did not include supporting PDF context.")
    if "source-data/finance_context_report.pdf" not in report.source_paths:
        raise SystemExit("Validation report did not record the supporting PDF source.")
    with zipfile.ZipFile(runtime.artifact_path(pptx.path)) as archive:
        if "ppt/presentation.xml" not in archive.namelist():
            raise SystemExit("PPTX export is missing presentation.xml.")
    print(f"Smoke passed: task={task.id} deck={workspace / deck.path} pptx={workspace / pptx.path} validation={workspace / validation.path}")


if __name__ == "__main__":
    asyncio.run(main())
