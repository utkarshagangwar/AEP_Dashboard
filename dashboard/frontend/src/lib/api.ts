/**
 * Centralised Axios instance with automatic JWT injection and 401 auto-refresh.
 *
 * Token storage:
 *   - access_token  → in-memory only (never localStorage — XSS protection)
 *   - refresh_token → httpOnly cookie set by /api/auth/login proxy (JS cannot read it)
 *
 * On 401: calls /api/auth/refresh (Next.js proxy reads cookie, rotates it,
 * returns new access_token), then retries the original request.
 *
 * This module also owns the ONE shared refresh promise used by every HTTP
 * client in the app — see the single-flight section below. All API calls
 * throughout the app must go through this instance or utils/apiClient.js.
 */
import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";

// ─── In-memory access token (survives page navigation, cleared on tab close) ──

let _accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  _accessToken = token;
}

export function getAccessToken(): string | null {
  return _accessToken;
}

export function clearAllAuth() {
  _accessToken = null;
  // Clear any legacy localStorage entries from old implementation
  if (typeof window !== "undefined") {
    localStorage.removeItem("aep_access_token");
    localStorage.removeItem("aep_refresh_token");
    localStorage.removeItem("aep_user");
  }
}

export function redirectToLogin() {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

// ─── Shared single-flight refresh ─────────────────────────────────────────────
//
// The backend ROTATES the refresh token on every /auth/refresh call and revokes
// the previous one immediately (see backend/app/api/v1/auth.py::refresh). That
// makes two concurrent refreshes fatal, not merely wasteful: the second one
// presents an already-revoked token, gets a 401, and the caller hard-logs-out a
// session that was perfectly valid.
//
// That is exactly what used to happen. This module owned one deduped refresh
// (isRefreshing + failedQueue) for axios callers, while utils/apiClient.js owned
// a second, independent one (_refreshPromise) for fetch callers. Each deduped
// correctly *on its own*, but nothing deduped ACROSS them — so any page using
// both clients could fire two refreshes a few hundred ms apart. Observed in the
// backend log as:
//     Access token refreshed: <uid>          POST /auth/refresh 200
//     Refresh token already revoked (<uid>)  POST /auth/refresh 401
//     GET /api/v1/projects                                      401  → logout
//
// Both clients now share the single promise below, so rotation can never race
// itself regardless of which client triggered the refresh.

let _refreshPromise: Promise<boolean> | null = null;

async function _doRefresh(): Promise<boolean> {
  try {
    // Bare fetch rather than the `api` instance below: routing this through
    // `api` would re-enter its own 401 response interceptor and recurse.
    // No body needed — /api/auth/refresh reads the httpOnly cookie itself.
    const res = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
    });
    if (!res.ok) {
      clearAllAuth();
      return false;
    }
    const data = await res.json();
    if (!data?.access_token) {
      clearAllAuth();
      return false;
    }
    setAccessToken(data.access_token);
    // Keep middleware's routing cookie in sync with the rotated token. This
    // used to live only in apiClient's copy of the refresh, so an axios-driven
    // refresh left the cookie holding a superseded token.
    if (typeof document !== "undefined") {
      document.cookie = `aep_token=${data.access_token}; path=/; max-age=${24 * 60 * 60}; SameSite=Lax`;
    }
    return true;
  } catch {
    clearAllAuth();
    return false;
  }
}

/** Redeem the httpOnly refresh cookie. Safe to call concurrently. */
export function refreshAccessToken(): Promise<boolean> {
  if (!_refreshPromise) {
    _refreshPromise = _doRefresh().finally(() => {
      _refreshPromise = null;
    });
  }
  return _refreshPromise;
}

/** The in-flight refresh, if one is running — so callers can await it. */
export function getInFlightRefresh(): Promise<boolean> | null {
  return _refreshPromise;
}

// ─── Axios instance ───────────────────────────────────────────────────────────

const api = axios.create({
  baseURL: "",
  headers: { "Content-Type": "application/json" },
  // Ensure cookies (httpOnly refresh token) are sent with same-origin requests
  withCredentials: true,
});

// ─── Request interceptor: inject access token ─────────────────────────────────

api.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  // If a refresh is already in flight, wait for it rather than racing it —
  // racing means this request goes out with a stale/absent token, 401s, and
  // only then queues behind a refresh it could simply have waited for.
  const inFlight = getInFlightRefresh();
  if (inFlight) await inFlight;

  const token = getAccessToken();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ─── Response interceptor: auto-refresh on 401 ────────────────────────────────

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retry?: boolean })
      | undefined;

    // Don't retry on non-401, already-retried, or auth endpoints
    if (
      error.response?.status !== 401 ||
      !original ||
      original._retry ||
      original.url?.includes("/auth/refresh") ||
      original.url?.includes("/auth/login")
    ) {
      return Promise.reject(error);
    }

    original._retry = true;

    // Shared with utils/apiClient.js — concurrent callers all await the same
    // rotation instead of each starting their own.
    const refreshed = await refreshAccessToken();
    if (!refreshed) {
      clearAllAuth();
      redirectToLogin();
      return Promise.reject(error);
    }

    const newAccessToken = getAccessToken();
    if (original.headers && newAccessToken) {
      original.headers.Authorization = `Bearer ${newAccessToken}`;
    }
    return api(original);
  },
);

export default api;
