"use client";
import { useState, useEffect } from "react";
import {
  LayoutDashboard,
  FolderOpen,
  Bug,
  PlayCircle,
  FileBarChart,
  Bot,
  Users,
  Shield,
} from "lucide-react";
import { getStoredUser, clearStoredUser } from "../utils/authStore";
import { apiFetch } from "../utils/apiClient";

// Icons match the ones from the second (now-removed) sidebar shell, kept
// consistent as this became the one universal nav for every page.
const NAV = [
  {
    label: "Dashboard",
    href: "/dashboard",
    // No permission key — the stats overview is open to anyone logged in.
    icon: <LayoutDashboard size={16} />,
  },
  {
    label: "Projects",
    href: "/projects",
    permission: "projects",
    icon: <FolderOpen size={16} />,
  },
  {
    label: "Defects",
    href: "/defects",
    permission: "defects",
    icon: <Bug size={16} />,
  },
  {
    label: "Execute",
    href: "/execute",
    permission: "execute",
    icon: <PlayCircle size={16} />,
  },
  {
    label: "Reports",
    href: "/reports",
    permission: "reports",
    icon: <FileBarChart size={16} />,
  },
  {
    label: "Vibe Testing",
    href: "/ai-testing",
    permission: "vibe_testing",
    icon: <Bot size={16} />,
  },
];

const ADMIN_NAV = [
  {
    label: "Users",
    href: "/admin/users",
    icon: <Users size={16} />,
  },
  {
    label: "Audit Logs",
    href: "/admin/audit-logs",
    icon: <Shield size={16} />,
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
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10 }}>
            <img
              src="/spider-logo.png"
              alt="AEP logo"
              width={62}
              height={40}
              style={{ flexShrink: 0, objectFit: "contain" }}
            />
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
