import { describe, expect, it } from "vitest";

import { fullIntakeUrl } from "@/lib/intake";

describe("fullIntakeUrl", () => {
  it("prefixes a relative intake path with the configured public base URL", () => {
    expect(
      fullIntakeUrl("/api/v1/intake/svc_abc", "https://incidents.example.com"),
    ).toBe("https://incidents.example.com/api/v1/intake/svc_abc");
  });

  it("trims a trailing slash on the configured base URL", () => {
    expect(
      fullIntakeUrl("/api/v1/intake/svc_abc", "https://incidents.example.com/"),
    ).toBe("https://incidents.example.com/api/v1/intake/svc_abc");
  });

  it("falls back to window.location.origin when no base URL is configured", () => {
    // jsdom default origin is http://localhost:3000 (or http://localhost).
    const out = fullIntakeUrl("/api/v1/intake/svc_abc", null);
    expect(out).toBe(`${window.location.origin}/api/v1/intake/svc_abc`);
  });

  it("returns an absolute intake URL unchanged", () => {
    expect(
      fullIntakeUrl("https://x.example.com/api/v1/intake/svc_abc", "https://y.example.com"),
    ).toBe("https://x.example.com/api/v1/intake/svc_abc");
  });

  it("returns null when there is no intake URL", () => {
    expect(fullIntakeUrl(null, "https://incidents.example.com")).toBeNull();
    expect(fullIntakeUrl(undefined)).toBeNull();
  });
});
