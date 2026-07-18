from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, PresentationConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workspace = root / "RobinWorkspace/sessions/observability-smoke"
    if workspace.exists():
        import shutil

        shutil.rmtree(workspace)
    seed_workspace(root / "scripts" / "seed_demo_workspace.py", workspace)
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
            presentation=PresentationConfig(base_url="http://127.0.0.1:3000/present"),
        )
    )
    await runtime.ingest_transcript("Robin, use the finance files to compare our 2024 quarterly results and make a few slides.", "Smoke")
    task = runtime.tasks[-1]
    await runtime._task_handles[task.id]
    events = runtime.recent_events()
    metrics = runtime.metrics()
    trace = workspace / "sessions" / "traces" / f"{task.id}.jsonl"
    if not any(event.type == "task.completed" for event in events):
        raise SystemExit("task.completed event was not emitted")
    if metrics.artifact_count < 3 or metrics.speech_count < 1:
        raise SystemExit(f"metrics did not reflect completed work: {metrics}")
    if not trace.exists():
        raise SystemExit(f"trace file was not written: {trace}")
    print(f"Observability smoke passed: events={metrics.event_count} trace={trace}")


def seed_workspace(path: Path, workspace: Path) -> None:
    spec = importlib.util.spec_from_file_location("seed_demo_workspace", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_root = module.ROOT
    original_source = module.SOURCE
    try:
        module.ROOT = workspace
        module.SOURCE = workspace / "source-data"
        module.main()
    finally:
        module.ROOT = original_root
        module.SOURCE = original_source


if __name__ == "__main__":
    asyncio.run(main())
