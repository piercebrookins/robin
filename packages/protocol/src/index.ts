export type MeetingState =
  | "ready" | "joining" | "waiting_room" | "in_meeting" | "working"
  | "sharing" | "recovery" | "human_takeover" | "leaving" | "stopped";

export type RiskClass =
  | "observe" | "reversible_local" | "meeting_control" | "external_commitment"
  | "sensitive" | "destructive" | "financial" | "credential_change"
  | "captcha" | "security_setting";

export type ComputerAction =
  | { type: "screenshot" }
  | { type: "open_url"; url: string }
  | { type: "click"; x: number; y: number; button?: "left" | "right" | "wheel" | "back" | "forward"; keys?: string[] }
  | { type: "double_click"; x: number; y: number; button?: "left" | "right" | "wheel" | "back" | "forward"; keys?: string[] }
  | { type: "move"; x: number; y: number; keys?: string[] }
  | { type: "scroll"; x: number; y: number; scrollX: number; scrollY: number; keys?: string[] }
  | { type: "type"; text: string }
  | { type: "keypress"; keys: string[] }
  | { type: "drag"; path: Array<{ x: number; y: number }>; keys?: string[] }
  | { type: "wait"; ms: number }
  | { type: "semantic"; app: string; role: string; title?: string; action: "press" | "focus" | "set_value"; value?: string };

export interface WindowInfo {
  id: number;
  owner: string;
  bundleId?: string;
  title: string;
  bounds: { x: number; y: number; width: number; height: number };
  focused: boolean;
  onScreen: boolean;
}

export interface CapturedFrame {
  mime: "image/png" | "image/jpeg";
  width: number;
  height: number;
  data: string;
  capturedAt: string;
  displayId: number;
}

export interface ActionReceipt {
  accepted: boolean;
  completed: number;
  error?: string;
  stopped?: boolean;
}

export interface ApprovalRequest {
  id: string;
  createdAt: string;
  expiresAt: string;
  risk: RiskClass;
  summary: string;
  exactAction: string;
  sensitiveData?: string[];
  status: "pending" | "approved" | "denied" | "expired";
}

export interface RobinEvent {
  id: string;
  timestamp: string;
  kind: string;
  severity: "debug" | "info" | "warning" | "error" | "critical";
  source: "daemon" | "realtime" | "worker" | "desktop" | "audio" | "policy" | "control";
  taskId?: string;
  data: Record<string, unknown>;
}

export interface HealthSnapshot {
  ok: boolean;
  mode: "production" | "simulator";
  state: MeetingState;
  stopped: boolean;
  takeover: boolean;
  checks: Record<string, { ok: boolean; message: string; updatedAt: string }>;
}

export interface ControlSnapshot {
  health: HealthSnapshot;
  meeting?: { url: string; state: MeetingState; muted: boolean; sharing: boolean };
  task?: { id: string; goal: string; status: string; progress: string };
  approvals: ApprovalRequest[];
  transcript: Array<{ id: string; at: string; speaker: string; text: string; final: boolean }>;
  events: RobinEvent[];
}
