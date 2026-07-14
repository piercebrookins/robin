import { access } from "node:fs/promises";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { NativeDesktopHarness } from "./desktop.js";

const exec = promisify(execFile);
if (process.argv[2] !== "doctor") { console.error("Usage: robin doctor"); process.exit(2); }
const checks: Array<[string, () => Promise<string>]> = [
  ["macOS", async () => (await exec("sw_vers", ["-productVersion"])).stdout.trim()],
  ["Apple silicon", async () => process.arch === "arm64" ? process.arch : Promise.reject(new Error(process.arch))],
  ["WindowServer session", async () => { const owner = (await exec("stat", ["-f", "%Su", "/dev/console"])).stdout.trim(); if (!owner || owner === "root") throw new Error("no graphical user is logged in"); return `console user ${owner}`; }],
  ["Display", async () => { const output = (await exec("system_profiler", ["SPDisplaysDataType"])).stdout; const resolution = output.match(/Resolution:\s*([^\n]+)/)?.[1]?.trim(); if (!resolution) throw new Error("active display resolution unavailable"); const expected = process.env.ROBIN_EXPECTED_RESOLUTION ?? "1920 x 1080"; if (!resolution.includes(expected)) throw new Error(`${resolution}; expected ${expected}`); return resolution; }],
  ["Zoom Workplace", async () => { await access("/Applications/zoom.us.app"); const version = (await exec("defaults", ["read", "/Applications/zoom.us.app/Contents/Info", "CFBundleShortVersionString"])).stdout.trim(); return `installed ${version}`; }],
  ["BlackHole routes", async () => { const output = (await exec("system_profiler", ["SPAudioDataType"])).stdout; for (const name of ["Robin Speaker", "Robin Microphone"]) if (!output.includes(name)) throw new Error(`${name} missing; run RobinMacHelper configure-audio`); return "both routes present"; }],
  ["Virtual audio probe", async () => probeAudio()],
  ["Helper signature", async () => { await exec("codesign", ["--verify", "--strict", resolve("apps/mac-helper/.build/release/RobinMacHelper")]); return "valid"; }],
  ["OpenAI key", async () => { await exec("security", ["find-generic-password", "-a", process.env.USER ?? "", "-s", "com.robin.agent.OPENAI_API_KEY"]); return "present in Keychain"; }],
  ["OpenAI connectivity", async () => { const key = (await exec("security", ["find-generic-password", "-a", process.env.USER ?? "", "-s", "com.robin.agent.OPENAI_API_KEY", "-w"])).stdout.trim(); const response = await fetch("https://api.openai.com/v1/models", { headers: { Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(10_000) }); if (!response.ok) throw new Error(`API returned ${response.status}`); return "authenticated"; }],
  ["Panel token", async () => { await exec("security", ["find-generic-password", "-a", process.env.USER ?? "", "-s", "com.robin.agent.ROBIN_PANEL_TOKEN"]); return "present in Keychain"; }],
  ["Mac permissions", async () => { const status = await new NativeDesktopHarness(process.env.ROBIN_HELPER_SOCKET ?? "/tmp/robin-helper.sock").permissionStatus(); const missing = Object.entries(status).filter(([, ok]) => !ok).map(([name]) => name); if (missing.length) throw new Error(`missing ${missing.join(", ")}`); return "granted"; }],
  ["Helper launch service", async () => { await exec("launchctl", ["print", `gui/${process.getuid?.()}/com.robin.helper`]); return "loaded"; }],
  ["Agent launch service", async () => { await exec("launchctl", ["print", `gui/${process.getuid?.()}/com.robin.agent`]); return "loaded"; }],
  ["FileVault", async () => { const status = (await exec("fdesetup", ["status"])).stdout.trim(); if (!/FileVault is On/i.test(status)) throw new Error(status); return "on"; }]
];
let failures = 0;
for (const [name, check] of checks) { try { console.log(`PASS  ${name}: ${await check()}`); } catch (error) { failures++; console.log(`FAIL  ${name}: ${error instanceof Error ? error.message.split("\n")[0] : String(error)}`); } }
console.log(failures ? `\nRobin is not ready: ${failures} check(s) need attention.` : "\nRobin is ready for a verification meeting.");
process.exitCode = failures ? 1 : 0;

async function probeAudio(): Promise<string> {
  const helper = resolve("apps/mac-helper/.build/release/RobinMacHelper");
  return await new Promise((resolveProbe, reject) => {
    const child = spawn(helper, ["audio-bridge", "--input", "Robin Speaker", "--output", "Robin Microphone", "--rate", "24000"], { stdio: ["pipe", "pipe", "pipe"] });
    const chunks: Buffer[] = []; let error = ""; child.stdout.on("data", chunk => chunks.push(Buffer.from(chunk))); child.stderr.on("data", chunk => error += chunk.toString());
    child.once("error", reject); child.once("exit", code => { if (code && !chunks.length) reject(new Error(error.trim() || `audio probe exited ${code}`)); });
    setTimeout(() => { child.kill("SIGTERM"); const pcm = Buffer.concat(chunks); if (pcm.length < 4800) { reject(new Error(error.trim() || "no PCM captured")); return; } let peak = 0; let energy = 0; for (let i = 0; i + 1 < pcm.length; i += 2) { const sample = pcm.readInt16LE(i); peak = Math.max(peak, Math.abs(sample)); energy += sample * sample; } const rms = Math.sqrt(energy / (pcm.length / 2)); if (peak >= 32760) reject(new Error("input is clipping")); else resolveProbe(`capture ready, RMS ${rms.toFixed(0)}, peak ${peak}`); }, 1000);
  });
}
