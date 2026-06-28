"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import { apiGet } from "../../utils/apiClient";

const STATUS_COLORS = {
  passed: "#16A34A",
  failed: "#DC2626",
  running: "#2563EB",
  queued: "#D97706",
  pending: "#6B7280",
  cancelled: "#6B7280",
  error: "#DC2626",
};
const STATUS_BG = {
  passed: "#DCFCE7",
  failed: "#FEE2E2",
  running: "#DBEAFE",
  queued: "#FEF3C7",
  pending: "#F3F4F6",
  cancelled: "#F3F4F6",
  error: "#FEE2E2",
};

function StatusPill({ status }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        background: STATUS_BG[status] || "#F3F4F6",
        color: STATUS_COLORS[status] || "#6B7280",
        border: `1px solid ${STATUS_COLORS[status] || "#E5E7EB"}30`,
        borderRadius: 999,
        padding: "3px 10px",
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        width: "fit-content",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: STATUS_COLORS[status] || "#6B7280",
          flexShrink: 0,
        }}
      />
      {status}
    </span>
  );
}

function formatDuration(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms === 0) return "0s";
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function ReportsPage() {
  const [projectFilter, setProjectFilter] = useState("");
  const [suiteFilter, setSuiteFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);

  const params = new URLSearchParams();
  if (projectFilter) params.set("project_id", projectFilter);
  if (suiteFilter) params.set("suite_id", suiteFilter);
  if (statusFilter) params.set("status", statusFilter);
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  params.set("page", String(page));
  params.set("limit", "20");

  const { data, isLoading, error } = useQuery({
    queryKey: ["reports", projectFilter, suiteFilter, statusFilter, fromDate, toDate, page],
    queryFn: () => apiGet(`/api/reports?${params.toString()}`),
    refetchInterval: 10000,
  });

  const { data: summaryData } = useQuery({
    queryKey: ["reports-summary"],
    queryFn: () => apiGet("/api/reports/stats/summary"),
    refetchInterval: 10000,
  });

  const { data: projectsData } = useQuery({
    queryKey: ["projects-list"],
    queryFn: () => apiGet("/api/projects?limit=100"),
  });

  const { data: suitesData } = useQuery({
    queryKey: ["suites-list"],
    queryFn: () => apiGet("/api/test-suites?limit=100"),
  });

  const runs = data?.data || [];
  const total = data?.total || 0;
  const projects = Array.isArray(projectsData) ? projectsData : [];
  const suites = Array.isArray(suitesData) ? suitesData : [];
  const summary = summaryData || {};

  return (
    <AppShell>
      <div style={{ maxWidth: 1200 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 24,
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
              Reports
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Test execution reports and analytics
            </p>
          </div>
        </div>

        {/* Summary Cards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 16,
            marginBottom: 24,
          }}
        >
          {[
            { label: "Total Runs (30d)", value: summary.total_runs || 0 },
            { label: "Pass Rate", value: `${summary.pass_rate || 0}%` },
            {
              label: "Avg Duration",
              value: formatDuration(summary.avg_duration_ms),
            },
            {
              label: "Projects Active",
              value: (summary.runs_per_project || []).length,
            },
          ].map((card) => (
            <div
              key={card.label}
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: "16px 20px",
              }}
            >
              <p
                style={{
                  margin: 0,
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#6B7280",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                {card.label}
              </p>
              <p
                style={{
                  margin: "8px 0 0",
                  fontSize: 24,
                  fontWeight: 700,
                  color: "#111827",
                }}
              >
                {card.value}
              </p>
            </div>
          ))}
        </div>

        {/* Filter Bar */}
        <div
          style={{
            display: "flex",
            gap: 10,
            marginBottom: 20,
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", gap: 6 }}>
            {[
              ["", "All Status"],
              ["passed", "Passed"],
              ["failed", "Failed"],
              ["running", "Running"],
              ["queued", "Queued"],
              ["cancelled", "Cancelled"],
            ].map(([val, label]) => (
              <button
                key={val}
                onClick={() => {
                  setStatusFilter(val);
                  setPage(1);
                }}
                style={{
                  padding: "6px 11px",
                  fontSize: 12,
                  fontWeight: statusFilter === val ? 600 : 400,
                  border: "1px solid #E5E7EB",
                  borderRadius: 999,
                  background: statusFilter === val ? "#111827" : "#fff",
                  color: statusFilter === val ? "#fff" : "#6B7280",
                  cursor: "pointer",
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <select
            value={projectFilter}
            onChange={(e) => {
              setProjectFilter(e.target.value);
              setPage(1);
            }}
            style={{
              padding: "6px 12px",
              fontSize: 12,
              border: "1px solid #E5E7EB",
              borderRadius: 999,
              outline: "none",
              color: "#6B7280",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            <option value="">All Projects</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            value={suiteFilter}
            onChange={(e) => {
              setSuiteFilter(e.target.value);
              setPage(1);
            }}
            style={{
              padding: "6px 12px",
              fontSize: 12,
              border: "1px solid #E5E7EB",
              borderRadius: 999,
              outline: "none",
              color: "#6B7280",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            <option value="">All Suites</option>
            {suites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <input
            type="date"
            value={fromDate}
            onChange={(e) => {
              setFromDate(e.target.value);
              setPage(1);
            }}
            placeholder="From"
            style={{
              padding: "5px 10px",
              fontSize: 12,
              border: "1px solid #E5E7EB",
              borderRadius: 999,
              outline: "none",
              color: "#6B7280",
              background: "#fff",
            }}
          />
          <span style={{ fontSize: 12, color: "#9CA3AF" }}>to</span>
          <input
            type="date"
            value={toDate}
            onChange={(e) => {
              setToDate(e.target.value);
              setPage(1);
            }}
            placeholder="To"
            style={{
              padding: "5px 10px",
              fontSize: 12,
              border: "1px solid #E5E7EB",
              borderRadius: 999,
              outline: "none",
              color: "#6B7280",
              background: "#fff",
            }}
          />
        </div>

        {error && (
          <div
            style={{
              background: "#FEF2F2",
              border: "1px solid #FECACA",
              borderRadius: 8,
              padding: "12px 16px",
              marginBottom: 20,
            }}
          >
            <p style={{ margin: 0, fontSize: 13, color: "#DC2626" }}>
              {error.message}
            </p>
          </div>
        )}

        {/* Results Table */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "2fr 1.2fr minmax(80px, 0.8fr) 0.6fr 0.6fr 0.8fr 80px",
              gap: 16,
              padding: "10px 32px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {[
              "Run ID",
              "Project / Suite",
              "Status",
              "Passed",
              "Failed",
              "Duration",
              "Date",
            ].map((h) => (
              <span
                key={h}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#6B7280",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                {h}
              </span>
            ))}
          </div>
          {isLoading ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              Loading…
            </div>
          ) : !runs.length ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No reports found.
            </div>
          ) : (
            runs.map((run, i) => (
              <a
                key={run.id}
                href={`/reports/${run.id}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "2fr 1.2fr minmax(80px, 0.8fr) 0.6fr 0.6fr 0.8fr 80px",
              gap: 16,
                  padding: "13px 32px",
                  borderBottom:
                    i < runs.length - 1 ? "1px solid #F3F4F6" : "none",
                  alignItems: "center",
                  textDecoration: "none",
                  color: "inherit",
                  cursor: "pointer",
                  transition: "background 0.1s",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 12,
                      fontWeight: 600,
                      color: "#2563EB",
                      fontFamily: "monospace",
                    }}
                  >
                    {String(run.id).slice(0, 8)}…
                  </p>
                  <p
                    style={{
                      margin: "2px 0 0",
                      fontSize: 11,
                      color: "#9CA3AF",
                    }}
                  >
                    by {run.triggered_by_name || "System"}
                  </p>
                </div>
                <div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 12,
                      fontWeight: 500,
                      color: "#374151",
                    }}
                  >
                    {run.project_name || "—"}
                  </p>
                  <p
                    style={{
                      margin: "2px 0 0",
                      fontSize: 11,
                      color: "#9CA3AF",
                    }}
                  >
                    {run.suite_name || "—"}
                  </p>
                </div>
                <StatusPill status={run.status} />
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "#16A34A",
                  }}
                >
                  {run.passed}
                </span>
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: run.failed > 0 ? "#DC2626" : "#6B7280",
                  }}
                >
                  {run.failed}
                </span>
                <span style={{ fontSize: 12, color: "#6B7280" }}>
                  {formatDuration(run.duration_ms)}
                </span>
                <span style={{ fontSize: 11, color: "#9CA3AF" }}>
                  {new Date(run.created_at).toLocaleDateString("en-GB", {
                    day: "numeric",
                    month: "short",
                  })}
                </span>
              </a>
            ))
          )}
        </div>

        {/* Pagination */}
        {total > 20 && (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              gap: 8,
              marginTop: 20,
            }}
          >
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              style={{
                padding: "6px 14px",
                fontSize: 12,
                border: "1px solid #E5E7EB",
                borderRadius: 8,
                background: "#fff",
                color: page === 1 ? "#D1D5DB" : "#374151",
                cursor: page === 1 ? "default" : "pointer",
              }}
            >
              ← Previous
            </button>
            <span
              style={{
                padding: "6px 12px",
                fontSize: 12,
                color: "#6B7280",
              }}
            >
              Page {page} of {Math.ceil(total / 20)}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={page >= Math.ceil(total / 20)}
              style={{
                padding: "6px 14px",
                fontSize: 12,
                border: "1px solid #E5E7EB",
                borderRadius: 8,
                background: "#fff",
                color:
                  page >= Math.ceil(total / 20) ? "#D1D5DB" : "#374151",
                cursor:
                  page >= Math.ceil(total / 20) ? "default" : "pointer",
              }}
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
