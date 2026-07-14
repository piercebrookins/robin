import { describe, expect, it } from "vitest";
import { MeetingStateMachine } from "../apps/daemon/src/state-machine.js";

describe("meeting lifecycle", () => {
  it("accepts the ordinary Zoom path", () => { const machine = new MeetingStateMachine(); for (const state of ["joining", "waiting_room", "in_meeting", "working", "sharing", "working", "in_meeting", "leaving", "ready"] as const) machine.transition(state); expect(machine.state).toBe("ready"); });
  it("rejects impossible transitions", () => { expect(() => new MeetingStateMachine().transition("sharing")).toThrow(/Invalid meeting transition/); });
});
