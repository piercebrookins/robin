import { describe, expect, it } from "vitest";
import { EventBus } from "../apps/daemon/src/events.js";
import { SimulatedDesktopHarness } from "../apps/daemon/src/desktop.js";
import { PolicyEngine } from "../apps/daemon/src/policy.js";
import { RobinOrchestrator } from "../apps/daemon/src/orchestrator.js";
import { SimulatedComputerWorker } from "../apps/daemon/src/worker.js";
import type { TaskWorker } from "../apps/daemon/src/worker.js";

describe("RobinOrchestrator", () => {
  it("runs the full simulated meeting and halts on emergency stop", async () => {
    const events = new EventBus(), desktop = new SimulatedDesktopHarness();
    const robin = new RobinOrchestrator(events, new PolicyEngine(), desktop, new SimulatedComputerWorker(desktop, events));
    await robin.join("https://zoom.us/j/123456789"); robin.markMeetingState("waiting_room"); robin.markMeetingState("in_meeting");
    await robin.delegate("Prepare a local demo note"); await new Promise(resolve => setTimeout(resolve, 50));
    await robin.share(); expect(robin.snapshot("simulator").meeting?.sharing).toBe(true);
    await robin.emergencyStop(); const snapshot = robin.snapshot("simulator"); expect(snapshot.health.stopped).toBe(true); expect(snapshot.meeting?.sharing).toBe(false); expect((await desktop.perform([{ type: "click", x: 1, y: 1 }])).stopped).toBe(true);
  });
  it("waits for desktop work before starting a verified production share", async () => {
    const events = new EventBus(), desktop = new SimulatedDesktopHarness(); let calls = 0; const order: string[] = [];
    const worker: TaskWorker = { run: async () => { calls++; if (calls === 1) { order.push("task-start"); await new Promise(resolve => setTimeout(resolve, 20)); order.push("task-end"); return { status: "completed", summary: "done", actions: 1, observedState: "task_complete" }; } order.push("share"); return { status: "completed", summary: "sharing", actions: 1, observedState: "zoom_sharing" }; } };
    const robin = new RobinOrchestrator(events, new PolicyEngine(), desktop, worker, true);
    await robin.join("https://zoom.us/j/123456789"); robin.markMeetingState("in_meeting"); await robin.delegate("Prepare result"); await robin.share();
    expect(order).toEqual(["task-start", "task-end", "share"]); expect(robin.meeting?.sharing).toBe(true);
  });
  it("turns an unexpected desktop-worker exception into safe takeover", async () => {
    const events = new EventBus(), desktop = new SimulatedDesktopHarness();
    const worker: TaskWorker = { run: async () => { throw new Error("helper connection lost"); } };
    const robin = new RobinOrchestrator(events, new PolicyEngine(), desktop, worker); await robin.join("https://zoom.us/j/123456789"); robin.markMeetingState("in_meeting"); await robin.delegate("Prepare result"); await new Promise(resolve => setTimeout(resolve, 10));
    expect(robin.snapshot("simulator").health.takeover).toBe(true); expect(robin.task?.status).toBe("takeover");
  });
});
