"use client";
import { useState, use } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import { apiGet, apiPost } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";

const SUITE_TYPE_COLORS = {
  smoke: { bg: "#DBEAFE", color: "#2563EB", border: "#BFDBFE" },
  regression: { bg: "#FEE2E2", color: "#DC2626", border: "#FECACA" },
  sanity: { bg: "#FEF3C7", color: "#D97706", border: "#FDE68A" },
  full: { bg: "#F3E8FF", color: "#9333EA", border: "#E9D5FF" },
  exploratory: { bg: "#DCFCE7", color: "#16A34A", border: "#BBF7D0" },
};

function SuiteTypeBadge({ type }) {
  const style = SUITE_TYPE_COLORS[type] || {
    bg: "#F3F4F6",
    color: "#6B7280",
    border: "#E5E7EB",
  };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        background: style.bg,
        color: style.color,
        border: `1px solid ${style.border}`,
        borderRadius: 999,
        padding: "2px 10px",
        fontSize: 11,
        fontWeight: 600,
        textTransform: "capitalize",
      }}
    >
      {type}
    </span>
  );
}

export default function ProjectDetailPage({ params }) {
  const resolvedParams = use(params);
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite = user && ["admin", "qa_lead"].includes(user.role);

  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({ name: "", suite_type: "smoke", description: "" });
  const [formError, setFormError] = useState("");

  const projectId = resolvedParams?.id;

  const { data: project, isLoading, error } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiGet(`/api/projects/${projectId}`),
    enabled: !!projectId,
  });

  const createSuiteMutation = useMutation({
    mutationFn: (body) => apiPost(`/api/projects/${projectId}/suites`, body),
    onSuccess: () => {
      qc.invalidateQueries(["project", projectId]);
      setShowModal(false);
      setForm({ name: "", suite_type: "smoke", description: "" });
      setFormError("");
    },
    onError: (e) => setFormError(e.message),
  });

  const suites = project?.suites || [];

  if (isLoading) {
    return (
      <AppShell>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "40vh",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 24,
                height: 24,
                border: "2px solid #2563EB",
                borderTopColor: "transparent",
                borderRadius: "50%",
                animation: "spin 0.8s linear infinite",
              }}
            />
            <span style={{ color: "#6B7280", fontSize: 14 }}>
              Loading project…
            </span>
          </div>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "40vh",
          }}
        >
          <div style={{ textAlign: "center", maxWidth: 400 }}>
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: 12,
                background: "#FEE2E2",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                margin: "0 auto 16px",
              }}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#DC2626" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <h2 style={{ margin: "0 0 8px", fontSize: 18, fontWeight: 600, color: "#111827" }}>
              Failed to load project
            </h2>
            <p style={{ margin: "0 0 20px", fontSize: 13, color: "#6B7280" }}>
              {error.message || "Could not load project details."}
            </p>
            <a
              href="/projects"
              style={{
                display: "inline-block",
                padding: "9px 20px",
                background: "#2563EB",
                color: "#fff",
                border: "none",
                borderRadius: 8,
                fontSize: 13,
                fontWeight: 600,
                textDecoration: "none",
              }}
            >
              Back to Projects
            </a>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div style={{ maxWidth: 1100 }}>
        {/* Breadcrumb */}
        <div style={{ marginBottom: 16 }}>
          <a
            href="/projects"
            style={{
              fontSize: 13,
              color: "#6B7280",
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Projects
          </a>
        </div>

        {/* Project Header */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            padding: "24px 28px",
            marginBottom: 24,
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
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
                {project?.name}
              </h1>
              {project?.description && (
                <p style={{ margin: "6px 0 0", fontSize: 14, color: "#6B7280", maxWidth: 600 }}>
                  {project.description}
                </p>
              )}
              <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
                <div>
                  <span style={{ fontSize: 11, fontWeight: 500, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    Suites
                  </span>
                  <p style={{ margin: "2px 0 0", fontSize: 18, fontWeight: 600, color: "#111827" }}>
                    {project?.suite_count ?? suites.length}
                  </p>
                </div>
                <div>
                  <span style={{ fontSize: 11, fontWeight: 500, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    Status
                  </span>
                  <p style={{ margin: "2px 0 0" }}>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 5,
                        background: project?.is_active ? "#DCFCE7" : "#F3F4F6",
                        color: project?.is_active ? "#16A34A" : "#6B7280",
                        border: `1px solid ${project?.is_active ? "#BBF7D0" : "#E5E7EB"}`,
                        borderRadius: 999,
                        padding: "2px 10px",
                        fontSize: 12,
                        fontWeight: 600,
                      }}
                    >
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: project?.is_active ? "#16A34A" : "#6B7280",
                          display: "inline-block",
                        }}
                      />
                      {project?.is_active ? "Active" : "Inactive"}
                    </span>
                  </p>
                </div>
                {project?.created_at && (
                  <div>
                    <span style={{ fontSize: 11, fontWeight: 500, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      Created
                    </span>
                    <p style={{ margin: "2px 0 0", fontSize: 13, color: "#374151" }}>
                      {new Date(project.created_at).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Environments Section */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            padding: "18px 24px",
            marginBottom: 24,
          }}
        >
          <h2 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600, color: "#111827" }}>
            Environments
          </h2>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {(project?.environments || ["dev", "staging", "production"]).map((env) => (
              <span
                key={env}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 14px",
                  fontSize: 13,
                  fontWeight: 500,
                  background: env === "production" ? "#FEF2F2" : env === "staging" ? "#FEF3C7" : "#DBEAFE",
                  color: env === "production" ? "#DC2626" : env === "staging" ? "#D97706" : "#2563EB",
                  border: `1px solid ${env === "production" ? "#FECACA" : env === "staging" ? "#FDE68A" : "#BFDBFE"}`,
                  borderRadius: 8,
                  textTransform: "capitalize",
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "currentColor",
                    display: "inline-block",
                  }}
                />
                {env}
              </span>
            ))}
          </div>
        </div>

        {/* Suites Section */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 16,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: "#111827" }}>
            Test Suites
          </h2>
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
              + Add Suite
            </button>
          )}
        </div>

        {/* Suites Table */}
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
              gridTemplateColumns: "2fr 1fr 1fr 120px",
              padding: "10px 20px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {["Name", "Type", "Description", "Created"].map((h) => (
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

          {suites.length === 0 ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No test suites yet.
              {canWrite && " Add your first suite above."}
            </div>
          ) : (
            suites.map((suite, i) => (
              <div
                key={suite.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "2fr 1fr 1fr 120px",
                  padding: "14px 20px",
                  borderBottom:
                    i < suites.length - 1 ? "1px solid #F3F4F6" : "none",
                  alignItems: "center",
                  transition: "background 0.1s",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <p
                  style={{
                    margin: 0,
                    fontSize: 13,
                    fontWeight: 600,
                    color: "#111827",
                  }}
                >
                  {suite.name}
                </p>
                <SuiteTypeBadge type={suite.suite_type} />
                <p
                  style={{
                    margin: 0,
                    fontSize: 13,
                    color: "#6B7280",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {suite.description || "—"}
                </p>
                <span style={{ fontSize: 12, color: "#9CA3AF" }}>
                  {new Date(suite.created_at).toLocaleDateString("en-GB", {
                    day: "numeric",
                    month: "short",
                  })}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Add Suite Modal */}
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
                Add Test Suite
              </h2>

              {/* Name */}
              <label style={{ display: "block", fontSize: 13, fontWeight: 500, color: "#374151", marginBottom: 6 }}>
                Name *
              </label>
              <input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Login Smoke Tests"
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  fontSize: 13,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  marginBottom: 14,
                  boxSizing: "border-box",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
              />

              {/* Suite Type */}
              <label style={{ display: "block", fontSize: 13, fontWeight: 500, color: "#374151", marginBottom: 6 }}>
                Suite Type *
              </label>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
                {["smoke", "regression", "sanity", "exploratory", "full"].map((t) => {
                  const isSelected = form.suite_type === t;
                  const colors = SUITE_TYPE_COLORS[t];
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setForm((f) => ({ ...f, suite_type: t }))}
                      style={{
                        padding: "6px 14px",
                        fontSize: 12,
                        fontWeight: 600,
                        textTransform: "capitalize",
                        border: `1.5px solid ${isSelected ? colors.color : colors.border}`,
                        borderRadius: 999,
                        background: isSelected ? colors.bg : "#fff",
                        color: isSelected ? colors.color : "#6B7280",
                        cursor: "pointer",
                        transition: "all 0.15s",
                      }}
                    >
                      {t}
                    </button>
                  );
                })}
              </div>

              {/* Description */}
              <label style={{ display: "block", fontSize: 13, fontWeight: 500, color: "#374151", marginBottom: 6 }}>
                Description
              </label>
              <textarea
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                rows={3}
                placeholder="Optional description…"
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  fontSize: 13,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  resize: "vertical",
                  marginBottom: 14,
                  boxSizing: "border-box",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
              />

              {formError && (
                <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                  {formError}
                </p>
              )}

              <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
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
                  onClick={() => createSuiteMutation.mutate(form)}
                  disabled={createSuiteMutation.isPending}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 600,
                    background: "#2563EB",
                    color: "#fff",
                    border: "none",
                    borderRadius: 8,
                    cursor: "pointer",
                    opacity: createSuiteMutation.isPending ? 0.7 : 1,
                  }}
                >
                  {createSuiteMutation.isPending ? "Creating…" : "Add Suite"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
