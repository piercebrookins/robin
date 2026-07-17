"use client";

import { Activity, AlertTriangle, BarChart3, CalendarDays, CheckCircle2, Database, Mic, MonitorUp, Play, Radio, RefreshCw, Square, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CORE_URL, CORE_WS_URL, getCalendar, getEvents, getMetrics, getPreflight, getState, getWorkspace, postJson } from "../lib/api";
import type { Artifact, AudioCaptureResult, CalendarSnapshot, EventEnvelope, PreflightSnapshot, RuntimeMetrics, RuntimeSnapshot, WorkspaceSnapshot } from "../lib/types";

export default function Dashboard() {
  const [state, setState] = useState<RuntimeSnapshot | null>(null);
  const [preflight, setPreflight] = useState<PreflightSnapshot | null>(null);
  const [calendar, setCalendar] = useState<CalendarSnapshot | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [metrics, setMetrics] = useState<RuntimeMetrics | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [meetingUrl, setMeetingUrl] = useState("https://meet.google.com/abc-defg-hij");
  const [taskText, setTaskText] = useState("Robin, use the finance files to compare our 2024 quarterly results and make a few slides.");
  const [captureResult, setCaptureResult] = useState<AudioCaptureResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setState(await getState());
      setPreflight(await getPreflight());
      setCalendar(await getCalendar());
      setEvents(await getEvents());
      setMetrics(await getMetrics());
      setWorkspace(await getWorkspace());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 1500);
    const socket = new WebSocket(`${CORE_WS_URL}/ws/state`);
    socket.onmessage = (event) => {
      setState(JSON.parse(event.data) as RuntimeSnapshot);
      getPreflight().then(setPreflight).catch(() => undefined);
      getCalendar().then(setCalendar).catch(() => undefined);
      getEvents().then(setEvents).catch(() => undefined);
      getMetrics().then(setMetrics).catch(() => undefined);
      getWorkspace().then(setWorkspace).catch(() => undefined);
    };
    return () => {
      window.clearInterval(id);
      socket.close();
    };
  }, []);

  const activeTasks = useMemo(() => state?.tasks.filter((task) => !["COMPLETED", "CANCELLED", "FAILED"].includes(task.status)) ?? [], [state]);

  async function act(path: string, body?: unknown) {
    try {
      setState(await postJson<RuntimeSnapshot>(path, body));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function captureSample() {
    try {
      setCaptureResult(await postJson<AudioCaptureResult>("/api/audio/capture/sample", { bundle_id: "com.google.Chrome", duration_ms: 1500 }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function reindexWorkspace() {
    try {
      setWorkspace(await postJson<WorkspaceSnapshot>("/api/workspace/reindex"));
      setEvents(await getEvents());
      setMetrics(await getMetrics());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="status-dot" /> Robin</div>
        <div className="list">
          <span className="pill">Runtime: {state?.runtime_state ?? "loading"}</span>
          <span className="pill">Meeting: {state?.meeting_state ?? "loading"}</span>
          <span className="pill">Listening: {state?.listening ? "yes" : "no"}</span>
          <span className="pill">Capture: {state?.capture_loop_running ? "running" : "stopped"}</span>
          <span className="pill">Auto-join: {state?.calendar_auto_join_running ? "running" : "stopped"}</span>
          <span className="pill">Presenting: {state?.presenting ? "yes" : "no"}</span>
        </div>
      </aside>

      <section className="main">
        <div className="toolbar">
          <input value={meetingUrl} onChange={(event) => setMeetingUrl(event.target.value)} aria-label="Google Meet URL" />
          <button className="primary" onClick={() => act("/api/meeting/join", { meeting_url: meetingUrl })} title="Join meeting"><Play size={16} /></button>
          <button onClick={() => act("/api/meeting/leave")} title="Leave meeting"><Square size={16} /></button>
          <button className="danger" onClick={() => act("/api/emergency-stop")} title="Emergency stop"><AlertTriangle size={16} /></button>
        </div>

        {error && <p className="bad">{error}</p>}

        <div className="grid">
          <section className="panel">
            <h2>Delegate Work</h2>
            <div className="toolbar">
              <input value={taskText} onChange={(event) => setTaskText(event.target.value)} aria-label="Simulated transcript or task text" />
              <button className="primary" onClick={() => act("/api/transcript", { speaker_name: "Demo", text: taskText })} title="Send transcript"><Mic size={16} /></button>
            </div>
          </section>

          <section className="panel">
            <h2>Health</h2>
            <div className="health">
              {state?.health.map((item) => (
                <div className="health-line" key={item.name}>
                  {item.ok ? <CheckCircle2 className="ok" size={17} /> : <XCircle className="bad" size={17} />}
                  <span><strong>{item.name}</strong> <span className="muted">{item.detail}</span></span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="panel" style={{ marginTop: 14 }}>
          <h2>Preflight</h2>
          <div className="health">
            {preflight?.checks.map((item) => (
              <div className="health-line" key={item.name}>
                {item.ok ? <CheckCircle2 className="ok" size={17} /> : <XCircle className="bad" size={17} />}
                <span><strong>{item.name}</strong> <span className="muted">{item.detail}</span></span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel" style={{ marginTop: 14 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Workspace</h2>
            <button onClick={reindexWorkspace} title="Reindex workspace"><RefreshCw size={16} />Reindex</button>
          </div>
          <div className="muted">{workspace?.root ?? "loading"} · {workspace?.file_count ?? 0} files</div>
          <div className="list" style={{ marginTop: 10 }}>
            {workspace?.files.slice(0, 6).map((file) => (
              <div className="health-line" key={file.id}>
                <Database size={17} />
                <span>
                  <strong>{file.relative_path}</strong>
                  <span className="muted"> {file.file_type} · {(file.size_bytes / 1024).toFixed(1)} KB</span>
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel" style={{ marginTop: 14 }}>
          <h2>Audio Capture</h2>
          <div className="toolbar">
            <button onClick={captureSample} title="Capture audio sample"><Mic size={16} />Capture Sample</button>
            <button className="primary" onClick={() => act("/api/audio/listen/start", {})} title="Start audio listening loop"><Radio size={16} />Start Listening</button>
            <button onClick={() => act("/api/audio/listen/stop")} title="Stop audio listening loop"><Square size={16} />Stop Listening</button>
            {captureResult && <span className={captureResult.ok ? "ok" : "bad"}>{captureResult.ok ? `Saved ${captureResult.path}` : captureResult.error}</span>}
          </div>
        </section>

        <section className="panel" style={{ marginTop: 14 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Calendar</h2>
            <div className="toolbar">
              <span className="pill">Auto: {calendar?.auto_join ? "enabled" : "disabled"}</span>
              <button onClick={() => act("/api/calendar/auto-join", { enabled: !calendar?.auto_join, interval_seconds: 5 })} title="Toggle calendar auto-join">
                <CalendarDays size={16} />{calendar?.auto_join ? "Disable" : "Enable"}
              </button>
            </div>
          </div>
          {!calendar?.enabled && <div className="muted">Calendar discovery is disabled.</div>}
          {calendar?.error && <div className="bad">{calendar.error}</div>}
          <div className="list">
            {calendar?.events.map((event) => (
              <div className="item" key={event.id}>
                <div className="row">
                  <CalendarDays size={16} />
                  <div className="item-title">{event.title}</div>
                  {event.conflicted && <span className="pill bad">conflict</span>}
                </div>
                <div className="muted">{new Date(event.start).toLocaleString()} · {event.meeting_url}</div>
                <div className="toolbar" style={{ marginTop: 8 }}>
                  <button className="primary" onClick={() => act(`/api/calendar/events/${event.id}/join`)} title="Join calendar meeting"><Play size={16} />Join</button>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel" style={{ marginTop: 14 }}>
          <h2>Observability</h2>
          <div className="metric-strip">
            <span className="pill">Events: {metrics?.event_count ?? 0}</span>
            <span className="pill">Tasks: {metrics?.task_count ?? 0}</span>
            <span className="pill">Artifacts: {metrics?.artifact_count ?? 0}</span>
            <span className="pill">Speech: {metrics?.speech_count ?? 0}</span>
          </div>
          <div className="list" style={{ marginTop: 10 }}>
            {events.slice().reverse().slice(0, 6).map((event) => (
              <div className="health-line" key={event.id ?? `${event.type}-${event.timestamp}`}>
                <Activity size={17} />
                <span><strong>{event.type}</strong> <span className="muted">{event.component} · {new Date(event.timestamp).toLocaleTimeString()}</span></span>
              </div>
            ))}
          </div>
        </section>

        <div className="grid">
          <section className="panel">
            <h2>Tasks</h2>
            <div className="list">
              {state?.tasks.map((task) => {
                const deck = latestArtifact(state.artifacts, task.id, "deck_json");
                const pptx = latestArtifact(state.artifacts, task.id, "deck_pptx", deck?.revision);
                const validation = latestArtifact(state.artifacts, task.id, "validation_json", deck?.revision);
                return (
                  <div className="item" key={task.id}>
                    <div className="row">
                      <BarChart3 size={16} />
                      <div className="item-title">{task.title}</div>
                      <span className="pill">{task.status}</span>
                      {validation && <span className={task.status === "FAILED" ? "pill bad" : "pill ok"}>validated</span>}
                    </div>
                    <div className="muted">Revision {task.revision}{validation ? ` · ${validation.path}` : ""}{task.error ? ` · ${task.error}` : ""}</div>
                    <div className="toolbar" style={{ marginTop: 8 }}>
                      {deck?.url && <a href={deck.url} target="_blank"><button title="Open presentation"><MonitorUp size={16} /></button></a>}
                      {pptx && <a href={`${CORE_URL}/api/artifacts/${pptx.path}`} target="_blank"><button title="Download PPTX">PPTX</button></a>}
                      {deck && <button onClick={() => act(`/api/tasks/${task.id}/present`)}>Present</button>}
                      {deck && state.presenting && <button onClick={() => act(`/api/presentations/${task.id}/stop`)}>Stop Presenting</button>}
                      {["FAILED", "CANCELLED", "COMPLETED"].includes(task.status) && <button onClick={() => act(`/api/tasks/${task.id}/retry`)}><RefreshCw size={16} />Retry</button>}
                      {activeTasks.some((active) => active.id === task.id) && <button onClick={() => act(`/api/tasks/${task.id}/cancel`)}>Cancel</button>}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="panel">
            <h2>Live Transcript</h2>
            <div className="list">
              {state?.transcript.slice().reverse().map((segment) => (
                <div className="item" key={segment.id}>
                  <div className="item-title">{segment.speaker_name ?? "Participant"}</div>
                  <div>{segment.text}</div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="panel" style={{ marginTop: 14 }}>
          <h2>Speech</h2>
          <div className="list">
            {state?.speech.slice().reverse().slice(0, 5).map((speech) => (
              <div className="item" key={speech.id}>
                <div className="item-title">{speech.mode} · {speech.voice} · {speech.format}</div>
                <div>{speech.text}</div>
                <div className="muted">{speech.byte_count} bytes{speech.path ? ` · ${speech.path}` : ""}</div>
              </div>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}

function latestArtifact(artifacts: Artifact[], taskId: string, type: Artifact["type"], revision?: number): Artifact | undefined {
  const matches = artifacts.filter((artifact) => artifact.task_id === taskId && artifact.type === type && (revision === undefined || artifact.revision === revision));
  return matches.sort((a, b) => b.revision - a.revision)[0];
}
