from __future__ import annotations

import asyncio
import os
import re
import time
from uuid import uuid4

import httpx


CORE_URL = os.getenv("ROBIN_CORE_URL", "http://127.0.0.1:8787")
TASK_REQUEST = (
    "Use the workspace files to compare actual 2024 quarterly results, identify the most "
    "important caveats, and make a concise cited presentation."
)


async def wait_for_audio_ready(runtime, timeout_s: float = 15.0):
    """Wait until live capture and transcription report healthy state."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = getattr(getattr(runtime, "audio", None), "runtime_state", None)
        if state is not None and state.capture_state == "capturing" and state.transcription_state == "connected":
            return state
        await asyncio.sleep(0.1)
    raise RuntimeError("Live audio capture/transcription did not become ready in time.")


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


async def wait_for_phrase(runtime, phrase: str, timeout_s: float = 15.0):
    deadline = time.monotonic() + timeout_s
    wanted = normalize(phrase)
    while time.monotonic() < deadline:
        for segment in reversed(getattr(runtime, "transcript", [])):
            if segment.source == "audio_stt" and wanted in normalize(segment.text):
                return segment
        await asyncio.sleep(0.01)
    raise RuntimeError(f"Did not hear the expected audio phrase: {phrase}")


def confirm_reply_heard(reply: str, second_participant_confirmed: bool) -> bool:
    if second_participant_confirmed:
        return True
    return os.getenv("ROBIN_REAL_MEET_REPLY_CONFIRMED", "").casefold() in {"1", "true", "yes"}


def validate_smoke_evidence(evidence: dict) -> None:
    participant = evidence.get("participant_transcript") or {}
    if participant.get("source") != "audio_stt":
        raise SystemExit("Participant transcript did not come from audio STT.")
    before = evidence.get("audio_before_cleanup") or {}
    if before.get("capture_state") != "capturing" or before.get("transcription_state") != "connected":
        raise SystemExit("Audio was not live during the smoke.")
    if before.get("last_frame_timestamp_ms") is None or before.get("last_frame_sequence") is None:
        raise SystemExit("Audio was not live during the smoke.")
    after = evidence.get("audio_after_cleanup") or {}
    if any(after.get(key) != "idle" for key in ("capture_state", "transcription_state", "playback_state")):
        raise SystemExit(f"Audio did not stop cleanly: {after}")
    if evidence.get("cleanup_elapsed_ms", 999999) > 2000:
        raise SystemExit("Audio cleanup exceeded two seconds.")
    if evidence.get("muted_after_cleanup") is not True:
        raise SystemExit("Robin was not muted after cleanup.")
    if evidence.get("transcription_session_active_after_cleanup"):
        raise SystemExit("Transcription session still active after cleanup.")
    if evidence.get("bridge_process_alive_after_cleanup"):
        raise SystemExit(f"Bridge process still alive: pid={evidence.get('bridge_pid_before_cleanup')}")
    if not any(
        event.get("type") in {"runtime.emergency_stop", "meeting.leave.cleanup"}
        for event in evidence.get("recent_events", [])
    ):
        raise SystemExit("Missing cleanup event.")


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> dict:
    response = await client.post(path, json=body or {})
    if not response.is_success:
        raise RuntimeError(f"{path} failed: {response.text}")
    return response.json()


async def main() -> None:
    meeting_url = os.getenv("ROBIN_REAL_MEET_URL")
    if not meeting_url:
        raise SystemExit("Set ROBIN_REAL_MEET_URL to a live Google Meet URL before running this smoke.")
    async with httpx.AsyncClient(base_url=CORE_URL, timeout=240) as client:
        try:
            initial = (await client.get("/api/state")).raise_for_status().json()
        except Exception as exc:
            raise SystemExit(
                f"Robin core is not running at {CORE_URL}. Start it with `make robin`."
            ) from exc
        existing_ids = {task["id"] for task in initial["tasks"]}
        task_id: str | None = None
        try:
            await post(
                client,
                "/api/meeting/join",
                {"meeting_url": meeting_url, "start_listening": False},
            )
            created = await post(
                client,
                "/api/tasks",
                {
                    "text": f"{TASK_REQUEST} Rehearsal run {uuid4().hex[:8]}.",
                    "requester_name": "Real Meet smoke",
                },
            )
            new_tasks = [task for task in created["tasks"] if task["id"] not in existing_ids]
            if len(new_tasks) != 1:
                raise RuntimeError(f"Expected one new task, found {len(new_tasks)}.")
            task_id = new_tasks[0]["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                state = (await client.get("/api/state")).raise_for_status().json()
                task = next(task for task in state["tasks"] if task["id"] == task_id)
                if task["status"] == "READY_TO_PRESENT":
                    validation = [
                        artifact
                        for artifact in state["artifacts"]
                        if artifact["task_id"] == task_id
                        and artifact["type"] == "validation_json"
                    ]
                    if not validation:
                        raise RuntimeError("Task became ready without validation evidence.")
                    break
                if task["status"] in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(
                        f"Task did not become ready: {task['status']} {task.get('error')}"
                    )
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Task did not become ready within 180 seconds.")
            await post(client, f"/api/tasks/{task_id}/present")
            print(f"Real Meet smoke passed through the live core API: task={task_id}")
        finally:
            try:
                await post(client, "/api/presentations/stop")
            except Exception:
                pass
            try:
                await post(client, "/api/meeting/leave")
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
