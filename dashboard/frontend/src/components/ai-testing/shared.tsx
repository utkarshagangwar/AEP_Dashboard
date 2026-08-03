"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { getAccessToken } from "@/lib/api";
import { apiFetch, refreshAccessToken } from "@/utils/apiClient";

// ── Shared goal-validation constants (Web Quick mode + Android New Test) ────

export const EXAMPLE_GOALS = [
  "Verify checkout flow as guest user",
  "Check admin can deactivate an account",
  "Confirm report export generates a CSV",
];

export const ACTION_WORDS = [
  "log", "verify", "check", "confirm", "test", "navigate", "click",
  "fill", "submit", "open", "close", "create", "delete", "login",
  "sign", "search", "filter", "export", "upload", "download",
];

export function isGoalValid(goal: string): boolean {
  const g = goal.trim().toLowerCase();
  return g.length >= 10 && ACTION_WORDS.some((w) => g.includes(w));
}

// ── Shared types for Vibe Testing (AI test runs) ─────────────────────────────

export interface RunEvent {
  sequence: number;
  status: "pending" | "running" | "passed" | "failed" | "inconclusive";
  description: string;
  step_type: "deterministic" | "ai_scoped";
  elapsed_ms?: number;
  screenshot_url?: string | null;
  highlighted_element?: {
    x_pct: number;
    y_pct: number;
    w_pct: number;
    h_pct: number;
    label: string;
  } | null;
  is_failing_step: boolean;
}

export interface CredentialProfile {
  id: string;
  name: string;
  // null/"standard" = plain username+password (today's only kind). "bypass"
  // = injects an auth cookie via an admin API-key login call instead of
  // typing into a login form — used to route around CAPTCHA-gated logins.
  kind?: "standard" | "bypass" | null;
  target_url?: string | null;
  project_id?: string | null;
  allowed_domains?: string[] | null;
}

export interface Skill {
  id: string;
  name: string;
  goal: string;
  project_id?: string | null;
  project_name?: string | null;
  environment?: string | null;
  source_type?: "sow" | "video" | null;
  // null/absent = fully specified by its source and runnable as written.
  // Set when the source document named this requirement without specifying
  // it well enough to execute; review_reason says what is missing.
  review_status?: "needs_review" | "needs_design_flow" | null;
  review_reason?: string | null;
  has_recording: boolean;
  manually_edited: boolean;
  step_count: number;
  times_replayed: number;
  last_replay_status?: string | null;
  last_replayed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrchestratorDecision {
  step: "hands" | "judge" | "self_execute";
  invoked: boolean;
  model_provider?: string | null;
  model_name?: string | null;
  is_deterministic: boolean;
  rationale: string;
  sequence: number;
}

export interface VisualFinding {
  engine: "pixel_diff" | "vision";
  severity: "critical" | "major" | "minor" | "info";
  element?: string | null;
  issue: string;
  expected?: string | null;
  actual?: string | null;
}

export interface RunResult {
  run_id: string;
  goal: string;
  environment?: string;
  credential_profile_name?: string;
  project_id?: string;
  status: string;
  duration_ms?: number;
  step_count: number;
  summary?: string;
  raw_summary?: string;
  run_type?: string;
  skill_id?: string;
  failing_step_index?: number;
  failing_step_description?: string;
  failing_step_screenshot_url?: string;
  // True once a full-session recording exists for this run (New Vibe Test
  // / Skill Replay only — see VideoPane below).
  video_available?: boolean;
  // Post-run DeepEval quality score (New Vibe Test / Skill Replay, web
  // platform only) — a second opinion on whether the agent's actions
  // actually accomplished the goal, independent of its own self-report.
  // Absent/null for every other run (Android, Autonomous QA, non-terminal
  // status, or scoring itself was unavailable).
  eval_score?: number | null;
  eval_reason?: string | null;
  eval_status?: string | null;
  // Second, complementary judge pass (New Vibe Test Phase 4) — final-state
  // screenshot vs. this run's own expected_results. Functional Test only.
  visual_eval_score?: number | null;
  visual_eval_reason?: string | null;
  visual_eval_status?: string | null;
  // Structured Functional Test fields (New Vibe Test Phase 1) — absent for
  // every run created via any other flow.
  test_category?: string | null;
  test_type?: string | null;
  linked_requirement?: string | null;
  events: RunEvent[];
  created_at?: string;
  // Autonomous QA (orchestrator) runs only:
  error_message?: string | null;
  ai_test_run_id?: string | null;
  visual_run_id?: string | null;
  self_execute_answer?: string | null;
  pixel_mismatch_pct?: number | null;
  decisions?: OrchestratorDecision[];
  findings?: VisualFinding[];
}

export const SEVERITY_STYLES: Record<string, string> = {
  critical: "text-red-700 border-red-300 bg-red-50",
  major: "text-orange-700 border-orange-300 bg-orange-50",
  minor: "text-yellow-700 border-yellow-300 bg-yellow-50",
  info: "text-gray-600 border-gray-300 bg-gray-50",
};

/** Small color chip next to a hex code — much easier to compare at a glance
 * than reading two hex strings side by side. */
export function ColorSwatch({ hex }: { hex?: string | null }) {
  if (!hex) return null;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block w-3 h-3 rounded-sm border border-gray-300"
        style={{ backgroundColor: hex }}
      />
      <span className="font-mono">{hex}</span>
    </span>
  );
}

export function FindingCard({ finding }: { finding: VisualFinding }) {
  return (
    <div className="rounded-md border border-gray-200 bg-white px-3 py-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge
          variant="outline"
          className={`text-xs ${SEVERITY_STYLES[finding.severity] || SEVERITY_STYLES.info}`}
        >
          {finding.severity}
        </Badge>
        <Badge variant="outline" className="text-xs text-gray-500">
          {finding.engine === "pixel_diff" ? "Pixel-diff" : "Vision"}
        </Badge>
        {finding.element && (
          <span className="text-xs text-gray-500">{finding.element}</span>
        )}
      </div>
      <p className="text-sm text-gray-700 mt-1">{finding.issue}</p>
      {(finding.expected || finding.actual) && (
        <div className="flex items-center gap-4 text-xs text-gray-500 mt-1.5">
          {finding.expected && (
            <span className="flex items-center gap-1">
              Expected: <ColorSwatch hex={finding.expected} />
            </span>
          )}
          {finding.actual && (
            <span className="flex items-center gap-1">
              Actual: <ColorSwatch hex={finding.actual} />
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Shared helpers ────────────────────────────────────────────────────────────

export function formatDuration(ms?: number | null): string {
  if (!ms) return "—";
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function formatElapsed(ms?: number): string {
  if (ms == null) return "—";
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// ── Shared sub-components ─────────────────────────────────────────────────────

export function StepIcon({ status }: { status: string }) {
  if (status === "running") {
    return (
      <span className="w-5 h-5 flex-shrink-0 rounded-full border-2 border-blue-500 border-t-transparent animate-spin inline-block" />
    );
  }
  if (status === "passed") {
    return (
      <span className="w-5 h-5 flex-shrink-0 rounded-full bg-green-500 flex items-center justify-center">
        <svg className="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
          <path
            d="M2 6l3 3 5-5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="w-5 h-5 flex-shrink-0 rounded-full bg-red-500 flex items-center justify-center">
        <svg className="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
          <path
            d="M3 3l6 6M9 3l-6 6"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </span>
    );
  }
  return (
    <span className="w-5 h-5 flex-shrink-0 rounded-full border-2 border-gray-300 bg-gray-50 inline-block" />
  );
}

export function ScreenshotPane({
  screenshotUrl,
  highlight,
}: {
  screenshotUrl?: string | null;
  highlight?: RunEvent["highlighted_element"] | null;
}) {
  if (!screenshotUrl) {
    return (
      <div className="flex-1 flex items-center justify-center bg-gray-100 text-gray-400 text-sm min-h-[200px] rounded-lg">
        Waiting for screenshot…
      </div>
    );
  }
  return (
    <div className="relative rounded-lg overflow-hidden border border-gray-200 bg-black">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={screenshotUrl}
        alt="Browser screenshot"
        className="w-full h-auto block"
      />
      {highlight && (
        <div
          className="absolute border-2 border-blue-400 bg-blue-400/10 rounded-sm"
          style={{
            left: `${highlight.x_pct}%`,
            top: `${highlight.y_pct}%`,
            width: `${highlight.w_pct}%`,
            height: `${highlight.h_pct}%`,
          }}
        >
          <span className="absolute -top-6 left-0 bg-blue-500 text-white text-xs px-1.5 py-0.5 rounded whitespace-nowrap shadow">
            {highlight.label}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Live view of the actual browser during a New Vibe Test / Skill Replay
 * run — an EventSource fed by the backend's CDP-screencast relay
 * (GET /ai-testing/runs/:id/live-frames), rendered as a continuously
 * updating <img> in the same visual slot ScreenshotPane used to occupy.
 * Runs that never opted into live capture (Autonomous QA, Android) simply
 * never publish frames — this component just never advances past
 * "Connecting…", which is fine since it's never mounted for those flows.
 */
export function LiveBrowserView({ runId }: { runId: string }) {
  const [frame, setFrame] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;

    (async () => {
      let token = getAccessToken();
      if (!token) {
        await refreshAccessToken();
        token = getAccessToken();
      }
      if (cancelled) return;

      es = new EventSource(
        `/api/ai-testing/runs/${runId}/live-frames?token=${encodeURIComponent(token || "")}`
      );

      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.jpg) setFrame(data.jpg);
        } catch {
          // ignore malformed frames
        }
      };
      es.onerror = () => es?.close();
    })();

    return () => {
      cancelled = true;
      es?.close();
    };
  }, [runId]);

  if (!frame) {
    return (
      <div className="flex-1 flex items-center justify-center bg-gray-100 text-gray-400 text-sm min-h-[200px] rounded-lg">
        Connecting to live browser…
      </div>
    );
  }
  return (
    <div className="relative rounded-lg overflow-hidden border border-gray-200 bg-black">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`data:image/jpeg;base64,${frame}`}
        alt="Live browser view"
        className="w-full h-auto block"
      />
    </div>
  );
}

/**
 * Full-session recording for a completed New Vibe Test / Skill Replay run
 * — an inline player plus a Download button. videoAvailable comes from the
 * run's video_available flag; when false (legacy run, or capture failed)
 * shows an empty state instead of a broken player.
 */
export function VideoPane({
  runId,
  videoAvailable,
}: {
  runId: string;
  videoAvailable?: boolean;
}) {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    if (!videoAvailable) return;
    let cancelled = false;
    (async () => {
      let t = getAccessToken();
      if (!t) {
        await refreshAccessToken();
        t = getAccessToken();
      }
      if (!cancelled) setToken(t);
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, videoAvailable]);

  if (!videoAvailable) {
    return (
      <div className="flex items-center justify-center bg-gray-100 text-gray-400 text-sm min-h-[200px] rounded-lg py-16">
        No video recorded for this run.
      </div>
    );
  }

  if (!token) {
    return (
      <div className="flex items-center justify-center bg-gray-100 text-gray-400 text-sm min-h-[200px] rounded-lg py-16">
        Loading video…
      </div>
    );
  }

  const videoUrl = `/api/ai-testing/runs/${runId}/video?token=${encodeURIComponent(token)}`;

  return (
    <div className="space-y-3">
      <div className="rounded-lg overflow-hidden border border-gray-200 bg-black">
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video controls src={videoUrl} className="w-full h-auto block max-h-[540px]" />
      </div>
      <div className="flex justify-end">
        <a
          href={videoUrl}
          download={`ai-test-${runId}.mp4`}
          className="inline-flex items-center gap-2 text-sm font-medium text-gray-700 border border-gray-200 rounded-md px-3 py-1.5 hover:bg-gray-50 transition-colors"
        >
          <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
            <path
              d="M8 2v8m0 0l-3-3m3 3l3-3M3 13h10"
              stroke="currentColor"
              strokeWidth="1.25"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Download video
        </a>
      </div>
    </div>
  );
}

export function StepRow({ event }: { event: RunEvent }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className={`rounded-lg border transition-colors ${
        event.is_failing_step
          ? "border-red-200 bg-red-50"
          : "border-gray-100 bg-white"
      }`}
    >
      <button
        onClick={() => setExpanded((x) => !x)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
      >
        <StepIcon status={event.status} />
        <span className="flex-1 text-sm text-gray-700 min-w-0">
          {event.description}
        </span>
        <Badge
          variant="outline"
          className={`text-xs flex-shrink-0 ${
            event.step_type === "ai_scoped"
              ? "border-purple-200 text-purple-600"
              : "border-gray-200 text-gray-500"
          }`}
        >
          {event.step_type === "ai_scoped" ? "AI" : "Script"}
        </Badge>
        {event.elapsed_ms != null && (
          <span className="text-xs text-gray-400 flex-shrink-0">
            {formatElapsed(event.elapsed_ms)}
          </span>
        )}
        {event.screenshot_url && (
          <svg
            className={`w-4 h-4 text-gray-400 flex-shrink-0 transition-transform ${
              expanded ? "rotate-180" : ""
            }`}
            viewBox="0 0 16 16"
            fill="none"
          >
            <path
              d="M4 6l4 4 4-4"
              stroke="currentColor"
              strokeWidth="1.25"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>
      {expanded && event.screenshot_url && (
        <div className="px-4 pb-4">
          <ScreenshotPane
            screenshotUrl={event.screenshot_url}
            highlight={event.highlighted_element}
          />
        </div>
      )}
    </div>
  );
}

export function RunStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    passed: "border-green-300 text-green-700 bg-green-50",
    failed: "border-red-300 text-red-700 bg-red-50",
    running: "border-blue-300 text-blue-700 bg-blue-50",
    pending: "border-gray-200 text-gray-500 bg-gray-50",
    inconclusive: "border-amber-300 text-amber-700 bg-amber-50",
    cancelled: "border-gray-300 text-gray-600 bg-gray-100",
    partial: "border-amber-300 text-amber-700 bg-amber-50",
    error: "border-red-300 text-red-700 bg-red-50",
    planning: "border-blue-300 text-blue-700 bg-blue-50",
    // New Vibe Test Phase 4 (D.15) — agent self-reported "passed" but the
    // independent GEval score came back below threshold; distinct from
    // both passed (green) and failed (red)/inconclusive (amber) since it's
    // neither — a human hasn't decided yet.
    needs_review: "border-purple-300 text-purple-700 bg-purple-50",
  };
  return (
    <Badge
      variant="outline"
      className={`text-xs capitalize ${styles[status] || styles.pending}`}
    >
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

/** Fetches a Visual Audit / UI Test run image with the JWT (a plain <img
 * src> can't send the Authorization header). Shared by VisualAuditSection
 * (the live/just-submitted run) and VisualTestRunDetail (New Vibe Test
 * Phase 5 — the same run viewed later from the Results tab history) so a
 * UI Test's screenshots look identical whether viewed live or from
 * history. Moved here (was duplicated only in VisualAuditSection before
 * Phase 5) rather than kept as two copies. */
export function AuthImage({
  runId,
  kind,
}: {
  runId: string;
  kind: "reference" | "screenshot" | "diff";
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setSrc(null);
    setFailed(false);
    (async () => {
      try {
        const res = await apiFetch(`/api/v1/visual-audits/${runId}/images/${kind}`);
        if (!res.ok) throw new Error("image unavailable");
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setSrc(objectUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [runId, kind]);

  if (failed)
    return <p className="text-xs text-gray-400">Image not available for this run.</p>;
  if (!src)
    return <div className="h-48 bg-gray-100 rounded-md animate-pulse" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={`${kind} image`}
      className="w-full border border-gray-200 rounded-md"
    />
  );
}
