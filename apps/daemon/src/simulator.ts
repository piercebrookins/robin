import { basename, dirname, resolve } from "node:path";
import { readFile } from "node:fs/promises";

export interface RecordedTurn { speaker: "participant" | "robin"; atMs: number; transcript: string; event?: "barge_in"; audioFile: string; wav: Buffer }
export interface RecordedMeetingFixture { sampleRate: number; channels: number; turns: RecordedTurn[] }

export async function loadRecordedMeetingFixture(manifestPath = resolve("fixtures/meeting-audio/turns.json")): Promise<RecordedMeetingFixture> {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as any;
  if (manifest?.format?.encoding !== "pcm16" || manifest.format.sampleRate !== 24_000 || manifest.format.channels !== 1 || !Array.isArray(manifest.turns)) throw new Error("Recorded meeting fixture must be mono PCM16 at 24 kHz");
  const turns: RecordedTurn[] = [];
  for (const value of manifest.turns) {
    if (!value || !["participant", "robin"].includes(value.speaker) || !Number.isFinite(value.atMs) || typeof value.transcript !== "string" || typeof value.audioFile !== "string" || basename(value.audioFile) !== value.audioFile) throw new Error("Recorded meeting turn is invalid");
    const wav = await readFile(resolve(dirname(manifestPath), value.audioFile)); validateWav(wav);
    turns.push({ speaker: value.speaker, atMs: value.atMs, transcript: value.transcript, ...(value.event === "barge_in" ? { event: "barge_in" as const } : {}), audioFile: value.audioFile, wav });
  }
  return { sampleRate: 24_000, channels: 1, turns };
}

export class RecordedMeetingPlayer {
  private timers: NodeJS.Timeout[] = [];
  constructor(private fixture: RecordedMeetingFixture, private speed = 1) {}
  play(onTurn: (turn: RecordedTurn) => void): void {
    this.stop();
    for (const turn of this.fixture.turns) this.timers.push(setTimeout(() => onTurn(turn), Math.max(0, turn.atMs / this.speed)));
  }
  stop(): void { for (const timer of this.timers) clearTimeout(timer); this.timers = []; }
}

function validateWav(data: Buffer): void {
  if (data.length < 44 || data.toString("ascii", 0, 4) !== "RIFF" || data.toString("ascii", 8, 12) !== "WAVE") throw new Error("Recorded audio fixture is not a WAV file");
  if (data.readUInt16LE(22) !== 1 || data.readUInt32LE(24) !== 24_000 || data.readUInt16LE(34) !== 16) throw new Error("Recorded audio WAV must be mono PCM16 at 24 kHz");
}
