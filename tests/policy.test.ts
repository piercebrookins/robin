import { describe, expect, it } from "vitest";
import { PolicyEngine } from "../apps/daemon/src/policy.js";

describe("PolicyEngine", () => {
  const context = { assignedGoal: "draft locally", requestedByOwner: true };
  it("allows reversible local work", () => { expect(new PolicyEngine().evaluate({ type: "type", text: "local draft" }, context).decision).toBe("allow"); });
  it("requires point-of-action approval for external commitments", () => { const result = new PolicyEngine().evaluate({ type: "type", text: "submit this form" }, context); expect(result.decision).toBe("approve"); });
  it.each(["buy this now", "delete permanently", "change the password", "solve this CAPTCHA", "open Privacy & Security settings"])("blocks: %s", text => { expect(new PolicyEngine().evaluate({ type: "type", text }, context).decision).toBe("block"); });
  it("blocks actions in applications outside the configured allow list", () => { const policy = new PolicyEngine(new Set(["com.apple.TextEdit"])); expect(policy.evaluate({ type: "semantic", app: "com.apple.mail", role: "button", action: "press" }, context)).toMatchObject({ decision: "block" }); });
  it("blocks protected panel and credential windows even inside an allowed browser", () => { const policy = new PolicyEngine(new Set(["com.apple.Safari"])); expect(policy.evaluate({ type: "click", x: 10, y: 10 }, { ...context, focusedWindow: { id: 1, owner: "Safari", bundleId: "com.apple.Safari", title: "Robin control", bounds: { x: 0, y: 0, width: 100, height: 100 }, focused: true, onScreen: true } }).decision).toBe("block"); });
  it("refuses to observe visible apps outside the dedicated allow list", () => { const policy = new PolicyEngine(new Set(["com.apple.TextEdit"])); expect(policy.canObserveWindow({ id: 1, owner: "Mail", bundleId: "com.apple.mail", title: "Inbox", bounds: { x: 0, y: 0, width: 100, height: 100 }, focused: false, onScreen: true })).toBe(false); });
  it("gates declared external intents and blocks declared destructive intents", () => {
    const policy = new PolicyEngine(new Set(["com.apple.TextEdit"]));
    expect(policy.evaluateIntent({ risk: "external_commitment", exactAction: "Submit the final form", targetApp: "com.apple.TextEdit" }).decision).toBe("approve");
    expect(policy.evaluateIntent({ risk: "destructive", exactAction: "Erase the document", targetApp: "com.apple.TextEdit" }).decision).toBe("block");
  });
});
