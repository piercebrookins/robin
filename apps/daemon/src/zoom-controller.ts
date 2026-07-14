import { randomUUID } from "node:crypto";
import type { DesktopHarness } from "./desktop.js";
import type { EventBus } from "./events.js";
import type { RobinOrchestrator } from "./orchestrator.js";
import type { TaskWorker } from "./worker.js";

export class ZoomLifecycleController {
  private abort: AbortController | undefined;
  constructor(private desktop: DesktopHarness, private worker: TaskWorker, private orchestrator: RobinOrchestrator, private events: EventBus, private pollMs = 1000, private maxAttempts = 90, private recoveryAfter = 5) {}
  start(): void { this.abort?.abort(); this.abort = new AbortController(); void this.run(this.abort.signal); }
  stop(): void { this.abort?.abort(); }
  private async run(signal: AbortSignal): Promise<void> {
    let ambiguous = 0;
    for (let attempt = 0; attempt < this.maxAttempts && !signal.aborted; attempt++) {
      const windows = await this.desktop.windows().catch(() => []); const text = windows.map(w => `${w.owner} ${w.title}`).join(" ").toLowerCase();
      if (/sign in|session expired|login/.test(text)) { await this.orchestrator.humanTakeover("Zoom login expired; sign in through the protected console."); return; }
      if (/waiting room|host will let you in|please wait/.test(text)) { if (this.orchestrator.state.state === "joining") this.orchestrator.markMeetingState("waiting_room"); ambiguous = 0; }
      if (/zoom meeting|meeting controls|participants/.test(text) && !/waiting room|host will let you in/.test(text)) { if (["joining", "waiting_room"].includes(this.orchestrator.state.state)) this.orchestrator.markMeetingState("in_meeting"); return; }
      for (const title of ["Join with Computer Audio", "Join Audio", "Got it"]) await this.desktop.perform([{ type: "semantic", app: "us.zoom.xos", role: "button", title, action: "press" }], signal).catch(() => undefined);
      ambiguous++;
      if (ambiguous === this.recoveryAfter) {
        this.events.publish({ kind: "meeting.recovery_started", severity: "warning", source: "daemon", data: { reason: "Zoom state was not recognized semantically" } });
        const result = await this.worker.run({ id: randomUUID(), goal: "Complete the ordinary Zoom join flow. Handle only normal join-audio, preview, waiting-room, and meeting dialogs. Do not enter credentials or change settings. Finish when visibly in the meeting or waiting room.", constraints: ["Normal Zoom meeting controls only", "No credentials", "No security settings"], successCriteria: ["In meeting or waiting room is visually verified"] }, signal);
        if (result.status !== "completed") { await this.orchestrator.humanTakeover(result.summary); return; }
      }
      await new Promise(resolve => setTimeout(resolve, this.pollMs));
    }
    if (!signal.aborted) await this.orchestrator.humanTakeover("Zoom join did not reach a verified meeting state within 90 seconds.");
  }
}
