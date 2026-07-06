"use client";
import { useState, useEffect } from "react";
import { getStoredUser, clearStoredUser } from "../utils/authStore";
import { apiFetch } from "../utils/apiClient";

const NAV = [
  {
    label: "Dashboard",
    href: "/dashboard",
    // No permission key — the stats overview is open to anyone logged in.
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <rect x="3" y="3" width="7" height="7" />
        <rect x="14" y="3" width="7" height="7" />
        <rect x="14" y="14" width="7" height="7" />
        <rect x="3" y="14" width="7" height="7" />
      </svg>
    ),
  },
  {
    label: "Projects",
    href: "/projects",
    permission: "projects",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    label: "Test Suites",
    href: "/test-suites",
    permission: "test_suites",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M9 11l3 3L22 4" />
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
      </svg>
    ),
  },
  {
    label: "Test Runs",
    href: "/test-runs",
    permission: "test_runs",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <polygon points="5 3 19 12 5 21 5 3" />
      </svg>
    ),
  },
  {
    label: "Defects",
    href: "/defects",
    permission: "defects",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
  },
  {
    label: "Execute",
    href: "/execute",
    permission: "execute",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <polygon points="5 3 19 12 5 21 5 3" />
      </svg>
    ),
  },
  {
    label: "Reports",
    href: "/reports",
    permission: "reports",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
      </svg>
    ),
  },
  {
    label: "Vibe Testing",
    href: "/ai-testing",
    permission: "vibe_testing",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="3" y="11" width="18" height="10" rx="2" />
        <circle cx="12" cy="5" r="2" />
        <path d="M12 7v4" />
        <path d="M8 11V10" />
        <path d="M16 11V10" />
        <line x1="8" y1="16" x2="8.01" y2="16" strokeWidth="3" />
        <line x1="16" y1="16" x2="16.01" y2="16" strokeWidth="3" />
      </svg>
    ),
  },
];

const ADMIN_NAV = [
  {
    label: "Users",
    href: "/admin/users",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
  },
  {
    label: "Audit Logs",
    href: "/admin/audit-logs",
    icon: (
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
        <polyline points="10 9 9 9 8 9" />
      </svg>
    ),
  },
];

function NavLink({ href, icon, label }) {
  const active =
    typeof window !== "undefined" && window.location.pathname === href;
  return (
    <a
      href={href}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 12px",
        borderRadius: 6,
        fontSize: 13,
        fontWeight: active ? 600 : 400,
        color: active ? "#111827" : "#6B7280",
        background: active ? "#F3F4F6" : "transparent",
        transition: "all 0.1s",
        cursor: "pointer",
        textDecoration: "none",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.background = "#F9FAFB";
          e.currentTarget.style.color = "#374151";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "#6B7280";
        }
      }}
    >
      <span
        style={{ color: active ? "#2563EB" : "currentColor", display: "flex" }}
      >
        {icon}
      </span>
      {label}
    </a>
  );
}

export default function AppShell({ children, noPadding = false }) {
  const [user, setUser] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const stored = getStoredUser();
    // Middleware protects routes; this is a fallback for edge cases
    if (!stored) {
      window.location.href = "/login";
      return;
    }
    setUser(stored);
  }, []);

  async function handleLogout() {
    try {
      const refresh = localStorage.getItem("aep_refresh_token");
      await apiFetch("/api/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refresh }),
      });
    } catch {}
    clearStoredUser();
    // Clear middleware auth cookie
    document.cookie = "aep_token=; path=/; max-age=0";
    window.location.href = "/login";
  }

  if (!user) return null;

  // Role carries no implicit access — admins always see everything, every
  // other role (old or new) only sees nav items explicitly granted via
  // user.permissions. Users/Audit Logs stay admin-only, matching the
  // backend (they're the access-control mechanism itself).
  const isAdmin = user.role === "admin";
  const visibleNav = NAV.filter(
    (n) => isAdmin || !n.permission || (user.permissions || []).includes(n.permission),
  );

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        background: "#F9FAFB",
        fontFamily: "Inter, sans-serif",
      }}
    >
      {/* Sidebar */}
      <aside
        style={{
          width: 220,
          flexShrink: 0,
          background: "#fff",
          borderRight: "1px solid #E5E7EB",
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          position: "sticky",
          top: 0,
        }}
      >
        {/* Logo */}
        <div
          style={{
            padding: "20px 16px 16px",
            borderBottom: "1px solid #E5E7EB",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 28,
                height: 28,
                background: "#2563EB",
                borderRadius: 6,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="white"
                strokeWidth="2.5"
              >
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            </div>
            <div>
              <p
                style={{
                  margin: 0,
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#111827",
                  lineHeight: 1.2,
                }}
              >
                AEP
              </p>
              <p
                style={{
                  margin: 0,
                  fontSize: 10,
                  color: "#9CA3AF",
                  lineHeight: 1.2,
                }}
              >
                QA Platform
              </p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: "12px 8px", overflowY: "auto" }}>
          <div style={{ marginBottom: 4 }}>
            <p
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: "#9CA3AF",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                padding: "4px 12px",
                margin: "0 0 4px",
              }}
            >
              Navigation
            </p>
            {visibleNav.map((n) => (
              <NavLink key={n.href} {...n} />
            ))}
          </div>

          {isAdmin && (
            <div
              style={{
                marginTop: 16,
                paddingTop: 16,
                borderTop: "1px solid #F3F4F6",
              }}
            >
              <p
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  color: "#9CA3AF",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  padding: "4px 12px",
                  margin: "0 0 4px",
                }}
              >
                Admin
              </p>
              {ADMIN_NAV.map((n) => (
                <NavLink key={n.href} {...n} />
              ))}
            </div>
          )}
        </nav>

        {/* User */}
        <div style={{ padding: "12px 8px", borderTop: "1px solid #E5E7EB" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "8px 12px",
              borderRadius: 6,
            }}
          >
            <div
              style={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                background: "#EFF6FF",
                border: "1px solid #BFDBFE",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <span style={{ fontSize: 11, fontWeight: 600, color: "#2563EB" }}>
                {user.full_name
                  ?.split(" ")
                  .map((w) => w[0])
                  .join("")
                  .toUpperCase()
                  .slice(0, 2)}
              </span>
            </div>
            <div style={{ flex: 1, overflow: "hidden" }}>
              <p
                style={{
                  margin: 0,
                  fontSize: 12,
                  fontWeight: 600,
                  color: "#111827",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {user.full_name}
              </p>
              <span
                style={{
                  display: "inline-block",
                  fontSize: 9,
                  fontWeight: 500,
                  color: "#6B7280",
                  background: "#F3F4F6",
                  border: "1px solid #E5E7EB",
                  borderRadius: 999,
                  padding: "1px 6px",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {user.role?.replace("_", " ")}
              </span>
            </div>
          </div>
          <button
            onClick={handleLogout}
            style={{
              width: "100%",
              padding: "7px 12px",
              fontSize: 12,
              fontWeight: 500,
              color: "#6B7280",
              background: "transparent",
              border: "1px solid #E5E7EB",
              borderRadius: 6,
              cursor: "pointer",
              marginTop: 6,
              transition: "all 0.1s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "#FEF2F2";
              e.currentTarget.style.color = "#DC2626";
              e.currentTarget.style.borderColor = "#FECACA";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "#6B7280";
              e.currentTarget.style.borderColor = "#E5E7EB";
            }}
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main */}
      <main
        style={{
          flex: 1,
          overflowY: "auto",
          padding: noPadding ? 0 : "32px 32px",
        }}
      >
        {children}
      </main>
    </div>
  );
}
