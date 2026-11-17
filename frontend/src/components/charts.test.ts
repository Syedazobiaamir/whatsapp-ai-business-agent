import { describe, expect, it } from "vitest";
import { niceMax } from "./charts";
import { duration } from "../pages/Analytics";

describe("niceMax (chart axis top)", () => {
  it("never goes below 4, so tiny counts still get a readable axis", () => expect(niceMax(0)).toBe(4));
  it("rounds up to a friendly number above the data", () => {
    expect(niceMax(7)).toBe(8);
    expect(niceMax(37)).toBe(40);
    expect(niceMax(130)).toBe(200);
  });
  it("is always >= the data", () => [5, 9, 23, 99, 101, 999].forEach((v) => expect(niceMax(v)).toBeGreaterThanOrEqual(v)));
});

describe("duration", () => {
  it("formats seconds, minutes and hours", () => {
    expect(duration(null)).toBe("—");
    expect(duration(40)).toBe("40 s");
    expect(duration(300)).toBe("5 min");
    expect(duration(5400)).toBe("1.5 h");
  });
});