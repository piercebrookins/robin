from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from robin_core.calendar import calendar_snapshot, read_local_calendar
from robin_core.config import BrowserConfig, CalendarConfig, DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
DTSTART:20260717T150000Z
DTEND:20260717T153000Z
SUMMARY:Finance Review
DESCRIPTION:https://meet.google.com/abc-defg-hij
END:VEVENT
BEGIN:VEVENT
UID:two
DTSTART:20260717T151500Z
DTEND:20260717T154500Z
SUMMARY:Overlapping Demo
LOCATION:https://meet.google.com/jkl-mnop-qrs
END:VEVENT
END:VCALENDAR
"""


def _ics_for(start: datetime, end: datetime) -> str:
    return f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
DTSTART:{start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:Finance Review
DESCRIPTION:https://meet.google.com/abc-defg-hij
END:VEVENT
END:VCALENDAR
"""


def test_local_ics_calendar_extracts_meet_events_and_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "calendar.ics"
    path.write_text(ICS)

    snapshot = calendar_snapshot(
        CalendarConfig(enabled=True, file_path=path, lookahead_hours=4),
        now=datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc),
    )

    assert snapshot.error is None
    assert len(snapshot.events) == 2
    assert snapshot.events[0].meeting_url == "https://meet.google.com/abc-defg-hij"
    assert snapshot.conflicts == [[snapshot.events[0].id, snapshot.events[1].id]]
    assert all(event.conflicted for event in snapshot.events)


def test_local_calendar_filters_out_events_without_meet_urls(tmp_path: Path) -> None:
    path = tmp_path / "calendar.ics"
    path.write_text(
        """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260717T150000Z
DTEND:20260717T153000Z
SUMMARY:No Link
END:VEVENT
END:VCALENDAR
"""
    )

    events = read_local_calendar(path, now=datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc), lookahead_hours=4)

    assert events == []


@pytest.mark.asyncio
async def test_runtime_joins_selected_calendar_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    calendar_path = source / "calendar.ics"
    start = datetime.now(timezone.utc).replace(microsecond=0)
    calendar_path.write_text(_ics_for(start=start, end=start + timedelta(minutes=30)))
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        browser=BrowserConfig(automation_mode="simulator"),
        calendar=CalendarConfig(enabled=True, file_path=calendar_path, lookahead_hours=24),
    )
    runtime = RobinRuntime(settings)
    event = runtime.calendar_snapshot().events[0]

    snapshot = await runtime.join_calendar_event(event.id)

    assert snapshot.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert snapshot.listening is True


@pytest.mark.asyncio
async def test_calendar_auto_join_enters_join_window_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    now = datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc)
    calendar_path = source / "calendar.json"
    calendar_path.write_text(
        """[
  {
    "title": "Auto Join Demo",
    "start": "2026-07-17T14:00:30+00:00",
    "end": "2026-07-17T14:30:00+00:00",
    "meeting_url": "https://meet.google.com/abc-defg-hij"
  }
]"""
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        browser=BrowserConfig(automation_mode="simulator"),
        calendar=CalendarConfig(enabled=True, file_path=calendar_path, lookahead_hours=4, auto_join=True, join_early_seconds=60),
    )
    runtime = RobinRuntime(settings)

    snapshot = await runtime.poll_calendar_once(now=now)

    assert snapshot.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert snapshot.listening is True
    assert any(event.type == "calendar.auto_join.started" for event in runtime.recent_events())


@pytest.mark.asyncio
async def test_calendar_auto_join_skips_conflicted_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    calendar_path = source / "calendar.ics"
    calendar_path.write_text(ICS)
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        browser=BrowserConfig(automation_mode="simulator"),
        calendar=CalendarConfig(enabled=True, file_path=calendar_path, lookahead_hours=4, auto_join=True, join_early_seconds=3600),
    )
    runtime = RobinRuntime(settings)

    snapshot = await runtime.poll_calendar_once(now=datetime(2026, 7, 17, 14, 30, tzinfo=timezone.utc))

    assert snapshot.meeting_url is None
    skipped = next(event for event in runtime.recent_events() if event.type == "calendar.auto_join.skipped")
    assert skipped.payload["reason"] == "conflict"


@pytest.mark.asyncio
async def test_calendar_auto_join_leaves_when_event_ends(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source-data"
    source.mkdir(parents=True)
    calendar_path = source / "calendar.json"
    calendar_path.write_text(
        """[
  {
    "title": "Short Demo",
    "start": "2026-07-17T14:00:00+00:00",
    "end": "2026-07-17T14:01:00+00:00",
    "meeting_url": "https://meet.google.com/abc-defg-hij"
  }
]"""
    )
    settings = Settings(
        workspace=WorkspaceConfig(root=workspace),
        database=DatabaseConfig(path=workspace / "robin.db"),
        browser=BrowserConfig(automation_mode="simulator"),
        calendar=CalendarConfig(enabled=True, file_path=calendar_path, lookahead_hours=4, auto_join=True, join_early_seconds=60),
    )
    runtime = RobinRuntime(settings)
    await runtime.poll_calendar_once(now=datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc))

    snapshot = await runtime.poll_calendar_once(now=datetime(2026, 7, 17, 14, 1, 1, tzinfo=timezone.utc))

    assert snapshot.meeting_state.value == "ENDED"
    assert any(event.type == "calendar.event.ended" for event in runtime.recent_events())
