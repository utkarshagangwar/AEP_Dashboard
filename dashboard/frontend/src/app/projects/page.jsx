"use client";
import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import PageContainer from "../../components/PageContainer";
import { apiGet, apiPost, apiPatch } from "../../utils/apiClient";
import { getStoredUser } from "../../utils/authStore";

const STATUS_COLORS = {
  active: "#16A34A",
  inactive: "#D97706",
  archived: "#6B7280",
};
const STATUS_BG = {
  active: "#DCFCE7",
  inactive: "#FEF3C7",
  archived: "#F3F4F6",
};

export default function ProjectsPage() {
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite = user && ["admin", "qa_lead"].includes(user.role);

  const [search, setSearch] = useState("");
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoveryResult, setDiscoveryResult] = useState(null);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [editingProject, setEditingProject] = useState(null);
  const [editForm, setEditForm] = useState({ name: "", description: "", is_active: true });
  const [editError, setEditError] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["projects", search],
    queryFn: () =>
      apiGet(`/api/projects?search=${encodeURIComponent(search)}&limit=50`),
  });

  const updateProjectMutation = useMutation({
    mutationFn: ({ id, body }) => apiPatch(`/api/projects/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries(["projects"]);
      setEditingProject(null);
      setEditError("");
    },
    onError: (e) => setEditError(e.message),
  });

  function openEditModal(p) {
    setEditingProject(p);
    setEditForm({
      name: p.name,
      description: p.description || "",
      is_active: p.is_active,
    });
    setEditError("");
    setOpenMenuId(null);
  }

  function saveEdit() {
    if (!editingProject) return;
    updateProjectMutation.mutate({
      id: editingProject.id,
      body: {
        name: editForm.name,
        description: editForm.description,
        is_active: editForm.is_active,
      },
    });
  }

  /* ── Discover projects from automation folder ──────────────────────── */
  async function handleDiscoverProjects() {
    setIsDiscovering(true);
    setDiscoveryResult(null);
    try {
      const result = await apiPost("/api/projects/discover-suites");
      setDiscoveryResult(result);
      qc.invalidateQueries(["projects"]);
    } catch (err) {
      setDiscoveryResult({ errors: [err.message] });
    } finally {
      setIsDiscovering(false);
    }
  }

  const projects = data?.data || [];
  const discoveredProjectCount = new Set(
    (discoveryResult?.discovered || []).map((d) => d.project)
  ).size;

  return (
    <AppShell noPadding>
      <PageContainer>
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
            <h1
              style={{
                margin: 0,
                fontSize: 22,
                fontWeight: 600,
                color: "#111827",
                letterSpacing: "-0.02em",
              }}
            >
              Projects
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Products under test: Vikaas, Vidya, ATG Meeting Recorder, Axon,
              RevOps, LMS
            </p>
          </div>
          <button
            onClick={handleDiscoverProjects}
            disabled={isDiscovering}
            style={{
              padding: "7px 16px",
              fontSize: 12,
              fontWeight: 600,
              background: isDiscovering ? "#D1D5DB" : "#F3F4F6",
              color: "#374151",
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              cursor: isDiscovering ? "not-allowed" : "pointer",
              whiteSpace: "nowrap",
              transition: "background 0.15s",
            }}
          >
            {isDiscovering ? "Scanning…" : "Discover Project"}
          </button>
        </div>

        {/* Discovery result banner */}
        {discoveryResult && (
          <div
            style={{
              marginBottom: 16,
              padding: "10px 14px",
              background: discoveryResult.errors?.length ? "#FEF2F2" : "#F0FDF4",
              border: `1px solid ${discoveryResult.errors?.length ? "#FECACA" : "#BBF7D0"}`,
              borderRadius: 8,
              fontSize: 13,
              color: discoveryResult.errors?.length ? "#DC2626" : "#166534",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>
              {discoveryResult.errors?.length
                ? `Error: ${discoveryResult.errors[0]}`
                : `Found ${discoveredProjectCount} project(s) with ${discoveryResult.discovered?.length || 0} suite(s), registered ${discoveryResult.registered?.length || 0} new`}
            </span>
            <button
              onClick={() => setDiscoveryResult(null)}
              style={{ background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "inherit" }}
            >
              ×
            </button>
          </div>
        )}

        {/* Search */}
        <div style={{ marginBottom: 20 }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search projects…"
            style={{
              padding: "8px 12px",
              fontSize: 13,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              width: 280,
              color: "#111827",
            }}
            onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
            onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
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

        {/* Table */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            overflow: openMenuId ? "visible" : "hidden",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "2fr 3fr minmax(80px, 0.8fr) 80px 80px 40px",
              gap: 16,
              padding: "10px 32px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {["Name", "Description", "Status", "Suites", "Defects", ""].map((h) => (
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
          ) : !projects.length ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No projects found. Try &ldquo;Discover Project&rdquo; above.
            </div>
          ) : (
            projects.map((p, i) => (
              <div
                key={p.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "2fr 3fr minmax(80px, 0.8fr) 80px 80px 40px",
              gap: 16,
                  padding: "14px 32px",
                  borderBottom:
                    i < projects.length - 1 ? "1px solid #F3F4F6" : "none",
                  alignItems: "center",
                  transition: "background 0.1s",
                  cursor: "pointer",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
                onClick={() => {
                  window.location.href = `/projects/${p.id}`;
                }}
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
                    {p.name}
                  </p>
                  <p
                    style={{
                      margin: "2px 0 0",
                      fontSize: 11,
                      color: "#9CA3AF",
                    }}
                  >
                    {p.created_by_name || "System"}
                  </p>
                </div>
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
                  {p.description || "—"}
                </p>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    background: STATUS_BG[p.status] || STATUS_BG.active,
                    color: STATUS_COLORS[p.status] || STATUS_COLORS.active,
                    border: "1px solid #E5E7EB",
                    borderRadius: 999,
                    padding: "2px 8px",
                    fontSize: 11,
                    fontWeight: 600,
                    width: "fit-content",
                  }}
                >
                  <span
                    style={{
                      width: 5,
                      height: 5,
                      borderRadius: "50%",
                      background: STATUS_COLORS[p.status] || STATUS_COLORS.active,
                      display: "inline-block",
                    }}
                  />
                  {p.status || (p.is_active ? "active" : "inactive")}
                </span>
                <span
                  style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}
                >
                  {p.suite_count || 0}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: p.open_defects > 0 ? "#DC2626" : "#374151",
                  }}
                >
                  {p.open_defects || 0}
                </span>
                {canWrite ? (
                  <div
                    style={{ position: "relative" }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={() =>
                        setOpenMenuId(openMenuId === p.id ? null : p.id)
                      }
                      aria-label="Project actions"
                      style={{
                        width: 28,
                        height: 28,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: openMenuId === p.id ? "#F3F4F6" : "transparent",
                        border: "none",
                        borderRadius: 6,
                        cursor: "pointer",
                        color: "#6B7280",
                      }}
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                        <circle cx="12" cy="5" r="1.8" />
                        <circle cx="12" cy="12" r="1.8" />
                        <circle cx="12" cy="19" r="1.8" />
                      </svg>
                    </button>
                    {openMenuId === p.id && (
                      <div
                        style={{
                          position: "absolute",
                          right: 0,
                          top: "calc(100% + 4px)",
                          background: "#fff",
                          border: "1px solid #E5E7EB",
                          borderRadius: 8,
                          boxShadow: "0 4px 16px rgba(0,0,0,0.1)",
                          zIndex: 20,
                          minWidth: 130,
                          overflow: "hidden",
                        }}
                      >
                        <button
                          onClick={() => openEditModal(p)}
                          style={{
                            display: "block",
                            width: "100%",
                            textAlign: "left",
                            padding: "8px 14px",
                            fontSize: 13,
                            color: "#374151",
                            background: "none",
                            border: "none",
                            cursor: "pointer",
                          }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = "#F9FAFB")}
                          onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                        >
                          Edit
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  <span />
                )}
              </div>
            ))
          )}
        </div>

        {/* Click-outside overlay to close the row action menu */}
        {openMenuId && (
          <div
            style={{ position: "fixed", inset: 0, zIndex: 10 }}
            onClick={() => setOpenMenuId(null)}
          />
        )}

        {/* Edit Project Modal */}
        {editingProject && (
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
                Edit Project
              </h2>

              {/* Name */}
              <label style={{ display: "block", fontSize: 13, fontWeight: 500, color: "#374151", marginBottom: 6 }}>
                Name *
              </label>
              <input
                value={editForm.name}
                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
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

              {/* Description */}
              <label style={{ display: "block", fontSize: 13, fontWeight: 500, color: "#374151", marginBottom: 6 }}>
                Description
              </label>
              <textarea
                value={editForm.description}
                onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
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

              {/* Status */}
              <label style={{ display: "block", fontSize: 13, fontWeight: 500, color: "#374151", marginBottom: 6 }}>
                Status
              </label>
              <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                {["active", "inactive"].map((s) => {
                  const isSelected = (editForm.is_active ? "active" : "inactive") === s;
                  return (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setEditForm((f) => ({ ...f, is_active: s === "active" }))}
                      style={{
                        padding: "6px 14px",
                        fontSize: 12,
                        fontWeight: 600,
                        textTransform: "capitalize",
                        border: `1.5px solid ${isSelected ? STATUS_COLORS[s] : "#E5E7EB"}`,
                        borderRadius: 999,
                        background: isSelected ? STATUS_BG[s] : "#fff",
                        color: isSelected ? STATUS_COLORS[s] : "#6B7280",
                        cursor: "pointer",
                        transition: "all 0.15s",
                      }}
                    >
                      {s}
                    </button>
                  );
                })}
              </div>

              {editError && (
                <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                  {editError}
                </p>
              )}

              <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                <button
                  onClick={() => setEditingProject(null)}
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
                  onClick={saveEdit}
                  disabled={updateProjectMutation.isPending}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 600,
                    background: "#2563EB",
                    color: "#fff",
                    border: "none",
                    borderRadius: 8,
                    cursor: "pointer",
                    opacity: updateProjectMutation.isPending ? 0.7 : 1,
                  }}
                >
                  {updateProjectMutation.isPending ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          </div>
        )}

      </PageContainer>
    </AppShell>
  );
}
