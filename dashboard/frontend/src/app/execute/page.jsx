"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import { apiGet, apiPost, apiDelete } from "../../utils/apiClient";

const STORAGE_KEY = "aep_execute_run_state";

function saveRunState(state) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {}
}

function loadRunState() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

const STATUS_COLORS = {
  passed: "#16A34A",
  failed: "#DC2626",
  running: "#2563EB",
  queued: "#D97706",
  error: "#DC2626",
  cancelled: "#6B7280",
};
const STATUS_BG = {
  passed: "#DCFCE7",
  failed: "#FEE2E2",
  running: "#DBEAFE",
  queued: "#FEF3C7",
  error: "#FEE2E2",
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
        border: `1px solid ${STATUS_COLORS[status] || "#E5E7EB"}22`,
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
          display: "inline-block",
          flexShrink: 0,
        }}
      />
      {status}
    </span>
  );
}

/* ── Main Page ──────────────────────────────────────────────────────────── */
export default function ExecutePage() {
  const [selectedProject, setSelectedProject] = useState("");
  const [selectedSuite, setSelectedSuite] = useState("");
  const [runId, setRunId] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const [results, setResults] = useState([]);
  const [summary, setSummary] = useState({ total: 0, completed: 0, passed: 0, failed: 0 });
  const [triggerError, setTriggerError] = useState("");
  const [isTriggering, setIsTriggering] = useState(false);
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoveryResult, setDiscoveryResult] = useState(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const eventSourceRef = useRef(null);
  const restoredRef = useRef(false);

  const queryClient = useQueryClient();

  /* ── Discover suites from automation folder ────────────────────────── */
  async function handleDiscoverSuites() {
    setIsDiscovering(true);
    setDiscoveryResult(null);
    try {
      const result = await apiPost("/api/projects/discover-suites");
      setDiscoveryResult(result);
      queryClient.invalidateQueries({ queryKey: ["projects-for-execute"] });
      queryClient.invalidateQueries({ queryKey: ["suites-for-execute"] });
    } catch (err) {
      setDiscoveryResult({ errors: [err.message] });
    } finally {
      setIsDiscovering(false);
    }
  }

  /* ── Queries ────────────────────────────────────────────────────────── */
  const { data: projectsData, isLoading: projectsLoading } = useQuery({
    queryKey: ["projects-for-execute"],
    queryFn: () => apiGet("/api/projects?limit=100"),
  });

  const { data: suitesData, isLoading: suitesLoading } = useQuery({
    queryKey: ["suites-for-execute", selectedProject],
    queryFn: () =>
      apiGet(
        `/api/test-suites?${selectedProject ? `project_id=${selectedProject}&` : ""}limit=100`
      ),
    enabled: !!selectedProject,
  });

  const projects = Array.isArray(projectsData) ? projectsData : [];
  const suites = Array.isArray(suitesData) ? suitesData : [];

  /* ── Filter suites by selected project ──────────────────────────────── */
  const filteredSuites = suites;

  /* ── SSE connection ─────────────────────────────────────────────────── */
  const connectSSE = useCallback((id) => {
    if (eventSourceRef.current) eventSourceRef.current.close();

    const token = typeof window !== "undefined" ? localStorage.getItem("aep_access_token") : "";
    const es = new EventSource(`/api/execute/${id}/stream?token=${encodeURIComponent(token || "")}`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          setTriggerError(data.error);
          es.close();
          return;
        }
        setRunStatus(data.status);
        setResults(data.results || []);
        setSummary({
          total: data.total || 0,
          completed: data.completed ?? (data.results || []).length,
          passed: data.passed || 0,
          failed: data.failed || 0,
        });

        if (["passed", "failed", "error", "cancelled"].includes(data.status)) {
          es.close();
          setIsTriggering(false);
        }
      } catch {}
    };

    let errorCount = 0;
    es.onerror = () => {
      errorCount++;
      if (errorCount > 5) {
        es.close();
        setIsTriggering(false);
      }
    };
  }, []);

  /* Cleanup SSE on unmount */
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) eventSourceRef.current.close();
    };
  }, []);

  /* ── Persist run state to sessionStorage ────────────────────────────── */
  useEffect(() => {
    if (!restoredRef.current) return;
    if (runId) {
      saveRunState({
        runId,
        runStatus,
        results,
        summary,
        selectedProject,
        selectedSuite,
      });
    }
  }, [runId, runStatus, results, summary, selectedProject, selectedSuite]);

  /* ── Restore run state on mount ────────────────────────────────────── */
  useEffect(() => {
    const saved = loadRunState();
    if (!saved?.runId) {
      restoredRef.current = true;
      return;
    }
    setSelectedProject(saved.selectedProject || "");
    setSelectedSuite(saved.selectedSuite || "");
    setRunId(saved.runId);
    setRunStatus(saved.runStatus);
    setResults(saved.results || []);
    setSummary(saved.summary || { total: 0, completed: 0, passed: 0, failed: 0 });

    const activeStatuses = ["queued", "running", "pending"];
    if (activeStatuses.includes(saved.runStatus)) {
      setIsTriggering(true);
      apiGet(`/api/execute/${saved.runId}`)
        .then((data) => {
          const finishedStatuses = ["passed", "failed", "error", "cancelled"];
          if (finishedStatuses.includes(data.status)) {
            setRunStatus(data.status);
            setIsTriggering(false);
            saveRunState({ ...saved, runStatus: data.status });
          } else {
            connectSSE(saved.runId);
          }
        })
        .catch(() => {
          setIsTriggering(false);
        });
    }

    restoredRef.current = true;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Trigger run ────────────────────────────────────────────────────── */
  async function handleTrigger() {
    if (!selectedSuite) return;
    setTriggerError("");
    setIsTriggering(true);
    setRunId(null);
    setRunStatus(null);
    setResults([]);
    setSummary({ total: 0, completed: 0, passed: 0, failed: 0 });

    try {
      const data = await apiPost("/api/execute", { suite_id: selectedSuite });
      const id = data.id;
      setRunId(id);
      setRunStatus(data.status || "queued");
      connectSSE(id);
    } catch (err) {
      setTriggerError(err.message);
      setIsTriggering(false);
    }
  }

  /* ── Cancel run ──────────────────────────────────────────────────────── */
  async function handleCancel() {
    if (!runId || isCancelling) return;
    setIsCancelling(true);
    try {
      await apiDelete(`/api/execute/${runId}`);
      if (eventSourceRef.current) eventSourceRef.current.close();
      setRunStatus("cancelled");
      setIsTriggering(false);
    } catch (err) {
      setTriggerError(err.message);
    } finally {
      setIsCancelling(false);
    }
  }

  /* ── Derived ────────────────────────────────────────────────────────── */
  const activeStatuses = ["queued", "running", "pending"];
  const isRunActive = isTriggering || activeStatuses.includes(runStatus);
  const selectedSuiteObj = filteredSuites.find((s) => s.id === selectedSuite);
  const selectedProjectObj = projects.find((p) => p.id === selectedProject);

  return (
    <AppShell>
      <div style={{ maxWidth: 1200 }}>
        {/* Header */}
        <div style={{ marginBottom: 28, display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
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
              Execute Tests
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Trigger a test suite and watch results stream in real time
            </p>
          </div>
          <button
            onClick={handleDiscoverSuites}
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
            {isDiscovering ? "Scanning…" : "Discover Suites"}
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
                : `Found ${discoveryResult.discovered?.length || 0} suites, registered ${discoveryResult.registered?.length || 0} new`}
            </span>
            <button
              onClick={() => setDiscoveryResult(null)}
              style={{ background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "inherit" }}
            >
              ×
            </button>
          </div>
        )}

        {/* ── Selector Card ──────────────────────────────────────────────── */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            padding: "24px 28px",
            marginBottom: 24,
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr auto",
              gap: 16,
              alignItems: "end",
            }}
          >
            {/* Project */}
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "#374151",
                  marginBottom: 6,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                Project
              </label>
              <select
                value={selectedProject}
                onChange={(e) => {
                  setSelectedProject(e.target.value);
                  setSelectedSuite("");
                }}
                disabled={projectsLoading}
                style={{
                  width: "100%",
                  padding: "9px 12px",
                  fontSize: 13,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  background: "#fff",
                  color: "#111827",
                  cursor: "pointer",
                }}
              >
                <option value="">
                  {projectsLoading ? "Loading…" : "Select a project…"}
                </option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Suite */}
            <div>
              <label
                style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "#374151",
                  marginBottom: 6,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                Test Suite
              </label>
              <select
                value={selectedSuite}
                onChange={(e) => setSelectedSuite(e.target.value)}
                disabled={!selectedProject || suitesLoading}
                style={{
                  width: "100%",
                  padding: "9px 12px",
                  fontSize: 13,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  background: "#fff",
                  color: "#111827",
                  cursor: "pointer",
                }}
              >
                <option value="">
                  {!selectedProject
                    ? "Select a project first…"
                    : suitesLoading
                      ? "Loading…"
                      : "Select a suite…"}
                </option>
                {filteredSuites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.suite_type ? ` (${s.suite_type})` : ""}
                  </option>
                ))}
              </select>
            </div>

            {/* Run Button */}
            <button
              onClick={handleTrigger}
              disabled={!selectedSuite || isRunActive}
              style={{
                padding: "9px 24px",
                fontSize: 13,
                fontWeight: 600,
                background: isRunActive || !selectedSuite ? "#93C5FD" : "#2563EB",
                color: "#fff",
                border: "none",
                borderRadius: 8,
                cursor: isRunActive || !selectedSuite ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
                transition: "background 0.15s",
              }}
            >
              {isRunActive ? (
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span
                    style={{
                      width: 12,
                      height: 12,
                      border: "2px solid #fff",
                      borderTopColor: "transparent",
                      borderRadius: "50%",
                      animation: "spin 0.8s linear infinite",
                    }}
                  />
                  Running…
                </span>
              ) : (
                "▶ Run Tests"
              )}
            </button>
          </div>

          {triggerError && (
            <div
              style={{
                marginTop: 14,
                padding: "10px 14px",
                background: "#FEF2F2",
                border: "1px solid #FECACA",
                borderRadius: 8,
                fontSize: 13,
                color: "#DC2626",
              }}
            >
              {triggerError}
            </div>
          )}

          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>

        {/* ── Run Info Bar ───────────────────────────────────────────────── */}
        {runId && (
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 12,
              padding: "16px 24px",
              marginBottom: 24,
              display: "flex",
              alignItems: "center",
              gap: 24,
            }}
          >
            <div>
              <span style={{ fontSize: 11, fontWeight: 600, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Suite
              </span>
              <p style={{ margin: "2px 0 0", fontSize: 14, fontWeight: 600, color: "#111827" }}>
                {selectedSuiteObj?.name || "—"}
              </p>
            </div>
            <div>
              <span style={{ fontSize: 11, fontWeight: 600, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Project
              </span>
              <p style={{ margin: "2px 0 0", fontSize: 14, fontWeight: 500, color: "#374151" }}>
                {selectedProjectObj?.name || "—"}
              </p>
            </div>
            <div>
              <span style={{ fontSize: 11, fontWeight: 600, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Status
              </span>
              <p style={{ margin: "2px 0 0" }}>
                <StatusPill status={runStatus || "queued"} />
              </p>
            </div>
            {summary.total > 0 && (
              <div style={{ minWidth: 100, maxWidth: 260 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "#9CA3AF", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    Progress
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "#111827" }}>
                    {Math.round((summary.completed / summary.total) * 100)}%
                  </span>
                </div>
                <div
                  style={{
                    width: "100%",
                    height: 6,
                    background: "#E5E7EB",
                    borderRadius: 999,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${Math.min((summary.completed / summary.total) * 100, 100)}%`,
                      height: "100%",
                      background: isRunActive
                        ? "linear-gradient(90deg, #2563EB, #60A5FA)"
                        : summary.failed > 0
                          ? "#D97706"
                          : "#16A34A",
                      borderRadius: 999,
                      transition: "width 0.5s ease-in-out",
                    }}
                  />
                </div>
                <p style={{ margin: "3px 0 0", fontSize: 11, color: "#6B7280" }}>
                  {summary.completed} of {summary.total} tests completed
                </p>
              </div>
            )}
            {isRunActive && (
              <div style={{ marginLeft: "auto" }}>
                <button
                  onClick={handleCancel}
                  disabled={isCancelling}
                  style={{
                    padding: "7px 18px",
                    fontSize: 12,
                    fontWeight: 600,
                    background: isCancelling ? "#F3F4F6" : "#FEF2F2",
                    color: isCancelling ? "#9CA3AF" : "#DC2626",
                    border: `1px solid ${isCancelling ? "#E5E7EB" : "#FECACA"}`,
                    borderRadius: 8,
                    cursor: isCancelling ? "not-allowed" : "pointer",
                    whiteSpace: "nowrap",
                    transition: "background 0.15s",
                  }}
                >
                  {isCancelling ? "Cancelling…" : "Cancel"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── Summary Bar ────────────────────────────────────────────────── */}
        {runId && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 12,
              marginBottom: 24,
            }}
          >
            {[
              { label: "Total", value: summary.total, color: "#111827" },
              { label: "Passed", value: summary.passed, color: "#16A34A" },
              { label: "Failed", value: summary.failed, color: "#DC2626" },
              {
                label: "Pass Rate",
                value:
                  summary.completed > 0
                    ? `${Math.round((summary.passed / summary.completed) * 100)}%`
                    : "—",
                color: summary.completed > 0 && summary.failed === 0 ? "#16A34A" : summary.failed > 0 ? "#D97706" : "#6B7280",
              },
            ].map((s) => (
              <div
                key={s.label}
                style={{
                  background: "#fff",
                  border: "1px solid #E5E7EB",
                  borderRadius: 10,
                  padding: "14px 18px",
                }}
              >
                <p
                  style={{
                    margin: 0,
                    fontSize: 11,
                    fontWeight: 600,
                    color: "#9CA3AF",
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                  }}
                >
                  {s.label}
                </p>
                <p
                  style={{
                    margin: "4px 0 0",
                    fontSize: 24,
                    fontWeight: 700,
                    color: s.color,
                    letterSpacing: "-0.02em",
                  }}
                >
                  {s.value}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* ── Results Table ──────────────────────────────────────────────── */}
        {runId && (
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
                padding: "14px 32px",
                borderBottom: "1px solid #E5E7EB",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#111827" }}>
                Test Results
              </h2>
              {isRunActive && (
                <span style={{ fontSize: 12, color: "#2563EB", fontWeight: 500 }}>
                  Streaming live…
                </span>
              )}
            </div>

            {/* Header */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "3fr minmax(70px, 0.8fr) minmax(60px, 0.6fr) 3fr",
                gap: 16,
                padding: "10px 32px",
                borderBottom: "1px solid #E5E7EB",
                background: "#F9FAFB",
              }}
            >
              {["Test Name", "Status", "Duration", "Error Message"].map((h) => (
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

            {/* Rows */}
            {results.length === 0 ? (
              <div
                style={{
                  padding: 48,
                  textAlign: "center",
                  color: "#9CA3AF",
                  fontSize: 13,
                }}
              >
                {isRunActive ? "Waiting for results…" : "No results yet. Trigger a run above."}
              </div>
            ) : (
              results.map((r, i) => (
                <div
                  key={r.id}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "3fr minmax(70px, 0.8fr) minmax(60px, 0.6fr) 3fr",
                gap: 16,
                    padding: "12px 32px",
                    borderBottom:
                      i < results.length - 1 ? "1px solid #F3F4F6" : "none",
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
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 500,
                      color: "#111827",
                      fontFamily: "monospace",
                    }}
                  >
                    {r.test_name}
                  </span>
                  <StatusPill status={r.status} />
                  <span style={{ fontSize: 12, color: "#6B7280" }}>
                    {r.duration_ms ? `${r.duration_ms} ms` : "—"}
                  </span>
                  <span
                    style={{
                      fontSize: 12,
                      color: "#DC2626",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {r.error_message || ""}
                  </span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}
