import { describe, expect, it } from "vitest";
import { playedAudioEndMs, shouldDropInputAudio } from "../apps/daemon/src/realtime.js";

describe("Realtime playback truncation", () => {
  it("uses elapsed audible time instead of all audio queued to Core Audio", () => {
    expect(playedAudioEndMs(5_000, 10_000, 10_420)).toBe(420);
  });

  it("never reports more audio than was queued", () => {
    expect(playedAudioEndMs(300, 10_000, 12_000)).toBe(300);
    expect(playedAudioEndMs(0, 10_000, 12_000)).toBe(0);
  });
  it("drops live input instead of allowing network latency to grow without bound", () => {
    expect(shouldDropInputAudio(63_000, 3_000, 65_536)).toBe(true);
    expect(shouldDropInputAudio(10_000, 3_000, 65_536)).toBe(false);
  });
});
