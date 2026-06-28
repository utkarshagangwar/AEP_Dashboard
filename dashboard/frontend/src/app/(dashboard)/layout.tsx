"use client";

import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { clearStoredUser, getStoredUser } from "@/utils/authStore";
import { apiFetch } from "@/utils/apiClient";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";

import {
  LayoutDashboard,
  PlayCircle,
  FolderOpen,
  FileBarChart,
  Bug,
  Users,
  Shield,
  LogOut,
  Menu,
  X,
} from "lucide-react";

// ─── Navigation items with role requirements ─────────────────────────────────
const NAV_ITEMS: Array<{
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  roles: string[] | null;
}> = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard, roles: null },
  { label: "Execute Tests", href: "/execute", icon: PlayCircle, roles: ["admin", "qa_lead", "qa_engineer"] },
  { label: "Projects", href: "/projects", icon: FolderOpen, roles: null },
  { label: "Reports", href: "/reports", icon: FileBarChart, roles: null },
  { label: "Defects", href: "/defects", icon: Bug, roles: null },
  { label: "Users", href: "/admin/users", icon: Users, roles: ["admin"] },
  { label: "Audit Log", href: "/audit", icon: Shield, roles: ["admin"] },
];

// ─── Role badge color mapping ────────────────────────────────────────────────
const ROLE_COLORS: Record<string, string> = {
  admin: "bg-red-100 text-red-700 border-red-200",
  qa_lead: "bg-blue-100 text-blue-700 border-blue-200",
  qa_engineer: "bg-green-100 text-green-700 border-green-200",
  developer: "bg-purple-100 text-purple-700 border-purple-200",
  viewer: "bg-gray-100 text-gray-600 border-gray-200",
};

function SidebarLink({
  href,
  icon: Icon,
  label,
  active,
}: {
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  active: boolean;
}) {
  return (
    <a
      href={href}
      className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
        active
          ? "bg-gray-100 text-gray-900"
          : "text-gray-500 hover:bg-gray-50 hover:text-gray-700"
      }`}
    >
      <Icon
        className={`h-4 w-4 flex-shrink-0 ${
          active ? "text-blue-600" : "text-gray-400"
        }`}
      />
      {label}
    </a>
  );
}

function DashboardShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Sync stored user for AppShell compatibility
  useEffect(() => {
    if (user) {
      const stored = getStoredUser();
      if (!stored || stored.id !== user.id) {
        localStorage.setItem("aep_user", JSON.stringify(user));
      }
    }
  }, [user]);

  async function handleLogout() {
    try {
      const refresh = localStorage.getItem("aep_refresh_token");
      await apiFetch("/api/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refresh }),
      });
    } catch {}
    clearStoredUser();
    document.cookie = "aep_token=; path=/; max-age=0";
    window.location.href = "/login";
  }

  const role = user?.role || "viewer";
  const initials = user?.full_name
    ?.split(" ")
    .map((w: string) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2) || "??";

  const filteredNav = NAV_ITEMS.filter(
    (item) => !item.roles || item.roles.includes(role)
  );

  return (
    <div className="flex h-screen bg-gray-50 font-sans">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-60 bg-white border-r border-gray-200 flex flex-col transition-transform lg:translate-x-0 lg:static lg:z-auto ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Logo */}
        <div className="p-5 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center flex-shrink-0">
              <PlayCircle className="w-4 h-4 text-white" />
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-900 leading-tight">AEP</p>
              <p className="text-[10px] text-gray-400 leading-tight">QA Platform</p>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-3 overflow-y-auto">
          <p className="px-3 py-1 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
            Navigation
          </p>
          <div className="mt-1 space-y-0.5">
            {filteredNav.map((item) => (
              <SidebarLink
                key={item.href}
                href={item.href}
                icon={item.icon}
                label={item.label}
                active={pathname === item.href}
              />
            ))}
          </div>
        </nav>

        {/* User section */}
        <div className="p-3 border-t border-gray-200">
          <div className="flex items-center gap-3 px-3 py-2">
            <Avatar className="h-7 w-7">
              <AvatarFallback className="bg-blue-50 text-blue-600 text-xs font-semibold">
                {initials}
              </AvatarFallback>
            </Avatar>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold text-gray-900 truncate">
                {user?.full_name || "Loading..."}
              </p>
              <span
                className={`inline-block text-[9px] font-medium px-1.5 py-0.5 rounded-full border ${
                  ROLE_COLORS[role] || "bg-gray-100 text-gray-600 border-gray-200"
                }`}
              >
                {role.replace("_", " ")}
              </span>
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-500 border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-600 hover:border-red-200 transition-colors"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="sticky top-0 z-30 bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="lg:hidden p-1.5 rounded-lg text-gray-500 hover:bg-gray-100"
            >
              {sidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
            <h1 className="text-lg font-semibold text-gray-900">
              Automation Execution Platform
            </h1>
          </div>

          <div className="flex items-center gap-3">
            <div className="text-right hidden sm:block">
              <p className="text-xs font-medium text-gray-900">{user?.full_name}</p>
              <Badge variant="secondary" className="text-[10px] h-4 px-1.5">
                {role.replace("_", " ")}
              </Badge>
            </div>
            <Avatar className="h-8 w-8">
              <AvatarFallback className="bg-blue-50 text-blue-600 text-xs font-semibold">
                {initials}
              </AvatarFallback>
            </Avatar>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <DashboardShell>{children}</DashboardShell>
    </AuthProvider>
  );
}
