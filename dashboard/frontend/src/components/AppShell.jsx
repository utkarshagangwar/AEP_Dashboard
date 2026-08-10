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
  LogOut,
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

// localStorage key for the collapsed/expanded choice. Scoped with the app
// prefix used everywhere else (aep_token, aep_user, ...).
const SIDEBAR_COLLAPSED_KEY = "aep_sidebar_collapsed";

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
    position: relative; /* anchors the collapsed-state tooltip below */
    /* Named properties, not "all": transitioning "all" also animates layout
       and colour properties we never intended to move. 180ms sits inside the
       150-250ms band that reads as responsive while still being visible.
       gap/padding are additionally animated so a row recenters around its
       icon in step with the sidebar's own width slide, instead of snapping
       the moment the rail finishes. */
    transition: background-color 180ms cubic-bezier(0.22, 1, 0.36, 1),
      color 180ms cubic-bezier(0.22, 1, 0.36, 1),
      gap 300ms var(--ease-out), padding 300ms var(--ease-out);
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
    /* The collapsed rail's one piece of motion that isn't a fade: icons
       scale up 12% under a slight-overshoot curve as the rail finishes
       narrowing, reading as the icon "settling" into the now-icon-only row
       rather than just being what's left after the label disappeared. */
    transition: transform 300ms cubic-bezier(0.34, 1.42, 0.4, 1) 60ms;
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

  /* ── Collapsible sidebar ──────────────────────────────────────────────
     Default is open; the collapsed/expanded choice persists in
     localStorage (aep_sidebar_collapsed) so it survives a reload.
     The rail's width is the one thing that "slides" — every child inside
     it quiets on its own terms instead of cross-fading as a screen: nav
     labels and section headings fade out and lose their reserved width,
     the active marker disappears, each row recenters around its icon, and
     the icon itself gets the small overshoot-scale defined above. Nothing
     here unmounts on collapse — same DOM, same handlers, so collapsing
     never risks losing nav state or user data mid-interaction. */
  .aep-sidebar {
    width: 220px;
    transition: width 300ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] {
    width: 64px;
  }
  @media (prefers-reduced-motion: reduce) {
    .aep-sidebar,
    .aep-nav-icon {
      transition: none;
    }
  }

  .aep-sidebar[data-collapsed="true"] .aep-nav-link {
    justify-content: center;
    gap: 0;
    padding-left: 0;
    padding-right: 0;
  }
  .aep-sidebar[data-collapsed="true"] .aep-nav-icon {
    transform: scale(1.12);
  }
  .aep-sidebar[data-collapsed="true"] .aep-nav-marker {
    display: none;
  }

  .aep-nav-label {
    overflow: hidden;
    white-space: nowrap;
    opacity: 1;
    transition: opacity 160ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] .aep-nav-label {
    opacity: 0;
    width: 0;
  }

  /* Collapsed-state tooltip. Always in the DOM (content comes from
     data-tip); only ever painted while the sidebar itself is collapsed, so
     it never doubles up with the visible label in the open state. */
  .aep-nav-link::after {
    content: attr(data-tip);
    position: absolute;
    left: calc(100% + 10px);
    top: 50%;
    transform: translateY(-50%) translateX(-4px);
    background: var(--foreground);
    color: var(--background);
    font-size: 11px;
    font-weight: 500;
    padding: 4px 8px;
    border-radius: 5px;
    white-space: nowrap;
    opacity: 0;
    pointer-events: none;
    transition: opacity 140ms var(--ease-out), transform 140ms var(--ease-out);
    z-index: 20;
  }
  .aep-sidebar[data-collapsed="true"] .aep-nav-link:hover::after,
  .aep-sidebar[data-collapsed="true"] .aep-nav-link:focus-visible::after {
    opacity: 1;
    transform: translateY(-50%) translateX(0);
  }

  .aep-logo-row {
    transition: justify-content 300ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] .aep-logo-row {
    justify-content: center;
  }
  .aep-logo-text {
    overflow: hidden;
    white-space: nowrap;
    opacity: 1;
    transition: opacity 160ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] .aep-logo-text {
    opacity: 0;
    width: 0;
  }

  .aep-admin-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted-foreground);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 4px 12px;
    margin: 0 0 4px;
    overflow: hidden;
    white-space: nowrap;
    opacity: 1;
    transition: opacity 160ms var(--ease-out), padding 300ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] .aep-admin-label {
    opacity: 0;
    height: 0;
    padding-top: 0;
    padding-bottom: 0;
    margin: 0;
  }

  .aep-user-row {
    gap: 10px;
    transition: gap 300ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] .aep-user-row {
    gap: 0;
    justify-content: center;
  }
  .aep-user-textwrap {
    flex: 1;
    overflow: hidden;
    opacity: 1;
    transition: opacity 160ms var(--ease-out);
  }
  .aep-sidebar[data-collapsed="true"] .aep-user-textwrap {
    flex: 0 1 0%;
    width: 0;
    opacity: 0;
  }

  /* Floating edge toggle — sits on the seam between sidebar and content,
     vertically anchored near the top, and slides with the rail because it's
     positioned relative to the (sticky, so still a containing block) aside
     rather than tracked in JS. The glyph itself never rotates: a static
     rectangle-with-divider reads as "this controls the sidebar panel" on
     sight, and the filled half swaps side to say which state you're in,
     instead of asking the eye to infer meaning from a chevron's direction
     mid-flight. */
  .aep-sidebar-toggle {
    position: absolute;
    top: 18px;
    right: -11px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: var(--background);
    border: 1px solid var(--border);
    box-shadow: 0 1px 3px oklch(0 0 0 / 10%);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: var(--muted-foreground);
    z-index: 6;
    transition: color 160ms var(--ease-out), border-color 160ms var(--ease-out);
  }
  .aep-sidebar-toggle:hover {
    color: var(--foreground);
    border-color: color-mix(in oklch, var(--loader-accent), transparent 45%);
  }
  .aep-sidebar-toggle:focus-visible {
    outline: 2px solid var(--foreground);
    outline-offset: 2px;
  }

  /* Always-visible scrollbar.
     overflow-y: scroll, not auto: the track is reserved whether or not the
     page overflows, so moving between a long page and a short one no longer
     shifts the content sideways by the scrollbar's width. Windows 11 and
     macOS both hide an auto scrollbar until you scroll, which is what made
     the scroll position of a long page invisible at rest.
     The styling exists so a permanently-present bar reads as part of the UI
     rather than a raw OS artifact — same ink-on-transparent ladder as the nav
     rows, so it inverts correctly in dark mode. */
  .aep-main {
    overflow-y: scroll;
    scrollbar-gutter: stable;
    /* Firefox */
    scrollbar-width: thin;
    scrollbar-color: color-mix(in oklch, var(--foreground), transparent 78%)
      transparent;
  }
  .aep-main::-webkit-scrollbar {
    width: 10px;
  }
  .aep-main::-webkit-scrollbar-track {
    background: transparent;
  }
  .aep-main::-webkit-scrollbar-thumb {
    background: color-mix(in oklch, var(--foreground), transparent 78%);
    border-radius: 999px;
    /* Inset the thumb inside the 10px track without changing layout width. */
    border: 2px solid transparent;
    background-clip: content-box;
  }
  .aep-main::-webkit-scrollbar-thumb:hover {
    background: color-mix(in oklch, var(--foreground), transparent 62%);
    background-clip: content-box;
  }
`;

// Static rect-with-divider glyph for the sidebar toggle — approved over a
// rotating chevron because it stays legible mid-transition (nothing spins)
// and reads as "the sidebar panel" on sight. The filled half tracks which
// state you're in/would move to: left filled while open (click collapses
// the left panel), right filled while collapsed (click reopens it).
function SidebarToggleIcon({ collapsed }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <line x1="9.5" y1="3" x2="9.5" y2="21" />
      <rect
        x={collapsed ? "14.8" : "4"}
        y="4"
        width="5.2"
        height="16"
        rx="1.6"
        fill="currentColor"
        stroke="none"
        style={{ transition: "x 300ms var(--ease-out)" }}
      />
    </svg>
  );
}

function NavLink({ href, icon, label, activePaths }) {
  // usePathname, not window.location: with client-side routing the URL changes
  // without a remount, so reading window.location here would leave the active
  // highlight stuck on whichever page happened to be loaded first.
  const pathname = usePathname();
  // Prefix match, not just exact equality: a nav entry for "/sow" should also
  // read as active on a nested detail route like "/sow/<id>" -- exact
  // matching only ever lit up on the list page itself, so opening a document
  // (or a project, or a report) left its section unhighlighted the whole time
  // you were in it. The "+ '/'" boundary keeps "/sow" from also matching an
  // unrelated path that merely starts with the same letters (e.g. "/soware").
  const paths = activePaths || [href];
  const active = paths.some((p) => pathname === p || pathname.startsWith(p + "/"));
  return (
    // aria-current drives the active styling as well as announcing it, so the
    // two can't drift apart the way a separate `active` class would.
    // data-tip feeds the collapsed-rail tooltip (see SHELL_CSS); it's inert
    // and unused by CSS while the sidebar is open.
    <Link
      href={href}
      className="aep-nav-link"
      aria-current={active ? "page" : undefined}
      data-tip={label}
    >
      <span className="aep-nav-icon">{icon}</span>
      <span className="aep-nav-label">{label}</span>
      {active && <span className="aep-nav-marker" aria-hidden="true" />}
    </Link>
  );
}

export default function AppShell({ children, noPadding = false }) {
  const [user, setUser] = useState(null);
  // Default open, per spec — the persisted value (if any) is applied after
  // mount rather than as the initial state, so server-rendered markup and
  // the first paint always agree on "open" and there's nothing to hydrate
  // mismatched.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const stored = getStoredUser();
    // Middleware protects routes; this is a fallback for edge cases
    if (!stored) {
      window.location.href = "/login";
      return;
    }
    setUser(stored);

    if (window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true") {
      setCollapsed(true);
    }
  }, []);

  function toggleSidebar() {
    setCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      return next;
    });
  }

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

  const initials = user.full_name
    ?.split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        // Transparent, not var(--muted): the body's grid+glow canvas texture
        // (app/global.css) is meant to show through the whole authenticated
        // app, not just the login/loading screens outside AppShell. The
        // sidebar below keeps its own solid background so nav text stays on
        // a flat, fully legible surface -- only the content canvas shows
        // the texture.
        background: "transparent",
        fontFamily: "Inter, sans-serif",
      }}
    >
      <style>{SHELL_CSS}</style>

      {/* Sidebar */}
      <aside
        className="aep-sidebar"
        data-collapsed={collapsed}
        style={{
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
        {/* Floating edge toggle. Positioned relative to this (sticky) aside,
            so it tracks the sidebar's own width transition automatically —
            no JS-computed offset needed. */}
        <button
          type="button"
          className="aep-sidebar-toggle"
          onClick={toggleSidebar}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <SidebarToggleIcon collapsed={collapsed} />
        </button>

        {/* Logo */}
        <div
          style={{
            padding: "20px 16px 16px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div
            className="aep-logo-row"
            style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10 }}
          >
            <img
              src="/spider-logo.png"
              alt="AEP logo"
              width={62}
              height={40}
              style={{ flexShrink: 0, objectFit: "contain" }}
            />
            <div className="aep-logo-text">
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
        {/* overflowX explicitly hidden: with only overflow-y set to a
            scrolling value, browsers compute overflow-x to "auto" too (per
            spec, an axis left at "visible" next to a non-visible one is
            coerced), which is what surfaced an unwanted horizontal
            scrollbar here — nothing in the nav actually needs to scroll
            sideways. */}
        <nav style={{ flex: 1, padding: "12px 8px", overflowY: "auto", overflowX: "hidden" }}>
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
              <p className="aep-admin-label">Admin</p>
              {ADMIN_NAV.map((n) => (
                <NavLink key={n.href} {...n} />
              ))}
            </div>
          )}
        </nav>

        {/* User */}
        <div style={{ padding: "12px 8px", borderTop: "1px solid var(--border)" }}>
          <div
            className="aep-user-row"
            style={{
              display: "flex",
              alignItems: "center",
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
                {initials}
              </span>
            </div>
            <div className="aep-user-textwrap">
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
                  whiteSpace: "nowrap",
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
              button rather than beside it.
              A faint red border stays visible at rest (30% mix, matching the
              `destructive` variant's own resting border) and strengthens on
              hover, so the button reads as "danger action" even before the
              pointer arrives.
              Collapsed: the label drops and the icon carries the action
              alone, with aria-label standing in for the now-hidden text so
              screen readers still get "Sign out" rather than nothing. */}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            aria-label={collapsed ? "Sign out" : undefined}
            className="mt-1.5 w-full text-xs text-muted-foreground border-2 border-[color-mix(in_oklch,var(--destructive)_30%,transparent)] hover:text-destructive hover:border-[color-mix(in_oklch,var(--destructive),transparent_65%)] [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)]"
          >
            <LogOut size={13} aria-hidden="true" />
            {!collapsed && <span className="ml-1.5">Sign out</span>}
          </Button>
        </div>
      </aside>

      {/* Main */}
      <main
        className="aep-main"
        style={{
          flex: 1,
          minWidth: 0,
          padding: noPadding ? 0 : "32px 32px",
        }}
      >
        {children}
      </main>
    </div>
  );
}
