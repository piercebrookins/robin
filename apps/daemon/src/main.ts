import { resolve } from "node:path";
import { loadConfig } from "./config.js";
import { EventBus } from "./events.js";
import { AuditWriter } from "./audit.js";
import { NativeDesktopHarness, SimulatedDesktopHarness } from "./desktop.js";
import { isProtectedWindow, PolicyEngine } from "./policy.js";
import { ComputerWorker, SimulatedComputerWorker } from "./worker.js";
import { RobinOrchestrator } from "./orchestrator.js";
import { createControlServer } from "./control.js";
import { AudioBridge } from "./audio.js";
import { RealtimeSession } from "./realtime.js";
import { ZoomLifecycleController } from "./zoom-controller.js";
import { loadRecordedMeetingFixture, RecordedMeetingPlayer } from "./simulator.js";

const config = loadConfig();
const events = new EventBus();
const audit = new AuditWriter(config.ROBIN_TRACE_DIR);
events.on("event", event => audit.write(event));

const desktop = config.ROBIN_MODE === "simulator" ? new SimulatedDesktopHarness() : new NativeDesktopHarness(config.ROBIN_HELPER_SOCKET, config.ROBIN_WORKSPACE_DISPLAY);
const policy = new PolicyEngine(config.allowedApps);
const worker = config.ROBIN_MODE === "simulator" ? new SimulatedComputerWorker(desktop, events) : new ComputerWorker(config.OPENAI_API_KEY!, config.ROBIN_OPENAI_MODEL, desktop, policy, events);
const orchestrator = new RobinOrchestrator(events, policy, desktop, worker, config.ROBIN_MODE === "production");
if (config.ROBIN_MODE === "simulator") {
  const fixture = await loadRecordedMeetingFixture(); const player = new RecordedMeetingPlayer(fixture, 5); const simulatedDesktop = desktop as SimulatedDesktopHarness;
  events.on("event", event => {
    if (event.kind === "meeting.join_requested") setTimeout(() => { try { simulatedDesktop.setScene("waiting_room"); orchestrator.markMeetingState("waiting_room"); setTimeout(() => { try { simulatedDesktop.setScene("in_meeting"); orchestrator.markMeetingState("in_meeting"); player.play(turn => { orchestrator.addTranscript(turn.speaker === "robin" ? "Robin" : "Participant", turn.transcript); events.publish({ kind: turn.event === "barge_in" ? "audio.barge_in" : "audio.fixture_turn", severity: "info", source: "audio", data: { speaker: turn.speaker, audioBytes: turn.wav.length } }); }); } catch {} }, 250); } catch {} }, 150);
    if (event.kind === "system.stop_requested" || (event.kind === "meeting.state" && event.data.state === "ready")) player.stop();
  });
}
const server = await createControlServer(config, orchestrator);

let realtime: RealtimeSession | undefined; let audio: AudioBridge | undefined;
let mediaReconnects = 0; let mediaReconnectTimer: NodeJS.Timeout | undefined;
let realtimeDispatchChain = Promise.resolve();
if (config.ROBIN_MODE === "production") {
  const zoom = new ZoomLifecycleController(desktop, worker, orchestrator, events);
  events.on("event", event => { if (event.kind === "meeting.join_requested") zoom.start(); if (event.kind === "system.stop_requested") zoom.stop(); });
  const helper = resolve("apps/mac-helper/.build/release/RobinMacHelper");
  realtime = new RealtimeSession({ apiKey: config.OPENAI_API_KEY!, model: config.ROBIN_REALTIME_MODEL });
  audio = new AudioBridge({ helperPath: helper, inputDevice: config.ROBIN_AUDIO_INPUT, outputDevice: config.ROBIN_AUDIO_OUTPUT });
  audio.on("input", pcm => realtime?.appendAudio(pcm)); audio.on("played", milliseconds => realtime?.markPlayed(milliseconds)); realtime.on("audio", pcm => audio?.play(pcm)); realtime.on("barge-in", () => audio?.interrupt());
  realtime.on("transcript", turn => orchestrator.addTranscript(turn.speaker, turn.text, turn.final));
  realtime.on("function", call => { realtimeDispatchChain = realtimeDispatchChain.then(() => dispatchRealtime(call)).catch(error => realtime?.functionResult(call.callId, { ok: false, error: String(error) })); });
  audio.on("disconnect", code => { orchestrator.updateCheck("audio", false, `Audio bridge disconnected (${code ?? "unknown"})`); });
  audio.on("ready", () => { orchestrator.updateCheck("audio", true, "Virtual routes connected"); void realtime?.connect().then(() => orchestrator.updateCheck("realtime", true, "Realtime connected")).catch(error => orchestrator.updateCheck("realtime", false, `Realtime connect failed: ${String(error)}`)); });
  audio.on("reconnecting", ({ attempt, delay }) => orchestrator.updateCheck("audio", false, `Audio reconnect ${attempt} scheduled in ${delay} ms`));
  audio.on("failed", error => void orchestrator.humanTakeover(`Audio recovery failed: ${String(error)}`));
  realtime.on("disconnect", () => orchestrator.updateCheck("realtime", false, "Realtime disconnected; reconnecting"));
  realtime.on("input-dropped", bytes => events.publish({ kind: "audio.input_dropped", severity: "warning", source: "audio", data: { bytes } }));
  realtime.on("reconnect-needed", () => {
    if (orchestrator.snapshot(config.ROBIN_MODE).health.stopped || orchestrator.snapshot(config.ROBIN_MODE).health.takeover) return;
    const delay = Math.min(10_000, 500 * 2 ** mediaReconnects++); if (mediaReconnectTimer) clearTimeout(mediaReconnectTimer);
    mediaReconnectTimer = setTimeout(() => void startMedia().catch(error => orchestrator.updateCheck("realtime", false, `Reconnect failed: ${String(error)}`)), delay);
  });
  events.on("event", event => {
    if (event.kind === "system.stop_requested") { if (mediaReconnectTimer) clearTimeout(mediaReconnectTimer); realtime?.stop(); audio?.stop(); orchestrator.updateCheck("audio", false, "Stopped by owner"); orchestrator.updateCheck("realtime", false, "Stopped by owner"); }
    if (event.kind === "system.resumed") void startMedia();
    if (event.kind === "task.finished" && event.data.status === "completed" && !orchestrator.snapshot(config.ROBIN_MODE).health.stopped && !orchestrator.snapshot(config.ROBIN_MODE).health.takeover) realtime?.speak("I finished the assigned task and visually verified the result.");
  });
  await startMedia().catch(error => { orchestrator.updateCheck("audio", false, `Media startup failed: ${String(error)}`); orchestrator.updateCheck("realtime", false, "Waiting for audio recovery"); events.publish({ kind: "media.start_failed", severity: "error", source: "audio", data: { message: String(error) } }); });
}

async function startMedia() {
  if (!realtime || !audio) return;
  await audio.start(); await realtime.connect(); mediaReconnects = 0; orchestrator.updateCheck("audio", true, "Virtual routes connected"); orchestrator.updateCheck("realtime", true, "Realtime connected");
}

async function dispatchRealtime(call: { name: string; callId: string; arguments: Record<string, any> }) {
  let result: unknown;
  switch (call.name) {
    case "delegate_task": result = { taskId: await orchestrator.delegate(call.arguments.goal, call.arguments.constraints, call.arguments.success_criteria) }; break;
    case "get_task_status": result = orchestrator.task ?? { status: "idle" }; break;
    case "request_share": await orchestrator.share(); result = { ok: true }; break;
    case "stop_share": await orchestrator.stopShare(); result = { ok: true }; break;
    case "mute_self": await orchestrator.mute(true); result = { ok: true }; break;
    case "unmute_self": await orchestrator.mute(false); result = { ok: true }; break;
    case "leave_meeting": await orchestrator.leave(); result = { ok: true }; break;
    case "cancel_task": await orchestrator.emergencyStop("Voice task cancellation"); result = { ok: true }; break;
    default: throw new Error(`Unknown Realtime function ${call.name}`);
  }
  realtime?.functionResult(call.callId, result);
}

async function healthTick() {
  try { const permissions = await desktop.permissionStatus(); const missing = Object.entries(permissions).filter(([,v])=>!v).map(([k])=>k); const unsafeWindows = (await desktop.windows()).filter(window => window.onScreen && (isProtectedWindow(window) || !policy.canObserveWindow(window))); const ok = missing.length === 0 && unsafeWindows.length === 0; orchestrator.updateCheck("desktop", ok, missing.length ? `Missing: ${missing.join(", ")}` : unsafeWindows.length ? "Protected or unapproved window is visible; model screenshots are blocked" : "Capture and control ready"); }
  catch (error) { orchestrator.updateCheck("desktop", false, `Helper unavailable: ${String(error)}`); }
  orchestrator.updateCheck("policy", true, "Approval gate active");
  const auditHealth = audit.health(); orchestrator.updateCheck("audit", auditHealth.ok, auditHealth.message);
  if (config.ROBIN_MODE === "production" && audio && realtime) {
    const audioHealth = audio.health(), realtimeHealth = realtime.health();
    orchestrator.updateCheck("audio", audioHealth.connected, audioHealth.connected ? `Connected; ${audioHealth.bufferedBytes} B queued, ${audioHealth.droppedBytes} B dropped` : `Disconnected; ${audioHealth.reconnects} reconnect attempt(s)`);
    orchestrator.updateCheck("realtime", realtimeHealth.connected, realtimeHealth.connected ? `Connected; ${realtimeHealth.bufferedBytes} B network backlog, ${realtimeHealth.droppedInputBytes} B dropped` : "Disconnected; recovery active");
  }
  if (config.ROBIN_MODE === "simulator") { orchestrator.updateCheck("audio", true, "Recorded fixture route"); orchestrator.updateCheck("realtime", true, "Simulated session"); }
}
await healthTick(); const healthTimer = setInterval(() => void healthTick(), 10_000);
await server.listen({ host: config.ROBIN_HOST, port: config.ROBIN_PORT });
events.publish({ kind: "daemon.started", severity: "info", source: "daemon", data: { host: config.ROBIN_HOST, port: config.ROBIN_PORT, mode: config.ROBIN_MODE } });
console.log(`Robin control is listening on http://${config.ROBIN_HOST}:${config.ROBIN_PORT}`);

async function shutdown(signal: string) { clearInterval(healthTimer); realtime?.stop(); audio?.stop(); await orchestrator.emergencyStop(signal); await audit.flush(); await server.close(); process.exit(0); }
process.once("SIGINT", () => void shutdown("SIGINT")); process.once("SIGTERM", () => void shutdown("SIGTERM"));
