"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import {
  RunResult,
  ScreenshotPane,
  StepRow,
  formatDuration,
} from "./shared";

/**
 * Reusable run detail view: status banner + Summary / Steps / Screenshots
 * subtabs. Used by the Results tab (history) — mirrors the complete-state
 * view on the New Test flow.
 */
export default function RunDetail({ result }: { result: RunResult }) {
  const [activeTab, setActiveTab] = useState<"summary" | "steps" | "screenshots">(
    "summary"
  );

  const isPassed = result.status === "passed";
  const isFailed = result.status === "failed";
  const isInconclusive =
    result.status === "inconclusive" || result.status === "cancelled";
  const screenshotEvents = result.events.filter((e) => e.screenshot_url);

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
            {isPassed
              ? "PASSED"
              : isFailed
              ? "FAILED"
              : result.status.toUpperCase()}
          </span>
          {result.run_type === "skill_replay" && (
            <span className="text-xs font-semibold text-white/80 bg-white/20 rounded-full px-3 py-1 uppercase tracking-wide">
              Skill Replay
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
        <div className="flex border-b border-gray-200 gap-6">
          {(["summary", "steps", "screenshots"] as const).map((tab) => (
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
                        ? new Date(result.created_at).toLocaleString("en-GB", {
                            hour12: false,
                          }) + " UTC"
                        : "—"}
                    </p>
                  </CardContent>
                </Card>
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

          {activeTab === "screenshots" && (
            <div className="grid grid-cols-2 gap-4">
              {screenshotEvents.length === 0 ? (
                <p className="col-span-2 text-sm text-gray-400 text-center py-10">
                  No screenshots captured during this run.
                </p>
              ) : (
                screenshotEvents.map((event) => (
                  <div
                    key={event.sequence}
                    className="rounded-lg border border-gray-200 overflow-hidden"
                  >
                    <ScreenshotPane
                      screenshotUrl={event.screenshot_url}
                      highlight={event.highlighted_element}
                    />
                    <div className="px-3 py-2 text-xs text-gray-500 border-t border-gray-100 bg-white">
                      Step {event.sequence} —{" "}
                      {event.description.length > 60
                        ? event.description.slice(0, 60) + "…"
                        : event.description}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
