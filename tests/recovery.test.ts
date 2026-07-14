import { describe, expect, it } from "vitest";
import OpenAI from "openai";
import { EventBus } from "../apps/daemon/src/events.js";
import { SimulatedDesktopHarness } from "../apps/daemon/src/desktop.js";
import { PolicyEngine } from "../apps/daemon/src/policy.js";
import { RobinOrchestrator } from "../apps/daemon/src/orchestrator.js";
import { ComputerWorker, type TaskWorker } from "../apps/daemon/src/worker.js";
import { ZoomLifecycleController } from "../apps/daemon/src/zoom-controller.js";

describe("bounded recovery", () => {
  it("requests takeover after three model timeouts", async () => {
    const desktop = new SimulatedDesktopHarness(), events = new EventBus();
    const client = { responses: { create: async () => { throw new Error("model timeout"); } } } as unknown as OpenAI;
    const worker = new ComputerWorker("test-key-12345678901234567890", "gpt-5.6", desktop, new PolicyEngine(), events, client, 1);
    const result = await worker.run({ id: "t1", goal: "test", constraints: [], successCriteria: [] }, new AbortController().signal);
    expect(result.status).toBe("takeover"); expect(events.recent().filter(e => e.kind === "worker.request_failed")).toHaveLength(3);
  });

  it("escalates an unrecognized Zoom dialog to computer use", async () => {
    const desktop = new SimulatedDesktopHarness(), events = new EventBus(), policy = new PolicyEngine(); let recoveryCalls = 0;
    const worker: TaskWorker = { run: async () => { recoveryCalls++; return { status: "takeover", summary: "Unexpected dialog", actions: 0 }; } };
    const robin = new RobinOrchestrator(events, policy, desktop, worker); await robin.join("https://zoom.us/j/123456789");
    const zoom = new ZoomLifecycleController(desktop, worker, robin, events, 1, 6, 2); zoom.start(); await new Promise(resolve => setTimeout(resolve, 25));
    expect(recoveryCalls).toBe(1); expect(robin.snapshot("simulator").health.takeover).toBe(true);
  });

  it("detects an expired Zoom login and stops without entering credentials", async () => {
    const base = new SimulatedDesktopHarness(); base.setScene("Sign In — session expired");
    const events = new EventBus(), worker: TaskWorker = { run: async () => ({ status: "completed", summary: "unused", actions: 0 }) };
    const robin = new RobinOrchestrator(events, new PolicyEngine(), base, worker); await robin.join("https://zoom.us/j/123456789"); base.setScene("Sign In — session expired");
    new ZoomLifecycleController(base, worker, robin, events, 1, 2, 1).start(); await new Promise(resolve => setTimeout(resolve, 10));
    expect(robin.snapshot("simulator").health.takeover).toBe(true);
  });
});
