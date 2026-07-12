"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/utils/apiClient";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Skill } from "./shared";

interface Project {
  id: string;
  name: string;
}

const NO_PROJECT_VALUE = "__none__";

// Lightweight line-based rendering for the Role/Objective/Context/
// Instructions/Notes markdown a prompt skill's goal is built from (see
// design_ingest.render_skill_markdown) — no markdown library needed, this
// only ever has to understand '#', '##', and '-' line prefixes. A recorded
// skill's goal (plain free text a user typed) just falls through as
// ordinary paragraphs.
function renderGoal(goal: string) {
  return goal.split("\n").map((line, i) => {
    if (line.startsWith("## ")) {
      return (
        <p key={i} className="text-sm font-semibold text-gray-800 mt-2.5 first:mt-0">
          {line.slice(3)}
        </p>
      );
    }
    if (line.startsWith("# ")) {
      return (
        <p
          key={i}
          className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mt-3 first:mt-0"
        >
          {line.slice(2)}
        </p>
      );
    }
    if (line.startsWith("- ")) {
      return (
        <li key={i} className="text-sm text-gray-600 ml-4 list-disc">
          {line.slice(2)}
        </li>
      );
    }
    if (line.trim() === "") return null;
    return (
      <p key={i} className="text-sm text-gray-600">
        {line}
      </p>
    );
  });
}

export default function SkillDetailModal({
  skill,
  projects,
  initialEditing = false,
  onClose,
  onSaved,
}: {
  skill: Skill;
  projects: Project[];
  initialEditing?: boolean;
  onClose: () => void;
  onSaved: (updated: Skill) => void;
}) {
  const [editing, setEditing] = useState(initialEditing);
  const [name, setName] = useState(skill.name);
  const [goal, setGoal] = useState(skill.goal);
  const [projectId, setProjectId] = useState(skill.project_id || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-sync local edit state whenever a different skill is opened.
  useEffect(() => {
    setName(skill.name);
    setGoal(skill.goal);
    setProjectId(skill.project_id || "");
    setEditing(initialEditing);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skill.id]);

  const willClearRecording =
    editing && skill.has_recording && goal.trim() !== skill.goal.trim();

  const handleCancel = () => {
    setName(skill.name);
    setGoal(skill.goal);
    setProjectId(skill.project_id || "");
    setError(null);
    setEditing(false);
  };

  const handleSave = async () => {
    const body: Record<string, unknown> = {};
    if (name.trim() !== skill.name) body.name = name.trim();
    if (goal.trim() !== skill.goal) body.goal = goal.trim();
    if ((projectId || null) !== (skill.project_id || null)) {
      body.project_id = projectId || null;
    }
    if (Object.keys(body).length === 0) {
      setEditing(false);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const resp = await apiFetch(`/api/ai-testing/skills/${skill.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      const updated = await resp.json();
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save skill");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col">
        <div className="px-6 py-4 border-b border-gray-100 flex items-start justify-between gap-4 flex-shrink-0">
          <div className="min-w-0 flex-1">
            {editing ? (
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full text-lg font-semibold text-gray-900 border border-gray-200 rounded-md px-2 py-1 focus:outline-none focus:ring-2 focus:ring-gray-900"
              />
            ) : (
              <h2 className="text-lg font-semibold text-gray-900 truncate">
                {skill.name}
              </h2>
            )}
            <p className="text-xs text-gray-400 mt-1">
              {skill.has_recording ? "Recorded skill" : "Prompt skill"}
              {skill.source_type ? ` · from ${skill.source_type}` : ""}
              {skill.manually_edited ? " · manually edited" : ""}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 flex-shrink-0"
            aria-label="Close"
          >
            <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <div className="px-6 py-4 overflow-y-auto flex-1 space-y-4">
          <div>
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
              Project
            </label>
            {editing ? (
              <Select
                value={projectId || NO_PROJECT_VALUE}
                onValueChange={(v) => setProjectId((v ?? "") === NO_PROJECT_VALUE ? "" : v ?? "")}
              >
                <SelectTrigger className="w-full h-9 text-sm">
                  <SelectValue placeholder="No project">
                    {(value: string | null) => {
                      if (!value || value === NO_PROJECT_VALUE) return "No project";
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
            ) : (
              <p className="text-sm text-gray-700">
                {skill.project_name || "No project"}
              </p>
            )}
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
              Goal
            </label>
            {editing ? (
              <textarea
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                rows={16}
                className="w-full font-mono text-xs resize-y rounded-md border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-gray-900"
              />
            ) : (
              <div className="bg-gray-50 border border-gray-100 rounded-md px-4 py-3 space-y-0.5">
                {renderGoal(skill.goal)}
              </div>
            )}
          </div>

          {willClearRecording && (
            <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
              This skill has recorded actions from a previous run. Saving this change will
              clear that recording — the next run re-plans with AI and records fresh actions
              matching the new instructions.
            </p>
          )}

          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </p>
          )}
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3 flex-shrink-0">
          {editing ? (
            <>
              <Button variant="outline" onClick={handleCancel} disabled={saving}>
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={saving || !name.trim() || !goal.trim()}
              >
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
              <Button onClick={() => setEditing(true)}>Edit</Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
