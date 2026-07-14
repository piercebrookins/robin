import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../apps/daemon/src/config.js";
import { createControlServer } from "../apps/daemon/src/control.js";
import { EventBus } from "../apps/daemon/src/events.js";
import { SimulatedDesktopHarness } from "../apps/daemon/src/desktop.js";
import { PolicyEngine } from "../apps/daemon/src/policy.js";
import { RobinOrchestrator } from "../apps/daemon/src/orchestrator.js";
import { SimulatedComputerWorker } from "../apps/daemon/src/worker.js";

const servers: Array<{ close(): Promise<unknown> }> = []; afterEach(async () => { await Promise.all(servers.splice(0).map(s => s.close())); });
describe("private control API", () => {
  it("authenticates and drives the simulator", async () => {
    const config = loadConfig({ ROBIN_MODE: "simulator", ROBIN_PANEL_TOKEN: "a-secure-test-token" });
    const events = new EventBus(), desktop = new SimulatedDesktopHarness(), robin = new RobinOrchestrator(events, new PolicyEngine(), desktop, new SimulatedComputerWorker(desktop, events));
    const server = await createControlServer(config, robin); servers.push(server);
    expect((await server.inject({ method: "GET", url: "/api/state" })).statusCode).toBe(401);
    const headers = { authorization: "Bearer a-secure-test-token" };
    expect((await server.inject({ method: "POST", url: "/api/meeting/join", headers, payload: { url: "https://zoom.us/j/123456789" } })).statusCode).toBe(200);
    await server.inject({ method: "POST", url: "/api/meeting/admitted", headers });
    expect((await server.inject({ method: "GET", url: "/api/state", headers })).json().meeting.state).toBe("in_meeting");
  });
});
