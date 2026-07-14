import { describe, expect, it } from "vitest";
import { PolicyEngine } from "../apps/daemon/src/policy.js";

describe("PolicyEngine", () => {
  const context = { assignedGoal: "draft locally", requestedByOwner: true };
  it("allows reversible local work", () => { expect(new PolicyEngine().evaluate({ type: "type", text: "local draft" }, context).decision).toBe("allow"); });
  it("requires point-of-action approval for external commitments", () => { const result = new PolicyEngine().evaluate({ type: "type", text: "submit this form" }, context); expect(result.decision).toBe("approve"); });
  it.each(["buy this now", "delete permanently", "change the password", "solve this CAPTCHA", "open Privacy & Security settings"])("blocks: %s", text => { expect(new PolicyEngine().evaluate({ type: "type", text }, context).decision).toBe("block"); });
  it("blocks actions in applications outside the configured allow list", () => { const policy = new PolicyEngine(new Set(["com.apple.TextEdit"])); expect(policy.evaluate({ type: "semantic", app: "com.apple.mail", role: "button", action: "press" }, context)).toMatchObject({ decision: "block" }); });
});
