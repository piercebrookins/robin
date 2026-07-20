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
INVITATION_PHRASE = os.getenv(
    "ROBIN_REAL_MEET_INVITATION_PHRASE",
    "Robin, I see your hand is raised. Could you share?",
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


def find_task(state: dict, task_id: str) -> dict:
    return next(task for task in state["tasks"] if task["id"] == task_id)


def handoff_for_task(state: dict, task_id: str) -> dict:
    handoff = state.get("presentation_handoff") or {}
    if handoff.get("task_id") != task_id:
        raise RuntimeError(f"Presentation handoff is not assigned to task {task_id}: {handoff}")
    return handoff


def validate_waiting_handoff(state: dict, task_id: str) -> None:
    task = find_task(state, task_id)
    if task["status"] != "READY_TO_PRESENT":
        raise RuntimeError(f"Task is not ready to present: {task['status']}")
    if not task.get("presentation_ready_at"):
        raise RuntimeError("Ready task is missing presentation_ready_at.")
    handoff = handoff_for_task(state, task_id)
    if handoff.get("state") != "WAITING_FOR_INVITATION" or handoff.get("hand_raised") is not True:
        raise RuntimeError(f"Robin did not raise its hand for the ready task: {handoff}")


def saw_autonomous_handoff(events: list[dict], task_id: str) -> bool:
    task_events = [event for event in events if event.get("task_id") == task_id]
    required = {
        "presentation.handoff.queued",
        "meeting.hand.raised",
        "presentation.invitation.detected",
        "presentation.handoff.started",
        "presentation.completed",
    }
    seen = {event.get("type") for event in task_events}
    return required <= seen


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> dict:
    response = await client.post(path, json=body or {})
    if not response.is_success:
        raise RuntimeError(f"{path} failed: {response.text}")
    return response.json()


async def get_json(client: httpx.AsyncClient, path: str) -> dict:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def wait_for_ready_handoff(
    client: httpx.AsyncClient, task_id: str, timeout_s: float = 180.0
) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = await get_json(client, "/api/state")
        task = find_task(state, task_id)
        if task["status"] in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Task did not become ready: {task['status']} {task.get('error')}")
        handoff = state.get("presentation_handoff") or {}
        if (
            task["status"] == "READY_TO_PRESENT"
            and handoff.get("task_id") == task_id
            and handoff.get("state") == "WAITING_FOR_INVITATION"
            and handoff.get("hand_raised") is True
        ):
            validation = [
                artifact
                for artifact in state["artifacts"]
                if artifact["task_id"] == task_id and artifact["type"] == "validation_json"
            ]
            if not validation:
                raise RuntimeError("Task became ready without validation evidence.")
            validate_waiting_handoff(state, task_id)
            return state
        await asyncio.sleep(0.5)
    raise RuntimeError("Robin did not become ready with hand raised before the deadline.")


async def wait_for_transcribed_invitation(
    client: httpx.AsyncClient, phrase: str, timeout_s: float = 60.0
) -> dict:
    deadline = time.monotonic() + timeout_s
    wanted = normalize(phrase)
    while time.monotonic() < deadline:
        state = await get_json(client, "/api/state")
        for segment in reversed(state.get("transcript", [])):
            if segment.get("source") in {"audio_stt", "merged"} and wanted in normalize(
                segment.get("text", "")
            ):
                return segment
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Did not hear the second participant invitation: {phrase}")


async def wait_for_autonomous_completion(
    client: httpx.AsyncClient, task_id: str, timeout_s: float = 180.0
) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = await get_json(client, "/api/state")
        task = find_task(state, task_id)
        if task["status"] == "COMPLETED":
            events = await get_json(client, "/api/events?limit=500")
            if not saw_autonomous_handoff(events, task_id):
                raise RuntimeError("Task completed without the expected handoff event chain.")
            return state
        if task["status"] in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Presentation did not complete: {task['status']} {task.get('error')}")
        await asyncio.sleep(0.5)
    raise RuntimeError("Autonomous handoff presentation did not complete before the deadline.")


async def main() -> None:
    meeting_url = os.getenv("ROBIN_REAL_MEET_URL")
    if not meeting_url:
        raise SystemExit("Set ROBIN_REAL_MEET_URL to a live Google Meet URL before running this smoke.")
    async with httpx.AsyncClient(base_url=CORE_URL, timeout=240) as client:
        try:
            initial = await get_json(client, "/api/state")
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
                {"meeting_url": meeting_url, "start_listening": True},
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
            await wait_for_ready_handoff(client, task_id)
            print(
                "\nRobin's hand should now be raised. From the second participant account, say:\n"
                f"  {INVITATION_PHRASE}\n",
                flush=True,
            )
            await wait_for_transcribed_invitation(client, INVITATION_PHRASE)
            await wait_for_autonomous_completion(client, task_id)
            print(f"Real Meet handoff smoke passed: task={task_id}")
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
