"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiFetch } from "@/utils/apiClient";
import { confirmDialog } from "@/lib/confirm";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DeleteIconButton } from "@/components/ui/delete-icon-button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { RunStatusBadge, type Skill } from "./shared";
import SkillDetailModal from "./SkillDetailModal";
import ProjectTestSetupDialog from "./ProjectTestSetupDialog";

interface Project {
  id: string;
  name: string;
  /** The project's environment labels. Added to the environments endpoint
   *  so per-environment URLs can be configured without another request. */
  environments?: string[];
}

interface SkillListResponse {
  data: Skill[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 20;
const NO_PROJECT_VALUE = "__none__";

// Page size used only when collecting every id for "Select all" — the
// backend caps `limit` at 100, so the ids are gathered in 100-row pages
// rather than one unbounded request.
const ID_FETCH_LIMIT = 100;

// Sentinel for "the filter hasn't been resolved yet". The stored/default
// project can only be worked out once the project list has arrived and
// localStorage is readable (i.e. after mount, never during SSR), and the
// skills query must not fire before then: with "All projects" removed
// there is no valid unscoped request to send.
const UNRESOLVED_PROJECT = "";

const PROJECT_FILTER_STORAGE_KEY = "aep.vibe.skills.projectFilter";

// Combined sort_by:sort_dir key for a single friendly dropdown — the API
// takes the two as separate query params, split back out in the query fn.
const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "created_at:desc", label: "Date added (newest first)" },
  { value: "created_at:asc", label: "Date added (oldest first)" },
  { value: "name:asc", label: "Name (A → Z)" },
  { value: "name:desc", label: "Name (Z → A)" },
];
const DEFAULT_SORT = SORT_OPTIONS[0].value;

/**
 * Skills tab — one place for every reusable skill, regardless of origin:
 *   - Recorded: auto-saved from a passed goal-based run. Replaying
 *     re-executes the recorded browser actions directly (no LLM planning).
 *   - Prompt: a detailed instruction extracted straight from a parsed
 *     SOW/video checkpoint, no live run required to produce it. Running one
 *     starts a normal AI-planned run; if it passes, this same skill is
 *     upgraded in place with a real recording.
 * Either way the run streams live using the same view as a goal-based run.
 *
 * Skills are ALWAYS scoped to exactly one project (or to "No project", for
 * skills that arrived unassigned). There is deliberately no "All projects"
 * view: a skill's login and start URL come from its project's Test setup,
 * so a mixed list invited running a skill written for one app against
 * another's configuration, and left the Test setup button with no single
 * project to act on. The chosen project is remembered in localStorage so
 * the tab reopens where the user left it.
 */
export default function SkillsTab({
  onReplayStarted,
}: {
  onReplayStarted: (runId: string, goal: string) => void;
}) {
  const [page, setPage] = useState(1);
  const [projectFilter, setProjectFilter] = useState<string>(UNRESOLVED_PROJECT);
  const [sortValue, setSortValue] = useState<string>(DEFAULT_SORT);
  const [allowFallback, setAllowFallback] = useState(false);
  const [envDialogOpen, setEnvDialogOpen] = useState(false);
  const [replayingId, setReplayingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailSkill, setDetailSkill] = useState<Skill | null>(null);
  const [detailEditing, setDetailEditing] = useState(false);
  // Bulk actions. Selection spans the whole filtered set, not just the
  // visible page — "Select all" selects every skill for the current
  // project, which is why paging no longer clears it. Changing the
  // project or sort still clears, because that is a different set.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectAllPending, setSelectAllPending] = useState(false);
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: projects = [], isSuccess: projectsLoaded } = useQuery<Project[]>({
    queryKey: ["ai-environments"],
    queryFn: () => apiGet("/api/ai-testing/environments"),
    staleTime: 60_000,
  });

  // True only for a real project id — "No project" and the unresolved
  // sentinel both fail it. Gates the per-project Test setup button.
  const hasSingleProject =
    projectFilter !== UNRESOLVED_PROJECT && projectFilter !== NO_PROJECT_VALUE;

  // Resolve the initial filter once the project list is in: the project
  // last used on this machine, falling back to the first project, and to
  // "No project" only when there are no projects at all. A stored id for
  // a project that has since been deleted is discarded rather than sent
  // to the API, which would 404 every skill request.
  useEffect(() => {
    if (projectFilter !== UNRESOLVED_PROJECT) return;
    if (!projectsLoaded) return;
    // No projects configured at all: "No project" is the only list this
    // tab can show, and leaving the filter unresolved would strand it on
    // the loading skeleton forever.
    if (projects.length === 0) {
      setProjectFilter(NO_PROJECT_VALUE);
      return;
    }
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(PROJECT_FILTER_STORAGE_KEY);
    } catch {
      // Private mode / storage disabled — fall through to the default.
      stored = null;
    }
    const storedIsValid =
      stored === NO_PROJECT_VALUE || projects.some((p) => p.id === stored);
    setProjectFilter(storedIsValid && stored ? stored : projects[0].id);
  }, [projects, projectsLoaded, projectFilter]);

  // The per-run login override moved into the Test setup popup. Replays
  // send no credential_profile_id, so the backend resolves it — skill
  // binding, then the project default. That resolution is the only one
  // that can attach a bypass profile's auth cookie, which is why it must
  // not be short-circuited from here.
  const buildReplayBody = () =>
    JSON.stringify({ allow_ai_fallback: allowFallback });

  const [sortBy, sortDir] = sortValue.split(":");

  // Every skills request is project-scoped. Built here so the list query
  // and the "Select all" id sweep can never disagree about which set they
  // are looking at.
  const buildSkillsQuery = useCallback(
    (pageNumber: number, limit: number) => {
      const params = new URLSearchParams({
        page: String(pageNumber),
        limit: String(limit),
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      params.set(
        "project_id",
        projectFilter === NO_PROJECT_VALUE ? "none" : projectFilter
      );
      return params.toString();
    },
    [projectFilter, sortBy, sortDir]
  );

  const { data, isLoading, isError } = useQuery<SkillListResponse>({
    queryKey: ["ai-skills", page, projectFilter, sortValue],
    queryFn: () => apiGet(`/api/ai-testing/skills?${buildSkillsQuery(page, LIMIT)}`),
    // Never fire before the stored/default project is known — an
    // unscoped request would list every project's skills, which is the
    // view this tab deliberately no longer has.
    enabled: projectFilter !== UNRESOLVED_PROJECT,
  });

  const clearSelection = () => setSelectedIds(new Set());

  const handleFilterChange = (value: string | null) => {
    // A null/empty selection would reset the filter to the unresolved
    // sentinel and disable the list query — ignore it and keep the
    // current project.
    if (!value) return;
    setProjectFilter(value);
    try {
      window.localStorage.setItem(PROJECT_FILTER_STORAGE_KEY, value);
    } catch {
      // Storage unavailable — the filter still works for this session.
    }
    setPage(1);
    clearSelection();
  };

  const handleSortChange = (value: string) => {
    setSortValue(value ?? DEFAULT_SORT);
    setPage(1);
    clearSelection();
  };

  // Selection deliberately survives paging now: "Select all" covers the
  // whole filtered set, so wiping it on the next page would make the
  // action impossible to use across more than 20 skills.
  const handlePageChange = (next: number) => {
    setPage(next);
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /**
   * Select every skill matching the current filter — not just the 20 on
   * screen. The ids are swept page by page at the API's maximum page size
   * (`limit` is capped at 100 server-side), because the bulk endpoints
   * take an explicit id list and there is no "apply to filter" form of
   * them; sending ids keeps bulk delete/assign/run acting on exactly what
   * was counted here, with no risk of a concurrently-added skill being
   * swept into a delete the user never saw.
   */
  const selectAllMatching = async () => {
    setError(null);
    setSelectAllPending(true);
    try {
      const ids: string[] = [];
      let pageNumber = 1;
      // Bounded by the reported total, so a server that keeps returning
      // rows can never spin this forever.
      for (;;) {
        const resp: SkillListResponse = await apiGet(
          `/api/ai-testing/skills?${buildSkillsQuery(pageNumber, ID_FETCH_LIMIT)}`
        );
        const batch = resp.data ?? [];
        ids.push(...batch.map((s) => s.id));
        if (batch.length < ID_FETCH_LIMIT || ids.length >= (resp.total ?? ids.length)) {
          break;
        }
        pageNumber += 1;
      }
      setSelectedIds(new Set(ids));
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Could not select all skills"
      );
    } finally {
      setSelectAllPending(false);
    }
  };

  const toggleSelectAll = (allSelected: boolean) => {
    if (allSelected) clearSelection();
    else void selectAllMatching();
  };

  const handleReplay = async (skill: Skill) => {
    setReplayingId(skill.id);
    setError(null);
    try {
      const resp = await apiFetch(`/api/ai-testing/skills/${skill.id}/replay`, {
        method: "POST",
        body: buildReplayBody(),
      });
      if (!resp.ok) {
        // A 400 here is the backend saying the run has no start URL and
        // naming exactly which project/environment setting is missing —
        // surface it verbatim rather than collapsing it to a status code.
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      const result = await resp.json();
      onReplayStarted(result.run_id, skill.goal);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to replay skill");
    } finally {
      setReplayingId(null);
    }
  };

  const handleDelete = async (skill: Skill) => {
    const ok = await confirmDialog({
      title: `Delete skill "${skill.name}"?`,
      body: "This cannot be undone.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    setError(null);
    try {
      const resp = await apiFetch(`/api/ai-testing/skills/${skill.id}`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      queryClient.invalidateQueries({ queryKey: ["ai-skills"] });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete skill");
    }
  };

  const handleSaved = (updated: Skill) => {
    queryClient.invalidateQueries({ queryKey: ["ai-skills"] });
    setDetailSkill(updated);
  };

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const ok = await confirmDialog({
      title: `Delete ${ids.length} skill${ids.length === 1 ? "" : "s"}?`,
      body: "This cannot be undone.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    setError(null);
    setBulkMessage(null);
    setBulkPending(true);
    try {
      const resp = await apiFetch("/api/ai-testing/skills/bulk-delete", {
        method: "POST",
        body: JSON.stringify({ skill_ids: ids }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      const result = await resp.json();
      clearSelection();
      queryClient.invalidateQueries({ queryKey: ["ai-skills"] });
      setBulkMessage(`Deleted ${result.deleted} skill${result.deleted === 1 ? "" : "s"}.`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Bulk delete failed");
    } finally {
      setBulkPending(false);
    }
  };

  const handleBulkAssignProject = async (projectIdValue: string) => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const project_id =
      projectIdValue === NO_PROJECT_VALUE || !projectIdValue ? null : projectIdValue;
    setError(null);
    setBulkMessage(null);
    setBulkPending(true);
    try {
      const resp = await apiFetch("/api/ai-testing/skills/bulk-assign-project", {
        method: "POST",
        body: JSON.stringify({ skill_ids: ids, project_id }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      const result = await resp.json();
      clearSelection();
      queryClient.invalidateQueries({ queryKey: ["ai-skills"] });
      const projectLabel =
        project_id === null ? "No project" : projects.find((p) => p.id === project_id)?.name || "the selected project";
      setBulkMessage(
        `Assigned ${result.updated} skill${result.updated === 1 ? "" : "s"} to ${projectLabel}.`
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Bulk assign failed");
    } finally {
      setBulkPending(false);
    }
  };

  const handleBulkRun = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    // Select all can now cover the entire project, so this button can
    // queue hundreds of browser runs from one click. Confirm first — it
    // spends real LLM tokens and browser sessions, and nothing here is
    // undoable once queued.
    const ok = await confirmDialog({
      title: `Run ${ids.length} skill${ids.length === 1 ? "" : "s"} now?`,
      body: "Each one starts its own browser run.",
      tone: "neutral",
      confirmLabel: "Run",
    });
    if (!ok) return;
    setError(null);
    setBulkMessage(null);
    setBulkPending(true);
    try {
      const startOne = (id: string) =>
        apiFetch(`/api/ai-testing/skills/${id}/replay`, {
          method: "POST",
          body: buildReplayBody(),
        }).then(async (resp) => {
          if (!resp.ok) {
            // Previously this collapsed every failure to "Server error
            // (400)". The 400 body names the missing project/environment
            // configuration, which is the whole point of failing fast —
            // discarding it here would leave the engineer no better off
            // than the old blank-page failure did.
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `Server error (${resp.status})`);
          }
          return resp.json();
        });

      // Submitted in small batches rather than all at once: with the
      // whole project selectable, one click could otherwise open several
      // hundred simultaneous requests, which browsers cap and queue
      // unpredictably anyway. Every id is still submitted — this only
      // paces them.
      const results: PromiseSettledResult<unknown>[] = [];
      const BATCH = 5;
      for (let i = 0; i < ids.length; i += BATCH) {
        results.push(
          ...(await Promise.allSettled(ids.slice(i, i + BATCH).map(startOne)))
        );
      }
      const succeeded = results.filter((r) => r.status === "fulfilled").length;
      const failed = results.length - succeeded;
      clearSelection();
      queryClient.invalidateQueries({ queryKey: ["ai-skills"] });
      setBulkMessage(
        `Queued ${succeeded} skill${succeeded === 1 ? "" : "s"} to run` +
          (failed ? ` — ${failed} failed to start.` : ".") +
          " Check the Results tab for progress."
      );
      // Surface one representative reason so a wholesale failure (e.g. the
      // project has no environment URL configured) is actionable rather
      // than just a count.
      if (failed > 0) {
        const firstReason = results.find(
          (r): r is PromiseRejectedResult => r.status === "rejected"
        )?.reason;
        const detail =
          firstReason instanceof Error ? firstReason.message : String(firstReason ?? "");
        if (detail) setError(detail);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Bulk run failed");
    } finally {
      setBulkPending(false);
    }
  };

  // The unresolved filter counts as loading: the query is disabled until
  // the stored/default project is known, and a disabled query reports
  // "not loading", which would otherwise flash the empty state on mount.
  if (isLoading || projectFilter === UNRESOLVED_PROJECT) {
    return (
      <div className="space-y-3">
        {[...Array(3)].map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
        Failed to load skills.
      </p>
    );
  }

  const skills = data?.data ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));
  // Compared against the filtered total, not the visible page, so the box
  // only reads "checked" when the whole set really is selected.
  const allSelected = total > 0 && selectedIds.size >= total;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="text-sm text-gray-500 max-w-2xl space-y-2">
          <p>
            Every saved test for one project, from either source: a{" "}
            <span className="font-medium text-gray-700">Recorded</span> skill is
            captured automatically when a goal-based test passes, and a{" "}
            <span className="font-medium text-gray-700">Prompt</span> skill is
            extracted from a parsed SOW or video checkpoint before it has ever
            run.
          </p>
          <p>
            Run replays a recorded skill&apos;s browser actions directly — no AI
            planning, no tokens. Running a prompt skill starts a fresh AI-planned
            run, and a pass upgrades that same skill into a recorded one. Both
            sign in using the project&apos;s Test setup login, and both stream
            live under Results.
          </p>
          <p>
            Select skills to run, reassign or delete them in bulk. Use Edit to
            rename a skill or rewrite its instructions.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-600 flex-shrink-0 cursor-pointer">
          <Checkbox
            checked={allowFallback}
            onCheckedChange={(checked) => setAllowFallback(checked === true)}
          />
          Fall back to AI planning if replay fails
        </label>
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        {/*
          One button replaces the old "Login" dropdown + "Configure
          environments" pair. Those exposed three concepts (environment
          label, base URL, default login) for what is really a single
          decision — which login — since a bypass credential profile
          already carries its own start URL.

          Only meaningful for a real project: setup is per-project, so
          there is nothing to configure while "No project" is selected.
        */}
        {hasSingleProject && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9"
                    onClick={() => setEnvDialogOpen(true)}
                  >
                    Test setup
                  </Button>
                }
              />
              <TooltipContent className="max-w-xs">
                Where this project&apos;s tests run and which login they use.
                Skills created from a SOW have no login of their own, so they
                use this.
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}

        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-500">Project</span>
          <Select value={projectFilter} onValueChange={handleFilterChange}>
            <SelectTrigger className="w-auto min-w-[180px] h-9 text-sm">
              <SelectValue placeholder="Loading…">
                {(value: string | null) => {
                  if (!value) return "Loading…";
                  if (value === NO_PROJECT_VALUE) return "No project";
                  return projects.find((p) => p.id === value)?.name || "No project";
                }}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_PROJECT_VALUE}>No project</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-500">Sort by</span>
          <Select value={sortValue} onValueChange={(v) => handleSortChange(v ?? DEFAULT_SORT)}>
            <SelectTrigger className="w-auto min-w-[200px] h-9 text-sm">
              <SelectValue placeholder={SORT_OPTIONS[0].label}>
                {(value: string | null) =>
                  SORT_OPTIONS.find((o) => o.value === value)?.label || SORT_OPTIONS[0].label
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
          {error}
        </p>
      )}

      {bulkMessage && (
        <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-md px-3 py-2">
          {bulkMessage}
        </p>
      )}

      {skills.length === 0 ? (
        <div className="text-center py-16 text-gray-400 text-sm">
          {projectFilter === NO_PROJECT_VALUE
            ? "No unassigned skills. Every saved skill belongs to a project — pick one above."
            : "No skills for this project yet. They appear here automatically once a goal-based test passes, or as soon as a SOW/video checkpoint is parsed."}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer select-none">
              <Checkbox
                checked={allSelected}
                disabled={selectAllPending}
                onCheckedChange={() => toggleSelectAll(allSelected)}
                aria-label={`Select all ${total} skills`}
              />
              {selectAllPending
                ? "Selecting…"
                : `Select all ${total} skill${total === 1 ? "" : "s"}`}
            </label>

            {selectedIds.size > 0 && (
              <div className="flex items-center gap-2 flex-wrap bg-gray-50 border border-gray-200 rounded-md px-3 py-2">
                <span className="text-xs font-medium text-gray-600">
                  {selectedIds.size} selected
                </span>
                <Select
                  value=""
                  onValueChange={(v) => v && handleBulkAssignProject(v)}
                  disabled={bulkPending}
                >
                  <SelectTrigger className="w-auto min-w-[160px] h-8 text-xs">
                    <SelectValue placeholder="Assign to project…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_PROJECT_VALUE}>No project</SelectItem>
                    {projects.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  onClick={handleBulkRun}
                  disabled={bulkPending}
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs"
                >
                  Run selected
                </Button>
                {/* Label stays "Delete" so the button keeps its designed
                    collapse/expand geometry; the toolbar it sits in already
                    reads "N selected", and aria-label carries the full intent. */}
                <DeleteIconButton
                  onClick={handleBulkDelete}
                  disabled={bulkPending}
                  aria-label="Delete selected skills"
                />
                <Button
                  onClick={clearSelection}
                  disabled={bulkPending}
                  size="sm"
                  variant="ghost"
                  className="h-8 text-xs text-gray-400"
                >
                  Clear
                </Button>
              </div>
            )}
          </div>

          {skills.map((skill) => (
            <Card key={skill.id} className="shadow-sm">
              <CardContent className="pt-4 pb-4 flex items-center gap-4">
                <Checkbox
                  checked={selectedIds.has(skill.id)}
                  onCheckedChange={() => toggleSelect(skill.id)}
                  className="flex-shrink-0"
                  aria-label={`Select ${skill.name}`}
                />
                <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center">
                  <svg className="w-5 h-5" viewBox="0 0 20 20" fill="none">
                    <path
                      d="M11 2L4 11h5l-1 7 7-9h-5l1-7z"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinejoin="round"
                    />
                  </svg>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <button
                      onClick={() => {
                        setDetailSkill(skill);
                        setDetailEditing(false);
                      }}
                      className="text-sm font-medium text-gray-900 truncate hover:underline text-left"
                    >
                      {skill.name}
                    </button>
                    <span
                      className={`flex-shrink-0 text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded border ${
                        skill.has_recording
                          ? "text-indigo-600 border-indigo-300 bg-indigo-50"
                          : "text-amber-600 border-amber-300 bg-amber-50"
                      }`}
                    >
                      {skill.has_recording ? "Recorded" : "Prompt"}
                    </span>
                    {skill.source_type && (
                      <span className="flex-shrink-0 text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded border text-gray-500 border-gray-300 bg-gray-50">
                        from {skill.source_type}
                      </span>
                    )}
                    {skill.test_type && skill.test_type !== "positive" && (
                      /* A negative skill PASSES when the system refuses the
                         action, so a red replay result on it means the
                         product ACCEPTED something it should have rejected —
                         the opposite reading from a red positive skill. The
                         label has to be on the row where the result is. */
                      <span
                        title={
                          (skill.test_type === "negative"
                            ? "Negative test — passes when the system correctly refuses or safely rejects the action. The action succeeding is a FAIL."
                            : "Edge-case test — passes when behaviour is defined and consistent, not necessarily when the action succeeds.") +
                          (skill.grounding === "derived"
                            ? " Expectation inferred from standard QA practice, not stated in the source document."
                            : "")
                        }
                        className={`flex-shrink-0 text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded border ${
                          skill.test_type === "negative"
                            ? "text-red-700 border-red-300 bg-red-50"
                            : "text-amber-700 border-amber-300 bg-amber-50"
                        }`}
                      >
                        {skill.test_type === "negative" ? "negative" : "edge"}
                        {skill.grounding === "derived" ? " · derived" : ""}
                      </span>
                    )}
                    {skill.review_status && (
                      /* The requirement is real but its source never spelled
                         out how to execute it. Surfaced so it can be
                         clarified — never silently presented as runnable. */
                      <span
                        title={skill.review_reason || undefined}
                        className="flex-shrink-0 text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded border text-amber-700 border-amber-300 bg-amber-50"
                      >
                        {skill.review_status === "needs_design_flow"
                          ? "needs design flow"
                          : "needs review"}
                      </span>
                    )}
                    <span className="flex-shrink-0 text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded border text-purple-600 border-purple-300 bg-purple-50">
                      {skill.project_name || "No project"}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 flex-wrap">
                    <span>
                      {skill.has_recording
                        ? `${skill.step_count} recorded steps`
                        : "not yet run"}
                    </span>
                    <span>·</span>
                    <span>
                      replayed {skill.times_replayed}
                      {skill.times_replayed === 1 ? " time" : " times"}
                    </span>
                    {skill.last_replay_status && (
                      <>
                        <span>·</span>
                        <span className="flex items-center gap-1.5">
                          last replay
                          <RunStatusBadge status={skill.last_replay_status} />
                        </span>
                      </>
                    )}
                    <span>·</span>
                    <span>
                      saved{" "}
                      {new Date(skill.updated_at).toLocaleDateString("en-GB")}
                    </span>
                  </div>
                </div>
                {/* Edit · Run · Delete as one three-segment option group, the
                    same control the SOW library row uses. Run keeps its dark
                    `default` fill as the middle segment, so the primary action
                    still reads as primary without breaking the pill. Segment
                    widths and corner rounding come from `.btn-option-group` in
                    app/global.css. */}
                <div className="btn-option-group flex-shrink-0">
                  <Button
                    onClick={() => {
                      setDetailSkill(skill);
                      setDetailEditing(true);
                    }}
                    variant="outline"
                    size="sm"
                  >
                    Edit
                  </Button>
                  <Button
                    onClick={() => handleReplay(skill)}
                    disabled={replayingId !== null}
                    size="sm"
                    className="gap-1.5"
                  >
                    <svg className="w-4 h-4" viewBox="0 0 16 16" fill="currentColor">
                      <path d="M5 3.5v9l7-4.5-7-4.5z" />
                    </svg>
                    {replayingId === skill.id
                      ? "Starting…"
                      : skill.has_recording
                      ? "Replay"
                      : "Run"}
                  </Button>
                  <DeleteIconButton
                    onClick={() => handleDelete(skill)}
                    aria-label={`Delete skill ${skill.name}`}
                  />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-gray-500">
          <span>
            Page {page} of {totalPages} · {total} skills
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => handlePageChange(page - 1)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => handlePageChange(page + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {detailSkill && (
        <SkillDetailModal
          skill={detailSkill}
          projects={projects}
          initialEditing={detailEditing}
          onClose={() => setDetailSkill(null)}
          onSaved={handleSaved}
        />
      )}

      {envDialogOpen && hasSingleProject && (
        <ProjectTestSetupDialog
          projectId={projectFilter}
          projectName={
            projects.find((p) => p.id === projectFilter)?.name ?? "Project"
          }
          environments={
            projects.find((p) => p.id === projectFilter)?.environments ?? []
          }
          onClose={() => setEnvDialogOpen(false)}
        />
      )}
    </div>
  );
}
