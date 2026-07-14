import { describe, expect, it } from "vitest";
import { BoundedPcmBuffer } from "../apps/daemon/src/audio.js";

describe("BoundedPcmBuffer", () => {
  it("bounds latency and drops oldest audio", () => { const buffer = new BoundedPcmBuffer(8); buffer.push(Buffer.from("123456")); buffer.push(Buffer.from("7890")); expect(buffer.length).toBeLessThanOrEqual(8); expect(buffer.read().toString()).toBe("7890"); expect(buffer.droppedBytes).toBe(6); });
  it("clears immediately on barge-in", () => { const buffer = new BoundedPcmBuffer(8); buffer.push(Buffer.from("1234")); buffer.clear(); expect(buffer.length).toBe(0); });
});
