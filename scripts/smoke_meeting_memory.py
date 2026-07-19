from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.config import load_settings
from robin_core.memory import MeetingMemoryManager
from robin_core.schemas import TranscriptSegment


def turn(meeting_id, text: str, speaker: str, offset: int) -> TranscriptSegment:
    return TranscriptSegment(
        meeting_id=meeting_id,
        speaker_name=speaker,
        text=text,
        started_at_ms=offset,
        ended_at_ms=offset + 100,
        source="simulator",
    )


async def main() -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for the live memory smoke test.")
    manager = MeetingMemoryManager(settings)
    meeting_id = uuid4()
    memory = []

    first = turn(
        meeting_id,
        "We decided that Morgan owns the launch checklist and it is due Friday.",
        "Avery",
        0,
    )
    additions, resolved = await manager.extract(first, memory)
    manager.merge(memory, additions, resolved)
    decision = next((item for item in memory if item.kind == "decision"), None)
    if decision is None:
        raise SystemExit(f"Initial decision was not extracted: {memory}")

    correction = turn(
        meeting_id,
        "Correction: cancel that prior ownership decision. Taylor now owns the launch checklist, due Monday.",
        "Avery",
        200,
    )
    additions, resolved = await manager.extract(correction, memory)
    manager.merge(memory, additions, resolved)
    if decision.status == "active":
        raise SystemExit("Correction did not resolve or supersede the prior decision.")
    if not any(
        item.kind in {"correction", "commitment", "decision"}
        and "Taylor" in item.text
        for item in memory
    ):
        raise SystemExit(f"Corrected owner was not retained: {memory}")
    print(
        "Meeting memory passed: "
        f"prior={decision.status}, added={len(memory)}, sources="
        f"{sum(len(item.source_segment_ids) for item in memory)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
