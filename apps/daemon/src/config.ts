import { z } from "zod";

const schema = z.object({
  ROBIN_HOST: z.string().default("127.0.0.1"),
  ROBIN_PORT: z.coerce.number().int().min(1024).max(65535).default(3939),
  ROBIN_PANEL_TOKEN: z.string().min(16).optional(),
  ROBIN_MODE: z.enum(["production", "simulator"]).default("simulator"),
  ROBIN_OPENAI_MODEL: z.string().default("gpt-5.6"),
  ROBIN_REALTIME_MODEL: z.string().default("gpt-realtime-2.1"),
  ROBIN_HELPER_SOCKET: z.string().default("/tmp/robin-helper.sock"),
  ROBIN_TRACE_DIR: z.string().default("./traces"),
  ROBIN_ALLOWED_APPS: z.string().default("us.zoom.xos,com.apple.Safari,com.apple.TextEdit"),
  ROBIN_WORKSPACE_DISPLAY: z.coerce.number().int().positive().default(1),
  ROBIN_AUDIO_INPUT: z.string().default("Robin Speaker"),
  ROBIN_AUDIO_OUTPUT: z.string().default("Robin Microphone"),
  OPENAI_API_KEY: z.string().min(20).optional()
});

export type RobinConfig = ReturnType<typeof loadConfig>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env) {
  const parsed = schema.parse(env);
  if (parsed.ROBIN_MODE === "production" && !parsed.OPENAI_API_KEY) {
    throw new Error("OPENAI_API_KEY is missing. Store it with scripts/keychain-secret.sh set OPENAI_API_KEY, then launch through the Keychain wrapper.");
  }
  if (parsed.ROBIN_MODE === "production" && !parsed.ROBIN_PANEL_TOKEN) {
    throw new Error("ROBIN_PANEL_TOKEN is missing. Store it with scripts/keychain-secret.sh set ROBIN_PANEL_TOKEN.");
  }
  if (parsed.ROBIN_MODE === "production" && !new Set(["127.0.0.1", "::1", "localhost"]).has(parsed.ROBIN_HOST)) {
    throw new Error("The production control panel must bind to loopback. Use a private authenticated tunnel for remote access.");
  }
  return { ...parsed, allowedApps: new Set(parsed.ROBIN_ALLOWED_APPS.split(",").map(v => v.trim()).filter(Boolean)) };
}
