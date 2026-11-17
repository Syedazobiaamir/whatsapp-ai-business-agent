import { afterEach, describe, expect, it, vi } from "vitest";
import { cx, label, timeAgo } from "./ui";

describe("cx", () => {
  it("joins only truthy class names", () => {
    expect(cx("a", false, null, undefined, "b")).toBe("a b");
  });
});

describe("label", () => {
  it("turns snake_case into words", () => {
    expect(label("needs_approval")).toBe("needs approval");
  });
});

describe("timeAgo", () => {
  afterEach(() => vi.useRealTimers());
  it.each([
    [10, "just now"],
    [5 * 60, "5m ago"],
    [3 * 3600, "3h ago"],
    [2 * 86400, "2d ago"],
  ])("%i seconds ago -> %s", (secondsAgo, expected) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-16T12:00:00Z"));
    expect(timeAgo(new Date(Date.now() - secondsAgo * 1000).toISOString())).toBe(expected);
  });
  it("shows a dash for missing dates", () => {
    expect(timeAgo(null)).toBe("—");
  });
});
