"use client";

import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  LoaderCircle,
  LogOut,
  Mic,
  MonitorUp,
  Play,
  RefreshCw,
  Volume2,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CORE_URL, CORE_WS_URL, getPreflight, getState, postJson } from "../lib/api";
import type { Artifact, EventEnvelope, PreflightSnapshot, RuntimeSnapshot } from "../lib/types";

const ACTIVE_TASKS = ["AWAITING_CLARIFICATION", "ACCEPTED", "QUEUED", "EXECUTING", "VALIDATING", "READY_TO_PRESENT", "PRESENTING"];
const ACTIVE_MEETING = ["NAVIGATING", "PREJOIN", "REQUESTING_ADMISSION", "JOINED", "LISTENING", "SPEAKING", "PRESENTING"];

export default function Dashboard() {
  const [state, setState] = useState<RuntimeSnapshot | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [meetingUrl, setMeetingUrl] = useState("");
  const [manualTask, setManualTask] = useState("Robin, use the finance files to compare our 2024 quarterly results and make a few slides.");
  const [connection, setConnection] = useState<"connecting" | "live" | "offline">("connecting");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<PreflightSnapshot | null>(null);
  const [audioTestMessage, setAudioTestMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    let stateSocket: WebSocket | undefined;
    let eventSocket: WebSocket | undefined;

    const connect = () => {
      if (cancelled) return;
      setConnection("connecting");
      getState().then(setState).catch(() => undefined);

      stateSocket = new WebSocket(`${CORE_WS_URL}/ws/state`);
      eventSocket = new WebSocket(`${CORE_WS_URL}/ws/events`);
      stateSocket.onopen = () => {
        setConnection("live");
        setError(null);
      };
      stateSocket.onmessage = (message) => setState(JSON.parse(message.data) as RuntimeSnapshot);
      eventSocket.onmessage = (message) => {
        const next = JSON.parse(message.data) as EventEnvelope;
        setEvents((current) => {
          if (next.id !== null && current.some((item) => item.id === next.id)) return current;
          return [...current, next].slice(-80);
        });
      };
      stateSocket.onclose = () => {
        if (cancelled) return;
        setConnection("offline");
        retryTimer = window.setTimeout(connect, 1200);
      };
      stateSocket.onerror = () => stateSocket?.close();
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      stateSocket?.close();
      eventSocket?.close();
    };
  }, []);

  const activeTask = useMemo(
    () => state?.tasks.slice().reverse().find((task) => ACTIVE_TASKS.includes(task.status)),
    [state],
  );
  const inMeeting = state ? ACTIVE_MEETING.includes(state.meeting_state) : false;
  const currentAction = describeCurrentAction(state, activeTask?.title, activeTask?.status);

  async function act(path: string, body?: unknown, label = "Working") {
    setBusy(label);
    try {
      setState(await postJson<RuntimeSnapshot>(path, body));
      setError(null);
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(null);
    }
  }

  async function joinAndListen() {
    const url = meetingUrl.trim();
    if (!/^https:\/\/meet\.google\.com\//.test(url)) {
      setError("Paste a complete Google Meet link first.");
      return;
    }
    await act(
      "/api/meeting/join",
      { meeting_url: url, start_listening: true },
      "Joining Meet",
    );
  }

  async function runChecks() {
    setBusy("Running checks");
    try {
      setPreflight(await getPreflight());
      setError(null);
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(null);
    }
  }

  async function testAudioInput() {
    setBusy("Listening for audio test");
    setAudioTestMessage("Speak from another participant or device for the next four seconds…");
    try {
      const result = await postJson<{ ok: boolean; transcript?: string; rms?: number; error?: string }>("/api/audio/test/input");
      const level = Number(result.rms ?? 0);
      setAudioTestMessage(
        result.ok
          ? `Heard and transcribed: “${result.transcript ?? "speech detected"}”`
          : result.error
            ? `Hearing test failed: ${result.error}`
            : `Chrome was quiet (level ${level.toFixed(4)}). Speak from another participant/device, not Robin's own Mac microphone.`,
      );
      setError(null);
    } catch (err) {
      setError(readError(err));
      setAudioTestMessage(null);
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="operator-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className={`status-beacon ${connection}`} aria-hidden="true" />
          <div>
            <strong>Robin</strong>
            <span>Meeting operator</span>
          </div>
        </div>
        <div className={`connection ${connection}`} aria-live="polite">
          {connection === "live" ? <Wifi size={16} /> : <WifiOff size={16} />}
          {connection === "live" ? "Live" : connection === "connecting" ? "Connecting" : "Reconnecting"}
        </div>
      </header>

      <section className="control-surface" aria-labelledby="join-title">
        <div>
          <h1 id="join-title">Bring Robin into the meeting</h1>
          <p>Paste a Meet link. Robin joins muted, starts listening, and reports every step below.</p>
        </div>
        <div className="join-row">
          <input
            value={meetingUrl}
            onChange={(event) => setMeetingUrl(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && !busy && !inMeeting && joinAndListen()}
            placeholder="https://meet.google.com/…"
            aria-label="Google Meet link"
            disabled={inMeeting || busy !== null}
          />
          {!inMeeting ? (
            <button className="primary join-button" onClick={joinAndListen} disabled={busy !== null || connection !== "live"}>
              {busy === "Joining Meet" ? <LoaderCircle className="spin" size={18} /> : <Play size={18} />}
              Join &amp; listen
            </button>
          ) : (
            <button onClick={() => act("/api/meeting/leave", {}, "Leaving Meet")} disabled={busy !== null}>
              <LogOut size={18} /> Leave
            </button>
          )}
          <button className="emergency" onClick={() => act("/api/emergency-stop", {}, "Stopping Robin")} disabled={busy !== null}>
            <AlertTriangle size={18} /> Stop
          </button>
        </div>
        {error && <div className="error-banner" role="alert"><XCircle size={18} />{error}</div>}
      </section>

      <section className="now-strip" aria-live="polite">
        <div className="now-icon">{busy ? <LoaderCircle className="spin" size={20} /> : <Activity size={20} />}</div>
        <div>
          <span>Robin is</span>
          <strong>{busy ?? currentAction}</strong>
        </div>
        <div className="state-chips">
          <StatusChip ok={inMeeting} label={state?.meeting_state ?? "Starting"} />
          <StatusChip ok={Boolean(state?.capture_loop_running)} label={state?.capture_loop_running ? "Listening" : "Not listening"} />
          <StatusChip ok={Boolean(state?.presenting)} label={state?.presenting ? "Presenting" : "Not presenting"} neutral={!state?.presenting} />
        </div>
      </section>

      <div className="operator-grid">
        <section className="timeline-section">
          <div className="section-heading">
            <div><h2>Live activity</h2><p>Updates arrive as they happen—no polling or page refresh.</p></div>
            <span className="event-count">{events.length} events</span>
          </div>
          <div className="timeline" aria-live="polite">
            {events.length === 0 && <div className="empty-state">Waiting for Robin to start…</div>}
            {events.slice().reverse().slice(0, 18).map((event) => (
              <div className={`timeline-row ${eventTone(event)}`} key={event.id ?? `${event.type}-${event.timestamp}`}>
                <span className="timeline-dot" aria-hidden="true" />
                <div>
                  <strong>{eventMessage(event)}</strong>
                  <span>{event.component} · {new Date(event.timestamp).toLocaleTimeString()}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <aside className="work-section">
          <div className="section-heading"><div><h2>Work</h2><p>Requests and generated results.</p></div></div>
          <div className="task-list">
            {state?.tasks.length === 0 && <div className="empty-state">Say “Robin…” in the meeting to delegate work.</div>}
            {state?.tasks.slice().reverse().slice(0, 5).map((task) => {
              const deck = latestArtifact(state.artifacts, task.id, "deck_json");
              const failed = task.status === "FAILED";
              return (
                <article className="task-row" key={task.id}>
                  <div className="task-topline">
                    <strong>{task.title}</strong>
                    <span className={`task-status ${failed ? "failed" : ""}`}>{task.status.replaceAll("_", " ")}</span>
                  </div>
                  {task.error && <p className="task-error">{task.error}</p>}
                  <div className="task-actions">
                    {deck?.url && <a href={deck.url} target="_blank" rel="noreferrer"><ExternalLink size={15} /> Open deck</a>}
                    {deck && <button onClick={() => act(`/api/tasks/${task.id}/present`, {}, "Starting presentation")}><MonitorUp size={15} /> Present</button>}
                    {failed && <button onClick={() => act(`/api/tasks/${task.id}/retry`, {}, "Retrying task")}><RefreshCw size={15} /> Retry</button>}
                  </div>
                </article>
              );
            })}
          </div>

          <div className="transcript-block">
            <h3>Live transcript</h3>
            <div className="transcript-list">
              {state?.transcript.length === 0 && <span className="muted">Nothing heard yet.</span>}
              {state?.transcript.slice().reverse().slice(0, 6).map((segment) => (
                <div key={segment.id}><strong>{segment.speaker_name ?? "Participant"}</strong><p>{segment.text}</p></div>
              ))}
            </div>
          </div>
        </aside>
      </div>

      <details className="system-details">
        <summary>System details and manual controls</summary>
        <div className="details-grid">
          <section>
            <div className="section-heading"><div><h2>Health</h2><p>Core services required for the rehearsal.</p></div><button onClick={runChecks} disabled={busy !== null}>Run full check</button></div>
            <div className="check-list">
              {state?.health.map((item) => <CheckRow key={item.name} ok={item.ok} name={item.name} detail={item.detail} />)}
              {preflight?.checks.map((item) => <CheckRow key={`preflight-${item.name}`} ok={item.ok} name={item.name} detail={item.detail} />)}
            </div>
          </section>
          <section>
            <h2>Manual request</h2>
            <p className="muted">Use only to test task handling without speaking in Meet.</p>
            <textarea value={manualTask} onChange={(event) => setManualTask(event.target.value)} aria-label="Manual task text" />
            <button className="primary" onClick={() => act("/api/transcript", { speaker_name: "Operator", text: manualTask }, "Sending request")} disabled={busy !== null}>
              <Mic size={16} /> Send request
            </button>
          </section>
          <section className="audio-checks">
            <h2>Audio checks</h2>
            <p className="muted">Run these in Meet before a rehearsal. The voice check briefly unmutes Robin; the hearing check listens to Chrome.</p>
            <div className="audio-check-actions">
              <button onClick={() => act("/api/audio/test/output", {}, "Testing Robin's voice")} disabled={busy !== null}>
                <Volume2 size={16} /> Test Robin voice
              </button>
              <button onClick={testAudioInput} disabled={busy !== null || !inMeeting}>
                <Mic size={16} /> Test hearing (4 sec)
              </button>
            </div>
            {audioTestMessage && <p className="audio-test-result" aria-live="polite">{audioTestMessage}</p>}
          </section>
        </div>
      </details>
    </main>
  );
}

function StatusChip({ ok, label, neutral = false }: { ok: boolean; label: string; neutral?: boolean }) {
  return <span className={`state-chip ${neutral ? "neutral" : ok ? "ok" : "off"}`}>{ok ? <CheckCircle2 size={14} /> : <span className="mini-dot" />}{label}</span>;
}

function CheckRow({ ok, name, detail }: { ok: boolean; name: string; detail: string }) {
  return <div className="check-row">{ok ? <CheckCircle2 className="ok" size={17} /> : <XCircle className="bad" size={17} />}<div><strong>{name.replaceAll("_", " ")}</strong><span>{detail}</span></div></div>;
}

function describeCurrentAction(state: RuntimeSnapshot | null, taskTitle?: string, taskStatus?: string) {
  if (!state) return "starting";
  if (state.meeting_state === "NAVIGATING" || state.meeting_state === "PREJOIN") return "joining the meeting";
  if (state.meeting_state === "REQUESTING_ADMISSION") return "waiting to be admitted";
  if (state.meeting_state === "SPEAKING") return "speaking in the meeting";
  if (state.presenting) return "presenting the finished work";
  if (taskStatus === "EXECUTING") return `working on ${taskTitle ?? "the request"}`;
  if (taskStatus === "VALIDATING") return "checking the finished analysis";
  if (taskStatus === "READY_TO_PRESENT") return "ready to present";
  if (state.capture_loop_running) return "listening for requests";
  return "ready for a meeting";
}

function eventMessage(event: EventEnvelope) {
  const error = typeof event.payload.error === "string" ? event.payload.error : null;
  if (error) return error;
  const messages: Record<string, string> = {
    "meeting.join.started": "Opening the Meet link",
    "meeting.joined": "Joined the meeting",
    "meeting.join.failed": "Could not join the meeting",
    "meeting.join.duplicate_suppressed": "Ignored a duplicate Join request",
    "audio.listen.started": "Listening started",
    "audio.realtime.starting": "Opening low-latency transcription",
    "audio.speech.detected": "Detected a participant speaking",
    "speech.interrupted": "Robin stopped speaking to listen",
    "presentation.narration.interrupted": "Narration paused for participant",
    "audio.transcript.partial": `Hearing: ${String(event.payload.text ?? "speech")}`,
    "audio.transcript.echo_suppressed": "Ignored Robin's echoed speech",
    "audio.realtime.fallback": "Realtime unavailable; switched to bounded transcription",
    "audio.listen.stopped": "Listening stopped",
    "audio.silence.skipped": "Listening—no speech detected",
    "audio.listen.iteration_failed": "Audio transcription will retry automatically",
    "audio.output.test.started": "Testing Robin's voice through BlackHole",
    "audio.output.test.passed": `Voice test passed (${String(event.payload.duration_seconds ?? "?")} sec of real speech)`,
    "audio.output.test.failed": "Robin's voice test failed",
    "audio.input.test.started": "Listening to Chrome for the audio test",
    "audio.input.test.passed": `Hearing test passed: ${String(event.payload.transcript ?? "speech detected")}`,
    "audio.input.test.quiet": "Hearing test captured silence from Chrome",
    "audio.input.test.failed": "Hearing test failed",
    "transcript.final": `Heard: ${String(event.payload.text ?? "speech")}`,
    "task.created": "Accepted a new task",
    "task.started": "Started working",
    "agent.started": `Planning with ${String(event.payload.model ?? "the general agent")}`,
    "agent.tool.completed": `Used ${String(event.payload.tool ?? "a workspace tool").replaceAll("_", " ")}`,
    "agent.deliverable.created": `Built a grounded deliverable from ${String(event.payload.source_count ?? "the selected")} source(s)`,
    "agent.deliverable.revision_requested": "Asked the agent to tighten or correct its draft",
    "task.validating": "Validating the result",
    "task.completed": "Verified work and slides are ready",
    "task.failed": "Task failed",
    "artifact.created": `Created ${String(event.payload.type ?? "an artifact").replaceAll("_", " ")}`,
    "speech.completed": `Said: ${String(event.payload.text ?? "status update")}`,
    "speech.failed": "Speech failed; work continued",
    "presentation.started": "Started presenting",
    "presentation.stopped": "Stopped presenting",
    "meeting.left": "Left the meeting",
  };
  return messages[event.type] ?? event.type.replaceAll(".", " ");
}

function eventTone(event: EventEnvelope) {
  if (event.payload.recovered === false || event.type.includes("failed") || event.type.includes("error")) return "error";
  if (event.type.includes("completed") || event.type === "meeting.joined") return "success";
  return "active";
}

function readError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  try {
    const parsed = JSON.parse(raw) as { detail?: string };
    return parsed.detail ?? raw;
  } catch {
    return raw;
  }
}

function latestArtifact(artifacts: Artifact[], taskId: string, type: Artifact["type"]): Artifact | undefined {
  return artifacts.filter((artifact) => artifact.task_id === taskId && artifact.type === type).sort((a, b) => b.revision - a.revision)[0];
}
