import { access } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { NativeDesktopHarness } from "./desktop.js";

const exec = promisify(execFile);
if (process.argv[2] !== "doctor") { console.error("Usage: robin doctor"); process.exit(2); }
const checks: Array<[string, () => Promise<string>]> = [
  ["macOS", async () => (await exec("sw_vers", ["-productVersion"])).stdout.trim()],
  ["Apple silicon", async () => process.arch === "arm64" ? process.arch : Promise.reject(new Error(process.arch))],
  ["Zoom Workplace", async () => { await access("/Applications/zoom.us.app"); return "installed"; }],
  ["BlackHole routes", async () => { const output = (await exec("system_profiler", ["SPAudioDataType"])).stdout; for (const name of ["Robin Speaker", "Robin Microphone"]) if (!output.includes(name)) throw new Error(`${name} missing; run RobinMacHelper configure-audio`); return "both routes present"; }],
  ["Helper signature", async () => { await exec("codesign", ["--verify", "--strict", resolve("apps/mac-helper/.build/release/RobinMacHelper")]); return "valid"; }],
  ["OpenAI key", async () => { await exec("security", ["find-generic-password", "-a", process.env.USER ?? "", "-s", "com.robin.agent.OPENAI_API_KEY"]); return "present in Keychain"; }],
  ["Panel token", async () => { await exec("security", ["find-generic-password", "-a", process.env.USER ?? "", "-s", "com.robin.agent.ROBIN_PANEL_TOKEN"]); return "present in Keychain"; }],
  ["Mac permissions", async () => { const status = await new NativeDesktopHarness(process.env.ROBIN_HELPER_SOCKET ?? "/tmp/robin-helper.sock").permissionStatus(); const missing = Object.entries(status).filter(([, ok]) => !ok).map(([name]) => name); if (missing.length) throw new Error(`missing ${missing.join(", ")}`); return "granted"; }],
  ["Launch service", async () => { await exec("launchctl", ["print", `gui/${process.getuid?.()}/com.robin.agent`]); return "loaded"; }]
];
let failures = 0;
for (const [name, check] of checks) { try { console.log(`PASS  ${name}: ${await check()}`); } catch (error) { failures++; console.log(`FAIL  ${name}: ${error instanceof Error ? error.message.split("\n")[0] : String(error)}`); } }
console.log(failures ? `\nRobin is not ready: ${failures} check(s) need attention.` : "\nRobin is ready for a verification meeting.");
process.exitCode = failures ? 1 : 0;
