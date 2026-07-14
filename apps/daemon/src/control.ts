import { resolve } from "node:path";
import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import type { RobinConfig } from "./config.js";
import type { RobinOrchestrator } from "./orchestrator.js";

export async function createControlServer(config: RobinConfig, orchestrator: RobinOrchestrator) {
  const server = Fastify({ logger: false, bodyLimit: 64 * 1024, trustProxy: false });
  const panelRoot = resolve(process.cwd(), "apps/control-panel/public");
  await server.register(fastifyStatic, { root: panelRoot, prefix: "/" });
  server.addHook("onRequest", async (request, reply) => {
    reply.header("Cache-Control", "no-store").header("X-Content-Type-Options", "nosniff").header("X-Frame-Options", "DENY").header("Referrer-Policy", "no-referrer").header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'");
    if (!request.url.startsWith("/api/")) return;
    const expected = config.ROBIN_PANEL_TOKEN;
    if (expected && request.headers.authorization !== `Bearer ${expected}`) return reply.code(401).send({ error: "Unauthorized" });
  });
  server.get("/api/state", async () => orchestrator.snapshot(config.ROBIN_MODE));
  server.get("/api/events", async (request, reply) => {
    reply.raw.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-store", Connection: "keep-alive" });
    const send = () => reply.raw.write(`data: ${JSON.stringify(orchestrator.snapshot(config.ROBIN_MODE))}\n\n`);
    const heartbeat = setInterval(() => reply.raw.write(": heartbeat\n\n"), 15_000); send(); orchestrator.events.on("event", send);
    request.raw.on("close", () => { clearInterval(heartbeat); orchestrator.events.off("event", send); });
    await new Promise(() => undefined);
  });
  server.post<{ Body: { url?: string; briefing?: string } }>("/api/meeting/join", async (request, reply) => { if (!request.body?.url) return reply.code(400).send({ error: "Meeting URL is required" }); await orchestrator.join(request.body.url, request.body.briefing ?? ""); return { ok: true }; });
  server.post("/api/meeting/admitted", async () => { orchestrator.markMeetingState("in_meeting"); return { ok: true }; });
  server.post<{ Body: { goal?: string; constraints?: string[]; successCriteria?: string[] } }>("/api/task", async (request, reply) => { if (!request.body?.goal) return reply.code(400).send({ error: "Task goal is required" }); return { taskId: await orchestrator.delegate(request.body.goal, request.body.constraints, request.body.successCriteria) }; });
  server.post<{ Params: { id: string }; Body: { approved?: boolean } }>("/api/approvals/:id", async request => ({ approval: orchestrator.policy.resolve(request.params.id, request.body?.approved === true) }));
  server.post("/api/meeting/share", async () => { await orchestrator.share(); return { ok: true }; });
  server.post("/api/meeting/stop-share", async () => { await orchestrator.stopShare(); return { ok: true }; });
  server.post<{ Body: { muted?: boolean } }>("/api/meeting/mute", async request => { await orchestrator.mute(request.body?.muted !== false); return { ok: true }; });
  server.post("/api/meeting/leave", async () => { await orchestrator.leave(); return { ok: true }; });
  server.post("/api/emergency-stop", async () => { await orchestrator.emergencyStop(); return { ok: true }; });
  server.post("/api/takeover", async () => { await orchestrator.humanTakeover(); return { ok: true }; });
  server.post("/api/resume", async () => { await orchestrator.resume(); return { ok: true }; });
  server.setErrorHandler((error, _request, reply) => reply.code(400).send({ error: error instanceof Error ? error.message : String(error) }));
  return server;
}
