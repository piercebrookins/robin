import { describe, expect, it } from "vitest";
import { loadRecordedMeetingFixture, RecordedMeetingPlayer } from "../apps/daemon/src/simulator.js";
import { SimulatedDesktopHarness } from "../apps/daemon/src/desktop.js";

describe("recorded meeting simulator", () => {
  it("loads real mono PCM16 recordings and emits a barge-in turn", async () => {
    const fixture = await loadRecordedMeetingFixture(); expect(fixture.turns).toHaveLength(3); expect(fixture.turns.every(turn => turn.wav.length > 40_000)).toBe(true);
    const seen: string[] = []; const player = new RecordedMeetingPlayer(fixture, 10_000); player.play(turn => seen.push(turn.event ?? turn.speaker)); await new Promise(resolve => setTimeout(resolve, 20)); player.stop();
    expect(seen).toContain("barge_in");
  });
  it("returns rendered fake-Zoom frames for visual computer-use tests", async () => {
    const desktop = new SimulatedDesktopHarness(); desktop.setScene("waiting_room"); const frame = await desktop.screenshot();
    expect(frame).toMatchObject({ mime: "image/png", width: 1280, height: 720 }); expect(frame.data.length).toBeGreaterThan(20_000);
  });
});
