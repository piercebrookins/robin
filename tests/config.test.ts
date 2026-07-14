import { describe, expect, it } from "vitest";
import { loadConfig } from "../apps/daemon/src/config.js";

describe("production configuration", () => {
  const base = { ROBIN_MODE: "production", OPENAI_API_KEY: "test-key-12345678901234567890", ROBIN_PANEL_TOKEN: "panel-token-123456" };
  it("requires panel authentication", () => { expect(() => loadConfig({ ROBIN_MODE: "production", OPENAI_API_KEY: base.OPENAI_API_KEY })).toThrow(/PANEL_TOKEN/); });
  it("refuses a non-loopback control bind", () => { expect(() => loadConfig({ ...base, ROBIN_HOST: "0.0.0.0" })).toThrow(/loopback/); });
  it("accepts a fully gated local production config", () => { expect(loadConfig(base).ROBIN_HOST).toBe("127.0.0.1"); });
});
