import { describe, expect, it } from "vitest";
import { redact, redactEvent } from "../apps/daemon/src/audit.js";

describe("audit redaction", () => {
  it("redacts nested secrets and meeting passwords", () => {
    const result = redact({ authorization: "Bearer abc.def", payload: "sk-supersecret123456 https://zoom.us/j/123?pwd=hunter2" });
    expect(JSON.stringify(result)).not.toContain("supersecret"); expect(JSON.stringify(result)).not.toContain("hunter2"); expect(JSON.stringify(result)).toContain("[REDACTED]");
  });
  it("removes owner-entered and exact-action prose while preserving audit structure", () => {
    const event = redactEvent({ id: "1", timestamp: new Date().toISOString(), kind: "policy.intent", severity: "info", source: "policy", data: { goal: "Confidential launch plan", exactAction: "Send plan to customer", risk: "external_commitment" } });
    const serialized = JSON.stringify(event);
    expect(serialized).not.toContain("Confidential launch plan"); expect(serialized).not.toContain("Send plan to customer");
    expect(event.kind).toBe("policy.intent"); expect(event.data.risk).toBe("external_commitment");
    const control = redactEvent({ id: "2", timestamp: new Date().toISOString(), kind: "meeting.join_requested", severity: "info", source: "control", data: { url: "https://zoom.us/j/123", briefingPresent: true } });
    expect(JSON.stringify(control.data)).not.toContain("zoom.us");
  });
});
