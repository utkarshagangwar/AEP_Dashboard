/**
 * Centralised API client with automatic JWT injection + token refresh.
 *
 * Token storage:
 *   - access_token  → in-memory only, via ../lib/api's setAccessToken/getAccessToken
 *                      (shared module-level state, so this client and the axios
 *                      instance in ../lib/api never disagree about who's logged in;
 *                      never persisted to localStorage — XSS protection).
 *   - refresh_token → httpOnly cookie set by /api/auth/login, never touched by JS.
 *
 * A short-lived, non-httpOnly `aep_token` cookie (same value/lifetime as the
 * access token) is also set, purely so Edge middleware (src/middleware.js) can
 * gate route access without a network round trip. It is never read here for
 * authorizing API calls — only the in-memory token is used for that.
 */
import {
  setAccessToken,
  getAccessToken,
  clearAllAuth,
  refreshAccessToken,
  getInFlightRefresh,
} from "../lib/api";
import { getStoredUser } from "./authStore";

const BASE = "";

function getTokens() {
  return { access: getAccessToken() };
}

function setTokens(access) {
  setAccessToken(access || null);
}

function clearTokens() {
  clearAllAuth();
}

// The refresh token is single-use (the backend revokes it and issues a new one
// on every /auth/refresh call), so every concurrent caller must share ONE
// in-flight rotation. That shared promise now lives in ../lib/api alongside the
// access token itself, because this module is only half the story: the axios
// instance there is the other HTTP client, and when the two kept separate
// dedupe state they could still fire two rotations a few hundred ms apart and
// hard-log-out a valid session. See the single-flight comment in ../lib/api.

// Kick off a redemption of the httpOnly refresh cookie the instant this
// module is evaluated — which happens while the JS module graph loads,
// strictly before React mounts anything. Every page fires several queries
// on mount (this page alone: environments, credential-profiles, plus
// whatever VisualAuditSection/SowCheckpointsSection load eagerly), and on
// a fresh page load the in-memory access token is always empty (it's
// intentionally never persisted — see header comment). Without this, each
// of those queries independently 401s before apiFetch's reactive refresh
// below ever gets a chance to run, purely because they're all scheduled
// (as React mount effects) before this shared promise exists. Starting the
// refresh here — and having apiFetch await it if already in flight, below
// — collapses that guaranteed first-wave of 401s into a single request
// that already carries a valid token. No-ops harmlessly if there's no
// cached session to redeem.
if (typeof window !== "undefined" && getStoredUser() && !getAccessToken()) {
  refreshAccessToken();
}

export async function apiFetch(path, options = {}) {
  // If a refresh is already in flight (the pre-emptive one above, one
  // triggered by a sibling request's 401, or one started by the axios client
  // in ../lib/api), wait for it before firing this request instead of racing
  // it — racing means this request goes out token-less, guaranteed-401s, and
  // only *then* joins the refresh queue.
  const inFlight = getInFlightRefresh();
  if (inFlight) {
    await inFlight;
  }
  const { access } = getTokens();
  // FormData bodies must NOT get an explicit Content-Type — the browser sets
  // multipart/form-data with the boundary itself.
  const isFormData =
    typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(access ? { Authorization: `Bearer ${access}` } : {}),
    ...options.headers,
  };

  let res = await fetch(`${BASE}${path}`, { ...options, headers });

  // Auto-refresh on 401
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      const { access: newAccess } = getTokens();
      headers.Authorization = `Bearer ${newAccess}`;
      res = await fetch(`${BASE}${path}`, { ...options, headers });
    } else {
      clearTokens();
      // Clear middleware auth cookie
      if (typeof document !== "undefined") {
        document.cookie = "aep_token=; path=/; max-age=0";
      }
      if (typeof window !== "undefined") window.location.href = "/login";
      throw new Error("Session expired. Please log in again.");
    }
  }

  return res;
}

export async function apiGet(path) {
  const res = await apiFetch(path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err.detail === "string" ? err.detail : Array.isArray(err.detail) ? err.detail.map(d => d.msg || JSON.stringify(d)).join("; ") : err.error || `Request failed: ${res.status}`;
    throw new Error(detail);
  }
  return res.json();
}

export async function apiPost(path, body) {
  const res = await apiFetch(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err.detail === "string" ? err.detail : Array.isArray(err.detail) ? err.detail.map(d => d.msg || JSON.stringify(d)).join("; ") : err.error || `Request failed: ${res.status}`;
    throw new Error(detail);
  }
  return res.json();
}

export async function apiPut(path, body) {
  const res = await apiFetch(path, {
    method: "PUT",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err.detail === "string" ? err.detail : Array.isArray(err.detail) ? err.detail.map(d => d.msg || JSON.stringify(d)).join("; ") : err.error || `Request failed: ${res.status}`;
    throw new Error(detail);
  }
  return res.json();
}

export async function apiPatch(path, body) {
  const res = await apiFetch(path, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err.detail === "string" ? err.detail : Array.isArray(err.detail) ? err.detail.map(d => d.msg || JSON.stringify(d)).join("; ") : err.error || `Request failed: ${res.status}`;
    throw new Error(detail);
  }
  return res.json();
}

export async function apiDelete(path) {
  const res = await apiFetch(path, { method: "DELETE" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err.detail === "string" ? err.detail : Array.isArray(err.detail) ? err.detail.map(d => d.msg || JSON.stringify(d)).join("; ") : err.error || `Request failed: ${res.status}`;
    throw new Error(detail);
  }
  return res.json();
}

export { setTokens, clearTokens, getTokens, refreshAccessToken };
