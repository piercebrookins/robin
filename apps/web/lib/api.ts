import type { Artifact, CalendarSnapshot, ChartSpec, DeckSpec, EventEnvelope, PreflightSnapshot, PresentationSession, RuntimeMetrics, RuntimeSnapshot, WorkspaceSnapshot } from "./types";

export const CORE_URL = process.env.NEXT_PUBLIC_ROBIN_CORE_URL ?? "http://127.0.0.1:8787";
export const CORE_WS_URL = CORE_URL.replace(/^http/, "ws");

export async function getState(): Promise<RuntimeSnapshot> {
  const response = await fetch(`${CORE_URL}/api/state`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load Robin state");
  return response.json();
}

export async function getPreflight(): Promise<PreflightSnapshot> {
  const response = await fetch(`${CORE_URL}/api/preflight`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load Robin preflight");
  return response.json();
}

export async function getCalendar(): Promise<CalendarSnapshot> {
  const response = await fetch(`${CORE_URL}/api/calendar`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load Robin calendar");
  return response.json();
}

export async function getEvents(limit = 25): Promise<EventEnvelope[]> {
  const response = await fetch(`${CORE_URL}/api/events?limit=${limit}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load Robin events");
  return response.json();
}

export async function getMetrics(): Promise<RuntimeMetrics> {
  const response = await fetch(`${CORE_URL}/api/metrics`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load Robin metrics");
  return response.json();
}

export async function getWorkspace(): Promise<WorkspaceSnapshot> {
  const response = await fetch(`${CORE_URL}/api/workspace`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load Robin workspace");
  return response.json();
}

export async function postJson<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(`${CORE_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getDeck(taskId: string, revision?: number): Promise<{ deck: DeckSpec; chart: ChartSpec | null }> {
  const state = await getState();
  const deckArtifact = latestArtifact(state.artifacts, taskId, "deck_json", revision);
  if (!deckArtifact) throw new Error("No deck artifact found");
  const deck = await getArtifactJson<DeckSpec>(deckArtifact);
  const chartArtifact = latestArtifact(state.artifacts, taskId, "chart_json", deck.revision);
  const chart = chartArtifact ? await getArtifactJson<ChartSpec>(chartArtifact) : null;
  return { deck, chart };
}

export async function getPresentationSession(taskId: string): Promise<PresentationSession> {
  const response = await fetch(`${CORE_URL}/api/presentations/${taskId}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Unable to load presentation state");
  return response.json();
}

export async function activatePresentation(taskId: string): Promise<PresentationSession> {
  return postJson<PresentationSession>(`/api/presentations/${taskId}/activate`);
}

export async function navigatePresentation(taskId: string, action: "next" | "previous"): Promise<PresentationSession> {
  return postJson<PresentationSession>(`/api/presentations/${taskId}/${action}`);
}

async function getArtifactJson<T>(artifact: Artifact): Promise<T> {
  const response = await fetch(`${CORE_URL}/api/artifacts/${artifact.path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Unable to load ${artifact.path}`);
  return response.json();
}

function latestArtifact(artifacts: Artifact[], taskId: string, type: Artifact["type"], revision?: number): Artifact | undefined {
  const matches = artifacts.filter((artifact) => artifact.task_id === taskId && artifact.type === type && (revision === undefined || artifact.revision === revision));
  return matches.sort((a, b) => b.revision - a.revision)[0];
}
