from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from robin_core.config import DatabaseConfig, Settings, WorkspaceConfig
from robin_core.runtime import RobinRuntime


@pytest.mark.asyncio
async def test_runtime_subscribe_receives_join_updates(tmp_path: Path) -> None:
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path / "workspace"),
        database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    stream = runtime.subscribe()
    first = await anext(stream)

    assert first.meeting_url is None

    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    update = await anext(stream)

    assert update.meeting_url == "https://meet.google.com/abc-defg-hij"
    await stream.aclose()


@pytest.mark.asyncio
async def test_runtime_event_stream_receives_structured_envelopes(tmp_path: Path) -> None:
    settings = Settings(
        workspace=WorkspaceConfig(root=tmp_path / "workspace"),
        database=DatabaseConfig(path=tmp_path / "workspace" / "robin.db"),
    )
    runtime = RobinRuntime(settings)
    stream = runtime.subscribe_events()

    await runtime.join_meeting("https://meet.google.com/abc-defg-hij")
    events = []
    while not events or events[-1].type != "meeting.joined":
        events.append(await asyncio.wait_for(anext(stream), timeout=1))

    assert events[-1].type == "meeting.joined"
    assert events[-1].meeting_id == runtime.meeting_id
    assert events[-1].payload["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert runtime.recent_events()[-1].type == "meeting.joined"
    await stream.aclose()
