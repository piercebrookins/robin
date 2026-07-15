import { createConnection, type Socket } from "node:net";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { ActionReceipt, CapturedFrame, ComputerAction, WindowInfo } from "../../../packages/protocol/src/index.js";

export interface DesktopHarness {
  screenshot(): Promise<CapturedFrame>;
  perform(actions: ComputerAction[], signal?: AbortSignal): Promise<ActionReceipt>;
  windows(): Promise<WindowInfo[]>;
  focusedWindow(): Promise<WindowInfo | undefined>;
  permissionStatus(): Promise<Record<string, boolean>>;
  emergencyStop(): Promise<void>;
  resume(): Promise<void>;
}

interface RpcResponse { id: string; result?: unknown; error?: string }

export class NativeDesktopHarness implements DesktopHarness {
  private stopped = false;
  constructor(private socketPath: string, private displayId = 1) {}

  private rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    return new Promise((resolve, reject) => {
      const id = randomUUID(); let data = ""; let socket: Socket;
      const finish = (error?: Error, result?: T) => { socket.destroy(); error ? reject(error) : resolve(result as T); };
      socket = createConnection(this.socketPath, () => socket.write(`${JSON.stringify({ id, method, params })}\n`));
      socket.setTimeout(15_000, () => finish(new Error("Mac helper timed out")));
      socket.on("data", chunk => {
        data += chunk.toString();
        const newline = data.indexOf("\n"); if (newline < 0) return;
        try { const response = JSON.parse(data.slice(0, newline)) as RpcResponse; response.error ? finish(new Error(response.error)) : finish(undefined, response.result as T); }
        catch (error) { finish(error as Error); }
      });
      socket.on("error", error => finish(error));
    });
  }

  screenshot() { return this.rpc<CapturedFrame>("screenshot", { displayId: this.displayId }); }
  windows() { return this.rpc<WindowInfo[]>("windows"); }
  async focusedWindow() { return (await this.windows()).find(w => w.focused); }
  permissionStatus() { return this.rpc<Record<string, boolean>>("permissions"); }
  async perform(actions: ComputerAction[], signal?: AbortSignal): Promise<ActionReceipt> {
    if (this.stopped || signal?.aborted) return { accepted: false, completed: 0, stopped: true };
    return this.rpc<ActionReceipt>("perform", { actions, displayId: this.displayId });
  }
  async emergencyStop() { this.stopped = true; await this.rpc("stop"); }
  async resume() { await this.rpc("resume"); this.stopped = false; }
}

export class SimulatedDesktopHarness implements DesktopHarness {
  private stopped = false;
  private scene = "ready";
  private actionLog: ComputerAction[] = [];
  constructor(private screenshotDirectory = resolve("fixtures/screenshots")) {}

  async screenshot(): Promise<CapturedFrame> {
    const filename = ["joining", "waiting_room", "in_meeting", "sharing"].includes(this.scene) ? `zoom-${this.scene.replaceAll("_", "-")}.png` : "zoom-in-meeting.png";
    try { const png = await readFile(resolve(this.screenshotDirectory, filename)); return { mime: "image/png", width: png.readUInt32BE(16), height: png.readUInt32BE(20), data: png.toString("base64"), capturedAt: new Date().toISOString(), displayId: 1 }; }
    catch { const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="; return { mime: "image/png", width: 1, height: 1, data: png, capturedAt: new Date().toISOString(), displayId: 1 }; }
  }
  async windows(): Promise<WindowInfo[]> { return [{ id: 1, owner: "Fake Zoom", bundleId: "us.zoom.xos", title: this.scene, bounds: { x: 130, y: 90, width: 1020, height: 620 }, focused: true, onScreen: true }]; }
  async focusedWindow() { return (await this.windows())[0]; }
  async permissionStatus() { return { screenRecording: true, accessibility: true, inputMonitoring: true, microphone: true }; }
  async perform(actions: ComputerAction[], signal?: AbortSignal): Promise<ActionReceipt> {
    if (this.stopped || signal?.aborted) return { accepted: false, completed: 0, stopped: true };
    for (const action of actions) {
      if (this.stopped || signal?.aborted) return { accepted: false, completed: this.actionLog.length, stopped: true };
      this.actionLog.push(action);
      if (action.type === "semantic" && action.title) { if (/stop share/i.test(action.title)) this.scene = "in_meeting"; else if (/share screen/i.test(action.title)) this.scene = "sharing"; else if (/leave/i.test(action.title)) this.scene = "ready"; else this.scene = action.title.toLowerCase().replaceAll(" ", "_"); }
      if (action.type === "open_url") this.scene = "joining";
      if (action.type === "wait") await new Promise(resolve => setTimeout(resolve, Math.min(action.ms, 20)));
    }
    return { accepted: true, completed: actions.length };
  }
  async emergencyStop() { this.stopped = true; }
  async resume() { this.stopped = false; }
  setScene(scene: string) { this.scene = scene; }
  actions() { return [...this.actionLog]; }
}
