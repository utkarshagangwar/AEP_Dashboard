"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { FindingCard, RunResult, formatDuration } from "./shared";

const STEP_LABELS: Record<string, string> = {
  hands: "Hands (browser agent)",
  judge: "Judge (visual audit)",
  self_execute: "Answered directly",
};

/**
 * Detail view for an "autonomous_qa" run (New Autonomous Visual QA Run /
 * orchestrator) — a different shape from a plain AITestRun, so it gets its
 * own layout instead of forcing it through RunDetail's steps/screenshots
 * tabs. Mirrors the inline "Run result" panel on the New Test tab so the
 * same run looks the same whether you're viewing it live or from history.
 */
export default function OrchestratorRunDetail({
  result,
  onNavigateToRun,
}: {
  result: RunResult;
  onNavigateToRun?: (runId: string) => void;
}) {
  const isPassed = result.status === "passed";
  const isFailed = result.status === "failed" || result.status === "error";
  const isOther = !isPassed && !isFailed;

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
            Autonomous QA
          </span>
        </div>
        <div className="text-right space-y-1">
          {result.pixel_mismatch_pct != null && (
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

      {/* Goal / environment */}
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
      </div>

      {/* What happened, in plain language */}
      {(result.summary || result.self_execute_answer || result.error_message) && (
        <Card className={isOther ? "border-amber-200 bg-amber-50" : undefined}>
          <CardContent className="pt-5 pb-4 space-y-2">
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
              What happened
            </div>
            {result.summary && (
              <p className="text-sm text-gray-700">{result.summary}</p>
            )}
            {result.self_execute_answer && (
              <p className="text-sm text-gray-700 whitespace-pre-wrap">
                {result.self_execute_answer}
              </p>
            )}
            {result.error_message && (
              <p className="text-sm text-red-600">{result.error_message}</p>
            )}
            {result.ai_test_run_id && onNavigateToRun && (
              <button
                onClick={() => onNavigateToRun(result.ai_test_run_id!)}
                className="text-sm text-blue-600 hover:underline"
              >
                View the AI agent's step-by-step run →
              </button>
            )}
          </CardContent>
        </Card>
      )}

      {/* Visual findings (from the linked Judge run, if any) */}
      {(result.findings?.length ?? 0) > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Visual differences found ({result.findings!.length})
          </div>
          <div className="space-y-2">
            {result.findings!.map((f, i) => (
              <FindingCard key={i} finding={f} />
            ))}
          </div>
        </div>
      )}

      {/* Routing trail */}
      {(result.decisions?.length ?? 0) > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Routing decisions
          </div>
          <div className="space-y-2">
            {result.decisions!.map((d, i) => (
              <div
                key={i}
                className="rounded-md border border-gray-100 bg-white px-3 py-2 flex items-center gap-3"
              >
                <Badge
                  variant="outline"
                  className={`text-xs ${
                    d.invoked
                      ? "border-gray-900 text-gray-900"
                      : "border-gray-200 text-gray-400"
                  }`}
                >
                  {d.invoked ? "Ran" : "Skipped"}
                </Badge>
                <span className="text-sm text-gray-700 flex-1">
                  {STEP_LABELS[d.step] || d.step}
                </span>
                <span className="text-xs text-gray-500">{d.rationale}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
