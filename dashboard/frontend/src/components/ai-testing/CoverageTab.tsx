"use client";

/**
 * Coverage report (New Vibe Test Phase 6, A.4/D.15) — requirement -> linked
 * UI test(s) and/or Functional Test(s) -> latest status -> (functional)
 * GEval score -> last-run date, plus per-test pass-rate so a genuinely
 * broken feature can be told apart from a flaky test. Backed by
 * GET /api/ai-testing/coverage — see that endpoint's docstring
 * (app/api/v1/ai_runs.py) for exactly how "the same test" is identified
 * across repeated runs (requirement + goal-hash for functional, requirement
 * + target URL + reference image for UI).
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { RunStatusBadge } from "./shared";

interface CoverageTestEntry {
  kind: "functional" | "ui";
  label: string;
  test_type?: string | null;
  latest_run_id: string;
  latest_status: string;
  last_run_at: string;
  latest_eval_score?: number | null;
  latest_pixel_mismatch_pct?: number | null;
  total_runs: number;
  pass_count: number;
  fail_count: number;
  needs_review_count: number;
  pass_rate?: number | null;
  flakiness_rate?: number | null;
}

interface CoverageRequirementGroup {
  linked_requirement: string;
  functional_tests: CoverageTestEntry[];
  ui_tests: CoverageTestEntry[];
}

interface CoverageResponse {
  requirements: CoverageRequirementGroup[];
  unlinked_functional_count: number;
  unlinked_ui_count: number;
}

function PassRateBadge({ entry }: { entry: CoverageTestEntry }) {
  if (entry.pass_rate == null) {
    return (
      <span className="text-xs text-gray-400">
        {entry.needs_review_count > 0
          ? `${entry.needs_review_count} awaiting review`
          : "no completed runs yet"}
      </span>
    );
  }
  const pct = Math.round(entry.pass_rate * 100);
  // Phase 7 (F.26): replaces the old "pass_count > 0 && fail_count > 0"
  // heuristic, which flagged a test that failed once months ago and has
  // passed every run since identically to one that alternates every run.
  // flakiness_rate is the fraction of consecutive decided-run transitions
  // that flip status (see backend/app/api/v1/ai_runs.py::_compute_flakiness_rate)
  // -- 0.3 means roughly 1-in-3 runs flips relative to the previous one,
  // which is a genuinely unstable test rather than a single old failure.
  const flaky = entry.flakiness_rate != null && entry.flakiness_rate >= 0.3;
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <span
        className={
          pct === 100
            ? "text-green-600 font-medium"
            : pct === 0
            ? "text-red-600 font-medium"
            : "text-amber-600 font-medium"
        }
      >
        {pct}% pass
      </span>
      <span className="text-gray-400">
        ({entry.pass_count}/{entry.pass_count + entry.fail_count} of {entry.total_runs} run
        {entry.total_runs === 1 ? "" : "s"})
      </span>
      {flaky && (
        <Badge
          variant="outline"
          className="text-[10px] border-amber-300 text-amber-700 bg-amber-50"
          title={`${Math.round((entry.flakiness_rate ?? 0) * 100)}% of consecutive runs flip status`}
        >
          Flaky
        </Badge>
      )}
    </span>
  );
}

function TestRow({ entry }: { entry: CoverageTestEntry }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 border-b border-gray-50 last:border-b-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-gray-800 truncate" title={entry.label}>
          {entry.label}
        </p>
        <div className="flex items-center gap-3 mt-1">
          <PassRateBadge entry={entry} />
          <span className="text-xs text-gray-400">
            last run {new Date(entry.last_run_at).toLocaleDateString("en-GB")}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {entry.kind === "functional" && entry.test_type && entry.test_type !== "happy" && (
          <Badge variant="outline" className="text-xs border-gray-200 text-gray-500">
            {entry.test_type === "negative" ? "Negative" : "Edge case"}
          </Badge>
        )}
        {entry.kind === "functional" && entry.latest_eval_score != null && (
          <Badge
            variant="outline"
            className={`text-xs font-semibold ${
              entry.latest_eval_score >= 0.7
                ? "border-green-300 text-green-700 bg-green-50"
                : entry.latest_eval_score >= 0.4
                ? "border-amber-300 text-amber-700 bg-amber-50"
                : "border-red-300 text-red-700 bg-red-50"
            }`}
          >
            {Math.round(entry.latest_eval_score * 100)}% AI score
          </Badge>
        )}
        {entry.kind === "ui" && entry.latest_pixel_mismatch_pct != null && (
          <span className="text-xs text-gray-500">
            {entry.latest_pixel_mismatch_pct}% pixel mismatch
          </span>
        )}
        <RunStatusBadge status={entry.latest_status} />
      </div>
    </div>
  );
}

export default function CoverageTab() {
  const { data, isLoading, isError } = useQuery<CoverageResponse>({
    queryKey: ["ai-coverage"],
    queryFn: () => apiGet("/api/ai-testing/coverage"),
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
        Failed to load the coverage report.
      </p>
    );
  }

  const requirements = data?.requirements ?? [];
  const unlinkedFunctional = data?.unlinked_functional_count ?? 0;
  const unlinkedUi = data?.unlinked_ui_count ?? 0;

  if (requirements.length === 0) {
    return (
      <div className="text-center py-16 text-gray-400 text-sm">
        No requirements linked yet. Add a &quot;linked requirement&quot; when
        creating a UI Test or Functional Test to see coverage here.
        {(unlinkedFunctional > 0 || unlinkedUi > 0) && (
          <p className="mt-2">
            ({unlinkedFunctional} functional and {unlinkedUi} UI test run
            {unlinkedFunctional + unlinkedUi === 1 ? "" : "s"} exist with no
            linked requirement.)
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {(unlinkedFunctional > 0 || unlinkedUi > 0) && (
        <p className="text-xs text-gray-400">
          {unlinkedFunctional} functional and {unlinkedUi} UI test run
          {unlinkedFunctional + unlinkedUi === 1 ? "" : "s"} have no linked
          requirement and aren&apos;t shown below.
        </p>
      )}
      {requirements.map((group) => (
        <div
          key={group.linked_requirement}
          className="rounded-xl border border-gray-200 bg-white px-5 py-4"
        >
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-sm font-semibold text-gray-900">
              {group.linked_requirement}
            </h3>
            <span className="text-xs text-gray-400">
              {group.functional_tests.length + group.ui_tests.length} test
              {group.functional_tests.length + group.ui_tests.length === 1 ? "" : "s"}
            </span>
          </div>
          {group.functional_tests.length > 0 && (
            <div className="mb-2">
              <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">
                Functional
              </div>
              {group.functional_tests.map((e) => (
                <TestRow key={`${e.kind}-${e.label}-${e.latest_run_id}`} entry={e} />
              ))}
            </div>
          )}
          {group.ui_tests.length > 0 && (
            <div>
              <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">
                UI
              </div>
              {group.ui_tests.map((e) => (
                <TestRow key={`${e.kind}-${e.label}-${e.latest_run_id}`} entry={e} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
