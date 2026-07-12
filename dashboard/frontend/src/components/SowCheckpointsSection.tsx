"use client";

/**
 * SOW Checkpoints — Phase 3 UI for the Vibe Testing tab ("The Brain").
 *
 * Upload a SOW (.txt/.md/.pdf); the backend parses it into visual
 * checkpoints and functional skills — detailed, step-by-step prompt
 * instructions an AI agent can execute — cached in the Memory Bank (same
 * document is never parsed twice). Functional skills are saved straight to
 * the Skills tab as soon as parsing finishes, no live browser run required;
 * each can also be sent to the Vibe goal box via onUseGoal for a one-off run.
 *
 * Feature-detected like VisualAuditSection: if the backend flag is off the
 * probe 404s and this component renders nothing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { apiDelete, apiFetch, apiGet, apiPost } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

interface Sow {
  id: string;
  file_name: string;
  parse_status: "not_required" | "pending" | "processing" | "done" | "error";
  parse_error?: string | null;
  checkpoint_count: number;
  total_parts: number;
  reused?: boolean;
  platform_name?: string | null;
  created_at: string;
}

interface Checkpoint {
  type: "functional" | "visual";
  title: string;
  description: string;
  role?: string | null;
  objective?: string | null;
  context?: string | null;
  instructions?: string[];
  notes?: string[];
  page?: string | null;
  expected?: string | null;
}

interface Part {
  part_number: number;
  total_parts: number;
  status: "pending" | "processing" | "done" | "error";
  error?: string | null;
  checkpoint_count: number;
  char_count: number;
  preview: string;
}

// Anything in this set keeps the poll loop running (see the effect below) —
// "pending" is included because a just-uploaded single-part document sits
// briefly in "pending" before a worker picks it up, and polling must keep
// running through that window to catch the eventual "processing" → "done"
// transition without a manual refresh.
const ACTIVE = new Set(["pending", "processing"]);

// What the top-level badge/status text should say — unlike ACTIVE (which
// answers "should we keep polling"), this must NOT claim work is happening
// while "pending" (queued/idle, e.g. a multi-part document just sitting
// there waiting for the user to click Analyse on the next part): only
// "processing" means a worker is actually parsing something right now.
function statusLabel(status: string, activeLabel: string): string {
  if (status === "processing") return activeLabel;
  if (status === "pending") return "pending";
  return status;
}

// Variant config: same pipeline, different source document type (Phase 3 vs 5)
const VARIANTS = {
  sow: {
    endpoint: "/api/v1/visual-audits/sow",
    title: "SOW Checkpoints",
    description:
      "Upload a requirements document — the AI extracts detailed, step-by-step " +
      "skills the agent can run directly (saved to the Skills tab automatically, " +
      "no live run needed). Parsed once, remembered forever.",
    accept: ".txt,.md,.pdf",
    uploadLabel: "Upload SOW (.txt / .md / .pdf)",
    emptyLabel: "No documents uploaded yet.",
    activeLabel: "parsing…",
    workingLabel: "Extracting checkpoints…",
    noneFoundLabel: "No testable requirements found in this document.",
    maxSizeMB: null as number | null,
    requiresPlatformName: false,
  },
  video: {
    endpoint: "/api/v1/visual-audits/video",
    title: "Video Walkthrough",
    description:
      "Upload a design walkthrough video — the AI watches it and extracts detailed, " +
      "step-by-step skills the agent can run directly (saved to the Skills tab " +
      "automatically, no live run needed). Each video is digested once and cached.",
    accept: ".mp4,.webm,.mov",
    uploadLabel: "Upload video (.mp4 / .webm / .mov)",
    emptyLabel: "No videos uploaded yet.",
    activeLabel: "digesting…",
    workingLabel: "Watching the video and extracting checkpoints — this can take a few minutes…",
    noneFoundLabel: "No testable requirements found in this video.",
    maxSizeMB: 50 as number | null,
    // Mandatory so the AI has a declared identity to check on-screen content
    // against instead of guessing/assuming — see backend video_ingest.py.
    requiresPlatformName: true,
  },
} as const;

// FastAPI error bodies aren't always a plain string: a raised HTTPException
// gives {detail: string}, but automatic request-validation failures (422)
// give {detail: [{msg, loc, type}, ...]} — passing that array straight into
// `new Error()` silently stringifies it to "[object Object]" instead of a
// readable message.
function extractErrorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return fallback;
}

const STATUS_STYLES: Record<string, string> = {
  done: "text-green-600 border-green-300 bg-green-50",
  error: "text-red-600 border-red-300 bg-red-50",
  pending: "text-gray-600 border-gray-300 bg-gray-50",
  processing: "text-blue-600 border-blue-300 bg-blue-50",
};

export default function SowCheckpointsSection({
  onUseGoal,
  variant = "sow",
}: {
  onUseGoal?: (goal: string) => void;
  variant?: keyof typeof VARIANTS;
}) {
  const cfg = VARIANTS[variant];
  const [enabled, setEnabled] = useState(false);
  const [sows, setSows] = useState<Sow[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [checkpoints, setCheckpoints] = useState<Record<string, Checkpoint[]>>({});
  const [parts, setParts] = useState<Record<string, Part[]>>({});
  const [analyzingPart, setAnalyzingPart] = useState<Record<string, number | null>>({});
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reusedNotice, setReusedNotice] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Video only: the product this walkthrough is about. Mandatory — without
  // it the AI has no declared identity to check on-screen content against
  // and will guess, which is exactly what produced wrong checkpoints before.
  const [platformName, setPlatformName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadSows = useCallback(async () => {
    try {
      const data = await apiGet(cfg.endpoint);
      setSows(data);
      setEnabled(true);
      return data as Sow[];
    } catch {
      setEnabled(false); // 404 → flag off: render nothing
      return [];
    }
  }, []);

  useEffect(() => {
    loadSows();
  }, [loadSows]);

  // Refresh the expanded document's parts/checkpoints while it has a
  // multi-part analysis in flight (list-level polling already covers the
  // "any pending/processing" case; this keeps the parts sub-list live too).
  const refreshExpandedDetail = useCallback(async () => {
    if (!expanded) return;
    const sow = sows.find((s) => s.id === expanded);
    if (!sow || sow.total_parts <= 1) return;
    try {
      const detail = await apiGet(`${cfg.endpoint}/${sow.id}`);
      setCheckpoints((prev) => ({ ...prev, [sow.id]: detail.checkpoints || [] }));
      setParts((prev) => ({ ...prev, [sow.id]: detail.parts || [] }));
    } catch {
      // Silent — the next poll tick retries.
    }
  }, [expanded, sows]);

  // Poll while any document is being parsed; parse always reaches a
  // terminal state server-side (done/error), so polling always stops.
  useEffect(() => {
    const anyActive = sows.some((s) => ACTIVE.has(s.parse_status));
    if (!anyActive) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(() => {
      loadSows();
      refreshExpandedDetail();
    }, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [sows, loadSows, refreshExpandedDetail]);

  const handleUpload = async (file: File) => {
    setError(null);
    setReusedNotice(null);

    const trimmedPlatformName = platformName.trim();
    if (cfg.requiresPlatformName && !trimmedPlatformName) {
      setError("Enter the platform/product name this video walks through before uploading.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    if (cfg.maxSizeMB && file.size > cfg.maxSizeMB * 1024 * 1024) {
      setError(
        `"${file.name}" is ${(file.size / (1024 * 1024)).toFixed(1)}MB, which exceeds the ${cfg.maxSizeMB}MB limit. Trim it and try again.`
      );
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      if (cfg.requiresPlatformName) {
        form.append("platform_name", trimmedPlatformName);
      }
      const res = await apiFetch(cfg.endpoint, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(extractErrorMessage(body, `Upload failed (${res.status})`));
      }
      const uploaded = await res.json().catch(() => null);
      await loadSows();
      if (uploaded?.reused) {
        setReusedNotice(uploaded.file_name || "Document");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const toggleExpand = async (sow: Sow) => {
    if (expanded === sow.id) {
      setExpanded(null);
      return;
    }
    setExpanded(sow.id);
    const shouldFetch = sow.total_parts > 1 || (sow.parse_status === "done" && !checkpoints[sow.id]);
    if (shouldFetch) {
      try {
        const detail = await apiGet(`${cfg.endpoint}/${sow.id}`);
        setCheckpoints((prev) => ({ ...prev, [sow.id]: detail.checkpoints || [] }));
        setParts((prev) => ({ ...prev, [sow.id]: detail.parts || [] }));
      } catch {
        setError("Could not load checkpoints for this document.");
      }
    }
  };

  const handleAnalyzePart = async (sow: Sow, partNumber: number) => {
    setAnalyzingPart((prev) => ({ ...prev, [sow.id]: partNumber }));
    setError(null);
    try {
      const detail = await apiPost(`${cfg.endpoint}/${sow.id}/parts/${partNumber}/analyze`, {});
      setCheckpoints((prev) => ({ ...prev, [sow.id]: detail.checkpoints || [] }));
      setParts((prev) => ({ ...prev, [sow.id]: detail.parts || [] }));
      await loadSows();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start analysis for this part");
    } finally {
      setAnalyzingPart((prev) => ({ ...prev, [sow.id]: null }));
    }
  };

  const handleDelete = async (sow: Sow) => {
    if (!window.confirm(`Delete "${sow.file_name}"? This also removes its extracted checkpoints.`)) {
      return;
    }
    setDeletingId(sow.id);
    setError(null);
    try {
      await apiDelete(`${cfg.endpoint}/${sow.id}`);
      setCheckpoints((prev) => {
        const next = { ...prev };
        delete next[sow.id];
        return next;
      });
      setParts((prev) => {
        const next = { ...prev };
        delete next[sow.id];
        return next;
      });
      if (expanded === sow.id) setExpanded(null);
      await loadSows();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this document.");
    } finally {
      setDeletingId(null);
    }
  };

  if (!enabled) return null;

  return (
    <Card className="shadow-sm mt-6">
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">{cfg.title}</CardTitle>
            <p className="text-sm text-gray-500 mt-1">{cfg.description}</p>
          </div>
          <Badge
            variant="outline"
            className="text-purple-600 border-purple-300 bg-purple-50 flex-shrink-0"
          >
            Beta
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <input
          ref={fileInputRef}
          type="file"
          accept={cfg.accept}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleUpload(f);
          }}
        />
        {cfg.requiresPlatformName && (
          <div className="space-y-1">
            <label htmlFor={`${variant}-platform-name`} className="text-sm text-gray-700 font-medium">
              Platform / product name <span className="text-red-500">*</span>
            </label>
            <Input
              id={`${variant}-platform-name`}
              value={platformName}
              onChange={(e) => setPlatformName(e.target.value)}
              placeholder="e.g. Acme Recruiting Portal"
              className="h-9 text-sm max-w-sm"
              disabled={uploading}
            />
            <p className="text-xs text-gray-400">
              Required — tells the AI what application this video walks through so it never has
              to guess (or assume) what it's looking at.
            </p>
          </div>
        )}
        <Button
          variant="outline"
          className="h-9 text-sm"
          disabled={uploading || (cfg.requiresPlatformName && !platformName.trim())}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? "Uploading…" : cfg.uploadLabel}
        </Button>
        {cfg.maxSizeMB && (
          <p className="text-xs text-gray-400 -mt-2">Max file size: {cfg.maxSizeMB}MB</p>
        )}

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {error}
          </p>
        )}

        {reusedNotice && (
          <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-md px-3 py-2">
            ⚡ "{reusedNotice}" matched a previously analyzed document — reused saved skill, no
            AI credits used.
          </p>
        )}

        {sows.length === 0 ? (
          <p className="text-sm text-gray-400">{cfg.emptyLabel}</p>
        ) : (
          <div className="space-y-2">
            {sows.map((sow) => (
              <div key={sow.id} className="border border-gray-200 rounded-md">
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => toggleExpand(sow)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggleExpand(sow);
                    }
                  }}
                  className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-gray-50 transition-colors cursor-pointer"
                >
                  <span className="text-sm text-gray-800 truncate mr-3">
                    {sow.file_name}
                    {sow.platform_name && (
                      <span className="text-gray-400 font-normal"> — {sow.platform_name}</span>
                    )}
                  </span>
                  <span className="flex items-center gap-2 flex-shrink-0">
                    {sow.parse_status === "done" && (
                      <span className="text-xs text-gray-400">
                        {sow.checkpoint_count} checkpoint
                        {sow.checkpoint_count === 1 ? "" : "s"}
                      </span>
                    )}
                    <Badge
                      variant="outline"
                      className={STATUS_STYLES[sow.parse_status] || ""}
                    >
                      {statusLabel(sow.parse_status, cfg.activeLabel)}
                    </Badge>
                    <button
                      type="button"
                      aria-label={`Delete ${sow.file_name}`}
                      title="Delete"
                      disabled={deletingId === sow.id}
                      className="text-gray-400 hover:text-red-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(sow);
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </span>
                </div>

                {expanded === sow.id && (
                  <div className="border-t border-gray-100 px-3 py-2 space-y-2">
                    {sow.parse_status === "error" && (
                      <p className="text-sm text-red-600">
                        {sow.parse_error || "Parsing failed."} Re-upload the
                        file to retry.
                      </p>
                    )}
                    {sow.total_parts <= 1 && sow.parse_status === "processing" && (
                      <p className="text-sm text-gray-500">{cfg.workingLabel}</p>
                    )}
                    {sow.total_parts <= 1 && sow.parse_status === "pending" && (
                      <p className="text-sm text-gray-500">Queued — waiting for a worker to pick it up…</p>
                    )}
                    {sow.total_parts > 1 && (
                      <div className="space-y-1.5 border border-gray-200 rounded-md p-2 bg-gray-50">
                        <p className="text-xs font-medium text-gray-500">
                          Parts (
                          {(parts[sow.id] || []).filter((p) => p.status === "done").length} of{" "}
                          {sow.total_parts} analyzed)
                        </p>
                        {(parts[sow.id] || []).length === 0 ? (
                          <p className="text-xs text-gray-400">Loading parts…</p>
                        ) : (
                          (parts[sow.id] || []).map((p) => {
                            const anyProcessing = (parts[sow.id] || []).some(
                              (pp) => pp.status === "processing"
                            );
                            const isSubmitting = analyzingPart[sow.id] === p.part_number;
                            const disableButton = anyProcessing || analyzingPart[sow.id] != null;
                            return (
                              <div
                                key={p.part_number}
                                className="flex items-center justify-between gap-3 py-1 border-b border-gray-100 last:border-0"
                              >
                                <div className="min-w-0">
                                  <span className="text-xs font-medium text-gray-700">
                                    Part {p.part_number} of {p.total_parts}
                                  </span>
                                  <p className="text-xs text-gray-500 truncate">{p.preview}</p>
                                  {p.status === "error" && (
                                    <p className="text-xs text-red-600 mt-0.5">
                                      {p.error || "Analysis failed."}
                                    </p>
                                  )}
                                </div>
                                <span className="flex-shrink-0 flex items-center gap-1.5">
                                  {p.status === "done" && (
                                    <>
                                      <Badge
                                        variant="outline"
                                        className="text-green-600 border-green-300 bg-green-50"
                                      >
                                        ✓ {p.checkpoint_count} checkpoint
                                        {p.checkpoint_count === 1 ? "" : "s"}
                                      </Badge>
                                    </>
                                  )}
                                  {p.status === "processing" && (
                                    <Badge
                                      variant="outline"
                                      className="text-blue-600 border-blue-300 bg-blue-50"
                                    >
                                      Analysing…
                                    </Badge>
                                  )}
                                  {(p.status === "pending" || p.status === "error") && (
                                    <Button
                                      variant="outline"
                                      className="h-7 text-xs"
                                      disabled={disableButton}
                                      onClick={() => handleAnalyzePart(sow, p.part_number)}
                                    >
                                      {isSubmitting
                                        ? "Starting…"
                                        : p.status === "error"
                                        ? "Retry"
                                        : "Analyse"}
                                    </Button>
                                  )}
                                </span>
                              </div>
                            );
                          })
                        )}
                      </div>
                    )}
                    {(sow.total_parts > 1 || sow.parse_status === "done") &&
                      (checkpoints[sow.id] ? (
                        checkpoints[sow.id].length === 0 ? (
                          <p className="text-sm text-gray-500">
                            {sow.total_parts > 1 && sow.parse_status !== "done"
                              ? "No checkpoints extracted yet — analyze a part above."
                              : cfg.noneFoundLabel}
                          </p>
                        ) : (
                          checkpoints[sow.id].map((cp, i) => {
                            const hasStructure =
                              cp.type === "functional" &&
                              (cp.instructions?.length ?? 0) > 0;
                            return (
                              <div
                                key={i}
                                className="flex items-start justify-between gap-3 py-2 border-b border-gray-50 last:border-0"
                              >
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-2">
                                    <Badge
                                      variant="outline"
                                      className={
                                        cp.type === "visual"
                                          ? "text-purple-600 border-purple-300 bg-purple-50"
                                          : "text-blue-600 border-blue-300 bg-blue-50"
                                      }
                                    >
                                      {cp.type}
                                    </Badge>
                                    <span className="text-sm font-medium text-gray-800 truncate">
                                      {cp.title}
                                    </span>
                                  </div>

                                  {hasStructure ? (
                                    <div className="mt-1.5 space-y-1.5 text-sm text-gray-600 border-l-2 border-blue-100 pl-3">
                                      {cp.role && (
                                        <div>
                                          <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mr-1.5">
                                            Role
                                          </span>
                                          {cp.role}
                                        </div>
                                      )}
                                      <div>
                                        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mr-1.5">
                                          Objective
                                        </span>
                                        {cp.objective}
                                      </div>
                                      {cp.context && (
                                        <div>
                                          <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mr-1.5">
                                            Context
                                          </span>
                                          {cp.context}
                                        </div>
                                      )}
                                      <div>
                                        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide block mb-0.5">
                                          Instructions
                                        </span>
                                        <ol className="list-decimal list-inside space-y-0.5">
                                          {cp.instructions!.map((step, si) => (
                                            <li key={si}>{step}</li>
                                          ))}
                                        </ol>
                                      </div>
                                      {cp.notes && cp.notes.length > 0 && (
                                        <div>
                                          <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide block mb-0.5">
                                            Notes
                                          </span>
                                          <ul className="list-disc list-inside space-y-0.5 text-gray-500">
                                            {cp.notes.map((note, ni) => (
                                              <li key={ni}>{note}</li>
                                            ))}
                                          </ul>
                                        </div>
                                      )}
                                      {cp.page && (
                                        <div className="text-xs text-gray-400">
                                          Page: {cp.page}
                                        </div>
                                      )}
                                    </div>
                                  ) : (
                                    <p className="text-sm text-gray-600 mt-0.5">
                                      {cp.description}
                                      {cp.page ? (
                                        <span className="text-gray-400"> — {cp.page}</span>
                                      ) : null}
                                    </p>
                                  )}

                                  {cp.expected && (
                                    <p className="text-xs text-gray-400 mt-1">
                                      Expected: {cp.expected}
                                    </p>
                                  )}
                                </div>
                                {onUseGoal && cp.type === "functional" && (
                                  <Button
                                    variant="outline"
                                    className="h-7 text-xs flex-shrink-0"
                                    onClick={() => onUseGoal(cp.description)}
                                  >
                                    Use as goal
                                  </Button>
                                )}
                              </div>
                            );
                          })
                        )
                      ) : (
                        <p className="text-sm text-gray-400">Loading…</p>
                      ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
