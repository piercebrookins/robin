import { EventEmitter } from "node:events";
import { randomUUID } from "node:crypto";
import type { RobinEvent } from "../../../packages/protocol/src/index.js";

export class EventBus extends EventEmitter {
  private history: RobinEvent[] = [];

  publish(event: Omit<RobinEvent, "id" | "timestamp">): RobinEvent {
    const complete: RobinEvent = { id: randomUUID(), timestamp: new Date().toISOString(), ...event };
    this.history.push(complete);
    if (this.history.length > 500) this.history.splice(0, this.history.length - 500);
    this.emit("event", complete);
    return complete;
  }

  recent(limit = 100): RobinEvent[] { return this.history.slice(-limit); }
}
