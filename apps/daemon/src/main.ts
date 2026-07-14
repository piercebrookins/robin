import { resolve } from "node:path";
import { loadConfig } from "./config.js";
import { EventBus } from "./events.js";
import { AuditWriter } from "./audit.js";
import { NativeDesktopHarness, SimulatedDesktopHarness } from "./desktop.js";
import { PolicyEngine } from "./policy.js";
import { ComputerWorker, SimulatedComputerWorker } from "./worker.js";
import { RobinOrchestrator } from "./orchestrator.js";
import { createControlServer } from "./control.js";
import { AudioBridge } from "./audio.js";
import { RealtimeSession } from "./realtime.js";
import { ZoomLifecycleController } from "./zoom-controller.js";

const config = loadConfig();
const events = new EventBus();
const audit = new AuditWriter(config.ROBIN_TRACE_DIR);
events.on("event", event => audit.write(event));

const desktop = config.ROBIN_MODE === "simulator" ? new SimulatedDesktopHarness() : new NativeDesktopHarness(config.ROBIN_HELPER_SOCKET, config.ROBIN_WORKSPACE_DISPLAY);
const policy = new PolicyEngine();
const worker = config.ROBIN_MODE === "simulator" ? new SimulatedComputerWorker(desktop, events) : new ComputerWorker(config.OPENAI_API_KEY!, config.ROBIN_OPENAI_MODEL, desktop, policy, events);
const orchestrator = new RobinOrchestrator(events, policy, desktop, worker, config.ROBIN_MODE === "production");
if (config.ROBIN_MODE === "simulator") events.on("event", event => {
  if (event.kind === "meeting.join_requested") setTimeout(() => { try { orchestrator.markMeetingState("waiting_room"); setTimeout(() => { try { orchestrator.markMeetingState("in_meeting"); orchestrator.addTranscript("Participant", "Robin, prepare a short local note."); } catch {} }, 250); } catch {} }, 150);
});
const server = await createControlServer(config, orchestrator);

let realtime: RealtimeSession | undefined; let audio: AudioBridge | undefined;
let mediaReconnects = 0; let mediaReconnectTimer: NodeJS.Timeout | undefined;
if (config.ROBIN_MODE === "production") {
  const zoom = new ZoomLifecycleController(desktop, worker, orchestrator, events);
  events.on("event", event => { if (event.kind === "meeting.join_requested") zoom.start(); if (event.kind === "system.stop_requested") zoom.stop(); });
  const helper = resolve("apps/mac-helper/.build/release/RobinMacHelper");
  realtime = new RealtimeSession({ apiKey: config.OPENAI_API_KEY!, model: config.ROBIN_REALTIME_MODEL });
  audio = new AudioBridge({ helperPath: helper, inputDevice: config.ROBIN_AUDIO_INPUT, outputDevice: config.ROBIN_AUDIO_OUTPUT });
  audio.on("input", pcm => realtime?.appendAudio(pcm)); audio.on("played", milliseconds => realtime?.markPlayed(milliseconds)); realtime.on("audio", pcm => audio?.play(pcm)); realtime.on("barge-in", () => audio?.interrupt());
  realtime.on("transcript", turn => orchestrator.addTranscript(turn.speaker, turn.text, turn.final));
  realtime.on("function", call => void dispatchRealtime(call).catch(error => realtime?.functionResult(call.callId, { ok: false, error: String(error) })));
  audio.on("disconnect", code => { orchestrator.updateCheck("audio", false, `Audio bridge disconnected (${code ?? "unknown"})`); });
  realtime.on("disconnect", () => orchestrator.updateCheck("realtime", false, "Realtime disconnected; reconnecting"));
  realtime.on("reconnect-needed", () => {
    if (orchestrator.snapshot(config.ROBIN_MODE).health.stopped || orchestrator.snapshot(config.ROBIN_MODE).health.takeover) return;
    const delay = Math.min(10_000, 500 * 2 ** mediaReconnects++); if (mediaReconnectTimer) clearTimeout(mediaReconnectTimer);
    mediaReconnectTimer = setTimeout(() => void startMedia().catch(error => orchestrator.updateCheck("realtime", false, `Reconnect failed: ${String(error)}`)), delay);
  });
  events.on("event", event => {
    if (event.kind === "system.stop_requested") { if (mediaReconnectTimer) clearTimeout(mediaReconnectTimer); realtime?.stop(); audio?.stop(); orchestrator.updateCheck("audio", false, "Stopped by owner"); orchestrator.updateCheck("realtime", false, "Stopped by owner"); }
    if (event.kind === "system.resumed") void startMedia();
  });
  await startMedia();
}

async function startMedia() {
  if (!realtime || !audio) return;
  audio.start(); await realtime.connect(); mediaReconnects = 0; orchestrator.updateCheck("audio", true, "Virtual routes connected"); orchestrator.updateCheck("realtime", true, "Realtime connected");
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
  try { const permissions = await desktop.permissionStatus(); const ok = Object.values(permissions).every(Boolean); orchestrator.updateCheck("desktop", ok, ok ? "Capture and control ready" : `Missing: ${Object.entries(permissions).filter(([,v])=>!v).map(([k])=>k).join(", ")}`); }
  catch (error) { orchestrator.updateCheck("desktop", false, `Helper unavailable: ${String(error)}`); }
  orchestrator.updateCheck("policy", true, "Approval gate active");
  if (config.ROBIN_MODE === "simulator") { orchestrator.updateCheck("audio", true, "Recorded fixture route"); orchestrator.updateCheck("realtime", true, "Simulated session"); }
}
await healthTick(); const healthTimer = setInterval(() => void healthTick(), 10_000);
await server.listen({ host: config.ROBIN_HOST, port: config.ROBIN_PORT });
events.publish({ kind: "daemon.started", severity: "info", source: "daemon", data: { host: config.ROBIN_HOST, port: config.ROBIN_PORT, mode: config.ROBIN_MODE } });
console.log(`Robin control is listening on http://${config.ROBIN_HOST}:${config.ROBIN_PORT}`);

async function shutdown(signal: string) { clearInterval(healthTimer); realtime?.stop(); audio?.stop(); await orchestrator.emergencyStop(signal); await audit.flush(); await server.close(); process.exit(0); }
process.once("SIGINT", () => void shutdown("SIGINT")); process.once("SIGTERM", () => void shutdown("SIGTERM"));
