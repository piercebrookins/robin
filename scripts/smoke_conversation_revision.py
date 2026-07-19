from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime
from robin_core.schemas import MeetingState, TaskStatus


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workspace = root / "RobinWorkspace" / "sessions" / "conversation-revision-smoke"
    if workspace.exists():
        shutil.rmtree(workspace)
    source = workspace / "source-data"
    source.mkdir(parents=True)
    (source / "quarterly_actuals.csv").write_text(
        "year,quarter,scenario,revenue,expenses,operating_income\n"
        "2024,Q1,actual,100,72,28\n"
        "2024,Q2,actual,120,82,38\n"
        "2024,Q3,actual,145,94,51\n"
        "2024,Q4,actual,180,112,68\n",
        encoding="utf-8",
    )
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    await runtime.ingest_transcript(
        "Robin, compare the quarterly actuals and make a short deck.", "Avery"
    )
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    if task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(f"Initial task did not verify: {task.status} {task.error}")

    await runtime.ingest_transcript("Robin, what sources did you use?", "Avery")
    if not any("quarterly_actuals.csv" in speech.text for speech in runtime.speech):
        raise SystemExit("Grounded source Q&A did not name the validated source")

    await runtime.ingest_transcript(
        "Robin, change it to include operating margin and only use actual results.",
        "Avery",
    )
    await runtime._task_handles[task.id]
    if task.revision != 2 or task.status != TaskStatus.READY_TO_PRESENT:
        raise SystemExit(
            f"Spoken revision did not produce verified revision 2: r{task.revision} {task.status}"
        )
    deck_revisions = sorted(
        artifact.revision
        for artifact in runtime.artifacts
        if artifact.task_id == task.id and artifact.type == "deck_json"
    )
    if deck_revisions != [1, 2]:
        raise SystemExit(f"Expected preserved deck revisions [1, 2], saw {deck_revisions}")

    await runtime.present_task(task.id)
    if task.status != TaskStatus.COMPLETED:
        raise SystemExit(f"Latest revision was not completed after narration: {task.status}")
    if runtime.snapshot().presenting:
        raise SystemExit("Presentation remained active after narration completed")
    narration_events = [
        event for event in runtime.recent_events(300) if event.type == "presentation.narration"
    ]
    if len(narration_events) < runtime._deck_slide_count(task.id):
        raise SystemExit("Robin did not narrate every slide in the latest revision")

    await runtime.ingest_transcript("Robin, which file supported those results?", "Avery")
    addressed = [
        event for event in runtime.recent_events(300) if event.type == "conversation.addressed"
    ]
    if len(addressed) < 2:
        raise SystemExit("Grounded Q&A did not remain available after presentation")
    await runtime.leave_meeting()
    snapshot = runtime.snapshot()
    if snapshot.meeting_state != MeetingState.ENDED:
        raise SystemExit(f"Meeting did not end cleanly: {snapshot.meeting_state}")
    if snapshot.capture_loop_running or snapshot.presenting:
        raise SystemExit("Capture or presentation state survived meeting cleanup")
    print(
        "Conversation/revision smoke passed: "
        f"task={task.id} revisions={deck_revisions} narration={len(narration_events)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
