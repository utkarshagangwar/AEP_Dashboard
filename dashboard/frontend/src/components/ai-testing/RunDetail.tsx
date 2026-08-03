"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  SegmentedTabs,
  SegmentedTabsIndicator,
  SegmentedTabsList,
  SegmentedTabsTab,
} from "@/components/ui/segmented-tabs";
import {
  RunResult,
  VideoPane,
  StepRow,
  formatDuration,
} from "./shared";

/**
 * Reusable run detail view: status banner + Summary / Steps / Screenshots
 * subtabs. Used by the Results tab (history) — mirrors the complete-state
 * view on the New Test flow.
 */
export default function RunDetail({ result }: { result: RunResult }) {
  const [activeTab, setActiveTab] = useState<"summary" | "steps" | "video">(
    "summary"
  );

  const isPassed = result.status === "passed";
  const isFailed = result.status === "failed";
  const isInconclusive =
    result.status === "inconclusive" || result.status === "cancelled";
  // New Vibe Test Phase 4 (D.15) / Phase 6 (surface prominently) — this
  // history view (RunDetail) previously fell through to the generic
  // amber/status-text branch for needs_review, the same treatment as
  // "inconclusive" even though they mean very different things. Matches
  // the dedicated banner/notice the live run view (ai-testing/page.tsx)
  // already got in Phase 4.
  const isNeedsReview = result.status === "needs_review";

  return (
    <div className="space-y-6">
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
          <span className="text-3xl font-bold tracking-wide text-white">
            {isPassed
              ? "PASSED"
              : isFailed
              ? "FAILED"
              : isNeedsReview
              ? "NEEDS REVIEW"
              : result.status.toUpperCase()}
          </span>
          {result.run_type === "skill_replay" && (
            <span className="text-xs font-semibold text-white/80 bg-white/20 rounded-full px-3 py-1 uppercase tracking-wide">
              Skill Replay
            </span>
          )}
          {result.test_category === "functional" && (
            <span className="text-xs font-semibold text-white/80 bg-white/20 rounded-full px-3 py-1 uppercase tracking-wide">
              Functional Test
              {result.test_type && result.test_type !== "happy"
                ? ` · ${result.test_type === "negative" ? "Negative" : "Edge case"}`
                : ""}
            </span>
          )}
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

      {/* Failing step card */}
      {isFailed && result.failing_step_description && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-5 pb-4 flex items-start gap-4">
            <div className="flex-shrink-0 w-9 h-9 rounded-full bg-red-100 flex items-center justify-center text-red-600 font-bold text-sm">
              {result.failing_step_index ?? "!"}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold text-red-500 uppercase tracking-wide mb-1">
                FAILING STEP
              </div>
              <p className="text-gray-800 font-medium">
                {result.failing_step_description}
              </p>
            </div>
            {result.failing_step_screenshot_url && (
              <div className="flex-shrink-0 w-28 h-20 rounded border border-red-200 overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={result.failing_step_screenshot_url}
                  alt="Failing step"
                  className="w-full h-full object-cover"
                />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Needs-review notice (New Vibe Test Phase 4/6) */}
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
                  reached by two independent gates — a low GEval score, OR an
                  application error observed on the page during the run — so
                  this heading can no longer assume the quality score was the
                  trigger. eval_reason carries the concrete cause either way. */}
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
          <CardContent className="pt-5 pb-4">
            <p className="text-sm font-medium text-amber-800">
              Test could not complete
            </p>
            <p className="text-sm text-amber-700 mt-0.5">
              {result.summary ||
                "The test was stopped or timed out before reaching a definitive outcome."}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Subtabs */}
      <div>
        <SegmentedTabs
          value={activeTab}
          onValueChange={(value) =>
            setActiveTab(value as "summary" | "steps" | "video")
          }
        >
          <SegmentedTabsList>
            <SegmentedTabsIndicator />
            {(["summary", "steps", "video"] as const).map((tab) => (
              // w-auto px-4 overrides the component's fixed 50px tab width,
              // which only fits very short labels.
              <SegmentedTabsTab
                key={tab}
                value={tab}
                className="w-auto px-4 capitalize"
              >
                {tab}
              </SegmentedTabsTab>
            ))}
          </SegmentedTabsList>
        </SegmentedTabs>

        <div className="pt-6">
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
              {result.eval_score != null && (
                <div className="flex gap-3 text-sm bg-white border border-gray-100 rounded-lg px-4 py-3">
                  <svg
                    className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5"
                    viewBox="0 0 16 16"
                    fill="none"
                  >
                    <path
                      d="M8 1.5l5.5 2v3.75c0 3.75-2.35 5.9-5.5 7-3.15-1.1-5.5-3.25-5.5-7V3.5l5.5-2z"
                      stroke="currentColor"
                      strokeWidth="1.25"
                      strokeLinejoin="round"
                    />
                  </svg>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                        AI Quality Score
                      </span>
                      <Badge
                        variant="outline"
                        className={`text-xs font-semibold ${
                          result.eval_score >= 0.7
                            ? "border-green-300 text-green-700 bg-green-50"
                            : result.eval_score >= 0.4
                            ? "border-amber-300 text-amber-700 bg-amber-50"
                            : "border-red-300 text-red-700 bg-red-50"
                        }`}
                      >
                        {Math.round(result.eval_score * 100)}%
                      </Badge>
                    </div>
                    {result.eval_reason && (
                      <p className="text-gray-600 whitespace-pre-line">
                        {result.eval_reason}
                      </p>
                    )}
                  </div>
                </div>
              )}
              {result.visual_eval_score != null && (
                <div className="flex gap-3 text-sm bg-white border border-gray-100 rounded-lg px-4 py-3">
                  <svg
                    className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5"
                    viewBox="0 0 16 16"
                    fill="none"
                  >
                    <path
                      d="M2 3.5h12v9H2v-9zm0 0l6 5 6-5"
                      stroke="currentColor"
                      strokeWidth="1.25"
                      strokeLinejoin="round"
                    />
                  </svg>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                        Expected Results Match
                      </span>
                      <Badge
                        variant="outline"
                        className={`text-xs font-semibold ${
                          result.visual_eval_score >= 0.7
                            ? "border-green-300 text-green-700 bg-green-50"
                            : result.visual_eval_score >= 0.4
                            ? "border-amber-300 text-amber-700 bg-amber-50"
                            : "border-red-300 text-red-700 bg-red-50"
                        }`}
                      >
                        {Math.round(result.visual_eval_score * 100)}%
                      </Badge>
                    </div>
                    {result.visual_eval_reason && (
                      <p className="text-gray-600 whitespace-pre-line">
                        {result.visual_eval_reason}
                      </p>
                    )}
                  </div>
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
                        ? new Date(result.created_at).toLocaleString("en-GB", {
                            hour12: false,
                          }) + " UTC"
                        : "—"}
                    </p>
                  </CardContent>
                </Card>
                {result.linked_requirement && (
                  <Card>
                    <CardContent className="pt-4 pb-4">
                      <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
                        LINKED REQUIREMENT
                      </div>
                      <p className="text-sm text-gray-800">{result.linked_requirement}</p>
                    </CardContent>
                  </Card>
                )}
              </div>
            </div>
          )}

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

          {activeTab === "video" && (
            <VideoPane runId={result.run_id} videoAvailable={result.video_available} />
          )}
        </div>
      </div>
    </div>
  );
}
