from __future__ import annotations

import pytest

from robin_core import main
from robin_core.schemas import HealthItem, RuntimeState


class FakeRuntime:
    runtime_state = RuntimeState.READY

    def __init__(self) -> None:
        self.health = [
            HealthItem(name="workspace", ok=True, detail="ready"),
            HealthItem(name="audio_capture", ok=False, detail="not capturing"),
        ]
        self.refresh_count = 0

    def refresh_health(self) -> None:
        self.refresh_count += 1


@pytest.mark.asyncio
async def test_health_endpoint_aggregates_required_health(monkeypatch) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(main, "runtime", runtime)

    response = await main.health()

    assert response["ok"] is False
    assert response["state"] == RuntimeState.READY
    assert runtime.refresh_count == 1
