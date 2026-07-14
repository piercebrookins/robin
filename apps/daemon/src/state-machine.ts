import type { MeetingState } from "../../../packages/protocol/src/index.js";

const transitions: Record<MeetingState, ReadonlySet<MeetingState>> = {
  ready: new Set(["joining", "stopped"]), joining: new Set(["waiting_room", "in_meeting", "recovery", "human_takeover", "leaving", "stopped"]),
  waiting_room: new Set(["in_meeting", "recovery", "human_takeover", "leaving", "stopped"]),
  in_meeting: new Set(["working", "sharing", "recovery", "human_takeover", "leaving", "stopped"]),
  working: new Set(["in_meeting", "sharing", "recovery", "human_takeover", "leaving", "stopped"]),
  sharing: new Set(["working", "in_meeting", "recovery", "human_takeover", "leaving", "stopped"]),
  recovery: new Set(["joining", "waiting_room", "in_meeting", "working", "human_takeover", "leaving", "stopped"]),
  human_takeover: new Set(["ready", "joining", "waiting_room", "in_meeting", "stopped"]),
  leaving: new Set(["ready", "recovery", "human_takeover", "stopped"]), stopped: new Set(["ready"])
};

export class MeetingStateMachine {
  constructor(public state: MeetingState = "ready") {}
  transition(next: MeetingState): void {
    if (next === this.state) return;
    if (!transitions[this.state].has(next)) throw new Error(`Invalid meeting transition: ${this.state} -> ${next}`);
    this.state = next;
  }
}
