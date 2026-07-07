"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import PageContainer from "../../components/PageContainer";
import { apiGet, apiPost } from "../../utils/apiClient";

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

  const [search, setSearch] = useState("");
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoveryResult, setDiscoveryResult] = useState(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["projects", search],
    queryFn: () =>
      apiGet(`/api/projects?search=${encodeURIComponent(search)}&limit=50`),
  });

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
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "2fr 3fr minmax(80px, 0.8fr) 80px 80px",
              gap: 16,
              padding: "10px 32px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {["Name", "Description", "Status", "Suites", "Defects"].map((h) => (
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
                  gridTemplateColumns: "2fr 3fr minmax(80px, 0.8fr) 80px 80px",
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
              </div>
            ))
          )}
        </div>

      </PageContainer>
    </AppShell>
  );
}
