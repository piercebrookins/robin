import { EventEmitter } from "node:events";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

export class BoundedPcmBuffer {
  private chunks: Buffer[] = [];
  private bytes = 0;
  droppedBytes = 0;
  constructor(readonly maxBytes: number) {}
  push(chunk: Buffer): void {
    if (chunk.length >= this.maxBytes) { this.droppedBytes += this.bytes + chunk.length - this.maxBytes; this.chunks = [chunk.subarray(chunk.length - this.maxBytes)]; this.bytes = this.maxBytes; return; }
    this.chunks.push(chunk); this.bytes += chunk.length;
    while (this.bytes > this.maxBytes) { const first = this.chunks.shift(); if (!first) break; this.bytes -= first.length; this.droppedBytes += first.length; }
  }
  read(maxBytes = this.bytes): Buffer {
    const all = Buffer.concat(this.chunks); const size = Math.min(maxBytes, all.length);
    const out = all.subarray(0, size); const rest = all.subarray(size);
    this.chunks = rest.length ? [rest] : []; this.bytes = rest.length; return out;
  }
  clear() { this.chunks = []; this.bytes = 0; }
  get length() { return this.bytes; }
}

export interface AudioBridgeOptions { helperPath: string; inputDevice: string; outputDevice: string; sampleRate?: number; maxBufferMs?: number }

export class AudioBridge extends EventEmitter {
  readonly outbound: BoundedPcmBuffer;
  private process: ChildProcessWithoutNullStreams | undefined;
  private reconnects = 0;
  private stopped = true;
  private reconnectTimer: NodeJS.Timeout | undefined;
  constructor(private options: AudioBridgeOptions) {
    super();
    const sampleRate = options.sampleRate ?? 24_000;
    this.outbound = new BoundedPcmBuffer(Math.ceil(sampleRate * 2 * ((options.maxBufferMs ?? 750) / 1000)));
  }
  start(): void { this.stopped = false; this.launch(); }
  private launch(): void {
    if (this.stopped || this.process) return;
    const rate = String(this.options.sampleRate ?? 24_000);
    this.process = spawn(this.options.helperPath, ["audio-bridge", "--input", this.options.inputDevice, "--output", this.options.outputDevice, "--rate", rate], { stdio: ["pipe", "pipe", "pipe"] });
    this.process.stdout.on("data", chunk => this.emit("input", Buffer.from(chunk)));
    this.process.stderr.on("data", chunk => this.emit("diagnostic", chunk.toString()));
    this.process.on("exit", code => { this.process = undefined; this.emit("disconnect", code); if (!this.stopped) { const delay = Math.min(5000, 250 * 2 ** this.reconnects++); this.reconnectTimer = setTimeout(() => this.launch(), delay); } });
  }
  play(pcm: Buffer): void { this.outbound.push(pcm); this.flush(); }
  private flush(): void { if (!this.process?.stdin.writable) return; const data = this.outbound.read(8192); if (data.length) { const writable = this.process.stdin.write(data); this.emit("played", data.length / 48); if (!writable) { this.process.stdin.once("drain", () => this.flush()); return; } } if (this.outbound.length) queueMicrotask(() => this.flush()); }
  interrupt(): void { this.outbound.clear(); this.process?.kill("SIGUSR1"); this.emit("barge-in"); }
  stop(): void { this.stopped = true; if (this.reconnectTimer) clearTimeout(this.reconnectTimer); this.outbound.clear(); this.process?.kill("SIGTERM"); this.process = undefined; }
  health() { return { connected: Boolean(this.process), bufferedBytes: this.outbound.length, droppedBytes: this.outbound.droppedBytes, reconnects: this.reconnects }; }
}
