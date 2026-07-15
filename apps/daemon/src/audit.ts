import { appendFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import type { RobinEvent } from "../../../packages/protocol/src/index.js";

const SECRET_KEYS = /api.?key|authorization|password|token|secret|cookie|credential/i;
const CONTENT_KEYS = /^(?:url|goal|briefing|transcript|text|summary|exact.?action|sensitive.?data)$/i;
const SECRET_VALUES = /\bsk-[A-Za-z0-9_-]{10,}\b|\bBearer\s+[A-Za-z0-9._~-]+/gi;
const ZOOM_URL = /https?:\/\/(?:[\w-]+\.)?zoom\.us\/j\/(\d+)(?:\?pwd=[^\s"']+)?/gi;

export function redact(value: unknown, key = ""): unknown {
  if (SECRET_KEYS.test(key)) return "[REDACTED]";
  if (CONTENT_KEYS.test(key)) return "[CONTENT REDACTED]";
  if (typeof value === "string") {
    return value.replace(SECRET_VALUES, "[REDACTED]").replace(ZOOM_URL, "https://zoom.us/j/$1?pwd=[REDACTED]");
  }
  if (Array.isArray(value)) return value.map(v => redact(v));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redact(v, k)]));
  }
  return value;
}

export function redactEvent(event: RobinEvent): RobinEvent {
  const value = redact(event) as RobinEvent;
  if (event.source === "control") value.data = Object.fromEntries(Object.keys(event.data).map(key => [key, "[CONTENT REDACTED]"]));
  return value;
}

export class AuditWriter {
  private chain = Promise.resolve();
  private failure: Error | undefined;
  constructor(private directory: string) {}

  write(event: RobinEvent): void {
    this.chain = this.chain.then(async () => {
      await mkdir(this.directory, { recursive: true, mode: 0o700 });
      const day = event.timestamp.slice(0, 10);
      await appendFile(resolve(this.directory, `${day}.jsonl`), `${JSON.stringify(redactEvent(event))}\n`, { mode: 0o600 });
    }).catch(error => { this.failure = error instanceof Error ? error : new Error(String(error)); });
  }

  async flush(): Promise<void> { await this.chain; }
  health(): { ok: boolean; message: string } { return this.failure ? { ok: false, message: `Trace write failed: ${this.failure.message}` } : { ok: true, message: "Redacted trace writable" }; }
}
