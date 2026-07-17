from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed_workspace(root / "scripts" / "seed_demo_workspace.py")
    workspace = root / "RobinWorkspace"
    runtime = RobinRuntime(
        Settings(
            workspace=WorkspaceConfig(root=workspace),
            database=DatabaseConfig(path=workspace / "robin.db"),
        )
    )
    snapshot = await runtime.reindex_workspace()
    if snapshot.file_count < 3:
        raise SystemExit(f"Expected seeded workspace files, saw {snapshot.file_count}")
    first = runtime.workspace_file(snapshot.files[0].id)
    if not first.relative_path.startswith("source-data/"):
        raise SystemExit(f"Unexpected workspace file path: {first.relative_path}")
    if not any(event.type == "workspace.reindexed" for event in runtime.recent_events()):
        raise SystemExit("workspace.reindexed event was not emitted")
    print(f"Workspace smoke passed: {snapshot.file_count} files indexed")


def seed_workspace(path: Path) -> None:
    spec = importlib.util.spec_from_file_location("seed_demo_workspace", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


if __name__ == "__main__":
    asyncio.run(main())
