"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Bug,
  Workflow,
  Bot,
  FileText,
  Users,
  Shield,
  Gauge,
} from "lucide-react";
import { getStoredUser, clearStoredUser } from "../utils/authStore";
import { apiFetch, clearTokens } from "../utils/apiClient";
import { Button } from "./ui/button";

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
    label: "Script Runs",
    href: "/script-run",
    permissions: ["projects", "execute"],
    activePaths: ["/script-run", "/projects", "/execute", "/reports"],
    icon: <Workflow size={16} />,
  },
  {
    label: "Defects",
    href: "/defects",
    permission: "defects",
    icon: <Bug size={16} />,
  },
  {
    label: "Vibe Testing",
    href: "/ai-testing",
    permission: "vibe_testing",
    icon: <Bot size={16} />,
  },
  {
    label: "SOW",
    href: "/sow",
    // Distinct from vibe_testing on purpose — see SOW_FEATURE_PLAN.md §11.1.
    permission: "sow",
    icon: <FileText size={16} />,
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
  {
    label: "AI Usage",
    href: "/admin/ai-usage",
    icon: <Gauge size={16} />,
  },
];

/**
 * Sidebar chrome.
 *
 * Everything here used to be hardcoded hex (#111827, #6B7280, #2563EB, …)
 * applied through inline styles, with hover simulated by onMouseEnter /
 * onMouseLeave handlers that mutated those inline styles. That had three
 * concrete consequences, all fixed below:
 *
 *   - No :focus-visible anywhere, so keyboard users got no indication of
 *     position in the primary navigation at all. A real WCAG failure, not a
 *     stylistic one. JS mouse events cannot express focus.
 *   - The hex palette bypassed the token system entirely, so the .dark block
 *     in global.css could never apply to the shell.
 *   - #9CA3AF on white is 2.6:1 — the section labels and the "QA Platform"
 *     subtitle both failed AA.
 *
 * Colour strategy is "ink + orange marker": --foreground carries every
 * interactive state (hover, active, focus), and the mascot's orange appears
 * only as the small marker dot on the active row. That keeps the one
 * saturated colour in the shell tied to the spider rather than competing
 * with it, and it means no accent has to survive a contrast check as text.
 */
const SHELL_CSS = `
  .aep-nav-link {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 400;
    line-height: 1.4;
    color: var(--muted-foreground);
    background: transparent;
    text-decoration: none;
    cursor: pointer;
    /* Named properties, not "all": transitioning "all" also animates layout
       and colour properties we never intended to move. 180ms sits inside the
       150-250ms band that reads as responsive while still being visible. */
    transition: background-color 180ms cubic-bezier(0.22, 1, 0.36, 1),
      color 180ms cubic-bezier(0.22, 1, 0.36, 1);
  }
  /* Three measured steps, not two shades of almost-white. --muted alone is
     1.09:1 against the sidebar, which is why the old active row was invisible;
     using it for BOTH hover and active would additionally make any hovered
     item look selected. Expressed as a transparency of --foreground so the
     ladder inverts correctly in dark mode instead of staying pale grey.
       hover 1.14:1 · active 1.47:1 · pressed 1.78:1  (measured vs #fff) */
  .aep-nav-link:hover {
    background: color-mix(in oklch, var(--foreground), transparent 96%);
    color: var(--foreground);
  }
  .aep-nav-link:active {
    background: color-mix(in oklch, var(--foreground), transparent 86%);
  }
  /* Ink rather than --ring: --ring is the app's blue, and reintroducing it
     here would put back exactly the second accent this strategy removes. */
  .aep-nav-link:focus-visible {
    outline: 2px solid var(--foreground);
    outline-offset: 2px;
  }
  .aep-nav-link[aria-current="page"] {
    background: color-mix(in oklch, var(--foreground), transparent 90%);
    color: var(--foreground);
    font-weight: 500;
  }
  .aep-nav-icon {
    display: flex;
    flex-shrink: 0;
    color: currentColor;
  }
  /* The only saturated colour in the shell, and it carries no text — so the
     mascot orange never has to clear a 4.5:1 contrast check. */
  .aep-nav-marker {
    width: 6px;
    height: 6px;
    margin-left: auto;
    border-radius: 50%;
    flex-shrink: 0;
    background: var(--loader-accent);
  }
  @media (prefers-reduced-motion: reduce) {
    .aep-nav-link {
      transition: none;
    }
  }
`;

function NavLink({ href, icon, label, activePaths }) {
  // usePathname, not window.location: with client-side routing the URL changes
  // without a remount, so reading window.location here would leave the active
  // highlight stuck on whichever page happened to be loaded first.
  const pathname = usePathname();
  const active = (activePaths || [href]).includes(pathname);
  return (
    // aria-current drives the active styling as well as announcing it, so the
    // two can't drift apart the way a separate `active` class would.
    <Link href={href} className="aep-nav-link" aria-current={active ? "page" : undefined}>
      <span className="aep-nav-icon">{icon}</span>
      {label}
      {active && <span className="aep-nav-marker" aria-hidden="true" />}
    </Link>
  );
}

export default function AppShell({ children, noPadding = false }) {
  const [user, setUser] = useState(null);

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
      // /api/auth/logout reads the refresh token from its own httpOnly
      // cookie server-side — nothing to pass from here.
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {}
    clearTokens();
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
  const visibleNav = NAV.filter((n) => {
    if (isAdmin || (!n.permission && !n.permissions)) return true;
    if (n.permission) return (user.permissions || []).includes(n.permission);
    return n.permissions.some((permission) => (user.permissions || []).includes(permission));
  });

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        background: "var(--muted)",
        fontFamily: "Inter, sans-serif",
      }}
    >
      <style>{SHELL_CSS}</style>

      {/* Sidebar */}
      <aside
        style={{
          width: 220,
          flexShrink: 0,
          background: "var(--background)",
          borderRight: "1px solid var(--border)",
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
            borderBottom: "1px solid var(--border)",
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
                  color: "var(--foreground)",
                  lineHeight: 1.2,
                }}
              >
                AEP
              </p>
              <p
                style={{
                  margin: 0,
                  fontSize: 10,
                  // Was #9CA3AF (2.6:1). --muted-foreground is 4.6:1.
                  color: "var(--muted-foreground)",
                  lineHeight: 1.2,
                }}
              >
                QA Platform
              </p>
            </div>
          </div>
        </div>

        {/* Nav.
            The first group deliberately has no label. "NAVIGATION" above the
            navigation named nothing — the divider below is what separates the
            two groups, and "Admin" is the only label carrying real information
            (these routes are privileged). */}
        <nav style={{ flex: 1, padding: "12px 8px", overflowY: "auto" }}>
          <div style={{ marginBottom: 4 }}>
            {visibleNav.map((n) => (
              <NavLink key={n.href} {...n} />
            ))}
          </div>

          {isAdmin && (
            <div
              style={{
                marginTop: 16,
                paddingTop: 16,
                borderTop: "1px solid var(--border)",
              }}
            >
              <p
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--muted-foreground)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
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
        <div style={{ padding: "12px 8px", borderTop: "1px solid var(--border)" }}>
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
                // Was a blue chip (#EFF6FF / #BFDBFE / #2563EB) — the second
                // accent this strategy removes from the shell.
                background: "var(--muted)",
                border: "1px solid var(--border)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--foreground)" }}>
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
                  color: "var(--foreground)",
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
                  color: "var(--muted-foreground)",
                  background: "var(--muted)",
                  border: "1px solid var(--border)",
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
          {/* Ghost rather than the danger tone: a permanently red Sign out in
              the sidebar reads as an alarm you can't dismiss. It stays muted
              at rest and turns red on approach, and pointing the rim and bloom
              at --destructive is what keeps that intent inside the shared
              button rather than beside it. */}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            className="mt-1.5 w-full text-xs text-muted-foreground hover:text-destructive hover:border-[color-mix(in_oklch,var(--destructive),transparent_65%)] [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)]"
          >
            Sign out
          </Button>
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
