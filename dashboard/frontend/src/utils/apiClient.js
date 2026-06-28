/**
 * Centralised API client with automatic JWT injection + token refresh.
 */

const BASE = "";

function getTokens() {
  if (typeof window === "undefined") return {};
  try {
    return {
      access: localStorage.getItem("aep_access_token"),
      refresh: localStorage.getItem("aep_refresh_token"),
    };
  } catch {
    return {};
  }
}

function setTokens(access, refresh) {
  try {
    if (access) localStorage.setItem("aep_access_token", access);
    if (refresh) localStorage.setItem("aep_refresh_token", refresh);
  } catch {}
}

function clearTokens() {
  try {
    localStorage.removeItem("aep_access_token");
    localStorage.removeItem("aep_refresh_token");
    localStorage.removeItem("aep_user");
  } catch {}
}

async function refreshAccessToken() {
  const { refresh } = getTokens();
  if (!refresh) return false;
  try {
    const res = await fetch(`${BASE}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) {
      clearTokens();
      return false;
    }
    const data = await res.json();
    setTokens(data.access_token, data.refresh_token);
    // Sync middleware auth cookie with refreshed token
    if (typeof document !== "undefined") {
      document.cookie = `aep_token=${data.access_token}; path=/; max-age=${7 * 24 * 60 * 60}; SameSite=Lax`;
    }
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

export async function apiFetch(path, options = {}) {
  const { access } = getTokens();
  const headers = {
    "Content-Type": "application/json",
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

export { setTokens, clearTokens, getTokens };
