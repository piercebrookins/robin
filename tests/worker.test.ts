import { describe, expect, it } from "vitest";
import OpenAI from "openai";
import { SimulatedDesktopHarness } from "../apps/daemon/src/desktop.js";
import { EventBus } from "../apps/daemon/src/events.js";
import { PolicyEngine } from "../apps/daemon/src/policy.js";
import { ComputerWorker } from "../apps/daemon/src/worker.js";

describe("GA computer tool loop", () => {
  it("uses the computer tool schema and executes batched actions", async () => {
    const requests: any[] = []; let call = 0;
    const client = { responses: { create: async (request: any) => {
      requests.push(request); call++;
      if (call === 1) return { id: "r1", output: [{ type: "computer_call", call_id: "c1", pending_safety_checks: [], actions: [{ type: "move", x: 40, y: 50 }, { type: "wait" }] }] };
      return { id: "r2", output: [{ type: "message" }], output_text: "Verified" };
    } } } as unknown as OpenAI;
    const desktop = new SimulatedDesktopHarness();
    const worker = new ComputerWorker("test-key-12345678901234567890", "gpt-5.6", desktop, new PolicyEngine(), new EventBus(), client);
    const result = await worker.run({ id: "t", goal: "move and verify", constraints: [], successCriteria: [] }, new AbortController().signal);
    expect(result).toMatchObject({ status: "completed", actions: 2 });
    expect(requests[0].tools[0]).toEqual({ type: "computer" });
    expect(requests[1].input[0]).toMatchObject({ type: "computer_call_output", call_id: "c1", output: { type: "computer_screenshot" } });
  });
});
