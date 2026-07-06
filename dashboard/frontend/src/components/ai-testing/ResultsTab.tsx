"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiFetch } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import OrchestratorRunDetail from "./OrchestratorRunDetail";
import RunDetail from "./RunDetail";
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
      if (!selectedRunId) return null;
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
    enabled: !!selectedRunId,
  });

  // ── Detail view ────────────────────────────────────────────────────────────
  if (selectedRunId) {
    return (
      <div className="space-y-4">
        <button
          onClick={() => setSelectedRunId(null)}
          className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-900 transition-colors"
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
        </button>
        {detailLoading || !detail ? (
          <div className="space-y-4">
            <Skeleton className="h-28 w-full rounded-xl" />
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        ) : detail.run_type === "autonomous_qa" ? (
          <OrchestratorRunDetail
            result={detail}
            onNavigateToRun={(runId) => setSelectedRunId(runId)}
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
                onClick={() => setSelectedRunId(run.id)}
                className="border-b border-gray-50 last:border-b-0 hover:bg-gray-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 text-gray-800 max-w-[320px]">
                  <span className="block truncate">{run.goal}</span>
                </td>
                <td className="px-4 py-3">
                  <RunStatusBadge status={run.status} />
                </td>
                <td className="px-4 py-3">
                  <Badge
                    variant="outline"
                    className={`text-xs ${
                      run.run_type === "skill_replay"
                        ? "border-indigo-200 text-indigo-600"
                        : run.run_type === "autonomous_qa"
                          ? "border-teal-200 text-teal-600"
                          : "border-purple-200 text-purple-600"
                    }`}
                  >
                    {run.run_type === "skill_replay"
                      ? "Replay"
                      : run.run_type === "autonomous_qa"
                        ? "Autonomous QA"
                        : "AI"}
                  </Badge>
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
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(run);
                    }}
                    disabled={deletingId === run.id}
                    title="Delete report"
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-red-200 text-xs font-medium text-red-600 hover:bg-red-50 hover:border-red-300 transition-colors disabled:opacity-50"
                  >
                    <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none">
                      <path
                        d="M2.5 4h11M6.5 4V2.75A.75.75 0 017.25 2h1.5a.75.75 0 01.75.75V4m2.75 0v9.25a1 1 0 01-1 1h-7.5a1 1 0 01-1-1V4M6.5 7v5M9.5 7v5"
                        stroke="currentColor"
                        strokeWidth="1.25"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    {deletingId === run.id ? "Deleting…" : "Delete"}
                  </button>
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
