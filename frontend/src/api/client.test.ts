import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, errorMessage, qs, setUnauthorizedHandler, tokenStore } from "./client";

// Minimal sessionStorage for the Node test environment.
const store = new Map<string, string>();
beforeEach(() => {
  store.clear();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, v),
    removeItem: (k: string) => store.delete(k),
  });
});
afterEach(() => vi.unstubAllGlobals());

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("errorMessage", () => {
  it("uses FastAPI's detail string", () => {
    expect(errorMessage({ detail: "Lead not found" }, 404)).toBe("Lead not found");
  });
  it("keeps the field name for body validation errors", () => {
    const body = { detail: [{ loc: ["body", "text"], msg: "String should have at most 4096 characters" }] };
    expect(errorMessage(body, 422)).toBe("text: String should have at most 4096 characters");
  });
  it("keeps the field name when there is no 'body' prefix (Stage 8 bug regression)", () => {
    const body = { detail: [{ loc: ["temperature"], msg: "Input should be less than or equal to 1" }] };
    expect(errorMessage(body, 422)).toBe("temperature: Input should be less than or equal to 1");
  });
  it("reads {message} objects (WhatsApp 502 errors)", () => {
    expect(errorMessage({ detail: { message: "WhatsApp could not send the message" } }, 502))
      .toBe("WhatsApp could not send the message");
  });
  it("falls back to the status code", () => {
    expect(errorMessage(null, 500)).toBe("Request failed (500)");
  });
});

describe("qs", () => {
  it("skips empty values and encodes the rest", () => {
    expect(qs({ q: "a b&c", page: 2, status: "", x: undefined, y: null, ok: false })).toBe("?q=a+b%26c&page=2&ok=false");
  });
  it("returns an empty string when nothing is set", () => {
    expect(qs({ q: "" })).toBe("");
  });
});

describe("api", () => {
  it("sends the bearer token and JSON body", async () => {
    tokenStore.set("tok123");
    const fetch = mockFetch(200, { ok: true });
    await api("/leads/1", { method: "PATCH", body: { status: "won" } });
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/leads/1");
    expect(init.headers).toEqual({ "Content-Type": "application/json", Authorization: "Bearer tok123" });
    expect(init.body).toBe('{"status":"won"}');
  });

  it("calls the sign-out handler on 401 (expired token)", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    mockFetch(401, { detail: "Not authenticated" });
    await expect(api("/leads")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does NOT sign out on a wrong password (401 from /auth/login)", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    mockFetch(401, { detail: "Incorrect email or password" });
    await expect(api("/auth/login", { method: "POST", body: {} })).rejects.toThrow("Incorrect email or password");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("returns undefined for 204 No Content", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 204 }));
    await expect(api("/faqs/1", { method: "DELETE" })).resolves.toBeUndefined();
  });
});
