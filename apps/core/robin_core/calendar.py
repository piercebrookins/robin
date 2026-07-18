from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CalendarConfig
from .schemas import CalendarEvent, CalendarSnapshot


MEET_RE = re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}", re.I)


def calendar_snapshot(config: CalendarConfig, now: datetime | None = None) -> CalendarSnapshot:
    if not config.enabled:
        return CalendarSnapshot(enabled=False, provider=config.provider, auto_join=config.auto_join)
    if config.provider != "local":
        return CalendarSnapshot(enabled=True, provider=config.provider, auto_join=config.auto_join, error=f"Unsupported calendar provider: {config.provider}")
    if not config.file_path:
        return CalendarSnapshot(enabled=True, provider=config.provider, auto_join=config.auto_join, error="calendar.file_path is not configured")
    if not config.file_path.exists():
        return CalendarSnapshot(enabled=True, provider=config.provider, auto_join=config.auto_join, error=f"Calendar file not found: {config.file_path}")
    try:
        events = read_local_calendar(config.file_path, now=now, lookahead_hours=config.lookahead_hours)
        conflicts = detect_conflicts(events)
        conflicted_ids = {event_id for pair in conflicts for event_id in pair}
        for event in events:
            event.conflicted = event.id in conflicted_ids
        return CalendarSnapshot(enabled=True, provider=config.provider, auto_join=config.auto_join, events=events, conflicts=conflicts)
    except Exception as exc:
        return CalendarSnapshot(enabled=True, provider=config.provider, auto_join=config.auto_join, error=str(exc))


def read_local_calendar(path: Path, now: datetime | None = None, lookahead_hours: int = 12) -> list[CalendarEvent]:
    current = now or datetime.now(timezone.utc)
    horizon = current + timedelta(hours=lookahead_hours)
    if path.suffix.lower() == ".json":
        records = json.loads(path.read_text())
        events = [_event_from_json(record, source=path.name) for record in records]
    else:
        events = _events_from_ics(path.read_text(), source=path.name)
    return sorted(
        [
            event
            for event in events
            if event.end >= current and event.start <= horizon
        ],
        key=lambda event: (event.start, event.title),
    )


def detect_conflicts(events: list[CalendarEvent]) -> list[list[str]]:
    conflicts: list[list[str]] = []
    for index, left in enumerate(events):
        for right in events[index + 1 :]:
            if left.start < right.end and right.start < left.end:
                conflicts.append([left.id, right.id])
    return conflicts


def _event_from_json(record: dict[str, Any], source: str) -> CalendarEvent:
    meeting_url = record.get("meeting_url") or _extract_meet_url(" ".join(str(value) for value in record.values()))
    if not meeting_url:
        raise ValueError(f"Calendar record has no Google Meet URL: {record.get('title', 'untitled')}")
    start = _parse_datetime(str(record["start"]))
    end = _parse_datetime(str(record["end"]))
    title = str(record.get("title") or "Untitled meeting")
    return CalendarEvent(id=_event_id(title, start, meeting_url), title=title, start=start, end=end, meeting_url=meeting_url, source=source)


def _events_from_ics(text: str, source: str) -> list[CalendarEvent]:
    lines = _unfold_ics(text)
    events: list[CalendarEvent] = []
    current: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT" and current is not None:
            event = _event_from_ics_props(current, source)
            if event:
                events.append(event)
            current = None
            continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.split(";", 1)[0].upper()] = value
    return events


def _event_from_ics_props(props: dict[str, str], source: str) -> CalendarEvent | None:
    blob = " ".join(props.values())
    meeting_url = _extract_meet_url(blob)
    if not meeting_url or "DTSTART" not in props or "DTEND" not in props:
        return None
    title = props.get("SUMMARY") or "Untitled meeting"
    start = _parse_datetime(props["DTSTART"])
    end = _parse_datetime(props["DTEND"])
    return CalendarEvent(id=_event_id(title, start, meeting_url), title=title, start=start, end=end, meeting_url=meeting_url, source=source)


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _extract_meet_url(text: str) -> str | None:
    match = MEET_RE.search(text)
    return match.group(0) if match else None


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z") and "T" in value and "-" not in value:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if "T" in value and "-" not in value:
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_id(title: str, start: datetime, meeting_url: str) -> str:
    digest = hashlib.sha1(f"{title}|{start.isoformat()}|{meeting_url}".encode()).hexdigest()
    return digest[:16]
