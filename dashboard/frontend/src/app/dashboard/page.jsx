"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import PageContainer from "../../components/PageContainer";
import { apiGet } from "../../utils/apiClient";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

const STATUS_COLORS = {
  passed: "#16A34A",
  failed: "#DC2626",
  running: "#2563EB",
  queued: "#D97706",
  pending: "#6B7280",
  cancelled: "#6B7280",
};

const STATUS_BG = {
  passed: "#DCFCE7",
  failed: "#FEE2E2",
  running: "#DBEAFE",
  queued: "#FEF3C7",
  pending: "#F3F4F6",
  cancelled: "#F3F4F6",
};

function StatCard({ label, value, sub, accent }) {
  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid #E5E7EB",
        borderRadius: 12,
        padding: "20px 24px",
      }}
    >
      <p
        style={{
          margin: "0 0 4px",
          fontSize: 12,
          fontWeight: 500,
          color: "#6B7280",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </p>
      <p
        style={{
          margin: "0 0 4px",
          fontSize: 28,
          fontWeight: 600,
          color: accent || "#111827",
          letterSpacing: "-0.02em",
        }}
      >
        {value ?? "—"}
      </p>
      {sub && (
        <p style={{ margin: 0, fontSize: 12, color: "#6B7280" }}>{sub}</p>
      )}
    </div>
  );
}

function StatusPill({ status }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        background: STATUS_BG[status] || "#F3F4F6",
        color: STATUS_COLORS[status] || "#6B7280",
        border: `1px solid ${STATUS_COLORS[status] || "#E5E7EB"}22`,
        borderRadius: 999,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: STATUS_COLORS[status] || "#6B7280",
          display: "inline-block",
        }}
      />
      {status}
    </span>
  );
}

function formatDuration(ms) {
  if (ms == null || ms === 0) return "0s";
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (remainingMinutes > 0) return `${hours}h ${remainingMinutes}m`;
  return `${hours}h`;
}

function PassRate({ passed, total }) {
  const pct = total > 0 ? Math.round((passed / total) * 100) : 0;
  const r = 16,
    stroke = 3,
    norm = r - stroke / 2;
  const circ = 2 * Math.PI * norm;
  const dash = (pct / 100) * circ;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <svg width="36" height="36" viewBox="0 0 36 36">
        <circle
          cx="18"
          cy="18"
          r={norm}
          fill="none"
          stroke="#F3F4F6"
          strokeWidth={stroke}
        />
        <circle
          cx="18"
          cy="18"
          r={norm}
          fill="none"
          stroke={pct >= 80 ? "#16A34A" : pct >= 50 ? "#D97706" : "#DC2626"}
          strokeWidth={stroke}
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 18 18)"
        />
        <text
          x="18"
          y="22"
          textAnchor="middle"
          fontSize="9"
          fontWeight="600"
          fill="#111827"
        >
          {pct}%
        </text>
      </svg>
    </span>
  );
}

export default function DashboardPage() {
  const [selectedProject, setSelectedProject] = useState("");

  const { data: projectsData } = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiGet("/api/projects"),
  });
  const projects = projectsData?.data || [];

  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard-stats", selectedProject],
    queryFn: () =>
      apiGet(
        `/api/dashboard/stats${selectedProject ? `?project_id=${selectedProject}` : ""}`,
      ),
    // 30s — was 10s. Dashboard stats don't need near-real-time refresh, and
    // this cuts background DB load (and Neon wake-ups) by two-thirds for
    // every open dashboard tab.
    refetchInterval: 30000,
  });

  const selectedProjectName = selectedProject
    ? projects.find((p) => p.id === selectedProject)?.name
    : null;

  return (
    <AppShell noPadding>
      <PageContainer>
        {/* Header */}
        <div
          style={{
            marginBottom: 28,
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 22,
                fontWeight: 600,
                color: "#111827",
                letterSpacing: "-0.02em",
              }}
            >
              Dashboard
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              QA execution overview —{" "}
              {selectedProjectName || "all products"}
            </p>
          </div>
          <Select
            value={selectedProject || "all"}
            onValueChange={(v) => setSelectedProject(v === "all" ? "" : v)}
            items={[
              { value: "all", label: "All Projects" },
              ...projects.map((p) => ({ value: p.id, label: p.name })),
            ]}
          >
            <SelectTrigger className="h-[33px] text-[13px]">
              <SelectValue placeholder="All Projects" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Projects</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {error && (
          <div
            style={{
              background: "#FEF2F2",
              border: "1px solid #FECACA",
              borderRadius: 8,
              padding: "12px 16px",
              marginBottom: 24,
            }}
          >
            <p style={{ margin: 0, fontSize: 13, color: "#DC2626" }}>
              Failed to load stats: {error.message}
            </p>
          </div>
        )}

        {/* Stat cards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: 16,
            marginBottom: 28,
          }}
        >
          <StatCard
            label={data?.active_projects?.label || "Active Projects"}
            value={data?.active_projects?.value}
            sub={data?.active_projects?.change_pct != null ? `${data.active_projects.change_pct > 0 ? "+" : ""}${data.active_projects.change_pct}% vs prior` : ""}
          />
          <StatCard
            label={data?.total_runs_today?.label || "Runs Today"}
            value={data?.total_runs_today?.value}
            sub={data?.total_runs_today?.change_pct != null ? `${data.total_runs_today.change_pct > 0 ? "+" : ""}${data.total_runs_today.change_pct}% vs yesterday` : ""}
            accent="#2563EB"
          />
          <StatCard
            label={data?.pass_rate_7d?.label || "Pass Rate (7d)"}
            value={data?.pass_rate_7d?.value != null ? `${data.pass_rate_7d.value}%` : undefined}
            sub={data?.pass_rate_7d?.change_pct != null ? `${data.pass_rate_7d.change_pct > 0 ? "+" : ""}${data.pass_rate_7d.change_pct}% vs prior` : ""}
            accent="#16A34A"
          />
          <StatCard
            label={data?.open_defects?.label || "Open Defects"}
            value={data?.open_defects?.value}
            sub={`${data?.critical_defects?.value ?? 0} critical`}
            accent={data?.critical_defects?.value > 0 ? "#DC2626" : "#111827"}
          />
          <StatCard
            label={data?.avg_execution_duration?.label || "Avg Duration"}
            value={data?.avg_execution_duration?.value != null ? formatDuration(data.avg_execution_duration.value) : undefined}
            sub={data?.avg_execution_duration?.change_pct != null ? `${data.avg_execution_duration.change_pct > 0 ? "+" : ""}${data.avg_execution_duration.change_pct}% vs prior` : ""}
          />
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 340px",
            gap: 20,
            alignItems: "start",
          }}
        >
          {/* Recent Runs */}
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
            }}
          >
            <div
              style={{
                padding: "18px 24px",
                borderBottom: "1px solid #E5E7EB",
              }}
            >
              <h2
                style={{
                  margin: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Recent Test Runs
              </h2>
            </div>
            {isLoading ? (
              <div
                style={{
                  padding: 32,
                  textAlign: "center",
                  color: "#9CA3AF",
                  fontSize: 13,
                }}
              >
                Loading…
              </div>
            ) : !data?.recent_runs?.length ? (
              <div
                style={{
                  padding: 32,
                  textAlign: "center",
                  color: "#9CA3AF",
                  fontSize: 13,
                }}
              >
                No runs yet
              </div>
            ) : (
              <div>
                {data.recent_runs.map((run, i) => (
                  <div
                    key={run.id}
                    style={{
                      padding: "14px 24px",
                      borderBottom:
                        i < data.recent_runs.length - 1
                          ? "1px solid #F3F4F6"
                          : "none",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                  >
                    <div style={{ flex: 1, overflow: "hidden" }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginBottom: 3,
                        }}
                      >
                        <p
                          style={{
                            margin: 0,
                            fontSize: 13,
                            fontWeight: 600,
                            color: "#111827",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {run.suite_name || "Unnamed Run"}
                        </p>
                        <StatusPill status={run.status} />
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <span style={{ fontSize: 11, color: "#6B7280" }}>
                          <span style={{ color: "#374151" }}>
                            {run.project_name}
                          </span>
                        </span>
                      </div>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        marginLeft: 16,
                        flexShrink: 0,
                      }}
                    >
                      {run.total > 0 && (
                        <PassRate
                          passed={run.passed}
                          total={run.total}
                        />
                      )}
                      <div style={{ textAlign: "right" }}>
                        <p
                          style={{ margin: 0, fontSize: 11, color: "#9CA3AF" }}
                        >
                          {new Date(run.created_at).toLocaleDateString(
                            "en-GB",
                            { day: "numeric", month: "short" },
                          )}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Defects by project */}
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
            }}
          >
            <div
              style={{
                padding: "18px 24px",
                borderBottom: "1px solid #E5E7EB",
              }}
            >
              <h2
                style={{
                  margin: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Top Open Defects
              </h2>
            </div>
            {isLoading ? (
              <div
                style={{
                  padding: 32,
                  textAlign: "center",
                  color: "#9CA3AF",
                  fontSize: 13,
                }}
              >
                Loading…
              </div>
            ) : (
              <div style={{ padding: "8px 0" }}>
                {!(data?.top_defects || []).length ? (
                  <div style={{ padding: "20px 24px", textAlign: "center", color: "#9CA3AF", fontSize: 13 }}>
                    No open defects
                  </div>
                ) : (data?.top_defects || []).map((defect) => (
                  <div
                    key={defect.id}
                    style={{
                      padding: "10px 24px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                  >
                    <div style={{ flex: 1, overflow: "hidden" }}>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 13,
                          fontWeight: 500,
                          color: "#111827",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {defect.title}
                      </p>
                      {defect.assigned_to_name && (
                        <span style={{ fontSize: 11, color: "#6B7280" }}>
                          {defect.assigned_to_name}
                        </span>
                      )}
                    </div>
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: defect.severity === "critical" ? "#DC2626" : defect.severity === "high" ? "#EA580C" : "#D97706",
                        background: defect.severity === "critical" ? "#FEE2E2" : defect.severity === "high" ? "#FEF3C7" : "#FFFBEB",
                        border: "1px solid #E5E7EB",
                        borderRadius: 999,
                        padding: "2px 10px",
                        textTransform: "uppercase",
                        flexShrink: 0,
                        marginLeft: 8,
                      }}
                    >
                      {defect.severity}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </PageContainer>
    </AppShell>
  );
}
