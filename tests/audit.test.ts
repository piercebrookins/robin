import { describe, expect, it } from "vitest";
import { redact } from "../apps/daemon/src/audit.js";

describe("audit redaction", () => {
  it("redacts nested secrets and meeting passwords", () => {
    const result = redact({ authorization: "Bearer abc.def", payload: "sk-supersecret123456 https://zoom.us/j/123?pwd=hunter2" });
    expect(JSON.stringify(result)).not.toContain("supersecret"); expect(JSON.stringify(result)).not.toContain("hunter2"); expect(JSON.stringify(result)).toContain("[REDACTED]");
  });
});
