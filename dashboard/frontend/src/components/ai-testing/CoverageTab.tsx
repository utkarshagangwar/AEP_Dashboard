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

interface SpecGapEntry {
  skill_id: string;
  name: string;
  test_type?: string | null;
  category?: string | null;
  behaviour_key?: string | null;
  project_name?: string | null;
  source_type?: string | null;
  decided_runs: number;
  fail_count: number;
  last_run_at?: string | null;
  last_failure_summary?: string | null;
}

interface SpecGapResponse {
  entries: SpecGapEntry[];
  total_derived_skills: number;
  evaluated_skills: number;
  min_runs: number;
}

/** Derived expectations the product has never once satisfied.
 *
 *  Most negative and edge tests are inferred from standard QA practice rather
 *  than stated in the source document, so a failure has two possible readings
 *  needing opposite responses: the product is wrong (raise a defect), or the
 *  inference is wrong because the product deliberately behaves differently
 *  and the document never said so (fix the document). Nothing aggregated
 *  that, so every failure got triaged as the first.
 *
 *  Rendered separately from the coverage report, and BEFORE its empty state,
 *  because the two are independent: a project can have no linked requirements
 *  and still have spec gaps worth reading.
 */
function SpecGapPanel() {
  const { data, isLoading, isError } = useQuery<SpecGapResponse>({
    queryKey: ["ai-spec-gaps"],
    queryFn: () => apiGet("/api/ai-testing/spec-gaps"),
    refetchInterval: 60_000,
  });

  // Silent while loading and on error: this panel is a secondary signal
  // sitting above the coverage report, and an error banner for it would
  // read as the coverage report itself having failed.
  if (isLoading || isError || !data) return null;
  if (data.entries.length === 0) {
    // Only worth saying "none" when something was actually checked —
    // otherwise it claims a clean bill of health nobody earned.
    if (data.evaluated_skills === 0) return null;
    return (
      <p className="text-xs text-gray-400">
        No spec-gap candidates: every one of the {data.evaluated_skills} inferred
        expectation{data.evaluated_skills === 1 ? "" : "s"} with at least{" "}
        {data.min_runs} decided run{data.min_runs === 1 ? "" : "s"} has passed at
        least once.
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/50 px-5 py-4">
      <div className="flex items-center gap-2 mb-1">
        <h3 className="text-sm font-semibold text-gray-900">
          Possible spec gaps
        </h3>
        <Badge
          variant="outline"
          className="text-amber-700 border-amber-300 bg-amber-50"
        >
          {data.entries.length}
        </Badge>
      </div>
      <p className="text-xs text-gray-600 mb-3">
        These tests assert behaviour that was <strong>inferred</strong> from standard
        QA practice, not stated in the source document — and the product has never
        once satisfied them ({data.evaluated_skills} of {data.total_derived_skills}{" "}
        inferred expectations had enough runs to judge). Either the product is wrong,
        or the assumption is and the document never said so. Worth confirming with
        the spec owner before raising a defect.
      </p>
      <div className="space-y-0">
        {data.entries.map((e) => (
          <div
            key={e.skill_id}
            className="flex items-start justify-between gap-4 py-2.5 border-b border-amber-100 last:border-b-0"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm text-gray-800 truncate" title={e.name}>
                {e.name}
              </p>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                {e.test_type && (
                  <Badge
                    variant="outline"
                    className={`text-xs ${
                      e.test_type === "negative"
                        ? "border-red-300 text-red-700 bg-red-50"
                        : "border-amber-300 text-amber-700 bg-amber-50"
                    }`}
                  >
                    {e.test_type}
                  </Badge>
                )}
                {e.category && (
                  <span className="text-xs text-gray-400">{e.category}</span>
                )}
                {e.project_name && (
                  <span className="text-xs text-gray-400">· {e.project_name}</span>
                )}
                {e.last_run_at && (
                  <span className="text-xs text-gray-400">
                    · last run {new Date(e.last_run_at).toLocaleDateString("en-GB")}
                  </span>
                )}
              </div>
              {e.last_failure_summary && (
                <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                  {e.last_failure_summary}
                </p>
              )}
            </div>
            <span className="text-xs text-red-600 font-medium flex-shrink-0">
              failed {e.fail_count}/{e.decided_runs}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CoverageReport() {
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

export default function CoverageTab() {
  return (
    <div className="space-y-4">
      <SpecGapPanel />
      <CoverageReport />
    </div>
  );
}
