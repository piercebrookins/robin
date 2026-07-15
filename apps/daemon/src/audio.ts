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

export interface AudioBridgeOptions { helperPath: string; inputDevice: string; outputDevice: string; sampleRate?: number; maxBufferMs?: number; readyTimeoutMs?: number; maxReconnects?: number }

export class AudioBridge extends EventEmitter {
  readonly outbound: BoundedPcmBuffer;
  private process: ChildProcessWithoutNullStreams | undefined;
  private reconnects = 0;
  private stopped = true;
  private ready = false;
  private starting: Promise<void> | undefined;
  private rejectStartup: ((error: Error) => void) | undefined;
  private reconnectTimer: NodeJS.Timeout | undefined;
  constructor(private options: AudioBridgeOptions) {
    super();
    const sampleRate = options.sampleRate ?? 24_000;
    this.outbound = new BoundedPcmBuffer(Math.ceil(sampleRate * 2 * ((options.maxBufferMs ?? 750) / 1000)));
  }
  start(): Promise<void> {
    const wasStopped = this.stopped;
    this.stopped = false;
    if (wasStopped) this.reconnects = 0;
    if (this.ready && this.process) return Promise.resolve();
    if (!this.starting) { let tracked: Promise<void>; tracked = this.launch().finally(() => { if (this.starting === tracked) this.starting = undefined; }); this.starting = tracked; }
    return this.starting;
  }
  private launch(): Promise<void> {
    if (this.stopped) return Promise.reject(new Error("Audio bridge is stopped"));
    if (this.process && this.ready) return Promise.resolve();
    const rate = String(this.options.sampleRate ?? 24_000);
    return new Promise((resolve, reject) => {
      let settled = false; let diagnostics = "";
      const finish = (error?: Error) => { if (settled) return; settled = true; clearTimeout(timeout); if (this.rejectStartup === rejectCurrent) this.rejectStartup = undefined; error ? reject(error) : resolve(); };
      const rejectCurrent = (error: Error) => finish(error); this.rejectStartup = rejectCurrent;
      const child = spawn(this.options.helperPath, ["audio-bridge", "--input", this.options.inputDevice, "--output", this.options.outputDevice, "--rate", rate], { stdio: ["pipe", "pipe", "pipe"] });
      this.process = child;
      const timeout = setTimeout(() => { child.kill("SIGTERM"); finish(new Error(`Audio bridge did not become ready: ${diagnostics.trim() || "startup timeout"}`)); }, this.options.readyTimeoutMs ?? 5_000);
      child.stdout.on("data", chunk => this.emit("input", Buffer.from(chunk)));
      child.stderr.on("data", chunk => {
        const text = chunk.toString(); diagnostics = `${diagnostics}${text}`.slice(-4_096); this.emit("diagnostic", text);
        if (/audio bridge ready/i.test(text)) { this.ready = true; this.emit("ready"); this.flush(); finish(); }
      });
      child.once("error", error => finish(error));
      child.on("exit", code => {
        clearTimeout(timeout); const current = this.process === child; if (!current) return; this.process = undefined; const wasReady = this.ready; this.ready = false;
        if (!settled) finish(new Error(diagnostics.trim() || `Audio bridge exited before ready (${code ?? "unknown"})`));
        this.emit("disconnect", code);
        if (!this.stopped) this.scheduleReconnect(wasReady);
      });
    });
  }
  private scheduleReconnect(wasReady: boolean): void {
    this.reconnects++;
    if (this.reconnects > (this.options.maxReconnects ?? 5)) { this.emit("failed", new Error("Audio bridge exceeded its reconnect budget")); return; }
    const delay = Math.min(5_000, 250 * 2 ** (this.reconnects - 1));
    this.emit("reconnecting", { attempt: this.reconnects, delay, wasReady });
    this.reconnectTimer = setTimeout(() => { if (this.stopped || this.process || this.starting) return; void this.start().catch(error => this.emit("reconnect-error", error)); }, delay);
  }
  play(pcm: Buffer): void { this.outbound.push(pcm); this.flush(); }
  private flush(): void { if (!this.ready || !this.process?.stdin.writable) return; const data = this.outbound.read(8192); if (data.length) { const writable = this.process.stdin.write(data); this.emit("played", data.length / 48); if (!writable) { this.process.stdin.once("drain", () => this.flush()); return; } } if (this.outbound.length) queueMicrotask(() => this.flush()); }
  interrupt(): void { this.outbound.clear(); this.process?.kill("SIGUSR1"); this.emit("barge-in"); }
  stop(): void { this.stopped = true; this.ready = false; if (this.reconnectTimer) clearTimeout(this.reconnectTimer); this.outbound.clear(); this.rejectStartup?.(new Error("Audio bridge stopped")); this.rejectStartup = undefined; this.process?.kill("SIGTERM"); this.process = undefined; }
  health() { return { connected: Boolean(this.process) && this.ready, bufferedBytes: this.outbound.length, droppedBytes: this.outbound.droppedBytes, reconnects: this.reconnects }; }
}
