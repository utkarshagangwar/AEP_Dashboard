"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { getStoredUser } from "../utils/authStore";

const TABS = [
  { label: "Projects", href: "/projects", permission: "projects" },
  { label: "Execute", href: "/execute", permission: "execute" },
  // Reports has no backend permission gate, matching the existing navigation
  // policy for this authenticated route.
  { label: "Reports", href: "/reports" },
];

// Links preserve existing route boundaries. Each surface owns independent
// queries, mutations, and active-run state, so grouping them must not mount
// one page inside another.
export default function ScriptRunTabs() {
  const pathname = usePathname();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const isAdmin = user?.role === "admin";
  const tabs = TABS.filter(
    (tab) => isAdmin || !tab.permission || (user?.permissions || []).includes(tab.permission)
  );

  if (tabs.length < 2) return null;

  return (
    <nav aria-label="Script Runs" style={{ marginBottom: 24 }}>
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 3,
          padding: 5,
          borderRadius: 999,
          background: "#FFFFFF",
          boxShadow: "0 8px 20px rgba(37, 99, 235, 0.12)",
        }}
      >
        {tabs.map((tab) => {
          const active = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={active ? "page" : undefined}
              style={{
                padding: "9px 18px",
                borderRadius: 999,
                color: active ? "#111827" : "#6B7280",
                background: active ? "#F3F4F6" : "transparent",
                fontSize: 14,
                fontWeight: active ? 650 : 600,
                lineHeight: 1.2,
                textDecoration: "none",
                transition: "background-color 160ms ease, color 160ms ease",
              }}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
