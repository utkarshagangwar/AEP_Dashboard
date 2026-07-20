"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
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
  skipped: "#D97706",
  error: "#7C3AED",
};
const STATUS_BG = {
  passed: "#DCFCE7",
  failed: "#FEE2E2",
  skipped: "#FEF3C7",
  error: "#EDE9FE",
};

export default function TestResultsPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const [runFilter, setRunFilter] = useState("");
  const [selected, setSelected] = useState(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["test-results", statusFilter, runFilter],
    queryFn: () =>
      apiGet(
        `/api/test-results?${statusFilter ? `status=${statusFilter}&` : ""}${runFilter ? `test_run_id=${runFilter}&` : ""}limit=100`,
      ),
  });

  const { data: runsData } = useQuery({
    queryKey: ["runs-list"],
    queryFn: () => apiGet("/api/test-runs?limit=100"),
  });

  const results = data?.data || [];
  const runs = runsData?.data || [];

  return (
    <AppShell>
      <div style={{ maxWidth: 1100, display: "flex", gap: 20 }}>
        <div style={{ flex: 1 }}>
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
              Test Results
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Individual test case outcomes
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
            {[
              ["", "All"],
              ["passed", "Passed"],
              ["failed", "Failed"],
              ["error", "Error"],
              ["skipped", "Skipped"],
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
            <Select
              value={runFilter || "all"}
              onValueChange={(v) => setRunFilter(v === "all" ? "" : v)}
              items={[
                { value: "all", label: "All Runs" },
                ...runs.map((r) => ({ value: r.id, label: r.name })),
              ]}
            >
              <SelectTrigger className="h-[29px] rounded-full text-xs text-gray-500">
                <SelectValue placeholder="All Runs" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Runs</SelectItem>
                {runs.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {r.name}
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
                gridTemplateColumns: "3fr 1.5fr 1fr 80px",
                padding: "10px 20px",
                borderBottom: "1px solid #E5E7EB",
                background: "#F9FAFB",
              }}
            >
              {["Test Name", "Run", "Status", "Duration"].map((h) => (
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
            ) : !results.length ? (
              <div
                style={{
                  padding: 40,
                  textAlign: "center",
                  color: "#9CA3AF",
                  fontSize: 13,
                }}
              >
                No results found.
              </div>
            ) : (
              results.map((r, i) => (
                <div
                  key={r.id}
                  onClick={() => setSelected(selected?.id === r.id ? null : r)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "3fr 1.5fr 1fr 80px",
                    padding: "12px 20px",
                    borderBottom:
                      i < results.length - 1 ? "1px solid #F3F4F6" : "none",
                    alignItems: "center",
                    cursor: "pointer",
                    background:
                      selected?.id === r.id ? "#F9FAFB" : "transparent",
                  }}
                  onMouseEnter={(e) => {
                    if (selected?.id !== r.id)
                      e.currentTarget.style.background = "#F9FAFB";
                  }}
                  onMouseLeave={(e) => {
                    if (selected?.id !== r.id)
                      e.currentTarget.style.background = "transparent";
                  }}
                >
                  <div>
                    <p
                      style={{
                        margin: 0,
                        fontSize: 12,
                        fontWeight: 600,
                        color: "#111827",
                        fontFamily: "monospace",
                      }}
                    >
                      {r.test_name}
                    </p>
                    {r.test_class && (
                      <p
                        style={{
                          margin: "2px 0 0",
                          fontSize: 11,
                          color: "#9CA3AF",
                          fontFamily: "monospace",
                        }}
                      >
                        {r.test_class}
                      </p>
                    )}
                  </div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 12,
                      color: "#6B7280",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {r.run_name}
                  </p>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      background: STATUS_BG[r.status],
                      color: STATUS_COLORS[r.status],
                      border: "1px solid #E5E7EB",
                      borderRadius: 999,
                      padding: "2px 8px",
                      fontSize: 10,
                      fontWeight: 700,
                      textTransform: "uppercase",
                    }}
                  >
                    <span
                      style={{
                        width: 4,
                        height: 4,
                        borderRadius: "50%",
                        background: STATUS_COLORS[r.status],
                      }}
                    />
                    {r.status}
                  </span>
                  <span style={{ fontSize: 12, color: "#6B7280" }}>
                    {r.duration_ms != null ? `${r.duration_ms}ms` : "—"}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Detail panel */}
        {selected && (
          <div style={{ width: 340, flexShrink: 0 }}>
            <div
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: 20,
                position: "sticky",
                top: 0,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: 16,
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
                  Result Detail
                </h3>
                <button
                  onClick={() => setSelected(null)}
                  style={{
                    border: "none",
                    background: "none",
                    cursor: "pointer",
                    color: "#9CA3AF",
                    fontSize: 18,
                    lineHeight: 1,
                  }}
                >
                  ×
                </button>
              </div>
              <p
                style={{
                  margin: "0 0 4px",
                  fontSize: 11,
                  color: "#9CA3AF",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                Test
              </p>
              <p
                style={{
                  margin: "0 0 16px",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "#111827",
                  fontFamily: "monospace",
                  wordBreak: "break-all",
                }}
              >
                {selected.test_name}
              </p>

              {[
                ["Class", selected.test_class],
                ["Run", selected.run_name],
                ["Environment", selected.environment],
                [
                  "Duration",
                  selected.duration_ms != null
                    ? `${selected.duration_ms}ms`
                    : null,
                ],
              ]
                .filter(([, v]) => v)
                .map(([label, value]) => (
                  <div key={label} style={{ marginBottom: 10 }}>
                    <p
                      style={{
                        margin: "0 0 2px",
                        fontSize: 11,
                        color: "#9CA3AF",
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                      }}
                    >
                      {label}
                    </p>
                    <p style={{ margin: 0, fontSize: 12, color: "#374151" }}>
                      {value}
                    </p>
                  </div>
                ))}

              {selected.error_message && (
                <div style={{ marginTop: 16 }}>
                  <p
                    style={{
                      margin: "0 0 6px",
                      fontSize: 11,
                      fontWeight: 600,
                      color: "#DC2626",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    Error Message
                  </p>
                  <div
                    style={{
                      background: "#FEF2F2",
                      border: "1px solid #FECACA",
                      borderRadius: 6,
                      padding: "10px 12px",
                    }}
                  >
                    <p
                      style={{
                        margin: 0,
                        fontSize: 11,
                        color: "#DC2626",
                        fontFamily: "monospace",
                        wordBreak: "break-word",
                      }}
                    >
                      {selected.error_message}
                    </p>
                  </div>
                </div>
              )}

              {selected.stack_trace && (
                <div style={{ marginTop: 12 }}>
                  <p
                    style={{
                      margin: "0 0 6px",
                      fontSize: 11,
                      fontWeight: 600,
                      color: "#6B7280",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    Stack Trace
                  </p>
                  <div
                    style={{
                      background: "#F9FAFB",
                      border: "1px solid #E5E7EB",
                      borderRadius: 6,
                      padding: "10px 12px",
                      maxHeight: 200,
                      overflowY: "auto",
                    }}
                  >
                    <pre
                      style={{
                        margin: 0,
                        fontSize: 10,
                        color: "#374151",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {selected.stack_trace}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
