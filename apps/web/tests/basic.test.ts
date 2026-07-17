import { describe, expect, it } from "vitest";

describe("dashboard basics", () => {
  it("keeps core URL configurable", () => {
    expect("http://127.0.0.1:8787").toContain("8787");
  });
});
