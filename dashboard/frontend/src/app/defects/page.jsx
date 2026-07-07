"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import PageContainer from "../../components/PageContainer";
import { apiGet, apiPost, apiPatch } from "../../utils/apiClient";
import { getStoredUser } from "../../utils/authStore";

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
const ST_COLORS = {
  open: "#DC2626",
  in_progress: "#2563EB",
  resolved: "#16A34A",
  closed: "#6B7280",
};
const ST_BG = {
  open: "#FEE2E2",
  in_progress: "#DBEAFE",
  resolved: "#DCFCE7",
  closed: "#F3F4F6",
};

export default function DefectsPage() {
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite =
    user &&
    ["admin", "qa_lead", "qa_engineer", "developer"].includes(user.role);

  const [sevFilter, setSevFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("open");
  const [projectFilter, setProjectFilter] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [editDefect, setEditDefect] = useState(null);
  const [form, setForm] = useState({
    title: "",
    description: "",
    severity: "medium",
    project_id: "",
  });
  const [formError, setFormError] = useState("");

  const params = new URLSearchParams();
  if (sevFilter) params.set("severity", sevFilter);
  if (statusFilter) params.set("status", statusFilter);
  if (projectFilter) params.set("project_id", projectFilter);
  params.set("limit", "50");

  const { data, isLoading, error } = useQuery({
    queryKey: ["defects", sevFilter, statusFilter, projectFilter],
    queryFn: () => apiGet(`/api/defects?${params.toString()}`),
  });

  const { data: projectsData } = useQuery({
    queryKey: ["projects-list"],
    queryFn: () => apiGet("/api/projects?limit=100"),
  });

  const { data: assignableData } = useQuery({
    queryKey: ["users-assignable"],
    queryFn: () => apiGet("/api/users/assignable"),
    enabled: !!canWrite,
  });

  const createMutation = useMutation({
    mutationFn: (body) => apiPost("/api/defects", body),
    onSuccess: () => {
      qc.invalidateQueries(["defects"]);
      setShowModal(false);
      setForm({
        title: "",
        description: "",
        severity: "medium",
        project_id: "",
      });
    },
    onError: (e) => setFormError(e.message),
  });

  const [editError, setEditError] = useState("");

  const updateMutation = useMutation({
    mutationFn: ({ id, ...body }) => apiPatch(`/api/defects/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries(["defects"]);
      setEditDefect(null);
      setEditError("");
    },
    onError: (e) =>
      setEditError(typeof e.message === "string" ? e.message : "Failed to update defect"),
  });

  const defects = data?.data || [];
  const projects = projectsData?.data || [];
  const assignableUsers = assignableData || [];

  return (
    <AppShell noPadding>
      <PageContainer>
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
              Defects
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Bug tracking across all products
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
              + Log Defect
            </button>
          )}
        </div>

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
              ["open", "Open"],
              ["in_progress", "In Progress"],
              ["resolved", "Resolved"],
              ["closed", "Closed"],
            ].map(([val, label]) => (
              <button
                key={val}
                onClick={() => setStatusFilter(val)}
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
            value={sevFilter}
            onChange={(e) => setSevFilter(e.target.value)}
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
            <option value="">All Severities</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
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
              gridTemplateColumns: "minmax(0, 3fr) minmax(80px, 1fr) minmax(80px, 0.8fr) minmax(80px, 0.8fr) minmax(80px, 1fr) minmax(80px, 1fr) 90px",
              gap: 16,
              padding: "10px 32px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {[
              "Title",
              "Project",
              "Severity",
              "Status",
              "Reported by",
              "Assigned to",
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
          ) : !defects.length ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No defects found.
            </div>
          ) : (
            defects.map((d, i) => (
              <div
                key={d.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 3fr) minmax(80px, 1fr) minmax(80px, 0.8fr) minmax(80px, 0.8fr) minmax(80px, 1fr) minmax(80px, 1fr) 90px",
              gap: 16,
                  padding: "13px 32px",
                  borderBottom:
                    i < defects.length - 1 ? "1px solid #F3F4F6" : "none",
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
                  <p
                    style={{
                      margin: 0,
                      fontSize: 13,
                      fontWeight: 600,
                      color: "#111827",
                    }}
                  >
                    {d.title}
                  </p>
                  {d.linked_test_name && (
                    <p
                      style={{
                        margin: "2px 0 0",
                        fontSize: 11,
                        color: "#9CA3AF",
                      }}
                    >
                      - {d.linked_test_name}
                    </p>
                  )}
                </div>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    fontSize: 12,
                    color: "#374151",
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: "#2563EB",
                    }}
                  />
                  {d.project_name}
                </span>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    background: SEV_BG[d.severity],
                    color: SEV_COLORS[d.severity],
                    border: "1px solid #E5E7EB",
                    borderRadius: 999,
                    padding: "2px 8px",
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    width: "fit-content",
                  }}
                >
                  <span
                    style={{
                      width: 4,
                      height: 4,
                      borderRadius: "50%",
                      background: SEV_COLORS[d.severity],
                      flexShrink: 0,
                    }}
                  />
                  {d.severity}
                </span>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    background: ST_BG[d.status],
                    color: ST_COLORS[d.status],
                    border: "1px solid #E5E7EB",
                    borderRadius: 999,
                    padding: "2px 8px",
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    width: "fit-content",
                  }}
                >
                  {d.status.replace("_", " ")}
                </span>
                <span style={{ fontSize: 12, color: "#6B7280" }}>
                  {d.reported_by_name || "—"}
                </span>
                <span style={{ fontSize: 12, color: d.assigned_to_name ? "#374151" : "#9CA3AF" }}>
                  {d.assigned_to_name || "Unassigned"}
                </span>
                <div style={{ display: "flex", gap: 6 }}>
                  {canWrite && d.status !== "closed" && (
                    <button
                      onClick={() => setEditDefect(d)}
                      style={{
                        fontSize: 11,
                        padding: "4px 8px",
                        border: "1px solid #E5E7EB",
                        borderRadius: 6,
                        background: "#fff",
                        color: "#374151",
                        cursor: "pointer",
                      }}
                    >
                      Edit
                    </button>
                  )}
                  {canWrite && d.status === "open" && (
                    <button
                      onClick={() =>
                        updateMutation.mutate({
                          id: d.id,
                          status: "in_progress",
                        })
                      }
                      style={{
                        fontSize: 11,
                        padding: "4px 8px",
                        border: "1px solid #BFDBFE",
                        borderRadius: 6,
                        background: "#EFF6FF",
                        color: "#2563EB",
                        cursor: "pointer",
                      }}
                    >
                      Start
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Create Modal */}
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
                width: 460,
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
                Log Defect
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
                  Title *
                </label>
                <input
                  value={form.title}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, title: e.target.value }))
                  }
                  placeholder="Describe the defect briefly"
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
                  value={form.description}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, description: e.target.value }))
                  }
                  rows={3}
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
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 12,
                  marginBottom: 20,
                }}
              >
                <div>
                  <label
                    style={{
                      display: "block",
                      fontSize: 13,
                      fontWeight: 500,
                      color: "#374151",
                      marginBottom: 6,
                    }}
                  >
                    Project *
                  </label>
                  <select
                    value={form.project_id}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, project_id: e.target.value }))
                    }
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      fontSize: 13,
                      border: "1px solid #E5E7EB",
                      borderRadius: 8,
                      outline: "none",
                      background: "#fff",
                    }}
                  >
                    <option value="">Select…</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
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
                  <select
                    value={form.severity}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, severity: e.target.value }))
                    }
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      fontSize: 13,
                      border: "1px solid #E5E7EB",
                      borderRadius: 8,
                      outline: "none",
                      background: "#fff",
                    }}
                  >
                    <option value="critical">Critical</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
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
                  onClick={() => {
                    if (!form.title.trim()) {
                      setFormError("Title is required");
                      return;
                    }
                    if (!form.project_id) {
                      setFormError("Project is required");
                      return;
                    }
                    setFormError("");
                    createMutation.mutate(form);
                  }}
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
                  {createMutation.isPending ? "Logging…" : "Log Defect"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Edit Modal */}
        {editDefect && (
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
                width: 420,
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
                Update Defect
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
                  Status
                </label>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {["open", "in_progress", "resolved", "closed"].map((s) => (
                    <button
                      key={s}
                      onClick={() =>
                        setEditDefect((d) => ({ ...d, status: s }))
                      }
                      style={{
                        padding: "6px 12px",
                        fontSize: 12,
                        fontWeight: editDefect.status === s ? 600 : 400,
                        border: `1px solid ${editDefect.status === s ? "#2563EB" : "#E5E7EB"}`,
                        borderRadius: 8,
                        background:
                          editDefect.status === s ? "#EFF6FF" : "#fff",
                        color: editDefect.status === s ? "#2563EB" : "#374151",
                        cursor: "pointer",
                      }}
                    >
                      {s.replace("_", " ")}
                    </button>
                  ))}
                </div>
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
                  {["critical", "high", "medium", "low"].map((s) => (
                    <button
                      key={s}
                      onClick={() =>
                        setEditDefect((d) => ({ ...d, severity: s }))
                      }
                      style={{
                        padding: "6px 12px",
                        fontSize: 12,
                        fontWeight: editDefect.severity === s ? 600 : 400,
                        border: `1px solid ${editDefect.severity === s ? SEV_COLORS[s] : "#E5E7EB"}`,
                        borderRadius: 8,
                        background:
                          editDefect.severity === s ? SEV_BG[s] : "#fff",
                        color:
                          editDefect.severity === s ? SEV_COLORS[s] : "#374151",
                        cursor: "pointer",
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
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
                  Assigned To
                </label>
                <select
                  value={editDefect.assigned_to || ""}
                  onChange={(e) =>
                    setEditDefect((d) => ({ ...d, assigned_to: e.target.value }))
                  }
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    fontSize: 13,
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    outline: "none",
                    background: "#fff",
                  }}
                >
                  <option value="">
                    {editDefect.assigned_to_name
                      ? `Currently: ${editDefect.assigned_to_name}`
                      : "Unassigned"}
                  </option>
                  {assignableUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.full_name} ({u.role.replace("_", " ")})
                    </option>
                  ))}
                </select>
              </div>
              {editError && (
                <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                  {editError}
                </p>
              )}
              <div
                style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
              >
                <button
                  onClick={() => {
                    setEditDefect(null);
                    setEditError("");
                  }}
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
                    updateMutation.mutate({
                      id: editDefect.id,
                      status: editDefect.status,
                      severity: editDefect.severity,
                      ...(editDefect.assigned_to
                        ? { assigned_to: editDefect.assigned_to }
                        : {}),
                    })
                  }
                  disabled={updateMutation.isPending}
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
                  {updateMutation.isPending ? "Saving…" : "Save Changes"}
                </button>
              </div>
            </div>
          </div>
        )}
      </PageContainer>
    </AppShell>
  );
}
