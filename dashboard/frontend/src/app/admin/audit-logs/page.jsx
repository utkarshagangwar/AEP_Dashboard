"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import { apiGet } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";

const ACTION_COLORS = {
  LOGIN: "#16A34A",
  LOGOUT: "#6B7280",
  CREATE: "#2563EB",
  UPDATE: "#D97706",
  DELETE: "#DC2626",
  TRIGGER: "#7C3AED",
  SEED: "#0891B2",
};
const ACTION_BG = {
  LOGIN: "#DCFCE7",
  LOGOUT: "#F3F4F6",
  CREATE: "#DBEAFE",
  UPDATE: "#FEF3C7",
  DELETE: "#FEE2E2",
  TRIGGER: "#EDE9FE",
  SEED: "#CFFAFE",
};

export default function AuditLogsPage() {
  const [user, setUser] = useState(null);
  const [actionFilter, setActionFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    // Middleware handles auth + role checks; this is a fallback
    const stored = getStoredUser();
    setUser(stored);
  }, []);

  const params = new URLSearchParams({ page, limit: 50 });
  if (actionFilter) params.set("action", actionFilter);
  if (resourceFilter) params.set("resource_type", resourceFilter);

  const { data, isLoading, error } = useQuery({
    queryKey: ["audit-logs", actionFilter, resourceFilter, page],
    queryFn: () => apiGet(`/api/audit-logs?${params.toString()}`),
  });

  const logs = data?.data || [];
  const total = data?.total || 0;

  if (!user) return null;

  return (
    <AppShell>
      <div style={{ maxWidth: 1100 }}>
        <div style={{ marginBottom: 24 }}>
          <h1
            style={{
              margin: 0,
              fontSize: 22,
              fontWeight: 600,
              color: "#111827",
              letterSpacing: "-0.02em",
            }}
          >
            Audit Logs
          </h1>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
            All platform activity — {total.toLocaleString()} total events
          </p>
        </div>

        <div
          style={{
            display: "flex",
            gap: 10,
            marginBottom: 20,
            flexWrap: "wrap",
          }}
        >
          <select
            value={actionFilter}
            onChange={(e) => {
              setActionFilter(e.target.value);
              setPage(1);
            }}
            style={{
              padding: "7px 12px",
              fontSize: 12,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              color: "#374151",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            <option value="">All Actions</option>
            {[
              "LOGIN",
              "LOGOUT",
              "CREATE",
              "UPDATE",
              "DELETE",
              "TRIGGER",
              "SEED",
            ].map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <select
            value={resourceFilter}
            onChange={(e) => {
              setResourceFilter(e.target.value);
              setPage(1);
            }}
            style={{
              padding: "7px 12px",
              fontSize: 12,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              color: "#374151",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            <option value="">All Resources</option>
            {[
              "user",
              "project",
              "test_suite",
              "test_run",
              "test_result",
              "defect",
            ].map((r) => (
              <option key={r} value={r}>
                {r.replace("_", " ")}
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
              gridTemplateColumns: "140px 1fr 1fr 1fr 160px",
              padding: "10px 20px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {["Action", "User", "Resource", "Details", "Timestamp"].map((h) => (
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
          ) : !logs.length ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No audit logs found.
            </div>
          ) : (
            logs.map((log, i) => (
              <div
                key={log.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "140px 1fr 1fr 1fr 160px",
                  padding: "12px 20px",
                  borderBottom:
                    i < logs.length - 1 ? "1px solid #F3F4F6" : "none",
                  alignItems: "center",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    background: ACTION_BG[log.action] || "#F3F4F6",
                    color: ACTION_COLORS[log.action] || "#6B7280",
                    border: "1px solid #E5E7EB",
                    borderRadius: 999,
                    padding: "2px 8px",
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: "0.05em",
                    width: "fit-content",
                  }}
                >
                  {log.action}
                </span>
                <div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 12,
                      fontWeight: 500,
                      color: "#111827",
                    }}
                  >
                    {log.user_name || "System"}
                  </p>
                  <p style={{ margin: 0, fontSize: 11, color: "#9CA3AF" }}>
                    {log.user_email || ""}
                  </p>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: 11,
                      background: "#F3F4F6",
                      border: "1px solid #E5E7EB",
                      borderRadius: 4,
                      padding: "2px 6px",
                      color: "#374151",
                    }}
                  >
                    {log.resource_type}
                  </span>
                  {log.resource_id && (
                    <p
                      style={{
                        margin: "3px 0 0",
                        fontSize: 10,
                        color: "#9CA3AF",
                        fontFamily: "monospace",
                      }}
                    >
                      {log.resource_id.slice(0, 8)}…
                    </p>
                  )}
                </div>
                <p
                  style={{
                    margin: 0,
                    fontSize: 11,
                    color: "#6B7280",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {log.details
                    ? Object.entries(log.details)
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(" · ")
                    : "—"}
                </p>
                <div>
                  <p style={{ margin: 0, fontSize: 12, color: "#374151" }}>
                    {new Date(log.created_at).toLocaleDateString("en-GB", {
                      day: "numeric",
                      month: "short",
                      year: "2-digit",
                    })}
                  </p>
                  <p style={{ margin: 0, fontSize: 11, color: "#9CA3AF" }}>
                    {new Date(log.created_at).toLocaleTimeString("en-GB", {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })}
                  </p>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Pagination */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginTop: 16,
          }}
        >
          <p style={{ margin: 0, fontSize: 12, color: "#6B7280" }}>
            Showing {Math.min((page - 1) * 50 + 1, total)}–
            {Math.min(page * 50, total)} of {total}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              style={{
                padding: "6px 12px",
                fontSize: 12,
                border: "1px solid #E5E7EB",
                borderRadius: 6,
                background: "#fff",
                color: page === 1 ? "#D1D5DB" : "#374151",
                cursor: page === 1 ? "not-allowed" : "pointer",
              }}
            >
              Previous
            </button>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={page * 50 >= total}
              style={{
                padding: "6px 12px",
                fontSize: 12,
                border: "1px solid #E5E7EB",
                borderRadius: 6,
                background: "#fff",
                color: page * 50 >= total ? "#D1D5DB" : "#374151",
                cursor: page * 50 >= total ? "not-allowed" : "pointer",
              }}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
