"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import { apiGet, apiPost } from "../../utils/apiClient";
import { getStoredUser } from "../../utils/authStore";

export default function TestSuitesPage() {
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite =
    user && ["admin", "qa_lead", "qa_engineer"].includes(user.role);

  const [search, setSearch] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({
    name: "",
    description: "",
    project_id: "",
    tags: "",
  });
  const [formError, setFormError] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["test-suites", search, projectFilter],
    queryFn: () =>
      apiGet(
        `/api/test-suites?search=${encodeURIComponent(search)}${projectFilter ? `&project_id=${projectFilter}` : ""}&limit=50`,
      ),
  });

  const { data: projectsData } = useQuery({
    queryKey: ["projects-list"],
    queryFn: () => apiGet("/api/projects?limit=100"),
  });

  const createMutation = useMutation({
    mutationFn: (body) => apiPost("/api/test-suites", body),
    onSuccess: () => {
      qc.invalidateQueries(["test-suites"]);
      setShowModal(false);
      setForm({ name: "", description: "", project_id: "", tags: "" });
    },
    onError: (e) => setFormError(e.message),
  });

  const suites = data?.data || [];
  const projects = projectsData?.data || [];

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
              Test Suites
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Grouped test collections per product
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
              + New Suite
            </button>
          )}
        </div>

        <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search suites…"
            style={{
              padding: "8px 12px",
              fontSize: 13,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              width: 240,
              color: "#111827",
            }}
            onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
            onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
          />
          <select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            style={{
              padding: "8px 12px",
              fontSize: 13,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              color: "#111827",
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
              gridTemplateColumns: "2fr 1.5fr 2fr 80px 80px",
              gap: 16,
              padding: "10px 32px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {["Suite Name", "Project", "Tags", "Runs", "Failed"].map((h) => (
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
          ) : !suites.length ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No test suites found.
            </div>
          ) : (
            suites.map((s, i) => (
              <div
                key={s.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "2fr 1.5fr 2fr 80px 80px",
              gap: 16,
                  padding: "14px 32px",
                  borderBottom:
                    i < suites.length - 1 ? "1px solid #F3F4F6" : "none",
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
                    {s.name}
                  </p>
                  {s.description && (
                    <p
                      style={{
                        margin: "2px 0 0",
                        fontSize: 11,
                        color: "#9CA3AF",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.description}
                    </p>
                  )}
                </div>
                <span
                  style={{
                    fontSize: 13,
                    color: "#374151",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: "#2563EB",
                      display: "inline-block",
                    }}
                  />
                  {s.project_name}
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {(s.tags || []).map((tag) => (
                    <span
                      key={tag}
                      style={{
                        fontSize: 10,
                        background: "#EFF6FF",
                        color: "#2563EB",
                        border: "1px solid #BFDBFE",
                        borderRadius: 999,
                        padding: "1px 6px",
                      }}
                    >
                      {tag}
                    </span>
                  ))}
                  {(!s.tags || s.tags.length === 0) && (
                    <span style={{ fontSize: 11, color: "#D1D5DB" }}>—</span>
                  )}
                </div>
                <span
                  style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}
                >
                  {s.run_count || 0}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: s.failed_runs > 0 ? "#DC2626" : "#374151",
                  }}
                >
                  {s.failed_runs || 0}
                </span>
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
                New Test Suite
              </h2>
              {["name", "description"].map((field) => (
                <div key={field} style={{ marginBottom: 14 }}>
                  <label
                    style={{
                      display: "block",
                      fontSize: 13,
                      fontWeight: 500,
                      color: "#374151",
                      marginBottom: 6,
                      textTransform: "capitalize",
                    }}
                  >
                    {field}
                    {field === "name" ? " *" : ""}
                  </label>
                  <input
                    value={form[field]}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, [field]: e.target.value }))
                    }
                    placeholder={field === "name" ? "Suite name" : "Optional…"}
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
              ))}
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
                    boxSizing: "border-box",
                    background: "#fff",
                  }}
                >
                  <option value="">Select a project…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
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
                  Tags (comma-separated)
                </label>
                <input
                  value={form.tags}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, tags: e.target.value }))
                  }
                  placeholder="e.g. regression, smoke, api"
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
                  onClick={() =>
                    createMutation.mutate({
                      ...form,
                      tags: form.tags
                        ? form.tags
                            .split(",")
                            .map((t) => t.trim())
                            .filter(Boolean)
                        : [],
                    })
                  }
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
                  {createMutation.isPending ? "Creating…" : "Create Suite"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
