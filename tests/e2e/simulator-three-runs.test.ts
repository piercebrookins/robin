import { describe, expect, it } from "vitest";
import { EventBus } from "../../apps/daemon/src/events.js";
import { SimulatedDesktopHarness } from "../../apps/daemon/src/desktop.js";
import { PolicyEngine } from "../../apps/daemon/src/policy.js";
import { RobinOrchestrator } from "../../apps/daemon/src/orchestrator.js";
import { SimulatedComputerWorker } from "../../apps/daemon/src/worker.js";

describe("three consecutive simulator acceptance runs", () => {
  for (let run = 1; run <= 3; run++) it(`completes run ${run}`, async () => {
    const events = new EventBus(), desktop = new SimulatedDesktopHarness();
    const robin = new RobinOrchestrator(events, new PolicyEngine(), desktop, new SimulatedComputerWorker(desktop, events));
    await robin.join(`https://zoom.us/j/12345678${run}`); robin.markMeetingState("waiting_room"); robin.markMeetingState("in_meeting");
    await robin.delegate("Create and verify a local Demo Result note"); await new Promise(resolve => setTimeout(resolve, 25));
    expect(robin.task?.status).toBe("completed"); await robin.share(); expect(robin.meeting?.sharing).toBe(true);
    await robin.stopShare(); expect(robin.meeting?.sharing).toBe(false); await robin.leave(); expect(robin.state.state).toBe("ready");
    expect(events.recent().some(event => event.kind === "task.finished")).toBe(true);
  });
});
