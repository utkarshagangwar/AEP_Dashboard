"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiFetch } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DeleteIconButton } from "@/components/ui/delete-icon-button";
import { Skeleton } from "@/components/ui/skeleton";
import OrchestratorRunDetail from "./OrchestratorRunDetail";
import RunDetail from "./RunDetail";
import VisualTestRunDetail, { VisualTestRunResult } from "./VisualTestRunDetail";
import { RunResult, RunStatusBadge, formatDuration } from "./shared";

interface RunListItem {
  id: string;
  goal: string;
  environment?: string | null;
  credential_profile_name?: string | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  step_count: number;
  run_type: string;
  platform?: string;
  created_at: string;
}

interface RunListResponse {
  data: RunListItem[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 20;

/** Results tab — history of past AI test runs with drill-down detail. */
export default function ResultsTab() {
  const [page, setPage] = useState(1);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // Which endpoint/shape to fetch the detail from — a "ui_test" row (New
  // Vibe Test Phase 5) is a VisualRun, not an AITestRun, so its detail
  // comes from a different API entirely. Set together with selectedRunId
  // when a list row is clicked.
  const [selectedRunType, setSelectedRunType] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const handleDelete = async (run: RunListItem) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this test report? This cannot be undone.")
    ) {
      return;
    }
    setDeletingId(run.id);
    setDeleteError(null);
    try {
      const resp = await apiFetch(`/api/ai-testing/runs/${run.id}`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      queryClient.invalidateQueries({ queryKey: ["ai-runs"] });
    } catch (err: unknown) {
      setDeleteError(
        err instanceof Error ? err.message : "Failed to delete report"
      );
    } finally {
      setDeletingId(null);
    }
  };

  const { data, isLoading, isError } = useQuery<RunListResponse>({
    queryKey: ["ai-runs", page],
    queryFn: () => apiGet(`/api/ai-testing/runs?page=${page}&limit=${LIMIT}`),
    refetchInterval: 15_000,
  });

  const { data: detail, isLoading: detailLoading } = useQuery<RunResult | null>({
    queryKey: ["ai-run-detail", selectedRunId],
    queryFn: async () => {
      if (!selectedRunId || selectedRunType === "ui_test") return null;
      const run = await apiGet(`/api/ai-testing/runs/${selectedRunId}`);
      return {
        run_id: run.id,
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
        eval_score: run.eval_score,
        eval_reason: run.eval_reason,
        eval_status: run.eval_status,
        visual_eval_score: run.visual_eval_score,
        visual_eval_reason: run.visual_eval_reason,
        visual_eval_status: run.visual_eval_status,
        test_category: run.test_category,
        test_type: run.test_type,
        linked_requirement: run.linked_requirement,
        events: run.events || [],
        created_at: run.created_at,
        error_message: run.error_message,
        ai_test_run_id: run.ai_test_run_id,
        visual_run_id: run.visual_run_id,
        self_execute_answer: run.self_execute_answer,
        pixel_mismatch_pct: run.pixel_mismatch_pct,
        decisions: run.decisions || [],
        findings: run.findings || [],
      } as RunResult;
    },
    enabled: !!selectedRunId && selectedRunType !== "ui_test",
  });

  // Separate query for a "ui_test" row (VisualRun) — different API, different
  // shape (findings/images, no goal/steps/events) — see VisualTestRunDetail.
  const { data: visualDetail, isLoading: visualDetailLoading } =
    useQuery<VisualTestRunResult | null>({
      queryKey: ["ai-run-detail-visual", selectedRunId],
      queryFn: async () => {
        if (!selectedRunId || selectedRunType !== "ui_test") return null;
        return apiGet(`/api/v1/visual-audits/${selectedRunId}`);
      },
      enabled: !!selectedRunId && selectedRunType === "ui_test",
    });

  // ── Detail view ────────────────────────────────────────────────────────────
  if (selectedRunId) {
    return (
      <div className="space-y-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setSelectedRunId(null);
            setSelectedRunType(null);
          }}
          className="text-gray-500"
        >
          <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
            <path
              d="M10 3L5 8l5 5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Back to results
        </Button>
        {selectedRunType === "ui_test" ? (
          visualDetailLoading || !visualDetail ? (
            <div className="space-y-4">
              <Skeleton className="h-28 w-full rounded-xl" />
              <Skeleton className="h-64 w-full rounded-xl" />
            </div>
          ) : (
            <VisualTestRunDetail result={visualDetail} />
          )
        ) : detailLoading || !detail ? (
          <div className="space-y-4">
            <Skeleton className="h-28 w-full rounded-xl" />
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        ) : detail.run_type === "autonomous_qa" ? (
          <OrchestratorRunDetail
            result={detail}
            onNavigateToRun={(runId) => {
              setSelectedRunId(runId);
              setSelectedRunType("ai");
            }}
          />
        ) : (
          <RunDetail result={detail} />
        )}
      </div>
    );
  }

  // ── List view ─────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="space-y-3">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
        Failed to load test results.
      </p>
    );
  }

  const runs = data?.data ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  if (runs.length === 0) {
    return (
      <div className="text-center py-16 text-gray-400 text-sm">
        No test runs yet. Run a goal-based test from the New Test tab — every
        run is saved here automatically.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {deleteError && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
          {deleteError}
        </p>
      )}
      <div className="rounded-xl border border-gray-200 bg-white overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-left text-xs text-gray-400 uppercase tracking-wide">
              <th className="px-4 py-3 font-semibold">Goal</th>
              <th className="px-4 py-3 font-semibold">Status</th>
              <th className="px-4 py-3 font-semibold">Type</th>
              <th className="px-4 py-3 font-semibold">Environment</th>
              <th className="px-4 py-3 font-semibold">Steps</th>
              <th className="px-4 py-3 font-semibold">Duration</th>
              <th className="px-4 py-3 font-semibold">Date</th>
              <th className="px-4 py-3 font-semibold text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.id}
                onClick={() => {
                  setSelectedRunId(run.id);
                  setSelectedRunType(run.run_type);
                }}
                className="border-b border-gray-50 last:border-b-0 hover:bg-gray-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 text-gray-800 max-w-[320px]">
                  <span className="block truncate">{run.goal}</span>
                </td>
                <td className="px-4 py-3">
                  <RunStatusBadge status={run.status} />
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Badge
                      variant="outline"
                      className={`text-xs ${
                        run.run_type === "skill_replay"
                          ? "border-indigo-200 text-indigo-600"
                          : run.run_type === "autonomous_qa"
                            ? "border-teal-200 text-teal-600"
                            : run.run_type === "ui_test"
                              ? "border-blue-200 text-blue-600"
                              : "border-purple-200 text-purple-600"
                      }`}
                    >
                      {run.run_type === "skill_replay"
                        ? "Replay"
                        : run.run_type === "autonomous_qa"
                          ? "Autonomous QA"
                          : run.run_type === "ui_test"
                            ? "UI Test"
                            : "AI"}
                    </Badge>
                    {run.platform === "android" && (
                      <Badge variant="outline" className="text-xs border-emerald-200 text-emerald-600">
                        Android
                      </Badge>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3 text-gray-500">
                  {run.environment || "Custom"}
                </td>
                <td className="px-4 py-3 text-gray-500">{run.step_count}</td>
                <td className="px-4 py-3 text-gray-500">
                  {formatDuration(run.duration_ms)}
                </td>
                <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                  {new Date(run.created_at).toLocaleString("en-GB", {
                    hour12: false,
                  })}
                </td>
                <td className="px-4 py-3 text-right">
                  <DeleteIconButton
                    onClick={(e) => {
                      // Row click opens the run detail — this must not.
                      e.stopPropagation();
                      handleDelete(run);
                    }}
                    disabled={deletingId === run.id}
                    label={deletingId === run.id ? "Deleting…" : "Delete"}
                    aria-label="Delete report"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-gray-500">
          <span>
            Page {page} of {totalPages} · {total} runs
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
