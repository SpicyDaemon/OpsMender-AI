import { beforeEach, describe, expect, it } from "vitest";
import {
  forgetReturnPath,
  isReturnPath,
  rememberReturnPath,
  takeReturnPath,
} from "./return-path";

describe("return path after sign-in (M1-99)", () => {
  beforeEach(() => sessionStorage.clear());

  it("accepts only the confirm page and its fragment", () => {
    expect(isReturnPath("/actions/confirm")).toBe(true);
    expect(isReturnPath("/actions/confirm#token=abc.def")).toBe(true);
    for (const path of [
      "//evil.example.com",
      "https://evil.example.com/actions/confirm",
      "/actions/confirmed",
      "/actions/confirm/../../dashboard",
      "/actions/confirm?next=//evil.example.com",
      "/dashboard",
      "",
    ]) {
      expect(isReturnPath(path)).toBe(false);
    }
  });

  it("gives the remembered path once, then the dashboard", () => {
    rememberReturnPath("/actions/confirm#token=abc.def");
    expect(takeReturnPath()).toBe("/actions/confirm#token=abc.def");
    expect(takeReturnPath()).toBe("/dashboard");
  });

  it("never remembers or returns another path", () => {
    rememberReturnPath("https://evil.example.com");
    expect(takeReturnPath()).toBe("/dashboard");
    sessionStorage.setItem("opsmender_return_to", "//evil.example.com");
    expect(takeReturnPath()).toBe("/dashboard");
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("forgets the path once the link is open", () => {
    rememberReturnPath("/actions/confirm#token=abc.def");
    forgetReturnPath();
    expect(takeReturnPath()).toBe("/dashboard");
  });
});
