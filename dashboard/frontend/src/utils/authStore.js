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
  if (typeof window === "undefined") return false;
  return !!localStorage.getItem("aep_access_token");
}

// RBAC helper — mirrors backend rules
const ROLE_ORDER = ["viewer", "developer", "qa_engineer", "qa_lead", "admin"];

export function roleAtLeast(userRole, minRole) {
  return ROLE_ORDER.indexOf(userRole) >= ROLE_ORDER.indexOf(minRole);
}
