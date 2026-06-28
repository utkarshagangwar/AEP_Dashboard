"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import AppShell from "../../../components/AppShell";
import { apiGet, apiPost, apiFetch } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";

const STATUS_COLORS = {
  passed: "#16A34A",
  failed: "#DC2626",
  error: "#DC2626",
  skipped: "#D97706",
  running: "#2563EB",
};
const STATUS_BG = {
  passed: "#DCFCE7",
  failed: "#FEE2E2",
  error: "#FEE2E2",
  skipped: "#FEF3C7",
  running: "#DBEAFE",
};
const SEVERITY_OPTIONS = ["critical", "high", "medium", "low"];
const SEV_COLORS = {
  critical: "#DC2626",
  high: "#EA580C",
  medium: "#D97706",
  low: "#6B7280",
};
const SEV_BG = {
  critical: "#FEE2E2",
  high: "#FEF3C7",
  medium: "#FFFBEB",
  low: "#F3F4F6",
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

function parseTestName(fullName) {
  if (!fullName) return { name: fullName, description: null };
  const parts = fullName.split(" :: ");
  if (parts.length >= 2) {
    return { name: parts[0].trim(), description: parts.slice(1).join(" :: ").trim() };
  }
  return { name: fullName, description: null };
}

function ChevronIcon({ expanded }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      style={{
        transition: "transform 0.2s",
        transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
        flexShrink: 0,
      }}
    >
      <path
        d="M6 4L10 8L6 12"
        stroke="#9CA3AF"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function ReportDetailPage() {
  const params = useParams();
  const runId = params.run_id;
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canDefect =
    user && ["admin", "qa_lead", "qa_engineer"].includes(user.role);

  const [logDefectFor, setLogDefectFor] = useState(null);
  const [defectForm, setDefectForm] = useState({
    title: "",
    description: "",
    severity: "medium",
  });
  const [defectError, setDefectError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [expandedRows, setExpandedRows] = useState({});
  const [showVideoPanel, setShowVideoPanel] = useState(false);
  const [showAiPanel, setShowAiPanel] = useState(false);

  const { data: report, isLoading, error } = useQuery({
    queryKey: ["report-detail", runId],
    queryFn: () => apiGet(`/api/reports/${runId}`),
    enabled: !!runId,
  });

  const { data: videosData } = useQuery({
    queryKey: ["report-videos", runId],
    queryFn: () => apiGet(`/api/reports/${runId}/videos`),
    enabled: !!runId,
  });

  const { data: aiData } = useQuery({
    queryKey: ["report-ai-suggestions", runId],
    queryFn: () => apiGet(`/api/reports/${runId}/ai-suggestions`),
    enabled: !!runId,
  });

  const approveMutation = useMutation({
    mutationFn: (filename) =>
      apiPost(`/api/reports/${runId}/ai-suggestions/${filename}/approve`, {}),
    onSuccess: () => {
      qc.invalidateQueries(["report-ai-suggestions", runId]);
    },
  });

  const defectMutation = useMutation({
    mutationFn: (body) => apiPost("/api/defects", body),
    onSuccess: () => {
      qc.invalidateQueries(["report-detail", runId]);
      setLogDefectFor(null);
      setDefectForm({ title: "", description: "", severity: "medium" });
    },
    onError: (e) => setDefectError(e.message),
  });

  function toggleRow(id) {
    setExpandedRows((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  async function handleExport() {
    setExporting(true);
    try {
      const res = await apiFetch(`/api/reports/${runId}?action=export`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report_${runId}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("Export failed:", e);
    } finally {
      setExporting(false);
    }
  }

  if (isLoading) {
    return (
      <AppShell>
        <div style={{ maxWidth: 1100 }}>
          <div style={{ padding: 40, textAlign: "center", color: "#9CA3AF" }}>
            Loading report…
          </div>
        </div>
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell>
        <div style={{ maxWidth: 1100 }}>
          <div
            style={{
              background: "#FEF2F2",
              border: "1px solid #FECACA",
              borderRadius: 12,
              padding: "24px 32px",
              textAlign: "center",
            }}
          >
            <h2
              style={{
                margin: "0 0 8px",
                fontSize: 16,
                fontWeight: 600,
                color: "#DC2626",
              }}
            >
              Failed to load report
            </h2>
            <p style={{ margin: 0, fontSize: 13, color: "#7F1D1D" }}>
              {error.message}
            </p>
          </div>
        </div>
      </AppShell>
    );
  }

  if (!report) return null;

  const passRate =
    report.total > 0
      ? Math.round((report.passed / report.total) * 100)
      : 0;

  const videos = videosData?.videos || [];
  const aiSuggestions = aiData?.suggestions || [];

  function findVideoForTest(testName) {
    const { name } = parseTestName(testName);
    const normalized = name.replace(/\s+/g, "_");
    return videos.find(
      (v) => v.filename.replace(/\.\w+$/, "") === normalized
    );
  }

  return (
    <AppShell>
      <div style={{ maxWidth: 1100 }}>
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 24,
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <a
                href="/reports"
                style={{
                  fontSize: 13,
                  color: "#6B7280",
                  textDecoration: "none",
                }}
              >
                ← Reports
              </a>
            </div>
            <h1
              style={{
                margin: "4px 0 0",
                fontSize: 22,
                fontWeight: 600,
                color: "#111827",
                letterSpacing: "-0.02em",
              }}
            >
              Run Report
            </h1>
            <p
              style={{
                margin: "4px 0 0",
                fontSize: 12,
                color: "#9CA3AF",
                fontFamily: "monospace",
              }}
            >
              {String(report.id).slice(0, 8)}
            </p>
          </div>
          <button
            onClick={handleExport}
            disabled={exporting}
            style={{
              padding: "9px 16px",
              background: "#fff",
              color: "#374151",
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            {exporting ? "Exporting…" : "↓ Export JSON"}
          </button>
        </div>

        {/* Summary Cards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(5, 1fr)",
            gap: 14,
            marginBottom: 24,
          }}
        >
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "14px 18px",
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 10,
                fontWeight: 600,
                color: "#6B7280",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Status
            </p>
            <div style={{ marginTop: 8 }}>
              <StatusPill status={report.status} />
            </div>
          </div>
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "14px 18px",
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 10,
                fontWeight: 600,
                color: "#6B7280",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Total Tests
            </p>
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 22,
                fontWeight: 700,
                color: "#111827",
              }}
            >
              {report.total}
            </p>
          </div>
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "14px 18px",
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 10,
                fontWeight: 600,
                color: "#6B7280",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Passed
            </p>
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 22,
                fontWeight: 700,
                color: "#16A34A",
              }}
            >
              {report.passed}
            </p>
          </div>
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "14px 18px",
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 10,
                fontWeight: 600,
                color: "#6B7280",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Failed
            </p>
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 22,
                fontWeight: 700,
                color: report.failed > 0 ? "#DC2626" : "#6B7280",
              }}
            >
              {report.failed}
            </p>
          </div>
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "14px 18px",
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 10,
                fontWeight: 600,
                color: "#6B7280",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Pass Rate
            </p>
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 22,
                fontWeight: 700,
                color: passRate >= 80 ? "#16A34A" : passRate >= 50 ? "#D97706" : "#DC2626",
              }}
            >
              {passRate}%
            </p>
          </div>
        </div>

        {/* Run Metadata Bar */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            padding: "14px 20px",
            marginBottom: 24,
            display: "flex",
            gap: 32,
            fontSize: 12,
            color: "#6B7280",
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <span>
            <strong style={{ color: "#374151" }}>Suite:</strong>{" "}
            {report.suite_name || "—"}
          </span>
          <span>
            <strong style={{ color: "#374151" }}>Project:</strong>{" "}
            {report.project_name || "—"}
          </span>
          <span>
            <strong style={{ color: "#374151" }}>Triggered by:</strong>{" "}
            {report.triggered_by_name || "System"}
          </span>
          <span>
            <strong style={{ color: "#374151" }}>Duration:</strong>{" "}
            {formatDuration(report.duration_ms)}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            {aiSuggestions.length > 0 && (
              <button
                onClick={() => { setShowAiPanel(!showAiPanel); setShowVideoPanel(false); }}
                style={{
                  padding: "6px 14px",
                  fontSize: 12,
                  fontWeight: 500,
                  border: "1px solid #FBCFE8",
                  borderRadius: 8,
                  background: showAiPanel ? "#FDF2F8" : "#fff",
                  color: "#BE185D",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="#BE185D" strokeWidth="1.5" fill="none" />
                  <path d="M8 5v3l2 1.5" stroke="#BE185D" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                AI Suggestions ({aiSuggestions.length})
              </button>
            )}
            {videos.length > 0 && (
              <button
                onClick={() => { setShowVideoPanel(!showVideoPanel); setShowAiPanel(false); }}
                style={{
                  padding: "6px 14px",
                  fontSize: 12,
                  fontWeight: 500,
                  border: "1px solid #C7D2FE",
                  borderRadius: 8,
                  background: showVideoPanel ? "#EEF2FF" : "#fff",
                  color: "#4338CA",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <path d="M3 3h6v10H3z" fill="#C7D2FE" rx="1" />
                  <path d="M10 5.5l3.5 2.5-3.5 2.5z" fill="#4338CA" />
                </svg>
                Videos ({videos.length})
              </button>
            )}
          </div>
        </div>

        {/* Video Panel */}
        {showVideoPanel && videos.length > 0 && (
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "16px 20px",
              marginBottom: 24,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 12,
              }}
            >
              <h3
                style={{
                  margin: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Test Recordings
              </h3>
              <button
                onClick={() => setShowVideoPanel(false)}
                style={{
                  background: "none",
                  border: "none",
                  fontSize: 18,
                  color: "#9CA3AF",
                  cursor: "pointer",
                }}
              >
                ×
              </button>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                gap: 10,
              }}
            >
              {videos.map((v) => (
                <a
                  key={v.filename}
                  href={`/api/reports/${runId}/videos/${v.filename}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 14px",
                    background: "#F9FAFB",
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    textDecoration: "none",
                    color: "#374151",
                    fontSize: 12,
                    transition: "background 0.1s",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = "#EEF2FF")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = "#F9FAFB")
                  }
                >
                  <svg
                    width="20"
                    height="20"
                    viewBox="0 0 20 20"
                    fill="none"
                    style={{ flexShrink: 0 }}
                  >
                    <rect
                      x="2"
                      y="4"
                      width="11"
                      height="12"
                      rx="2"
                      fill="#C7D2FE"
                    />
                    <path d="M14 7l4-2v10l-4-2z" fill="#4338CA" />
                  </svg>
                  <div style={{ minWidth: 0 }}>
                    <p
                      style={{
                        margin: 0,
                        fontWeight: 500,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {v.test_name}
                    </p>
                    <p
                      style={{
                        margin: "2px 0 0",
                        fontSize: 10,
                        color: "#9CA3AF",
                      }}
                    >
                      {(v.size_bytes / 1024).toFixed(0)} KB
                    </p>
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* AI Suggestions Panel */}
        {showAiPanel && aiSuggestions.length > 0 && (
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "16px 20px",
              marginBottom: 24,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 14,
              }}
            >
              <h3
                style={{
                  margin: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Failed Locators & AI Suggestions
              </h3>
              <button
                onClick={() => setShowAiPanel(false)}
                style={{
                  background: "none",
                  border: "none",
                  fontSize: 18,
                  color: "#9CA3AF",
                  cursor: "pointer",
                }}
              >
                ×
              </button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {aiSuggestions.map((s) => (
                <div
                  key={s.filename}
                  style={{
                    border: "1px solid #E5E7EB",
                    borderRadius: 10,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      padding: "12px 16px",
                      background: "#F9FAFB",
                      borderBottom: "1px solid #F3F4F6",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 12,
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 13,
                          fontWeight: 600,
                          color: "#111827",
                        }}
                      >
                        {s.test_name}
                      </p>
                      <p
                        style={{
                          margin: "2px 0 0",
                          fontSize: 10,
                          color: "#9CA3AF",
                        }}
                      >
                        {s.model} · {s.timestamp ? new Date(s.timestamp).toLocaleString() : ""}
                      </p>
                    </div>
                    <span
                      style={{
                        padding: "3px 10px",
                        fontSize: 10,
                        fontWeight: 600,
                        borderRadius: 999,
                        textTransform: "uppercase",
                        letterSpacing: "0.04em",
                        flexShrink: 0,
                        background:
                          s.status === "approved"
                            ? "#DCFCE7"
                            : s.status === "completed"
                              ? "#DBEAFE"
                              : "#FEF3C7",
                        color:
                          s.status === "approved"
                            ? "#16A34A"
                            : s.status === "completed"
                              ? "#2563EB"
                              : "#D97706",
                      }}
                    >
                      {s.status}
                    </span>
                  </div>
                  <div style={{ padding: "12px 16px" }}>
                    <div style={{ marginBottom: 10 }}>
                      <p
                        style={{
                          margin: "0 0 4px",
                          fontSize: 10,
                          fontWeight: 600,
                          color: "#DC2626",
                          textTransform: "uppercase",
                          letterSpacing: "0.05em",
                        }}
                      >
                        Failed Locator
                      </p>
                      <code
                        style={{
                          display: "block",
                          padding: "8px 10px",
                          background: "#FEF2F2",
                          border: "1px solid #FECACA",
                          borderRadius: 6,
                          fontSize: 11,
                          fontFamily: "monospace",
                          color: "#991B1B",
                          wordBreak: "break-all",
                          whiteSpace: "pre-wrap",
                          lineHeight: 1.5,
                        }}
                      >
                        {s.failed_locator}
                      </code>
                    </div>
                    {s.failure_message && (
                      <div style={{ marginBottom: 10 }}>
                        <p
                          style={{
                            margin: "0 0 4px",
                            fontSize: 10,
                            fontWeight: 600,
                            color: "#6B7280",
                            textTransform: "uppercase",
                            letterSpacing: "0.05em",
                          }}
                        >
                          Failure Reason
                        </p>
                        <p
                          style={{
                            margin: 0,
                            padding: "8px 10px",
                            background: "#F9FAFB",
                            border: "1px solid #E5E7EB",
                            borderRadius: 6,
                            fontSize: 11,
                            fontFamily: "monospace",
                            color: "#374151",
                            wordBreak: "break-word",
                            whiteSpace: "pre-wrap",
                            lineHeight: 1.5,
                            maxHeight: 100,
                            overflowY: "auto",
                          }}
                        >
                          {s.failure_message}
                        </p>
                      </div>
                    )}
                    {s.analysis && (
                      <div style={{ marginBottom: 10 }}>
                        <p
                          style={{
                            margin: "0 0 4px",
                            fontSize: 10,
                            fontWeight: 600,
                            color: "#4338CA",
                            textTransform: "uppercase",
                            letterSpacing: "0.05em",
                          }}
                        >
                          AI Analysis
                        </p>
                        <p
                          style={{
                            margin: 0,
                            padding: "8px 10px",
                            background: "#EEF2FF",
                            border: "1px solid #C7D2FE",
                            borderRadius: 6,
                            fontSize: 12,
                            color: "#312E81",
                            lineHeight: 1.5,
                            whiteSpace: "pre-wrap",
                          }}
                        >
                          {s.analysis}
                        </p>
                      </div>
                    )}
                    {s.suggestions && s.suggestions.length > 0 && (
                      <div style={{ marginBottom: 10 }}>
                        <p
                          style={{
                            margin: "0 0 6px",
                            fontSize: 10,
                            fontWeight: 600,
                            color: "#16A34A",
                            textTransform: "uppercase",
                            letterSpacing: "0.05em",
                          }}
                        >
                          Suggested Locators
                        </p>
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 6,
                          }}
                        >
                          {s.suggestions.map((sug, idx) => (
                            <div
                              key={idx}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 10,
                                padding: "6px 10px",
                                background: "#F0FDF4",
                                border: "1px solid #BBF7D0",
                                borderRadius: 6,
                              }}
                            >
                              <code
                                style={{
                                  flex: 1,
                                  fontSize: 11,
                                  fontFamily: "monospace",
                                  color: "#166534",
                                  wordBreak: "break-all",
                                }}
                              >
                                {typeof sug === "string" ? sug : sug.locator || JSON.stringify(sug)}
                              </code>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "flex-end",
                        gap: 8,
                        marginTop: 8,
                      }}
                    >
                      {s.status !== "approved" && (
                        <button
                          onClick={() => approveMutation.mutate(s.filename)}
                          disabled={approveMutation.isPending}
                          style={{
                            padding: "6px 16px",
                            fontSize: 12,
                            fontWeight: 600,
                            background: "#16A34A",
                            color: "#fff",
                            border: "none",
                            borderRadius: 6,
                            cursor: "pointer",
                            display: "flex",
                            alignItems: "center",
                            gap: 5,
                            opacity: approveMutation.isPending ? 0.6 : 1,
                          }}
                        >
                          <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                            <path
                              d="M3 8.5L6.5 12L13 4"
                              stroke="#fff"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                          Approve
                        </button>
                      )}
                      {s.status === "approved" && (
                        <span
                          style={{
                            padding: "6px 16px",
                            fontSize: 12,
                            fontWeight: 600,
                            color: "#16A34A",
                            display: "flex",
                            alignItems: "center",
                            gap: 5,
                          }}
                        >
                          <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                            <path
                              d="M3 8.5L6.5 12L13 4"
                              stroke="#16A34A"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                          Approved
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Test Results Table */}
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
              gridTemplateColumns: "28px 1.2fr 2.2fr 0.7fr 0.7fr 90px",
              gap: 8,
              padding: "10px 20px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            <span />
            {["Sub-feature", "Test Case", "Status", "Duration", "Actions"].map(
              (h) => (
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
              ),
            )}
          </div>
          {report.results && report.results.length > 0 ? (
            report.results.map((result, i) => {
              const { name: testCaseName, description: testDesc } = parseTestName(result.test_name);
              const isFailed = result.status === "failed" || result.status === "error";
              const isExpanded = !!expandedRows[result.id];
              const video = findVideoForTest(result.test_name);
              return (
                <div
                  key={result.id}
                  style={{
                    borderBottom:
                      i < report.results.length - 1
                        ? "1px solid #F3F4F6"
                        : "none",
                    background: isExpanded ? "#FAFBFC" : "transparent",
                    transition: "background 0.15s",
                  }}
                >
                  <div
                    onClick={() => toggleRow(result.id)}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "28px 1.2fr 2.2fr 0.7fr 0.7fr 90px",
                      gap: 8,
                      padding: "12px 20px",
                      alignItems: "center",
                      cursor: "pointer",
                      userSelect: "none",
                    }}
                    onMouseEnter={(e) => {
                      if (!isExpanded) e.currentTarget.style.background = "#F9FAFB";
                    }}
                    onMouseLeave={(e) => {
                      if (!isExpanded) e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <ChevronIcon expanded={isExpanded} />
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 500,
                        color: "#6B7280",
                      }}
                    >
                      {result.source_suite || report.suite_name || "—"}
                    </span>
                    <div>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 13,
                          fontWeight: 500,
                          color: "#111827",
                        }}
                      >
                        {testCaseName}
                      </p>
                      {testDesc && !isExpanded && (
                        <p
                          style={{
                            margin: "2px 0 0",
                            fontSize: 11,
                            color: "#9CA3AF",
                            lineHeight: 1.3,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {testDesc}
                        </p>
                      )}
                    </div>
                    <StatusPill status={result.status} />
                    <span style={{ fontSize: 12, color: "#6B7280" }}>
                      {formatDuration(result.duration_ms)}
                    </span>
                    <div onClick={(e) => e.stopPropagation()}>
                      {canDefect && isFailed && (
                        <button
                          onClick={() => {
                            setLogDefectFor(result);
                            setDefectForm({
                              title: `${testCaseName} — failed`,
                              description: result.error_message || "",
                              severity: "medium",
                            });
                          }}
                          style={{
                            fontSize: 11,
                            padding: "4px 10px",
                            border: "1px solid #BFDBFE",
                            borderRadius: 6,
                            background: "#EFF6FF",
                            color: "#2563EB",
                            cursor: "pointer",
                            fontWeight: 500,
                          }}
                        >
                          + Log Defect
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Expanded Details */}
                  {isExpanded && (
                    <div
                      style={{
                        padding: "0 20px 16px 56px",
                        display: "flex",
                        flexDirection: "column",
                        gap: 10,
                      }}
                    >
                      {testDesc && (
                        <div
                          style={{
                            padding: "10px 14px",
                            background: "#F0F9FF",
                            border: "1px solid #BAE6FD",
                            borderRadius: 8,
                          }}
                        >
                          <p
                            style={{
                              margin: "0 0 4px",
                              fontSize: 10,
                              fontWeight: 600,
                              color: "#0369A1",
                              textTransform: "uppercase",
                              letterSpacing: "0.05em",
                            }}
                          >
                            Description
                          </p>
                          <p
                            style={{
                              margin: 0,
                              fontSize: 12,
                              color: "#0C4A6E",
                              lineHeight: 1.5,
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                            }}
                          >
                            {testDesc}
                          </p>
                        </div>
                      )}

                      {isFailed && result.error_message && (
                        <div
                          style={{
                            padding: "10px 14px",
                            background: "#FEF2F2",
                            border: "1px solid #FECACA",
                            borderRadius: 8,
                          }}
                        >
                          <p
                            style={{
                              margin: "0 0 4px",
                              fontSize: 10,
                              fontWeight: 600,
                              color: "#991B1B",
                              textTransform: "uppercase",
                              letterSpacing: "0.05em",
                            }}
                          >
                            Actual Behaviour
                          </p>
                          <p
                            style={{
                              margin: 0,
                              fontSize: 12,
                              color: "#7F1D1D",
                              fontFamily: "monospace",
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                              lineHeight: 1.5,
                            }}
                          >
                            {result.error_message}
                          </p>
                        </div>
                      )}

                      {!isFailed && !testDesc && (
                        <p style={{ margin: 0, fontSize: 12, color: "#9CA3AF" }}>
                          No additional details available for this test case.
                        </p>
                      )}

                      {video && (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <a
                            href={`/api/reports/${runId}/videos/${video.filename}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 6,
                              padding: "5px 12px",
                              fontSize: 11,
                              fontWeight: 500,
                              border: "1px solid #C7D2FE",
                              borderRadius: 6,
                              background: "#EEF2FF",
                              color: "#4338CA",
                              textDecoration: "none",
                            }}
                          >
                            <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                              <path d="M4 3l9 5-9 5z" fill="#4338CA" />
                            </svg>
                            Watch Recording
                          </a>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          ) : (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No test results for this run.
            </div>
          )}
        </div>
      </div>

      {/* Log Defect Slide-Over */}
      {logDefectFor && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            justifyContent: "flex-end",
            zIndex: 50,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setLogDefectFor(null);
          }}
        >
          <div
            style={{
              width: 460,
              height: "100%",
              background: "#fff",
              boxShadow: "-4px 0 20px rgba(0,0,0,0.12)",
              padding: "24px 28px",
              overflowY: "auto",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 20,
              }}
            >
              <h2
                style={{
                  margin: 0,
                  fontSize: 16,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Log Defect
              </h2>
              <button
                onClick={() => setLogDefectFor(null)}
                style={{
                  background: "none",
                  border: "none",
                  fontSize: 20,
                  color: "#9CA3AF",
                  cursor: "pointer",
                }}
              >
                ×
              </button>
            </div>

            <div
              style={{
                background: "#F9FAFB",
                border: "1px solid #E5E7EB",
                borderRadius: 8,
                padding: "10px 14px",
                marginBottom: 18,
              }}
            >
              <p style={{ margin: 0, fontSize: 12, color: "#6B7280" }}>
                Linking to test:
              </p>
              <p
                style={{
                  margin: "4px 0 0",
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                {logDefectFor.test_name}
              </p>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  marginBottom: 6,
                }}
              >
                Title *
              </label>
              <input
                value={defectForm.title}
                onChange={(e) =>
                  setDefectForm((f) => ({ ...f, title: e.target.value }))
                }
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  fontSize: 13,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  boxSizing: "border-box",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
              />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  marginBottom: 6,
                }}
              >
                Description
              </label>
              <textarea
                value={defectForm.description}
                onChange={(e) =>
                  setDefectForm((f) => ({
                    ...f,
                    description: e.target.value,
                  }))
                }
                rows={4}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  fontSize: 13,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  resize: "vertical",
                  boxSizing: "border-box",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
              />
            </div>
            <div style={{ marginBottom: 20 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  marginBottom: 6,
                }}
              >
                Severity
              </label>
              <div style={{ display: "flex", gap: 8 }}>
                {SEVERITY_OPTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() =>
                      setDefectForm((f) => ({ ...f, severity: s }))
                    }
                    style={{
                      padding: "6px 14px",
                      fontSize: 12,
                      fontWeight: defectForm.severity === s ? 600 : 400,
                      border: `1px solid ${defectForm.severity === s ? SEV_COLORS[s] : "#E5E7EB"}`,
                      borderRadius: 8,
                      background:
                        defectForm.severity === s ? SEV_BG[s] : "#fff",
                      color:
                        defectForm.severity === s
                          ? SEV_COLORS[s]
                          : "#374151",
                      cursor: "pointer",
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
            {defectError && (
              <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                {defectError}
              </p>
            )}
            <div
              style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
            >
              <button
                onClick={() => setLogDefectFor(null)}
                style={{
                  padding: "8px 16px",
                  fontSize: 13,
                  fontWeight: 500,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  background: "#fff",
                  cursor: "pointer",
                  color: "#374151",
                }}
              >
                Cancel
              </button>
              <button
                onClick={() =>
                  defectMutation.mutate({
                    test_result_id: logDefectFor.id,
                    title: defectForm.title,
                    description: defectForm.description,
                    severity: defectForm.severity,
                  })
                }
                disabled={defectMutation.isPending || !defectForm.title.trim()}
                style={{
                  padding: "8px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  background: "#2563EB",
                  color: "#fff",
                  border: "none",
                  borderRadius: 8,
                  cursor: "pointer",
                  opacity:
                    defectMutation.isPending || !defectForm.title.trim()
                      ? 0.6
                      : 1,
                }}
              >
                {defectMutation.isPending ? "Creating…" : "Create Defect"}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
