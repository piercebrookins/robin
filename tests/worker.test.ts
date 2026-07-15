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
      return { id: "r2", output: [{ type: "function_call", name: "report_task_result", call_id: "done1", arguments: JSON.stringify({ status: "completed", summary: "Verified", success_criteria_met: true, visual_evidence: ["The final screenshot shows the requested result."], observed_state: "task_complete" }) }] };
    } } } as unknown as OpenAI;
    const desktop = new SimulatedDesktopHarness();
    const worker = new ComputerWorker("test-key-12345678901234567890", "gpt-5.6", desktop, new PolicyEngine(), new EventBus(), client);
    const result = await worker.run({ id: "t", goal: "move and verify", constraints: [], successCriteria: [] }, new AbortController().signal);
    expect(result).toMatchObject({ status: "completed", actions: 2, observedState: "task_complete" });
    expect(requests[0].tools[0]).toEqual({ type: "computer" });
    expect(requests[1].input[0]).toMatchObject({ type: "computer_call_output", call_id: "c1", output: { type: "computer_screenshot" } });
    expect(requests[1].input[0].output.detail).toBe("original");
  });

  it("refuses a mutating action until a one-time intent is declared", async () => {
    const requests: any[] = []; let call = 0;
    const client = { responses: { create: async (request: any) => {
      requests.push(request); call++;
      if (call === 1) return { id: "r1", output: [{ type: "computer_call", call_id: "c1", actions: [{ type: "click", x: 10, y: 20 }] }] };
      if (call === 2) return { id: "r2", output: [
        { type: "function_call", name: "authorize_desktop_action", call_id: "a1", arguments: JSON.stringify({ risk: "meeting_control", exact_action: "Click the visible Zoom toolbar", target_app: "us.zoom.xos", sensitive_data: [] }) },
        { type: "computer_call", call_id: "c2", actions: [{ type: "click", x: 10, y: 20, keys: ["META"] }] }
      ] };
      return { id: "r3", output: [{ type: "function_call", name: "report_task_result", call_id: "done", arguments: JSON.stringify({ status: "completed", summary: "Verified", success_criteria_met: true, visual_evidence: ["Toolbar state changed in the final screenshot."], observed_state: "task_complete" }) }] };
    } } } as unknown as OpenAI;
    const desktop = new SimulatedDesktopHarness();
    const worker = new ComputerWorker("test-key-12345678901234567890", "gpt-5.6", desktop, new PolicyEngine(new Set(["com.apple.TextEdit"])), new EventBus(), client);
    const result = await worker.run({ id: "t", goal: "click locally", constraints: [], successCriteria: [] }, new AbortController().signal);
    expect(result).toMatchObject({ status: "completed", actions: 1 });
    expect(requests[1].input).toEqual(expect.arrayContaining([expect.objectContaining({ role: "user" })]));
    expect(desktop.actions()).toContainEqual(expect.objectContaining({ type: "click", keys: ["CMD"] }));
  });
  it("fails closed before uploading a screenshot when the control panel is visible", async () => {
    class ProtectedDesktop extends SimulatedDesktopHarness { override async windows() { return [{ id: 1, owner: "Safari", bundleId: "com.apple.Safari", title: "Robin control", bounds: { x: 0, y: 0, width: 1280, height: 720 }, focused: true, onScreen: true }]; } }
    let requests = 0; const client = { responses: { create: async () => { requests++; return {}; } } } as unknown as OpenAI;
    const worker = new ComputerWorker("test-key-12345678901234567890", "gpt-5.6", new ProtectedDesktop(), new PolicyEngine(new Set(["com.apple.Safari"])), new EventBus(), client);
    const result = await worker.run({ id: "protected", goal: "work", constraints: [], successCriteria: [] }, new AbortController().signal);
    expect(result.status).toBe("takeover"); expect(requests).toBe(0);
  });
});
