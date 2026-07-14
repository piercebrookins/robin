import { describe, expect, it } from "vitest";
import { EventBus } from "../apps/daemon/src/events.js";
import { SimulatedDesktopHarness } from "../apps/daemon/src/desktop.js";
import { PolicyEngine } from "../apps/daemon/src/policy.js";
import { RobinOrchestrator } from "../apps/daemon/src/orchestrator.js";
import { SimulatedComputerWorker } from "../apps/daemon/src/worker.js";

describe("RobinOrchestrator", () => {
  it("runs the full simulated meeting and halts on emergency stop", async () => {
    const events = new EventBus(), desktop = new SimulatedDesktopHarness();
    const robin = new RobinOrchestrator(events, new PolicyEngine(), desktop, new SimulatedComputerWorker(desktop, events));
    await robin.join("https://zoom.us/j/123456789"); robin.markMeetingState("waiting_room"); robin.markMeetingState("in_meeting");
    await robin.delegate("Prepare a local demo note"); await new Promise(resolve => setTimeout(resolve, 50));
    await robin.share(); expect(robin.snapshot("simulator").meeting?.sharing).toBe(true);
    await robin.emergencyStop(); const snapshot = robin.snapshot("simulator"); expect(snapshot.health.stopped).toBe(true); expect(snapshot.meeting?.sharing).toBe(false); expect((await desktop.perform([{ type: "click", x: 1, y: 1 }])).stopped).toBe(true);
  });
});
