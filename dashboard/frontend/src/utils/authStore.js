/**
 * Simple auth store — reads/writes to localStorage.
 * Framework-agnostic; React components use the useAuth hook.
 */

export function getStoredUser() {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("aep_user");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setStoredUser(user) {
  try {
    localStorage.setItem("aep_user", JSON.stringify(user));
  } catch {}
}

export function clearStoredUser() {
  try {
    localStorage.removeItem("aep_user");
    localStorage.removeItem("aep_access_token");
    localStorage.removeItem("aep_refresh_token");
  } catch {}
}

export function isAuthenticated() {
  // The access token is in-memory only now (see ../lib/api), so it's never
  // present here on a fresh page load — the cached profile is the signal.
  return !!getStoredUser();
}

// RBAC helper — mirrors backend rules
const ROLE_ORDER = ["viewer", "developer", "qa_engineer", "qa_lead", "admin"];

export function roleAtLeast(userRole, minRole) {
  return ROLE_ORDER.indexOf(userRole) >= ROLE_ORDER.indexOf(minRole);
}
