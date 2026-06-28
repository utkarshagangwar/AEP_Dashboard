"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import { apiGet, apiPost, apiDelete } from "../../utils/apiClient";
import { getStoredUser } from "../../utils/authStore";

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

function StatusPill({ status }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        background: STATUS_BG[status] || "#F3F4F6",
        color: STATUS_COLORS[status] || "#6B7280",
        border: `1px solid #E5E7EB`,
        borderRadius: 999,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        width: "fit-content",
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: STATUS_COLORS[status] || "#6B7280",
          flexShrink: 0,
        }}
      />
      {status}
    </span>
  );
}

function ProgressBar({ passed, failed, skipped, total }) {
  if (!total)
    return <span style={{ fontSize: 12, color: "#9CA3AF" }}>No results</span>;
  const passedPct = (passed / total) * 100;
  const failedPct = (failed / total) * 100;
  const skippedPct = (skipped / total) * 100;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div
        style={{
          display: "flex",
          height: 6,
          borderRadius: 999,
          overflow: "hidden",
          background: "#F3F4F6",
          width: 120,
        }}
      >
        <div style={{ width: `${passedPct}%`, background: "#16A34A" }} />
        <div style={{ width: `${failedPct}%`, background: "#DC2626" }} />
        <div style={{ width: `${skippedPct}%`, background: "#D97706" }} />
      </div>
      <span style={{ fontSize: 11, color: "#6B7280" }}>
        {passed}P / {failed}F / {skipped}S of {total}
      </span>
    </div>
  );
}

export default function TestRunsPage() {
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite =
    user && ["admin", "qa_lead", "qa_engineer"].includes(user.role);

  const [statusFilter, setStatusFilter] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({
    name: "",
    suite_id: "",
    environment: "dev",
  });
  const [formError, setFormError] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["test-runs", statusFilter],
    queryFn: () =>
      apiGet(
        `/api/test-runs?${statusFilter ? `status=${statusFilter}&` : ""}limit=50`,
      ),
  });

  const { data: suitesData } = useQuery({
    queryKey: ["suites-for-run"],
    queryFn: () => apiGet("/api/test-suites?limit=100"),
  });

  const createMutation = useMutation({
    mutationFn: (body) => apiPost("/api/test-runs", body),
    onSuccess: () => {
      qc.invalidateQueries(["test-runs"]);
      setShowModal(false);
      setForm({ name: "", suite_id: "", environment: "dev" });
    },
    onError: (e) => setFormError(typeof e.message === "string" ? e.message : JSON.stringify(e.message)),
  });

  const cancelMutation = useMutation({
    mutationFn: (id) => apiDelete(`/api/test-runs/${id}`),
    onSuccess: () => qc.invalidateQueries(["test-runs"]),
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => apiDelete(`/api/test-runs/${id}?action=delete`),
    onSuccess: () => qc.invalidateQueries(["test-runs"]),
  });

  const runs = data?.data || [];
  const suites = Array.isArray(suitesData) ? suitesData : [];

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
              Test Runs
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Execution history across all suites
            </p>
          </div>
          {canWrite && (
            <button
              onClick={() => {
                setShowModal(true);
                setFormError("");
              }}
              style={{
                padding: "9px 16px",
                background: "#2563EB",
                color: "#fff",
                border: "none",
                borderRadius: 8,
                fontSize: 13,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              ▶ Trigger Run
            </button>
          )}
        </div>

        <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
          {[
            ["", "All Statuses"],
            ["passed", "Passed"],
            ["failed", "Failed"],
            ["running", "Running"],
            ["queued", "Queued"],
            ["cancelled", "Cancelled"],
          ].map(([val, label]) => (
            <button
              key={val}
              onClick={() => setStatusFilter(val)}
              style={{
                padding: "6px 12px",
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
              gridTemplateColumns: "2.5fr 1.5fr minmax(80px, 0.8fr) 1.5fr 120px",
              gap: 16,
              padding: "10px 32px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {[
              "Run Name",
              "Suite / Project",
              "Status",
              "Progress",
              "Actions",
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
              No test runs found.
            </div>
          ) : (
            runs.map((run, i) => (
              <div
                key={run.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "2.5fr 1.5fr minmax(80px, 0.8fr) 1.5fr 120px",
              gap: 16,
                  padding: "14px 32px",
                  borderBottom:
                    i < runs.length - 1 ? "1px solid #F3F4F6" : "none",
                  alignItems: "center",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <div>
                  <a
                    href={`/reports/${run.id}`}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      margin: 0,
                      fontSize: 13,
                      fontWeight: 600,
                      color: "#2563EB",
                      textDecoration: "none",
                      display: "block",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
                    onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
                  >
                    {run.suite_name || `Run ${String(run.id).slice(0, 8)}`}
                  </a>
                  <p
                    style={{
                      margin: "2px 0 0",
                      fontSize: 11,
                      color: "#9CA3AF",
                    }}
                  >
                    by {run.triggered_by_name || "System"} ·{" "}
                    {new Date(run.created_at).toLocaleDateString("en-GB", {
                      day: "numeric",
                      month: "short",
                      year: "2-digit",
                    })}
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
                    {run.suite_name}
                  </p>
                  <p
                    style={{
                      margin: "2px 0 0",
                      fontSize: 11,
                      color: "#9CA3AF",
                    }}
                  >
                    {run.project_name}
                  </p>
                </div>
                <StatusPill status={run.status} />
                <ProgressBar
                  passed={run.passed || 0}
                  failed={run.failed || 0}
                  skipped={Math.max(0, (run.total || 0) - (run.passed || 0) - (run.failed || 0))}
                  total={run.total || 0}
                />
                <div style={{ display: "flex", gap: 6 }}>
                  {["running", "queued", "pending"].includes(run.status) &&
                    canWrite && (
                      <button
                        onClick={() => cancelMutation.mutate(run.id)}
                        style={{
                          fontSize: 11,
                          padding: "4px 8px",
                          border: "1px solid #FECACA",
                          borderRadius: 6,
                          background: "#FEF2F2",
                          color: "#DC2626",
                          cursor: "pointer",
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  {!["running", "queued", "pending"].includes(run.status) &&
                    canWrite && (
                      <button
                        onClick={() => {
                          if (window.confirm("Delete this test run permanently?")) {
                            deleteMutation.mutate(run.id);
                          }
                        }}
                        style={{
                          fontSize: 11,
                          padding: "4px 8px",
                          border: "1px solid #FECACA",
                          borderRadius: 6,
                          background: "#FEF2F2",
                          color: "#DC2626",
                          cursor: "pointer",
                        }}
                      >
                        Delete
                      </button>
                    )}
                </div>
              </div>
            ))
          )}
        </div>

        {showModal && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 50,
            }}
          >
            <div
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: 28,
                width: 440,
                boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
              }}
            >
              <h2
                style={{
                  margin: "0 0 20px",
                  fontSize: 16,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Trigger New Run
              </h2>
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
                  Run Name *
                </label>
                <input
                  value={form.name}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, name: e.target.value }))
                  }
                  placeholder="e.g. Regression Run #42"
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
                  Test Suite *
                </label>
                <select
                  value={form.suite_id}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, suite_id: e.target.value }))
                  }
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    fontSize: 13,
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    outline: "none",
                    boxSizing: "border-box",
                    background: "#fff",
                  }}
                >
                  <option value="">Select a suite…</option>
                  {suites.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.project_name})
                    </option>
                  ))}
                </select>
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
                  Environment
                </label>
                <div style={{ display: "flex", gap: 8 }}>
                  {["dev", "staging", "production"].map((env) => (
                    <button
                      key={env}
                      onClick={() =>
                        setForm((f) => ({ ...f, environment: env }))
                      }
                      style={{
                        flex: 1,
                        padding: "8px",
                        fontSize: 12,
                        fontWeight: form.environment === env ? 600 : 400,
                        border: `1px solid ${form.environment === env ? "#2563EB" : "#E5E7EB"}`,
                        borderRadius: 8,
                        background:
                          form.environment === env ? "#EFF6FF" : "#fff",
                        color: form.environment === env ? "#2563EB" : "#374151",
                        cursor: "pointer",
                      }}
                    >
                      {env}
                    </button>
                  ))}
                </div>
              </div>
              {formError && (
                <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                  {formError}
                </p>
              )}
              <div
                style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
              >
                <button
                  onClick={() => setShowModal(false)}
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
                  onClick={() => createMutation.mutate(form)}
                  disabled={createMutation.isPending}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 600,
                    background: "#2563EB",
                    color: "#fff",
                    border: "none",
                    borderRadius: 8,
                    cursor: "pointer",
                  }}
                >
                  {createMutation.isPending ? "Triggering…" : "▶ Trigger Run"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
