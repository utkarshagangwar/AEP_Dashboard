"use client";

/**
 * Detail view for a "ui_test" run (New Vibe Test Phase 5, E.22) — a
 * VisualRun (visual_runs table), fetched directly from
 * GET /api/v1/visual-audits/{id} rather than forced through AIRunResponse's
 * shape (findings/reference-screenshot-diff images have no equivalent on a
 * plain AITestRun). Mirrors VisualAuditSection's own inline "Run result"
 * panel — same status colors, image tabs, findings table — so a UI Test
 * looks the same whether you're viewing it live (just submitted) or from
 * the Results tab history, same design goal OrchestratorRunDetail already
 * follows for Autonomous QA runs.
 */

import { useState } from "react";
import { AuthImage, ColorSwatch, formatDuration } from "./shared";
import { Badge } from "@/components/ui/badge";

interface Finding {
  engine: "pixel_diff" | "vision";
  severity: "critical" | "major" | "minor" | "info";
  element?: string | null;
  issue: string;
  expected?: string | null;
  actual?: string | null;
}

export interface VisualTestRunResult {
  id: string;
  target_url: string;
  artifact_id?: string | null;
  environment?: string | null;
  status: string;
  pixel_mismatch_pct?: number | null;
  summary?: string | null;
  error_message?: string | null;
  duration_ms?: number | null;
  linked_requirement?: string | null;
  created_at: string;
  findings: Finding[];
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: "text-red-700 border-red-300 bg-red-50",
  major: "text-orange-700 border-orange-300 bg-orange-50",
  minor: "text-yellow-700 border-yellow-300 bg-yellow-50",
  info: "text-gray-600 border-gray-300 bg-gray-50",
};

const TERMINAL = new Set(["passed", "failed", "partial", "error", "cancelled"]);

export default function VisualTestRunDetail({
  result,
}: {
  result: VisualTestRunResult;
}) {
  const [imageTab, setImageTab] = useState<"reference" | "screenshot" | "diff">("diff");
  const isDone = TERMINAL.has(result.status);
  const isPassed = result.status === "passed";
  const isFailed = result.status === "failed" || result.status === "error";

  return (
    <div className="space-y-6">
      {/* Status banner */}
      <div
        className={`rounded-xl px-8 py-6 flex items-center justify-between ${
          isPassed ? "bg-green-600" : isFailed ? "bg-red-600" : "bg-amber-500"
        }`}
      >
        <div className="flex items-center gap-4">
          <span className="text-3xl font-bold tracking-wide text-white">
            {result.status.toUpperCase()}
          </span>
          <span className="text-xs font-semibold text-white/80 bg-white/20 rounded-full px-3 py-1 uppercase tracking-wide">
            UI Test
          </span>
        </div>
        <div className="text-right space-y-1">
          {typeof result.pixel_mismatch_pct === "number" && (
            <>
              <div className="text-xs font-semibold text-white/60 uppercase tracking-wide">
                PIXEL MISMATCH
              </div>
              <div className="text-white font-semibold">
                {result.pixel_mismatch_pct}%
              </div>
            </>
          )}
          <div className="text-xs font-semibold text-white/60 uppercase tracking-wide mt-2">
            DURATION
          </div>
          <div className="text-white font-semibold">
            {formatDuration(result.duration_ms)}
          </div>
        </div>
      </div>

      {/* Target URL / environment / linked requirement */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-gray-200 bg-white px-4 py-4">
          <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
            TARGET URL
          </div>
          <p className="text-sm text-gray-800 break-all">{result.target_url}</p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white px-4 py-4">
          <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
            ENVIRONMENT
          </div>
          <p className="text-sm text-gray-800">{result.environment || "Custom"}</p>
        </div>
        {result.linked_requirement && (
          <div className="rounded-xl border border-gray-200 bg-white px-4 py-4">
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
              LINKED REQUIREMENT
            </div>
            <p className="text-sm text-gray-800">{result.linked_requirement}</p>
          </div>
        )}
      </div>

      {result.summary && <p className="text-sm text-gray-600">{result.summary}</p>}
      {result.error_message && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
          {result.error_message}
        </p>
      )}

      {isDone && result.status !== "error" && result.status !== "cancelled" && (
        <>
          {/* Image tabs */}
          <div className="flex gap-2">
            {(["reference", "screenshot", "diff"] as const).map((kind) => (
              <button
                key={kind}
                onClick={() => setImageTab(kind)}
                className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                  imageTab === kind
                    ? "border-gray-900 bg-gray-900 text-white"
                    : "border-gray-200 text-gray-600 hover:bg-gray-100"
                }`}
              >
                {kind === "diff" ? "Diff overlay" : kind}
              </button>
            ))}
          </div>
          <AuthImage runId={result.id} kind={imageTab} />

          {/* Findings table */}
          {result.findings.length > 0 ? (
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                  <tr>
                    <th className="text-left px-3 py-2">Severity</th>
                    <th className="text-left px-3 py-2">Engine</th>
                    <th className="text-left px-3 py-2">Issue</th>
                    <th className="text-left px-3 py-2">Expected</th>
                    <th className="text-left px-3 py-2">Actual</th>
                  </tr>
                </thead>
                <tbody>
                  {result.findings.map((f, i) => (
                    <tr key={i} className="border-t border-gray-100">
                      <td className="px-3 py-2">
                        <Badge variant="outline" className={SEVERITY_STYLES[f.severity] || ""}>
                          {f.severity}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-gray-500 text-xs">
                        {f.engine === "pixel_diff" ? "Pixel diff" : "AI vision"}
                      </td>
                      <td className="px-3 py-2 text-gray-700">
                        {f.element ? `${f.element}: ` : ""}
                        {f.issue}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-600">
                        {f.expected ? <ColorSwatch hex={f.expected} /> : "—"}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-600">
                        {f.actual ? <ColorSwatch hex={f.actual} /> : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-green-600">No discrepancies found above threshold.</p>
          )}
        </>
      )}
    </div>
  );
}
