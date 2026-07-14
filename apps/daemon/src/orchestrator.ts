import { randomUUID } from "node:crypto";
import type { ControlSnapshot, MeetingState } from "../../../packages/protocol/src/index.js";
import type { DesktopHarness } from "./desktop.js";
import { EventBus } from "./events.js";
import { MeetingStateMachine } from "./state-machine.js";
import { PolicyEngine } from "./policy.js";
import type { TaskWorker, WorkerTask } from "./worker.js";

export class RobinOrchestrator {
  readonly state = new MeetingStateMachine();
  readonly transcript: ControlSnapshot["transcript"] = [];
  meeting: NonNullable<ControlSnapshot["meeting"]> | undefined;
  task: NonNullable<ControlSnapshot["task"]> | undefined;
  private taskAbort?: AbortController;
  private stopped = false;
  private takeover = false;
  private checks: ControlSnapshot["health"]["checks"] = {};

  constructor(readonly events: EventBus, readonly policy: PolicyEngine, private desktop: DesktopHarness, private worker?: TaskWorker, private production = false) {}

  async join(url: string, briefing = ""): Promise<void> {
    if (this.stopped || this.takeover) throw new Error("Robin is stopped or under human control");
    if (!/^https:\/\/(?:[\w-]+\.)?zoom\.us\/j\/\d+/i.test(url)) throw new Error("Enter a normal https://…zoom.us/j/… meeting link");
    this.setState("joining"); this.meeting = { url, state: "joining", muted: true, sharing: false };
    this.events.publish({ kind: "meeting.join_requested", severity: "info", source: "control", data: { url, briefingPresent: Boolean(briefing) } });
    await this.desktop.perform([{ type: "open_url", url }]);
  }
  markMeetingState(state: "waiting_room" | "in_meeting"): void { this.setState(state); }
  async delegate(goal: string, constraints: string[] = [], successCriteria: string[] = []): Promise<string> {
    if (!this.worker) throw new Error("Computer worker is unavailable");
    if (!this.meeting || !["in_meeting", "working", "sharing"].includes(this.state.state)) throw new Error("Robin is not in a meeting");
    this.taskAbort?.abort(); this.taskAbort = new AbortController();
    const work: WorkerTask = { id: randomUUID(), goal, constraints, successCriteria };
    this.task = { id: work.id, goal, status: "running", progress: "Observing the desktop" }; this.setState("working");
    this.events.publish({ kind: "task.started", severity: "info", source: "daemon", taskId: work.id, data: { goal } });
    void this.worker.run(work, this.taskAbort.signal).then(result => {
      if (!this.task || this.task.id !== work.id) return;
      this.task.status = result.status; this.task.progress = result.summary;
      this.events.publish({ kind: "task.finished", severity: result.status === "completed" ? "info" : "warning", source: "worker", taskId: work.id, data: result as unknown as Record<string, unknown> });
      if (result.status === "takeover") void this.humanTakeover(result.summary); else if (this.state.state === "working") this.setState("in_meeting");
    });
    return work.id;
  }
  async share(): Promise<void> {
    this.ensureActive();
    if (this.production && this.worker) {
      const result = await this.worker.run({ id: randomUUID(), goal: "In Zoom, start screen sharing through the normal Share Screen UI. Select only the dedicated Robin workspace display or the visible work application, never the control panel or unrelated windows. Verify Zoom's green sharing indicator before finishing.", constraints: ["Do not share sensitive or unrelated content", "Use the normal Zoom UI"], successCriteria: ["Green Zoom sharing indicator is visible", "Only the intended workspace is selected"] }, new AbortController().signal);
      if (result.status !== "completed") { await this.humanTakeover(result.summary); throw new Error(result.summary); }
    } else await this.desktop.perform([{ type: "semantic", app: "us.zoom.xos", role: "button", title: "Share Screen", action: "press" }]);
    if (this.meeting) this.meeting.sharing = true; this.setState("sharing");
  }
  async stopShare(): Promise<void> { await this.desktop.perform([{ type: "semantic", app: "us.zoom.xos", role: "button", title: "Stop Share", action: "press" }]); if (this.meeting) this.meeting.sharing = false; this.setState(this.task?.status === "running" ? "working" : "in_meeting"); }
  async mute(muted: boolean): Promise<void> { this.ensureActive(); await this.desktop.perform([{ type: "semantic", app: "us.zoom.xos", role: "button", title: muted ? "Mute" : "Unmute", action: "press" }]); if (this.meeting) this.meeting.muted = muted; }
  async leave(): Promise<void> { if (this.state.state === "ready") return; this.setState("leaving"); await this.desktop.perform([{ type: "semantic", app: "us.zoom.xos", role: "button", title: "Leave", action: "press" }, { type: "semantic", app: "us.zoom.xos", role: "button", title: "Leave Meeting", action: "press" }]); this.meeting = undefined; this.task = undefined; this.setState("ready"); }
  async emergencyStop(reason = "Owner pressed emergency stop"): Promise<void> {
    this.stopped = true; this.taskAbort?.abort(); this.events.publish({ kind: "system.stop_requested", severity: "critical", source: "control", data: { reason } });
    if (this.meeting) { await this.desktop.perform([{ type: "semantic", app: "us.zoom.xos", role: "button", title: "Mute", action: "press" }, ...(this.meeting.sharing ? [{ type: "semantic", app: "us.zoom.xos", role: "button", title: "Stop Share", action: "press" } as const] : [])]).catch(() => undefined); }
    await this.desktop.emergencyStop(); this.state.state = "stopped"; if (this.meeting) { this.meeting.muted = true; this.meeting.sharing = false; this.meeting.state = "stopped"; }
    this.events.publish({ kind: "system.emergency_stop", severity: "critical", source: "control", data: { reason } });
  }
  async humanTakeover(reason = "Owner took control"): Promise<void> { this.takeover = true; this.taskAbort?.abort(); this.events.publish({ kind: "system.stop_requested", severity: "critical", source: "control", data: { reason } }); await this.desktop.emergencyStop(); this.state.state = "human_takeover"; this.events.publish({ kind: "system.human_takeover", severity: "critical", source: "control", data: { reason } }); }
  async resume(): Promise<void> { await this.desktop.resume(); this.stopped = false; this.takeover = false; this.state.state = this.meeting ? "in_meeting" : "ready"; this.events.publish({ kind: "system.resumed", severity: "info", source: "control", data: {} }); }
  addTranscript(speaker: string, text: string, final = true): void { this.transcript.push({ id: randomUUID(), at: new Date().toISOString(), speaker, text, final }); if (this.transcript.length > 500) this.transcript.shift(); }
  updateCheck(name: string, ok: boolean, message: string): void { this.checks[name] = { ok, message, updatedAt: new Date().toISOString() }; }
  snapshot(mode: "production" | "simulator"): ControlSnapshot { return { health: { ok: !this.stopped && !this.takeover && Object.values(this.checks).every(c => c.ok), mode, state: this.state.state, stopped: this.stopped, takeover: this.takeover, checks: this.checks }, ...(this.meeting ? { meeting: this.meeting } : {}), ...(this.task ? { task: this.task } : {}), approvals: this.policy.pending(), transcript: this.transcript.slice(-200), events: this.events.recent(200) }; }
  private ensureActive() { if (this.stopped || this.takeover) throw new Error("Robin is stopped or under human control"); }
  private setState(state: MeetingState) { this.state.transition(state); if (this.meeting) this.meeting.state = state; this.events.publish({ kind: "meeting.state", severity: "info", source: "daemon", data: { state } }); }
}
