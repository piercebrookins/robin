import { randomUUID } from "node:crypto";
import type { ApprovalRequest, ComputerAction, RiskClass, WindowInfo } from "../../../packages/protocol/src/index.js";

export interface PolicyContext {
  assignedGoal: string;
  focusedWindow?: WindowInfo;
  requestedByOwner: boolean;
  screenText?: string;
}

const BLOCKED = new Set<RiskClass>(["destructive", "financial", "credential_change", "captcha", "security_setting"]);
const APPROVAL = new Set<RiskClass>(["external_commitment", "sensitive"]);

export class PolicyEngine {
  readonly approvals = new Map<string, ApprovalRequest>();
  constructor(private allowedApps?: ReadonlySet<string>) {}

  classify(action: ComputerAction, context: PolicyContext): RiskClass {
    const text = `${context.screenText ?? ""} ${action.type === "type" ? action.text : ""}`.toLowerCase();
    if (/captcha|verify you are human/.test(text)) return "captcha";
    if (/password|passcode|api key|secret key|two.factor|2fa/.test(text)) return "credential_change";
    if (/system settings|privacy & security|firewall|filevault|accessibility permission/.test(text)) return "security_setting";
    if (/buy|purchase|wire transfer|payment|checkout/.test(text)) return "financial";
    if (/delete permanently|erase|empty trash|factory reset/.test(text)) return "destructive";
    if (/send|publish|submit|upload|invite|share sensitive/.test(text)) return "external_commitment";
    if (/private|confidential|ssn|social security|medical record/.test(text)) return "sensitive";
    if (context.focusedWindow?.bundleId === "us.zoom.xos") return "meeting_control";
    if (action.type === "open_url" && /zoom\.us\/j\//i.test(action.url)) return "meeting_control";
    if (action.type === "screenshot" || action.type === "move" || action.type === "wait") return "observe";
    return "reversible_local";
  }

  evaluate(action: ComputerAction, context: PolicyContext):
    | { decision: "allow"; risk: RiskClass }
    | { decision: "block"; risk: RiskClass; reason: string }
    | { decision: "approve"; risk: RiskClass; request: ApprovalRequest } {
    const targetApp = action.type === "semantic" ? action.app : context.focusedWindow?.bundleId;
    if (this.allowedApps && targetApp && targetApp !== "us.zoom.xos" && !this.allowedApps.has(targetApp)) return { decision: "block", risk: "sensitive", reason: `Application ${targetApp} is not allow-listed` };
    if (this.allowedApps && !targetApp && !["screenshot", "move", "wait"].includes(action.type)) return { decision: "block", risk: "sensitive", reason: "The active application identity could not be verified" };
    const risk = this.classify(action, context);
    if (BLOCKED.has(risk)) return { decision: "block", risk, reason: `${risk.replaceAll("_", " ")} actions are blocked in the MVP` };
    if (APPROVAL.has(risk)) {
      const approvableRisk = risk === "sensitive" ? "sensitive" : "external_commitment";
      const request = this.createApproval(approvableRisk, `Robin is ready to perform a ${risk.replaceAll("_", " ")} action.`, describeAction(action));
      return { decision: "approve", risk, request };
    }
    return { decision: "allow", risk };
  }

  createApproval(risk: "external_commitment" | "sensitive", summary: string, exactAction: string, sensitiveData?: string[]): ApprovalRequest {
    const now = Date.now();
    const request: ApprovalRequest = { id: randomUUID(), createdAt: new Date(now).toISOString(), expiresAt: new Date(now + 120_000).toISOString(), risk, summary, exactAction, ...(sensitiveData?.length ? { sensitiveData } : {}), status: "pending" };
    this.approvals.set(request.id, request); return request;
  }

  resolve(id: string, approved: boolean): ApprovalRequest {
    const request = this.approvals.get(id);
    if (!request || request.status !== "pending") throw new Error("Approval is missing or no longer pending");
    if (Date.parse(request.expiresAt) < Date.now()) request.status = "expired";
    else request.status = approved ? "approved" : "denied";
    return request;
  }

  pending(): ApprovalRequest[] {
    const now = Date.now();
    for (const request of this.approvals.values()) if (request.status === "pending" && Date.parse(request.expiresAt) < now) request.status = "expired";
    return [...this.approvals.values()].filter(a => a.status === "pending");
  }
}

function describeAction(action: ComputerAction): string {
  if (action.type === "type") return `Type: ${action.text.slice(0, 160)}`;
  if (action.type === "semantic") return `${action.action} ${action.role} “${action.title ?? ""}” in ${action.app}`;
  return action.type.replaceAll("_", " ");
}
