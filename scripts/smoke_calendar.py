from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import BrowserConfig, CalendarConfig, DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workspace = root / "RobinWorkspace"
    calendar_path = workspace / "source-data/calendar_smoke.json"
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "sessions" / "smoke-calendar" / "robin.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    start = datetime.now(timezone.utc) + timedelta(seconds=30)
    calendar_path.write_text(
        json.dumps(
            [
                {
                    "title": "Robin Calendar Smoke",
                    "start": start.isoformat(),
                    "end": (start + timedelta(minutes=30)).isoformat(),
                    "meeting_url": "https://meet.google.com/abc-defg-hij",
                }
            ],
            indent=2,
        )
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=db_path),
        browser=BrowserConfig(automation_mode="simulator"),
        calendar=CalendarConfig(enabled=True, file_path=calendar_path, lookahead_hours=4, auto_join=True, join_early_seconds=60),
    )
    runtime = RobinRuntime(settings)
    snapshot = runtime.calendar_snapshot()
    if snapshot.error or not snapshot.events:
        raise SystemExit(f"Calendar smoke did not find an event: {snapshot.error}")
    joined = await runtime.poll_calendar_once()
    if joined.meeting_url != "https://meet.google.com/abc-defg-hij":
        raise SystemExit(f"Auto-join did not join expected meeting: {joined.meeting_url}")
    print(f"Calendar smoke passed: {snapshot.events[0].title} -> {joined.meeting_url}")


if __name__ == "__main__":
    asyncio.run(main())
