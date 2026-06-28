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
 * All API calls throughout the app must use this instance.
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

function clearAllAuth() {
  _accessToken = null;
  // Clear any legacy localStorage entries from old implementation
  if (typeof window !== "undefined") {
    localStorage.removeItem("aep_access_token");
    localStorage.removeItem("aep_refresh_token");
    localStorage.removeItem("aep_user");
  }
}

function redirectToLogin() {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

// ─── Axios instance ───────────────────────────────────────────────────────────

const api = axios.create({
  baseURL: "",
  headers: { "Content-Type": "application/json" },
  // Ensure cookies (httpOnly refresh token) are sent with same-origin requests
  withCredentials: true,
});

// ─── Request interceptor: inject access token ─────────────────────────────────

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ─── Response interceptor: auto-refresh on 401 ────────────────────────────────

let isRefreshing = false;
let failedQueue: Array<{
  resolve: (token: string) => void;
  reject: (error: unknown) => void;
}> = [];

function processQueue(error: unknown, token: string | null) {
  failedQueue.forEach((p) => (error || !token ? p.reject(error) : p.resolve(token!)));
  failedQueue = [];
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    // Don't retry on non-401, already-retried, or auth endpoints
    if (
      error.response?.status !== 401 ||
      original._retry ||
      original.url?.includes("/auth/refresh") ||
      original.url?.includes("/auth/login")
    ) {
      return Promise.reject(error);
    }

    if (isRefreshing) {
      // Park this request until the ongoing refresh finishes
      return new Promise<string>((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      }).then((token) => {
        if (original.headers) original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      });
    }

    original._retry = true;
    isRefreshing = true;

    try {
      // /api/auth/refresh is our Next.js proxy — it reads the httpOnly cookie
      // automatically (withCredentials=true) and rotates it server-side.
      const { data } = await axios.post(
        "/api/auth/refresh",
        {},
        { withCredentials: true },
      );

      const newAccessToken: string = data.access_token;
      setAccessToken(newAccessToken);
      processQueue(null, newAccessToken);

      if (original.headers) original.headers.Authorization = `Bearer ${newAccessToken}`;
      return api(original);
    } catch (refreshError) {
      processQueue(refreshError, null);
      clearAllAuth();
      redirectToLogin();
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  },
);

export default api;
