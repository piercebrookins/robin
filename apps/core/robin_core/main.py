from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .runtime import RobinRuntime
from .browser.operator_agent import BrowserOperatorResult
from .preflight import run_preflight
from .schemas import (
    AudioCaptureSampleRequest,
    AudioListenLoopRequest,
    AudioTranscribeRequest,
    BrowserOperatorRequest,
    CalendarAutoJoinRequest,
    CalendarSnapshot,
    EventEnvelope,
    FileIndexRecord,
    JoinMeetingRequest,
    PresentationGotoRequest,
    PresentationSession,
    RehearsalConfirmationRequest,
    RehearsalEvidence,
    RuntimeSnapshot,
    RuntimeMetrics,
    TaskCreateRequest,
    TranscriptIngestRequest,
    WorkspaceSnapshot,
)


runtime = RobinRuntime()


def _cors_origins() -> list[str]:
    origins = {"http://127.0.0.1:3000", "http://localhost:3000"}
    parsed = urlparse(runtime.settings.presentation.base_url)
    if parsed.scheme and parsed.netloc:
        origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return sorted(origins)


app = FastAPI(title="Robin Core", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    if runtime.settings.calendar.enabled and runtime.settings.calendar.auto_join:
        await runtime.set_calendar_auto_join(True)


@app.on_event("shutdown")
async def shutdown() -> None:
    if runtime.settings.calendar.auto_join:
        await runtime.set_calendar_auto_join(False)


@app.get("/health")
async def health() -> dict:
    runtime.refresh_health()
    health_items = [item.model_dump(mode="json") for item in runtime.health]
    return {
        "ok": all(item["ok"] for item in health_items),
        "state": runtime.runtime_state,
        "health": health_items,
    }


@app.get("/api/preflight")
async def preflight() -> dict:
    checks = run_preflight(runtime.settings)
    return {
        "ok": all(item.ok for item in checks),
        "checks": [item.model_dump(mode="json") for item in checks],
    }


@app.post("/api/audio/bridge/refresh", response_model=RuntimeSnapshot)
async def refresh_audio_bridge() -> RuntimeSnapshot:
    try:
        await runtime.refresh_bridge_health()
        return await runtime.publish()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/audio/test/output", response_model=RuntimeSnapshot)
async def test_audio_output() -> RuntimeSnapshot:
    try:
        return await runtime.test_audio_output()
    except Exception as exc:
        await runtime.emit_event("audio.output.test.failed", {"error": str(exc)}, component="audio")
        await runtime.publish()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/audio/test/input")
async def test_audio_input() -> dict:
    try:
        return await runtime.test_audio_input()
    except Exception as exc:
        await runtime.emit_event("audio.input.test.failed", {"error": str(exc)}, component="audio")
        await runtime.publish()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/state", response_model=RuntimeSnapshot)
async def state() -> RuntimeSnapshot:
    runtime.refresh_health()
    return runtime.snapshot()


@app.get("/api/calendar", response_model=CalendarSnapshot)
async def calendar() -> CalendarSnapshot:
    return runtime.calendar_snapshot()


@app.get("/api/events", response_model=list[EventEnvelope])
async def events(limit: int = 100) -> list[EventEnvelope]:
    return runtime.recent_events(max(1, min(limit, 500)))


@app.get("/api/metrics", response_model=RuntimeMetrics)
async def metrics() -> RuntimeMetrics:
    return runtime.metrics()


@app.get("/api/workspace", response_model=WorkspaceSnapshot)
async def workspace() -> WorkspaceSnapshot:
    return runtime.workspace_snapshot()


@app.post("/api/workspace/reindex", response_model=WorkspaceSnapshot)
async def reindex_workspace() -> WorkspaceSnapshot:
    try:
        return await runtime.reindex_workspace()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workspace/files", response_model=list[FileIndexRecord])
async def workspace_files() -> list[FileIndexRecord]:
    return runtime.workspace_snapshot().files


@app.get("/api/workspace/files/{file_id}", response_model=FileIndexRecord)
async def workspace_file(file_id: UUID) -> FileIndexRecord:
    try:
        return runtime.workspace_file(file_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.websocket("/ws/state")
async def state_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async for snapshot in runtime.subscribe():
            await websocket.send_text(snapshot.model_dump_json())
    except WebSocketDisconnect:
        return


@app.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async for event in runtime.subscribe_events():
            await websocket.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        return


@app.post("/api/meeting/join", response_model=RuntimeSnapshot)
async def join_meeting(request: JoinMeetingRequest) -> RuntimeSnapshot:
    try:
        return await runtime.join_meeting(
            request.meeting_url,
            start_listening=request.start_listening,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/calendar/events/{event_id}/join", response_model=RuntimeSnapshot)
async def join_calendar_event(event_id: str) -> RuntimeSnapshot:
    try:
        return await runtime.join_calendar_event(event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/calendar/auto-join", response_model=RuntimeSnapshot)
async def set_calendar_auto_join(request: CalendarAutoJoinRequest) -> RuntimeSnapshot:
    try:
        return await runtime.set_calendar_auto_join(request.enabled, request.interval_seconds)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/meeting/leave", response_model=RuntimeSnapshot)
async def leave_meeting() -> RuntimeSnapshot:
    return await runtime.leave_meeting()


@app.post("/api/transcript", response_model=RuntimeSnapshot)
async def ingest_transcript(request: TranscriptIngestRequest) -> RuntimeSnapshot:
    return await runtime.ingest_transcript(
        request.text,
        speaker_name=request.speaker_name,
        started_at_ms=request.started_at_ms,
        ended_at_ms=request.ended_at_ms,
    )


@app.post("/api/audio/transcribe", response_model=RuntimeSnapshot)
async def transcribe_audio(request: AudioTranscribeRequest) -> RuntimeSnapshot:
    try:
        return await runtime.transcribe_audio_file(request.path, request.speaker_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/audio/capture/sample")
async def capture_audio_sample(request: AudioCaptureSampleRequest) -> dict:
    try:
        return await runtime.capture_audio_sample(
            bundle_id=request.bundle_id,
            duration_ms=request.duration_ms,
            output_name=request.output_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/audio/listen/start", response_model=RuntimeSnapshot)
async def start_audio_listen(request: AudioListenLoopRequest) -> RuntimeSnapshot:
    try:
        return await runtime.start_listening_loop(
            bundle_id=request.bundle_id,
            duration_ms=request.duration_ms,
            interval_ms=request.interval_ms,
            max_iterations=request.max_iterations,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/audio/listen/stop", response_model=RuntimeSnapshot)
async def stop_audio_listen() -> RuntimeSnapshot:
    return await runtime.stop_listening_loop()


@app.post("/api/operator/browser", response_model=BrowserOperatorResult)
async def run_browser_operator(request: BrowserOperatorRequest) -> BrowserOperatorResult:
    try:
        return await runtime.run_browser_operator(
            request.request,
            page_name=request.page_name,
            approval_token=request.approval_token,
        )
    except Exception as exc:
        await runtime.emit_event(
            "browser.operator.failed", {"error": str(exc)}, component="browser_operator"
        )
        await runtime.publish()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tasks", response_model=RuntimeSnapshot)
async def create_task(request: TaskCreateRequest) -> RuntimeSnapshot:
    await runtime.create_task(request.text, request.requester_name)
    return runtime.snapshot()


@app.post("/api/tasks/{task_id}/cancel", response_model=RuntimeSnapshot)
async def cancel_task(task_id: UUID) -> RuntimeSnapshot:
    try:
        await runtime.cancel_task(task_id)
        return runtime.snapshot()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/retry", response_model=RuntimeSnapshot)
async def retry_task(task_id: UUID) -> RuntimeSnapshot:
    try:
        return await runtime.retry_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/present", response_model=RuntimeSnapshot)
async def present_task(task_id: UUID) -> RuntimeSnapshot:
    try:
        return await runtime.present_task(task_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentations/stop", response_model=RuntimeSnapshot)
async def stop_presenting() -> RuntimeSnapshot:
    try:
        return await runtime.stop_presenting()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentation-handoff/lower-hand", response_model=RuntimeSnapshot)
async def lower_presentation_hand() -> RuntimeSnapshot:
    try:
        return await runtime.lower_presentation_hand()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/presentations/{task_id}", response_model=PresentationSession)
async def presentation_state(task_id: UUID) -> PresentationSession:
    try:
        return runtime.presentation_state(task_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/presentations/{task_id}/activate", response_model=PresentationSession)
async def activate_presentation(task_id: UUID) -> PresentationSession:
    try:
        state = runtime.activate_presentation(task_id)
        await runtime.publish()
        return state
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentations/{task_id}/stop", response_model=RuntimeSnapshot)
async def stop_task_presentation(task_id: UUID) -> RuntimeSnapshot:
    try:
        return await runtime.stop_presenting(task_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentations/{task_id}/next", response_model=PresentationSession)
async def next_presentation_slide(task_id: UUID) -> PresentationSession:
    try:
        return await runtime.navigate_presentation(task_id, "next")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentations/{task_id}/previous", response_model=PresentationSession)
async def previous_presentation_slide(task_id: UUID) -> PresentationSession:
    try:
        return await runtime.navigate_presentation(task_id, "previous")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentations/{task_id}/goto/{index}", response_model=PresentationSession)
async def goto_presentation_slide_path(task_id: UUID, index: int) -> PresentationSession:
    try:
        return await runtime.navigate_presentation(task_id, "goto", index=index)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/presentations/{task_id}/goto", response_model=PresentationSession)
async def goto_presentation_slide(
    task_id: UUID, request: PresentationGotoRequest
) -> PresentationSession:
    try:
        return await runtime.navigate_presentation(task_id, "goto", index=request.index)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/emergency-stop", response_model=RuntimeSnapshot)
async def emergency_stop() -> RuntimeSnapshot:
    return await runtime.emergency_stop()


@app.post("/api/rehearsals/confirm", response_model=RehearsalEvidence)
async def confirm_rehearsal(
    request: RehearsalConfirmationRequest,
) -> RehearsalEvidence:
    try:
        return await runtime.record_rehearsal_confirmation(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/artifacts/{artifact_path:path}")
async def artifact(artifact_path: str):
    try:
        path = runtime.artifact_path(artifact_path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(Path(path))
