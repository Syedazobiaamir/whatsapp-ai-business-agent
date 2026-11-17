// Tiny fetch wrapper: adds the login token, turns HTTP errors into readable exceptions.
// The base URL is relative ("/api/v1"): Vite (dev) and nginx (production) forward it to FastAPI.

const BASE = "/api/v1";
const TOKEN_KEY = "wa-agent-token";

// sessionStorage: the token disappears when the browser tab closes (less risk than localStorage).
// Production upgrade: an httpOnly cookie, which JavaScript cannot read at all.
export const tokenStore = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (t: string) => sessionStorage.setItem(TOKEN_KEY, t),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/Pydantic validation errors: [{loc: [...], msg: "..."}]
    return detail.map((d) => {
      const loc: (string | number)[] = d.loc ?? [];
      // FastAPI prefixes request errors with "body"/"query"/"path"; keep the real field name.
      const field = (["body", "query", "path"].includes(String(loc[0])) ? loc.slice(1) : loc).join(".");
      return field ? `${field}: ${d.msg}` : d.msg;
    }).join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
  return `Request failed (${status})`;
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const token = tokenStore.get();
  const res = await fetch(BASE + path, {
    method: options.method ?? "GET",
    headers: {
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && path !== "/auth/login") onUnauthorized(); // expired/invalid token
    throw new ApiError(res.status, errorMessage(body, res.status));
  }
  return body as T;
}

/** Build "?a=1&b=x" from an object, skipping empty values. */
export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}
