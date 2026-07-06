"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiFetch } from "@/utils/apiClient";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { RunStatusBadge } from "./shared";

interface Skill {
  id: string;
  name: string;
  goal: string;
  environment?: string | null;
  step_count: number;
  times_replayed: number;
  last_replay_status?: string | null;
  last_replayed_at?: string | null;
  created_at: string;
  updated_at: string;
}

interface SkillListResponse {
  data: Skill[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 20;

/**
 * Skills tab — recurring goals auto-saved from passed runs. Replaying a
 * skill re-executes the recorded browser actions directly (no LLM planning
 * tokens); the replay runs as a normal test run and streams live.
 */
export default function SkillsTab({
  onReplayStarted,
}: {
  onReplayStarted: (runId: string, goal: string) => void;
}) {
  const [page, setPage] = useState(1);
  const [allowFallback, setAllowFallback] = useState(false);
  const [replayingId, setReplayingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery<SkillListResponse>({
    queryKey: ["ai-skills", page],
    queryFn: () => apiGet(`/api/ai-testing/skills?page=${page}&limit=${LIMIT}`),
  });

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
          Skills are recorded automatically whenever a goal-based test passes.
          Replaying a skill re-executes the recorded browser actions directly —
          no AI planning, no extra tokens.
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

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
          {error}
        </p>
      )}

      {skills.length === 0 ? (
        <div className="text-center py-16 text-gray-400 text-sm">
          No skills saved yet. Skills appear here automatically after a
          goal-based test passes.
        </div>
      ) : (
        <div className="space-y-3">
          {skills.map((skill) => (
            <Card key={skill.id} className="shadow-sm">
              <CardContent className="pt-4 pb-4 flex items-center gap-4">
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
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {skill.name}
                  </p>
                  <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 flex-wrap">
                    <span>{skill.step_count} recorded steps</span>
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
                  onClick={() => handleReplay(skill)}
                  disabled={replayingId !== null}
                  size="sm"
                  className="gap-1.5 flex-shrink-0"
                >
                  <svg className="w-4 h-4" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M5 3.5v9l7-4.5-7-4.5z" />
                  </svg>
                  {replayingId === skill.id ? "Starting…" : "Replay"}
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
