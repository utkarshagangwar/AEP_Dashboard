"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiFetch } from "@/utils/apiClient";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RunStatusBadge, type Skill } from "./shared";
import SkillDetailModal from "./SkillDetailModal";

interface Project {
  id: string;
  name: string;
}

interface SkillListResponse {
  data: Skill[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 20;
const ALL_PROJECTS_VALUE = "__all__";
const NO_PROJECT_VALUE = "__none__";

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
 * Skills are scoped per project (via the same filter used everywhere else
 * in Vibe Testing) so that with multiple projects in play, a skill written
 * for one app is never confused with — or accidentally run against —
 * another.
 */
export default function SkillsTab({
  onReplayStarted,
}: {
  onReplayStarted: (runId: string, goal: string) => void;
}) {
  const [page, setPage] = useState(1);
  const [projectFilter, setProjectFilter] = useState<string>(ALL_PROJECTS_VALUE);
  const [sortValue, setSortValue] = useState<string>(DEFAULT_SORT);
  const [allowFallback, setAllowFallback] = useState(false);
  const [replayingId, setReplayingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailSkill, setDetailSkill] = useState<Skill | null>(null);
  const [detailEditing, setDetailEditing] = useState(false);
  // Bulk actions: selection is page-scoped — cleared whenever the visible
  // set changes (page/filter/sort) so a selection can never silently point
  // at rows the user can no longer see.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ["ai-environments"],
    queryFn: () => apiGet("/api/ai-testing/environments"),
    staleTime: 60_000,
  });

  const [sortBy, sortDir] = sortValue.split(":");

  const { data, isLoading, isError } = useQuery<SkillListResponse>({
    queryKey: ["ai-skills", page, projectFilter, sortValue],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(page),
        limit: String(LIMIT),
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      if (projectFilter === NO_PROJECT_VALUE) params.set("project_id", "none");
      else if (projectFilter !== ALL_PROJECTS_VALUE) params.set("project_id", projectFilter);
      return apiGet(`/api/ai-testing/skills?${params.toString()}`);
    },
  });

  const clearSelection = () => setSelectedIds(new Set());

  const handleFilterChange = (value: string) => {
    setProjectFilter(value ?? ALL_PROJECTS_VALUE);
    setPage(1);
    clearSelection();
  };

  const handleSortChange = (value: string) => {
    setSortValue(value ?? DEFAULT_SORT);
    setPage(1);
    clearSelection();
  };

  const handlePageChange = (next: number) => {
    setPage(next);
    clearSelection();
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAllOnPage = (skills: Skill[]) => {
    setSelectedIds((prev) => {
      const allSelected = skills.length > 0 && skills.every((s) => prev.has(s.id));
      if (allSelected) return new Set();
      return new Set(skills.map((s) => s.id));
    });
  };

  const handleReplay = async (skill: Skill) => {
    setReplayingId(skill.id);
    setError(null);
    try {
      const resp = await apiFetch(`/api/ai-testing/skills/${skill.id}/replay`, {
        method: "POST",
        body: JSON.stringify({ allow_ai_fallback: allowFallback }),
      });
      if (!resp.ok) {
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
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete skill "${skill.name}"? This cannot be undone.`)
    ) {
      return;
    }
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
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `Delete ${ids.length} skill${ids.length === 1 ? "" : "s"}? This cannot be undone.`
      )
    ) {
      return;
    }
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
    setError(null);
    setBulkMessage(null);
    setBulkPending(true);
    try {
      const results = await Promise.allSettled(
        ids.map((id) =>
          apiFetch(`/api/ai-testing/skills/${id}/replay`, {
            method: "POST",
            body: JSON.stringify({ allow_ai_fallback: allowFallback }),
          }).then((resp) => {
            if (!resp.ok) throw new Error(`Server error (${resp.status})`);
            return resp.json();
          })
        )
      );
      const succeeded = results.filter((r) => r.status === "fulfilled").length;
      const failed = results.length - succeeded;
      clearSelection();
      queryClient.invalidateQueries({ queryKey: ["ai-skills"] });
      setBulkMessage(
        `Queued ${succeeded} skill${succeeded === 1 ? "" : "s"} to run` +
          (failed ? ` — ${failed} failed to start.` : ".") +
          " Check the Results tab for progress."
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Bulk run failed");
    } finally {
      setBulkPending(false);
    }
  };

  if (isLoading) {
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

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <p className="text-sm text-gray-500 max-w-2xl">
          Skills come from two places: recorded automatically whenever a
          goal-based test passes, or extracted directly from a parsed SOW/video
          checkpoint. Replaying a recorded skill re-executes its actions
          directly — no AI planning, no extra tokens; running a prompt skill
          starts a fresh AI-planned run and upgrades it to a recorded one if it
          passes.
        </p>
        <label className="flex items-center gap-2 text-sm text-gray-600 flex-shrink-0 cursor-pointer">
          <input
            type="checkbox"
            checked={allowFallback}
            onChange={(e) => setAllowFallback(e.target.checked)}
            className="rounded border-gray-300"
          />
          Fall back to AI planning if replay fails
        </label>
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-500">Project</span>
          <Select value={projectFilter} onValueChange={(v) => handleFilterChange(v ?? ALL_PROJECTS_VALUE)}>
            <SelectTrigger className="w-auto min-w-[180px] h-9 text-sm">
              <SelectValue placeholder="All projects">
                {(value: string | null) => {
                  if (!value || value === ALL_PROJECTS_VALUE) return "All projects";
                  if (value === NO_PROJECT_VALUE) return "No project";
                  return projects.find((p) => p.id === value)?.name || "All projects";
                }}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_PROJECTS_VALUE}>All projects</SelectItem>
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
          {projectFilter === ALL_PROJECTS_VALUE
            ? "No skills saved yet. Skills appear here automatically after a goal-based test passes, or as soon as a SOW/video checkpoint is parsed."
            : "No skills for this project yet."}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={skills.length > 0 && skills.every((s) => selectedIds.has(s.id))}
                onChange={() => toggleSelectAllOnPage(skills)}
                className="rounded border-gray-300"
              />
              Select all on this page
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
                <Button
                  onClick={handleBulkDelete}
                  disabled={bulkPending}
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs text-gray-500 hover:text-red-600 hover:border-red-300"
                >
                  Delete selected
                </Button>
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
                <input
                  type="checkbox"
                  checked={selectedIds.has(skill.id)}
                  onChange={() => toggleSelect(skill.id)}
                  className="flex-shrink-0 rounded border-gray-300"
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
                <Button
                  onClick={() => {
                    setDetailSkill(skill);
                    setDetailEditing(true);
                  }}
                  variant="outline"
                  size="sm"
                  className="flex-shrink-0"
                >
                  Edit
                </Button>
                <Button
                  onClick={() => handleReplay(skill)}
                  disabled={replayingId !== null}
                  size="sm"
                  className="gap-1.5 flex-shrink-0"
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
                <Button
                  onClick={() => handleDelete(skill)}
                  variant="outline"
                  size="sm"
                  className="flex-shrink-0 text-gray-500 hover:text-red-600 hover:border-red-300"
                >
                  Delete
                </Button>
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
    </div>
  );
}
