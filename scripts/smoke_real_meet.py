from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

import httpx


CORE_URL = os.getenv("ROBIN_CORE_URL", "http://127.0.0.1:8787")
TASK_REQUEST = (
    "Use the workspace files to compare actual 2024 quarterly results, identify the most "
    "important caveats, and make a concise cited presentation."
)


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
