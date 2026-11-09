import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

/**
 * HTTP client for the M4 backend.
 * - The access token is kept in memory only (never in localStorage).
 * - The refresh token is an httpOnly cookie set by the backend; on a 401 the client
 *   tries one silent refresh, otherwise the session-expired handler is called.
 */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

export const api = axios.create({ baseURL: API_BASE_URL, withCredentials: true, timeout: 180_000 });

let accessToken: string | null = null;
let onSessionExpired: (() => void) | null = null;
let refreshing: Promise<string | null> | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}
export function getAccessToken() {
  return accessToken;
}
export function setSessionExpiredHandler(fn: () => void) {
  onSessionExpired = fn;
}

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = axios
      .post(`${API_BASE_URL}/api/auth/refresh`, {}, { withCredentials: true })
      .then((r) => {
        setAccessToken(r.data.access_token);
        return r.data.access_token as string;
      })
      .catch(() => null)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

api.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    const url = original?.url ?? "";
    if (error.response?.status === 401 && original && !original._retried && !url.includes("/api/auth/")) {
      original._retried = true;
      const token = await refreshAccessToken();
      if (token) {
        original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      }
      setAccessToken(null);
      onSessionExpired?.();
    }
    return Promise.reject(error);
  },
);

/** Human-readable message from an API error (FastAPI `detail` or validation errors). */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    if (!err.response) return "Cannot reach the M4 backend. Is the API server running?";
    const d = (err.response.data as { detail?: unknown })?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d))
      return d
        .map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`)
        .join("; ");
    return `Request failed (HTTP ${err.response.status}).`;
  }
  return err instanceof Error ? err.message : "Unexpected error.";
}
