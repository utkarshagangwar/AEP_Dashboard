"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";

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
  };
  return (
    <Badge
      variant="outline"
      className={`text-xs capitalize ${styles[status] || styles.pending}`}
    >
      {status}
    </Badge>
  );
}
