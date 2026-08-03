"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiFetch, apiPost, refreshAccessToken } from "@/utils/apiClient";
import { getAccessToken } from "@/lib/api";
import AppShell from "@/components/AppShell";
import PageContainer from "@/components/PageContainer";
import AutonomousQASection from "@/components/AutonomousQASection";
import VisualAuditSection from "@/components/VisualAuditSection";
import CoverageTab from "@/components/ai-testing/CoverageTab";
import ResultsTab from "@/components/ai-testing/ResultsTab";
import SkillsTab from "@/components/ai-testing/SkillsTab";
import ModeSelector, { TestMode } from "@/components/ai-testing/ModeSelector";
import CreateBypassProfileDialog from "@/components/ai-testing/CreateBypassProfileDialog";
import FunctionalTestFields, {
  FunctionalTestPayload,
} from "@/components/ai-testing/FunctionalTestFields";
import {
  CredentialProfile,
  RunEvent,
  RunResult,
  StepIcon,
  LiveBrowserView,
  VideoPane,
  StepRow,
  formatDuration,
  formatElapsed,
} from "@/components/ai-testing/shared";
import AndroidNewTestPanel from "@/components/ai-testing/AndroidNewTestPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PasswordRevealSwitch } from "@/components/ui/password-reveal-switch";
import {
  SegmentedTabs,
  SegmentedTabsIndicator,
  SegmentedTabsList,
  SegmentedTabsTab,
} from "@/components/ui/segmented-tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// ── Types ────────────────────────────────────────────────────────────────────

type UIState = "idle" | "running" | "complete";
type PageTab = "new" | "results" | "coverage" | "skills";

interface Environment {
  id: string;
  name: string;
}

// The Credential Profile control branches by this exact environment name
// (case-insensitive) — see BYPASS_ENVIRONMENT_NAME's usage below for why a
// hardcoded name match was chosen over dynamic detection.
const BYPASS_ENVIRONMENT_NAME = "ig automation";

// Synthetic entry prepended to the Environment dropdown — not a real
// environment id from the backend, never sent as project_id. Lets a user
// explicitly opt out of every saved environment and drive the ad-hoc
// URL/Email/Password fields directly (e.g. testing a site that has no
// pre-configured environment entry at all). Picking it still counts as
// "an Environment was chosen" for gating purposes below.
const NO_ENVIRONMENT_VALUE = "__no_environment__";

// ── Helpers ───────────────────────────────────────────────────────────────────

// Prepends https:// when the user typed a bare host/path (e.g.
// "interviewgod.com/login") — without this, urlparse().hostname on the
// backend comes back None for a schemeless URL, which trips the
// allowed_domains validation with a confusing error.
function normalizeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return trimmed;
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

// Client-side-only preview of the compiled Functional Test goal, shown in
// the live-run header before the run's real (server-compiled) goal text
// comes back from GET /api/ai-testing/runs/:id. The backend's
// _compile_functional_goal() in app/api/v1/ai_runs.py is the source of
// truth — this only needs to be close enough for a transient header, not
// byte-identical, and is never sent to the API.
function previewFunctionalGoal(payload: FunctionalTestPayload): string {
  const lines: string[] = [];
  if (payload.test_type !== "happy") {
    lines.push(`Test type: ${payload.test_type === "negative" ? "NEGATIVE" : "EDGE CASE"}`);
  }
  if (payload.preconditions) lines.push(`Preconditions: ${payload.preconditions}`);
  payload.steps.forEach((s, i) => lines.push(`${i + 1}. ${s.text}`));
  return lines.join(" — ") || "Functional test";
}

// ── Top-level tab bar (New Test | Results | Skills) ─────────────────────────

const PAGE_TABS: { id: PageTab; label: string }[] = [
  { id: "new", label: "New Test" },
  { id: "results", label: "Results" },
  { id: "coverage", label: "Coverage" },
  { id: "skills", label: "Skills" },
];

function PageTabBar({
  active,
  onChange,
}: {
  active: PageTab;
  onChange: (tab: PageTab) => void;
}) {
  return (
    <SegmentedTabs
      value={active}
      onValueChange={(value) => onChange(value as PageTab)}
      className="mb-8"
    >
      <SegmentedTabsList>
        <SegmentedTabsIndicator />
        {PAGE_TABS.map((tab) => (
          // w-auto px-4 overrides the component's fixed 50px tab width, which
          // only fits very short labels ("New Test" and "Coverage" would clip).
          <SegmentedTabsTab
            key={tab.id}
            value={tab.id}
            className="w-auto px-4"
          >
            {tab.label}
          </SegmentedTabsTab>
        ))}
      </SegmentedTabsList>
    </SegmentedTabs>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AITestingPage() {
  const [uiState, setUiState] = useState<UIState>("idle");
  const [pageTab, setPageTab] = useState<PageTab>("new");
  const [goal, setGoal] = useState("");
  const [projectId, setProjectId] = useState<string>("");
  const [profileId, setProfileId] = useState<string>("");
  // One-off "Website without/with login" path — only used when the selected
  // Environment isn't the bypass-capable one (see BYPASS_ENVIRONMENT_NAME).
  // Never saved as a reusable profile; sent directly with the run.
  const [loginMode, setLoginMode] = useState<"none" | "without_login" | "with_login">("none");
  const [adhocUrl, setAdhocUrl] = useState("");
  const [adhocLoginId, setAdhocLoginId] = useState("");
  const [adhocPassword, setAdhocPassword] = useState("");
  const [showAdhocPassword, setShowAdhocPassword] = useState(false);
  const [bypassDialogOpen, setBypassDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const [activeTab, setActiveTab] = useState<"summary" | "steps" | "video">("summary");
  const [loggingDefect, setLoggingDefect] = useState(false);
  const [defectTitle, setDefectTitle] = useState("");
  const [defectDescription, setDefectDescription] = useState("");
  const [defectLogged, setDefectLogged] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // New-test mode picker for the three Vibe Testing surfaces. Switching
  // modes only toggles visibility, so an in-flight visual audit remains
  // intact while another web-test mode is selected.
  const [testMode, setTestMode] = useState<TestMode>("functional");
  const [testType, setTestType] = useState<"web" | "android">("web");
  // Structured Functional Test authoring state (New Vibe Test Phase 1) —
  // reported live by FunctionalTestFields on every edit. null until that
  // component has mounted and reported its first value.
  const [functionalPayload, setFunctionalPayload] = useState<FunctionalTestPayload | null>(
    null
  );
  // Bumped to force FunctionalTestFields to remount (clearing its internal
  // state) on "Run Another Test" — mirrors the old quick-mode's setGoal("").
  const [functionalFormKey, setFunctionalFormKey] = useState(0);

  const logRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const runStartRef = useRef<number>(0);
  // Wall-clock timestamp each step first became "running", so the log can
  // show a live-ticking elapsed time for whichever step is currently in
  // progress instead of a static snapshot that never moves until the step
  // resolves (which, for a real AI-driven step, can take a while).
  const stepStartRef = useRef<Map<number, number>>(new Map());

  // ── Data queries ─────────────────────────────────────────────────────────

  const { data: environments = [], isLoading: envLoading } = useQuery<Environment[]>({
    queryKey: ["ai-environments"],
    queryFn: () => apiGet("/api/ai-testing/environments"),
    staleTime: 60_000,
  });

  // NO_ENVIRONMENT_VALUE is a frontend-only sentinel, not a real project
  // id — the backend's project_id filter expects a UUID, so passing the
  // sentinel through would 422. Treat it the same as "no filter."
  const profileFilterProjectId = projectId && projectId !== NO_ENVIRONMENT_VALUE ? projectId : "";
  const { data: profiles = [], isLoading: profilesLoading, refetch: refetchProfiles } = useQuery<CredentialProfile[]>({
    queryKey: ["ai-credential-profiles", profileFilterProjectId],
    queryFn: () =>
      apiGet(
        `/api/ai-testing/credential-profiles${profileFilterProjectId ? `?project_id=${profileFilterProjectId}` : ""}`
      ),
    staleTime: 60_000,
  });

  // ── Environment-dependent Credential Profile branching ──────────────────
  // Environment is a mandatory gate: nothing chosen yet → neither the
  // Credential Profile picker nor the ad-hoc URL/login fields are shown at
  // all (previously the Credential Profile picker rendered by default with
  // no Environment picked, which let it be filled in out of order). Once
  // something is chosen — a real environment, or the explicit "No
  // Environment" entry — the bypass-capable environment shows the
  // saved-profile picker; everything else (including "No Environment")
  // shows the one-off "Website without/with login" pills, since most
  // environments (and by definition "No Environment") have no saved
  // credential profile at all.
  const selectedEnv = environments.find((e) => e.id === projectId);
  const isIgAutomation =
    !!selectedEnv && selectedEnv.name.trim().toLowerCase() === BYPASS_ENVIRONMENT_NAME;
  const hasEnvironmentChoice = projectId !== "";
  const adhocValid =
    !hasEnvironmentChoice
      ? false
      : isIgAutomation
      ? true
      : (loginMode === "without_login" && adhocUrl.trim().length > 0) ||
        (loginMode === "with_login" &&
          adhocUrl.trim().length > 0 &&
          adhocLoginId.trim().length > 0 &&
          adhocPassword.length > 0);

  // Switching environments invalidates whatever was picked/typed for the
  // previous one — without this, a stale profileId from a different
  // environment would silently stay in handleSubmit's body (e.g. picking
  // "IG Login bypass" under IG Automation, then switching to another
  // environment, would otherwise still submit that bypass profile id
  // against the wrong app).
  useEffect(() => {
    setProfileId("");
    setLoginMode("none");
    setAdhocUrl("");
    setAdhocLoginId("");
    setAdhocPassword("");
  }, [projectId]);

  // Shared by handleSubmit and handleRerun so the two never diverge on how
  // a run body is built. Takes projId explicitly (rather than always
  // reading the projectId state) because handleRerun may submit against a
  // different project than whatever's currently selected in the dropdown.
  function buildRunBody(goalText: string, projId: string): Record<string, unknown> {
    const body: Record<string, unknown> = { goal: goalText };
    // NO_ENVIRONMENT_VALUE is a frontend-only sentinel, never a real
    // project id — must never be sent to the backend as project_id.
    if (projId && projId !== NO_ENVIRONMENT_VALUE) body.project_id = projId;

    const env = environments.find((e) => e.id === projId);
    const igAuto = !!env && env.name.trim().toLowerCase() === BYPASS_ENVIRONMENT_NAME;

    if (igAuto && profileId) {
      body.credential_profile_id = profileId;
    } else if (!igAuto && loginMode !== "none" && adhocUrl.trim()) {
      body.target_url = normalizeUrl(adhocUrl);
      if (loginMode === "with_login" && adhocLoginId.trim() && adhocPassword) {
        body.login_identifier = adhocLoginId.trim();
        body.login_password = adhocPassword;
      }
    }
    return body;
  }

  // ── Auto-scroll log ───────────────────────────────────────────────────────

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [events]);

  // ── Elapsed timer ─────────────────────────────────────────────────────────

  useEffect(() => {
    if (uiState === "running") {
      runStartRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - runStartRef.current);
      }, 500);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [uiState]);

  // ── Fetch final run result ────────────────────────────────────────────────

  const fetchRunResult = useCallback(async (id: string) => {
    try {
      const run = await apiGet(`/api/ai-testing/runs/${id}`);
      setRunResult({
        run_id: id,
        goal: run.goal,
        environment: run.environment,
        credential_profile_name: run.credential_profile_name,
        project_id: run.project_id,
        status: run.status,
        duration_ms: run.duration_ms,
        step_count: run.step_count,
        summary: run.summary,
        raw_summary: run.raw_summary,
        run_type: run.run_type,
        skill_id: run.skill_id,
        failing_step_index: run.failing_step_index,
        failing_step_description: run.failing_step_description,
        failing_step_screenshot_url: run.failing_step_screenshot_url,
        video_available: run.video_available,
        test_category: run.test_category,
        test_type: run.test_type,
        linked_requirement: run.linked_requirement,
        events: run.events || [],
        created_at: run.created_at,
      });
    } catch (err) {
      console.error("Failed to fetch run result:", err);
    } finally {
      setUiState("complete");
      setActiveTab("summary");
    }
  }, []);

  // ── SSE subscription ──────────────────────────────────────────────────────

  useEffect(() => {
    if (uiState !== "running" || !runId) return;

    let cancelled = false;
    let es: EventSource | null = null;

    // The access token is in-memory only (see lib/api.ts) — it is never
    // written to localStorage. Reading it from localStorage here (the old
    // code) always produced an empty string, so the stream opened with
    // token="" → the Next.js proxy forwarded no Authorization header →
    // FastAPI's get_current_user 401'd → EventSource's spec-mandated
    // behavior for a non-200 response is to fire onerror without ever
    // invoking onmessage, and that handler just closed the connection. Net
    // effect: the run silently never received a single event and the UI
    // sat on "Initialising…" / "Waiting for first screenshot…" forever,
    // even though the run itself was actually executing server-side. Pull
    // the real in-memory token instead, and if it's momentarily unset
    // (e.g. this fires in the same tick as a token refresh), await one
    // refresh before opening the connection rather than opening it
    // guaranteed-broken.
    (async () => {
      let token = getAccessToken();
      if (!token) {
        await refreshAccessToken();
        token = getAccessToken();
      }
      if (cancelled) return;

      es = new EventSource(
        `/api/ai-testing/runs/${runId}/stream?token=${encodeURIComponent(token || "")}`
      );

      // "needs_review" included (New Vibe Test Phase 4) — a run can now
      // finish in this status instead of "passed" once GEval has weighed
      // in; it's just as terminal as any other outcome, and omitting it
      // would leave this stream open (and the UI stuck showing "running")
      // for any run that gets flagged.
      const TERMINAL = new Set(["passed", "failed", "inconclusive", "cancelled", "needs_review"]);

      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.error) {
            es?.close();
            return;
          }

          if (Array.isArray(data.new_events) && data.new_events.length > 0) {
            // Upsert by sequence: a step is first sent as "running" and later
            // re-sent as "passed"/"failed" on the *same* sequence once it
            // resolves, so incoming events must overwrite existing entries,
            // not just be appended when the sequence hasn't been seen before
            // (otherwise a step's completion update would be silently dropped
            // and it would look stuck in "running" forever).
            setEvents((prev) => {
              const bySeq = new Map(prev.map((e) => [e.sequence, e]));
              for (const e of data.new_events as RunEvent[]) {
                bySeq.set(e.sequence, e);
                if (e.status === "running" && !stepStartRef.current.has(e.sequence)) {
                  stepStartRef.current.set(e.sequence, Date.now());
                }
              }
              return Array.from(bySeq.values()).sort((a, b) => a.sequence - b.sequence);
            });
          }

          if (TERMINAL.has(data.run_status)) {
            es?.close();
            fetchRunResult(runId);
          }
        } catch {
          // ignore malformed SSE frames
        }
      };

      es.onerror = () => es?.close();
    })();

    return () => {
      cancelled = true;
      es?.close();
    };
  }, [uiState, runId, fetchRunResult]);

  // ── Actions ───────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    if (!functionalPayload?.isValid || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    setEvents([]);
    setElapsedMs(0);
    setRunResult(null);
    setDefectLogged(false);
    stepStartRef.current.clear();

    try {
      const previewGoal = previewFunctionalGoal(functionalPayload);
      setGoal(previewGoal);
      const body = {
        ...buildRunBody(previewGoal, projectId),
        test_category: "functional",
        preconditions: functionalPayload.preconditions || undefined,
        steps: functionalPayload.steps,
        expected_results: functionalPayload.expected_results,
        test_data: functionalPayload.test_data.length ? functionalPayload.test_data : undefined,
        test_type: functionalPayload.test_type,
        linked_requirement: functionalPayload.linked_requirement || undefined,
        viewport_preset: functionalPayload.viewport_preset,
      };

      const resp = await apiFetch("/api/ai-testing/runs", {
        method: "POST",
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }

      const data = await resp.json();
      setRunId(data.run_id);
      setUiState("running");
    } catch (err: unknown) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to start test run"
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleStop = async () => {
    if (!runId) return;
    try {
      await apiFetch(`/api/ai-testing/runs/${runId}`, { method: "DELETE" });
    } catch {
      // best-effort cancel
    }
    if (runId) fetchRunResult(runId);
  };

  const handleRerun = async () => {
    if (!runResult) return;
    const savedGoal = runResult.goal;
    const savedProjectId = runResult.project_id || "";
    setEvents([]);
    setElapsedMs(0);
    setRunResult(null);
    setDefectLogged(false);
    stepStartRef.current.clear();
    setSubmitting(true);
    setSubmitError(null);

    try {
      const body = buildRunBody(savedGoal, savedProjectId);

      const resp = await apiFetch("/api/ai-testing/runs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`Server error (${resp.status})`);
      const data = await resp.json();
      setRunId(data.run_id);
      setGoal(savedGoal);
      setProjectId(savedProjectId);
      setUiState("running");
    } catch (err: unknown) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to rerun test"
      );
      setUiState("idle");
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditGoal = () => {
    setUiState("idle");
    setRunResult(null);
    setEvents([]);
  };

  const handleRunAnother = () => {
    setUiState("idle");
    setGoal("");
    setProjectId("");
    setProfileId("");
    setLoginMode("none");
    setAdhocUrl("");
    setAdhocLoginId("");
    setAdhocPassword("");
    setRunResult(null);
    setEvents([]);
    setDefectLogged(false);
    setSubmitError(null);
    setFunctionalPayload(null);
    setFunctionalFormKey((k) => k + 1);
  };

  // A skill replay started from the Skills tab hands off into the existing
  // live-run view — same run_id/SSE flow as a goal-based run.
  const handleReplayStarted = (newRunId: string, skillGoal: string) => {
    setEvents([]);
    setElapsedMs(0);
    setRunResult(null);
    setDefectLogged(false);
    setSubmitError(null);
    setLoginMode("none");
    setAdhocUrl("");
    setAdhocLoginId("");
    setAdhocPassword("");
    stepStartRef.current.clear();
    setGoal(skillGoal);
    setRunId(newRunId);
    setPageTab("new");
    setUiState("running");
  };

  const handleOpenLogDefect = () => {
    if (!runResult) return;
    // Prefix follows the actual verdict (2026-07-28): this card is now also
    // reachable from a needs_review run, where "AI Test Failed" would be
    // factually wrong on the defect title.
    setDefectTitle(
      `${
        runResult.status === "needs_review"
          ? "AI Test Needs Review"
          : "AI Test Failed"
      }: ${
        runResult.failing_step_description?.slice(0, 90) ||
        runResult.goal.slice(0, 90)
      }`
    );
    setDefectDescription(
      [
        `Goal: ${runResult.goal}`,
        `Failing step: ${runResult.failing_step_description || "Unknown"}`,
        `Environment: ${runResult.environment || "N/A"}`,
        `Credential profile: ${runResult.credential_profile_name || "None"}`,
        `Summary: ${runResult.summary || "N/A"}`,
      ].join("\n\n")
    );
    setLoggingDefect(true);
  };

  const handleSubmitDefect = async () => {
    if (!defectTitle.trim()) return;
    try {
      const body: Record<string, unknown> = {
        title: defectTitle,
        description: defectDescription,
        severity: "high",
      };
      if (runResult?.project_id) body.project_id = runResult.project_id;

      await apiPost("/api/defects", body);
      setLoggingDefect(false);
      setDefectLogged(true);
    } catch (err) {
      console.error("Log defect failed:", err);
    }
  };

  // ── Results / Skills tabs ─────────────────────────────────────────────────
  // The live-run view takes over the whole page, so tab navigation is only
  // available when no run is actively streaming.

  if (pageTab !== "new" && uiState !== "running") {
    return (
      <AppShell noPadding>
        <div className="min-h-full bg-gray-50">
          <PageContainer>
            <div className="mb-8">
              <h1 className="text-3xl font-bold text-gray-900">Vibe Testing</h1>
              <p className="text-gray-500 mt-1">
                {pageTab === "results"
                  ? "Every goal-based test run is saved here — open one to review its summary, steps, and video."
                  : pageTab === "coverage"
                  ? "Requirements linked from UI and Functional Tests, with latest status and pass-rate history — see what's actually tested."
                  : "Recurring tests saved as replayable skills — rerun them without burning AI planning tokens."}
              </p>
            </div>
            <PageTabBar active={pageTab} onChange={setPageTab} />
            {pageTab === "results" ? (
              <ResultsTab />
            ) : pageTab === "coverage" ? (
              <CoverageTab />
            ) : (
              <SkillsTab onReplayStarted={handleReplayStarted} />
            )}
          </PageContainer>
        </div>
      </AppShell>
    );
  }

  // ── State 1: Prompt ───────────────────────────────────────────────────────

  if (uiState === "idle") {
    return (
      <AppShell noPadding>
        <div className="min-h-full bg-gray-50">
          <PageContainer>
            <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-3xl font-bold text-gray-900">Vibe Testing</h1>
                <p className="text-gray-500 mt-1">
                  Describe a goal, or bring a design — the AI drives the
                  browser or app.
                </p>
              </div>
              <div className="flex flex-shrink-0 overflow-hidden rounded-md border border-gray-200">
                <button
                  type="button"
                  onClick={() => setTestType("web")}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${
                    testType === "web"
                      ? "bg-gray-900 text-white"
                      : "bg-white text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  Web app testing
                </button>
                <button
                  type="button"
                  onClick={() => setTestType("android")}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${
                    testType === "android"
                      ? "bg-gray-900 text-white"
                      : "bg-white text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  Android app testing
                </button>
              </div>
            </div>

            <PageTabBar active={pageTab} onChange={setPageTab} />

            {testType === "android" ? (
              <AndroidNewTestPanel onRunStarted={handleReplayStarted} />
            ) : (
              <>
                <ModeSelector mode={testMode} onModeChange={setTestMode} />

                {/* UI Test — VisualAuditSection is self-contained (its own
                    reference upload/Figma-imported picker, target URL, run,
                    poll, and findings display) and doesn't participate in
                    the goal/runId/SSE state machine below at all — same
                    pattern as AutonomousQASection. */}
                <div className={testMode === "ui" ? "" : "hidden"}>
                  <VisualAuditSection />
                </div>

                <div className={testMode === "functional" ? "" : "hidden"}>
                  <Card className="shadow-sm">
              <CardHeader className="pb-4">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-lg">Functional Test</CardTitle>
                    <p className="text-sm text-gray-500 mt-1">
                      Author preconditions, ordered steps, and expected
                      results — the AI drives the browser and checks each one.
                    </p>
                  </div>
                  <Badge
                    variant="outline"
                    className="text-green-600 border-green-300 bg-green-50 gap-1.5 flex-shrink-0"
                  >
                    <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
                    Agent ready
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <FunctionalTestFields key={functionalFormKey} onChange={setFunctionalPayload} />

                <div className="flex gap-3 flex-wrap items-start">
                  {/* Website without/with login toggle now leads the row,
                      ahead of the Environment picker. */}
                  {hasEnvironmentChoice && !isIgAutomation && (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setLoginMode("without_login")}
                        className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                          loginMode === "without_login"
                            ? "border-gray-900 bg-gray-900 text-white"
                            : "border-gray-200 text-gray-600 hover:bg-gray-100"
                        }`}
                      >
                        Website without login
                      </button>
                      <button
                        type="button"
                        onClick={() => setLoginMode("with_login")}
                        className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                          loginMode === "with_login"
                            ? "border-gray-900 bg-gray-900 text-white"
                            : "border-gray-200 text-gray-600 hover:bg-gray-100"
                        }`}
                      >
                        Website with login
                      </button>
                    </div>
                  )}

                  {envLoading ? (
                    <Skeleton className="h-9 w-36 rounded-md" />
                  ) : (
                    <Select
                      value={projectId}
                      onValueChange={(v) => setProjectId(v ?? "")}
                      items={[
                        { value: NO_ENVIRONMENT_VALUE, label: "No Environment" },
                        ...environments.map((env) => ({ value: env.id, label: env.name })),
                      ]}
                    >
                      <SelectTrigger className="w-auto min-w-[150px] h-9 text-sm">
                        <svg
                          className="w-4 h-4 text-gray-400 mr-1.5"
                          viewBox="0 0 16 16"
                          fill="none"
                        >
                          <circle
                            cx="8"
                            cy="8"
                            r="6.5"
                            stroke="currentColor"
                            strokeWidth="1.25"
                          />
                          <path
                            d="M8 4.5v3.75L10.25 10"
                            stroke="currentColor"
                            strokeWidth="1.25"
                            strokeLinecap="round"
                          />
                        </svg>
                        <SelectValue placeholder="Environment" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NO_ENVIRONMENT_VALUE}>
                          No Environment
                        </SelectItem>
                        {environments.map((env) => (
                          <SelectItem key={env.id} value={env.id}>
                            {env.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}

                  {!hasEnvironmentChoice ? (
                    <div className="flex items-center h-9 px-3 rounded-md border border-dashed border-gray-200 text-xs text-gray-400">
                      Select an Environment first
                    </div>
                  ) : isIgAutomation ? (
                    profilesLoading ? (
                      <Skeleton className="h-9 w-36 rounded-md" />
                    ) : (
                      <div className="flex items-center gap-2">
                        <Select
                          value={profileId}
                          onValueChange={(v) => setProfileId(v ?? "")}
                          items={profiles.map((p) => ({ value: p.id, label: p.name }))}
                        >
                          <SelectTrigger className="w-auto min-w-[150px] h-9 text-sm">
                            <svg
                              className="w-4 h-4 text-gray-400 mr-1.5"
                              viewBox="0 0 16 16"
                              fill="none"
                            >
                              <circle
                                cx="8"
                                cy="5.5"
                                r="2.5"
                                stroke="currentColor"
                                strokeWidth="1.25"
                              />
                              <path
                                d="M2.5 13c0-2.485 2.462-4.5 5.5-4.5s5.5 2.015 5.5 4.5"
                                stroke="currentColor"
                                strokeWidth="1.25"
                                strokeLinecap="round"
                              />
                            </svg>
                            <SelectValue placeholder="Credential Profile" />
                          </SelectTrigger>
                          <SelectContent>
                            {profiles.map((p) => (
                              <SelectItem key={p.id} value={p.id}>
                                {p.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        {isIgAutomation && (
                          <button
                            type="button"
                            onClick={() => setBypassDialogOpen(true)}
                            className="text-xs text-blue-600 hover:underline whitespace-nowrap"
                          >
                            + Create bypass profile
                          </button>
                        )}
                      </div>
                    )
                  ) : null}
                </div>

                {hasEnvironmentChoice && !isIgAutomation && loginMode !== "none" && (
                  <div className="flex gap-2 flex-wrap sm:flex-nowrap">
                    <input
                      value={adhocUrl}
                      onChange={(e) => setAdhocUrl(e.target.value)}
                      placeholder="URL, e.g. https://example.com"
                      className="flex-1 min-w-[200px] rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                    />
                    {loginMode === "with_login" && (
                      <>
                        <input
                          value={adhocLoginId}
                          onChange={(e) => setAdhocLoginId(e.target.value)}
                          placeholder="Email or phone"
                          className="flex-1 min-w-[160px] rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                        />
                        <input
                          type={showAdhocPassword ? "text" : "password"}
                          value={adhocPassword}
                          onChange={(e) => setAdhocPassword(e.target.value)}
                          placeholder="Password"
                          className="flex-1 min-w-[160px] rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                        />
                        <PasswordRevealSwitch
                          revealed={showAdhocPassword}
                          onRevealedChange={setShowAdhocPassword}
                        />
                      </>
                    )}
                  </div>
                )}

                {submitError && (
                  <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
                    {submitError}
                  </p>
                )}

                <div className="pt-1">
                  <Button
                    variant="invert"
                    onClick={handleSubmit}
                    disabled={!functionalPayload?.isValid || !adhocValid || submitting}
                    className="w-full h-11 text-base font-medium"
                  >
                    <svg
                      className="w-5 h-5 mr-2"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                    >
                      <path
                        fillRule="evenodd"
                        d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"
                        clipRule="evenodd"
                      />
                    </svg>
                    {submitting ? "Starting…" : "Run Test"}
                  </Button>
                  {!functionalPayload?.isValid ? (
                    <p className="text-xs text-gray-400 text-center mt-2">
                      Add at least one step and one expected result to enable
                    </p>
                  ) : !hasEnvironmentChoice ? (
                    <p className="text-xs text-gray-400 text-center mt-2">
                      Select an Environment (or “No Environment”) to enable
                    </p>
                  ) : !adhocValid ? (
                    <p className="text-xs text-gray-400 text-center mt-2">
                      {loginMode === "none"
                        ? "Choose “Website without login” or “Website with login” to enable"
                        : "Fill in the required fields above to enable"}
                    </p>
                  ) : null}
                </div>
              </CardContent>
            </Card>

                </div>

                {/* AutonomousQASection remains feature-detected server-side
                    and renders null when its backend flag is off. */}
                <div className={testMode === "visual" ? "" : "hidden"}>
                  <AutonomousQASection />
                </div>
              </>
            )}
          </PageContainer>

          <div className="sticky bottom-0 left-0 right-0 border-t border-gray-100 bg-white px-6 py-3 flex items-center justify-between text-xs text-gray-400">
            <div className="flex items-center gap-2">
              <svg
                className="w-4 h-4"
                viewBox="0 0 16 16"
                fill="none"
              >
                <path
                  d="M8 2l1.5 3L13 5.5 10.5 8l.5 3.5L8 10 5 11.5l.5-3.5L3 5.5 6.5 5 8 2z"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  strokeLinejoin="round"
                />
              </svg>
              AI-driven · no test script required · pixel-diff accuracy
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-green-500" />
              AGENT READY
            </div>
          </div>
        </div>

        {bypassDialogOpen && (
          <CreateBypassProfileDialog
            projectId={projectId}
            onClose={() => setBypassDialogOpen(false)}
            onCreated={(profile) => {
              refetchProfiles();
              setProfileId(profile.id);
            }}
          />
        )}
      </AppShell>
    );
  }

  // ── State 2: Live Run ─────────────────────────────────────────────────────

  if (uiState === "running") {
    const selectedEnv = environments.find((e) => e.id === projectId);

    return (
      <AppShell noPadding>
        <div className="flex flex-col h-full bg-white overflow-hidden">
          {/* Goal header bar */}
          <div className="flex items-center justify-between px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0">
            <div className="flex items-center gap-2 text-sm overflow-hidden min-w-0">
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide flex-shrink-0">
                GOAL
              </span>
              <span className="text-gray-700 truncate">{goal}</span>
            </div>
            <button
              onClick={handleStop}
              className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-900 px-3 py-1.5 rounded-md hover:bg-gray-100 transition-colors flex-shrink-0 ml-4"
            >
              <span className="w-3 h-3 bg-gray-600 rounded-sm inline-block" />
              Stop Test
            </button>
          </div>

          {/* Two-panel layout */}
          <div className="flex flex-1 overflow-hidden">
            {/* Left: Action log (~38%) */}
            <div className="flex flex-col border-r border-gray-100 overflow-hidden flex-shrink-0 w-[38%]">
              {/* Log header */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100 flex-shrink-0">
                <span className="font-semibold text-gray-900 text-sm">
                  Live Action Log
                </span>
                <div className="flex items-center gap-1.5 text-xs text-green-600">
                  <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                  Live
                </div>
              </div>

              {/* Step counter + timer */}
              <div className="px-5 py-2 border-b border-gray-50 flex-shrink-0 flex items-center gap-4 text-xs text-gray-500">
                <span className="font-medium text-gray-700">
                  Step {events.length}
                </span>
                <span className="flex items-center gap-1">
                  <svg
                    className="w-3.5 h-3.5"
                    viewBox="0 0 14 14"
                    fill="none"
                  >
                    <circle
                      cx="7"
                      cy="7"
                      r="5.5"
                      stroke="currentColor"
                      strokeWidth="1.25"
                    />
                    <path
                      d="M7 4v3l2 1.5"
                      stroke="currentColor"
                      strokeWidth="1.25"
                      strokeLinecap="round"
                    />
                  </svg>
                  elapsed {formatElapsed(elapsedMs)}
                </span>
              </div>

              {/* Log entries */}
              <div ref={logRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-1.5">
                {events.length === 0 ? (
                  <div className="text-sm text-gray-400 text-center mt-10">
                    Initialising…
                  </div>
                ) : (
                  events.map((event) => (
                    <div
                      key={event.sequence}
                      className={`flex items-center gap-3 py-2 px-3 rounded-lg text-sm ${
                        event.status === "running"
                          ? "bg-blue-50 border border-blue-100"
                          : event.is_failing_step
                          ? "bg-red-50 border border-red-100"
                          : ""
                      }`}
                    >
                      <StepIcon status={event.status} />
                      <span
                        className={`flex-1 min-w-0 truncate ${
                          event.status === "running"
                            ? "text-gray-900 font-medium"
                            : "text-gray-600"
                        }`}
                      >
                        {event.description}
                      </span>
                      {(() => {
                        // While a step is still running, show a live-ticking
                        // elapsed time (re-rendered every 500ms by the same
                        // interval that drives the overall run timer above)
                        // instead of the static snapshot from when the step
                        // started — otherwise a step that's genuinely taking
                        // a while looks frozen/stuck rather than in progress.
                        const stepStart = stepStartRef.current.get(event.sequence);
                        const displayMs =
                          event.status === "running" && stepStart != null
                            ? Date.now() - stepStart
                            : event.elapsed_ms;
                        return displayMs != null ? (
                          <span className="text-gray-400 text-xs flex-shrink-0">
                            {formatElapsed(displayMs)}
                          </span>
                        ) : null;
                      })()}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Right: Browser frame (~62%) */}
            <div className="flex-1 flex flex-col p-5 overflow-hidden">
              <div className="flex flex-col rounded-xl border border-gray-200 overflow-hidden flex-1 shadow-sm">
                {/* Browser chrome */}
                <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex-shrink-0">
                  <span className="w-3 h-3 rounded-full bg-gray-300" />
                  <span className="w-3 h-3 rounded-full bg-gray-300" />
                  <span className="w-3 h-3 rounded-full bg-gray-300" />
                  <div className="flex-1 flex items-center bg-white rounded border border-gray-200 px-3 py-1 text-xs text-gray-500 ml-2">
                    <svg
                      className="w-3 h-3 mr-1.5 text-gray-400"
                      viewBox="0 0 12 12"
                      fill="none"
                    >
                      <rect
                        x="1"
                        y="1.5"
                        width="10"
                        height="9"
                        rx="1.5"
                        stroke="currentColor"
                        strokeWidth="1"
                      />
                      <path
                        d="M1 4.5h10"
                        stroke="currentColor"
                        strokeWidth="1"
                      />
                    </svg>
                    {selectedEnv
                      ? selectedEnv.name
                      : projectId === NO_ENVIRONMENT_VALUE
                      ? "No Environment"
                      : "Application under test"}
                  </div>
                </div>

                {/* Live browser view */}
                <div className="flex-1 overflow-auto bg-gray-100 flex items-start justify-center p-2">
                  {runId ? (
                    <LiveBrowserView runId={runId} />
                  ) : (
                    <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                      Connecting to live browser…
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  // ── State 3: Result ───────────────────────────────────────────────────────

  const result = runResult;
  if (!result) {
    return (
      <AppShell noPadding>
        <div className="p-8 flex items-center gap-3 text-gray-400">
          <span className="w-5 h-5 rounded-full border-2 border-gray-300 border-t-transparent animate-spin inline-block" />
          Loading result…
        </div>
      </AppShell>
    );
  }

  const isPassed = result.status === "passed";
  const isFailed = result.status === "failed";
  const isInconclusive =
    result.status === "inconclusive" || result.status === "cancelled";
  // New Vibe Test Phase 4 (D.15) — the agent self-reported "passed" but the
  // independent GEval score came back below threshold. Distinct from all
  // three states above: not a pass (disputed), not a failure (the agent did
  // complete something), not the existing "stopped/timed out before a
  // definitive outcome" inconclusive banner either — a human needs to look.
  const isNeedsReview = result.status === "needs_review";

  return (
    <AppShell noPadding>
      <div className="min-h-full bg-gray-50">
        {/* Goal bar */}
        <div className="border-b border-gray-100 bg-white px-6 py-3 text-sm">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide mr-2">
            GOAL
          </span>
          <span className="text-gray-700">{result.goal}</span>
        </div>

        <PageContainer>
          <div className="space-y-6">
          <PageTabBar active={pageTab} onChange={setPageTab} />

          {/* Status banner */}
          <div
            className={`rounded-xl px-8 py-6 flex items-center justify-between ${
              isPassed
                ? "bg-green-600"
                : isFailed
                ? "bg-red-600"
                : isNeedsReview
                ? "bg-purple-600"
                : "bg-amber-500"
            }`}
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-white/20 flex items-center justify-center flex-shrink-0">
                {isPassed && (
                  <svg
                    className="w-7 h-7 text-white"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <path
                      d="M5 12l5 5L19 7"
                      stroke="currentColor"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
                {isFailed && (
                  <svg
                    className="w-7 h-7 text-white"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      cx="12"
                      cy="12"
                      r="9"
                      stroke="currentColor"
                      strokeWidth="2"
                    />
                    <path
                      d="M8 8l8 8M16 8l-8 8"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                )}
                {isInconclusive && (
                  <svg
                    className="w-7 h-7 text-white"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      cx="12"
                      cy="12"
                      r="9"
                      stroke="currentColor"
                      strokeWidth="2"
                    />
                    <path
                      d="M12 8v5M12 16v.5"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                )}
                {isNeedsReview && (
                  <svg
                    className="w-7 h-7 text-white"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      cx="12"
                      cy="12"
                      r="9"
                      stroke="currentColor"
                      strokeWidth="2"
                    />
                    <path
                      d="M9.5 9a2.5 2.5 0 013.9-2.1c.9.6 1.3 1.8.7 2.9-.5 1-1.6 1.2-2.1 2.2v.5"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <circle cx="12" cy="16.5" r="1" fill="currentColor" />
                  </svg>
                )}
              </div>
              <span className="text-3xl font-bold tracking-wide text-white">
                {isPassed
                  ? "PASSED"
                  : isFailed
                  ? "FAILED"
                  : isNeedsReview
                  ? "NEEDS REVIEW"
                  : "INCONCLUSIVE"}
              </span>
            </div>
            <div className="text-right space-y-1">
              <div className="text-xs font-semibold text-white/60 uppercase tracking-wide">
                DURATION
              </div>
              <div className="text-white font-semibold">
                {formatDuration(result.duration_ms)}
              </div>
              <div className="text-xs font-semibold text-white/60 uppercase tracking-wide mt-2">
                STEPS
              </div>
              <div className="text-white font-semibold">{result.step_count}</div>
            </div>
          </div>

          {/* Failing step card. Shown for Failed runs and (2026-07-28) for
              needs_review runs that carry failing-step evidence — an
              application error detected mid-run populates the same fields
              (see ai_execution._persist_result's app-error gate), and that
              evidence plus the Log Defect action is the whole point of
              catching it. Colour follows the run status so a needs_review
              run never renders as if it had hard-failed. */}
          {(isFailed || isNeedsReview) && result.failing_step_description && (
            <Card
              className={
                isFailed
                  ? "border-red-200 bg-red-50"
                  : "border-purple-200 bg-purple-50"
              }
            >
              <CardContent className="pt-5 pb-4 flex items-start gap-4">
                <div
                  className={`flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center font-bold text-sm ${
                    isFailed
                      ? "bg-red-100 text-red-600"
                      : "bg-purple-100 text-purple-600"
                  }`}
                >
                  {result.failing_step_index ?? "!"}
                </div>
                <div className="flex-1 min-w-0">
                  <div
                    className={`text-xs font-semibold uppercase tracking-wide mb-1 ${
                      isFailed ? "text-red-500" : "text-purple-500"
                    }`}
                  >
                    {isFailed ? "FAILING STEP" : "PROBLEM DETECTED"}
                  </div>
                  <p className="text-gray-800 font-medium">
                    {result.failing_step_description}
                  </p>
                </div>
                {result.failing_step_screenshot_url && (
                  <div
                    className={`flex-shrink-0 w-28 h-20 rounded border overflow-hidden ${
                      isFailed ? "border-red-200" : "border-purple-200"
                    }`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={result.failing_step_screenshot_url}
                      alt="Failing step"
                      className="w-full h-full object-cover"
                    />
                  </div>
                )}
                <Button
                  onClick={handleOpenLogDefect}
                  disabled={defectLogged}
                  variant="outline"
                  className={`flex-shrink-0 gap-2 ${
                    isFailed
                      ? "border-red-300 text-red-700 hover:bg-red-100"
                      : "border-purple-300 text-purple-700 hover:bg-purple-100"
                  }`}
                >
                  <svg
                    className="w-4 h-4"
                    viewBox="0 0 16 16"
                    fill="none"
                  >
                    <path
                      d="M8 2v4M8 2H5a1 1 0 00-1 1v10a1 1 0 001 1h6a1 1 0 001-1V6M8 2l3 4"
                      stroke="currentColor"
                      strokeWidth="1.25"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  {defectLogged ? "Defect Logged ✓" : "Log Defect"}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Needs-review notice (New Vibe Test Phase 4, D.15) — the agent
              self-reported "passed" but the independent GEval score came
              back below threshold. Surfaces the score/reason right here so
              it's not just a banner with no explanation. */}
          {isNeedsReview && (
            <Card className="border-purple-200 bg-purple-50">
              <CardContent className="pt-5 pb-4 flex items-start gap-3">
                <svg
                  className="w-5 h-5 text-purple-600 flex-shrink-0 mt-0.5"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                    clipRule="evenodd"
                  />
                </svg>
                <div>
                  {/* Copy is cause-neutral (2026-07-28): needs_review is now
                      reached by two independent gates — a low GEval score, OR
                      an application error observed on the page during the run
                      — so this heading can no longer assume the quality score
                      was the trigger. eval_reason carries the concrete cause
                      either way. */}
                  <p className="text-sm font-medium text-purple-800">
                    Agent reported success, but this run needs human review
                    {result.eval_score != null &&
                      ` (quality score: ${Math.round(result.eval_score * 100)}%)`}
                  </p>
                  <p className="text-sm text-purple-700 mt-0.5">
                    {result.eval_reason ||
                      "An independent check disputed this run's result. Review the steps and video before trusting this as a pass."}
                  </p>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Inconclusive notice */}
          {isInconclusive && (
            <Card className="border-amber-200 bg-amber-50">
              <CardContent className="pt-5 pb-4 flex items-start gap-3">
                <svg
                  className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                    clipRule="evenodd"
                  />
                </svg>
                <div>
                  <p className="text-sm font-medium text-amber-800">
                    Test could not complete
                  </p>
                  <p className="text-sm text-amber-700 mt-0.5">
                    {result.summary ||
                      "The test was stopped or timed out before reaching a definitive outcome."}
                  </p>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Tabs */}
          <div>
            <div className="flex border-b border-gray-200 gap-6">
              {(["summary", "steps", "video"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`pb-3 text-sm font-medium border-b-2 -mb-px transition-colors capitalize ${
                    activeTab === tab
                      ? "border-gray-900 text-gray-900"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            <div className="pt-6">
              {/* Summary */}
              {activeTab === "summary" && (
                <div className="space-y-4">
                  {result.summary && (
                    <div className="flex gap-3 text-sm text-gray-600 bg-white border border-gray-100 rounded-lg px-4 py-3">
                      <svg
                        className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5"
                        viewBox="0 0 16 16"
                        fill="currentColor"
                      >
                        <path
                          fillRule="evenodd"
                          d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 5.5a.75.75 0 01.75.75V11a.75.75 0 01-1.5 0V7.25A.75.75 0 018 6.5zm0-2.25a1 1 0 110 2 1 1 0 010-2z"
                          clipRule="evenodd"
                        />
                      </svg>
                      <p className="whitespace-pre-line">{result.summary}</p>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-4">
                    <Card>
                      <CardContent className="pt-4 pb-4">
                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
                          GOAL
                        </div>
                        <p className="text-sm text-gray-800">{result.goal}</p>
                      </CardContent>
                    </Card>
                    <Card>
                      <CardContent className="pt-4 pb-4">
                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
                          ENVIRONMENT
                        </div>
                        <p className="text-sm text-gray-800">
                          {result.environment || "Custom"}
                        </p>
                      </CardContent>
                    </Card>
                    <Card>
                      <CardContent className="pt-4 pb-4">
                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
                          TIMESTAMP
                        </div>
                        <p className="text-sm text-gray-800">
                          {result.created_at
                            ? new Date(result.created_at).toLocaleString(
                                "en-GB",
                                { hour12: false }
                              ) + " UTC"
                            : "—"}
                        </p>
                      </CardContent>
                    </Card>
                  </div>
                </div>
              )}

              {/* Steps */}
              {activeTab === "steps" && (
                <div className="space-y-2">
                  {result.events.length === 0 ? (
                    <p className="text-sm text-gray-400 text-center py-10">
                      No steps recorded for this run.
                    </p>
                  ) : (
                    result.events.map((event) => (
                      <StepRow key={event.sequence} event={event} />
                    ))
                  )}
                </div>
              )}

              {/* Video */}
              {activeTab === "video" && (
                <VideoPane runId={result.run_id} videoAvailable={result.video_available} />
              )}
            </div>
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between pt-4 border-t border-gray-100">
            <div className="flex items-center gap-3">
              {!isInconclusive && (
                <button
                  onClick={handleEditGoal}
                  className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800 transition-colors"
                >
                  <svg
                    className="w-4 h-4"
                    viewBox="0 0 16 16"
                    fill="none"
                  >
                    <path
                      d="M11 2l3 3L5 14H2v-3L11 2z"
                      stroke="currentColor"
                      strokeWidth="1.25"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Edit Goal
                </button>
              )}
              {isInconclusive && (
                <>
                  <Button
                    onClick={handleRerun}
                    disabled={submitting}
                    variant="outline"
                    className="gap-2"
                  >
                    <svg
                      className="w-4 h-4"
                      viewBox="0 0 16 16"
                      fill="none"
                    >
                      <path
                        d="M2.5 8a5.5 5.5 0 015.5-5.5A5.5 5.5 0 0113.5 8"
                        stroke="currentColor"
                        strokeWidth="1.25"
                        strokeLinecap="round"
                      />
                      <path
                        d="M2.5 8l2-2.5M2.5 8l2 2"
                        stroke="currentColor"
                        strokeWidth="1.25"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    {submitting ? "Rerunning…" : "Rerun"}
                  </Button>
                  <button
                    onClick={handleEditGoal}
                    className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800 transition-colors"
                  >
                    <svg
                      className="w-4 h-4"
                      viewBox="0 0 16 16"
                      fill="none"
                    >
                      <path
                        d="M11 2l3 3L5 14H2v-3L11 2z"
                        stroke="currentColor"
                        strokeWidth="1.25"
                        strokeLinejoin="round"
                      />
                    </svg>
                    Edit Goal
                  </button>
                </>
              )}
            </div>
            <Button onClick={handleRunAnother} className="gap-2">
              <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
                <path
                  d="M13.5 8a5.5 5.5 0 01-5.5 5.5A5.5 5.5 0 012.5 8"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  strokeLinecap="round"
                />
                <path
                  d="M13.5 8l-2-2.5M13.5 8l-2 2"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Run Another Test
            </Button>
          </div>
          </div>
        </PageContainer>

        {/* Log Defect modal */}
        {loggingDefect && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg">
              <div className="px-6 py-4 border-b border-gray-100">
                <h2 className="text-lg font-semibold text-gray-900">
                  Log Defect
                </h2>
                <p className="text-sm text-gray-500 mt-0.5">
                  Pre-filled from the failing AI test step
                </p>
              </div>
              <div className="px-6 py-4 space-y-4">
                <div>
                  <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
                    Title
                  </label>
                  <input
                    value={defectTitle}
                    onChange={(e) => setDefectTitle(e.target.value)}
                    className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
                    Description
                  </label>
                  <textarea
                    value={defectDescription}
                    onChange={(e) => setDefectDescription(e.target.value)}
                    rows={6}
                    className="w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                  />
                </div>
              </div>
              <div className="px-6 py-4 border-t border-gray-100 flex gap-3 justify-end">
                <Button
                  variant="outline"
                  onClick={() => setLoggingDefect(false)}
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleSubmitDefect}
                  disabled={!defectTitle.trim()}
                >
                  Log Defect
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
