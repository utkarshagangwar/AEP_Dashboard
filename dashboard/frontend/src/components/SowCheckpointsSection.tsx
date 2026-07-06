"use client";

/**
 * SOW Checkpoints — Phase 3 UI for the Vibe Testing tab ("The Brain").
 *
 * Upload a SOW (.txt/.md/.pdf); the backend parses it into testable
 * checkpoints (cached in the Memory Bank — same document is never parsed
 * twice). Each checkpoint can be sent to the Vibe goal box via onUseGoal.
 *
 * Feature-detected like VisualAuditSection: if the backend flag is off the
 * probe 404s and this component renders nothing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, apiGet } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface Sow {
  id: string;
  file_name: string;
  parse_status: "not_required" | "pending" | "processing" | "done" | "error";
  parse_error?: string | null;
  checkpoint_count: number;
  created_at: string;
}

interface Checkpoint {
  type: "functional" | "visual";
  title: string;
  description: string;
  page?: string | null;
  expected?: string | null;
}

const ACTIVE = new Set(["pending", "processing"]);

// Variant config: same pipeline, different source document type (Phase 3 vs 5)
const VARIANTS = {
  sow: {
    endpoint: "/api/v1/visual-audits/sow",
    title: "SOW Checkpoints",
    description:
      "Upload a requirements document — the AI extracts testable checkpoints " +
      "you can run as Vibe goals. Parsed once, remembered forever.",
    accept: ".txt,.md,.pdf",
    uploadLabel: "Upload SOW (.txt / .md / .pdf)",
    emptyLabel: "No documents uploaded yet.",
    activeLabel: "parsing…",
    workingLabel: "Extracting checkpoints…",
    noneFoundLabel: "No testable requirements found in this document.",
  },
  video: {
    endpoint: "/api/v1/visual-audits/video",
    title: "Video Walkthrough",
    description:
      "Upload a design walkthrough video — the AI watches it and extracts " +
      "testable checkpoints. Each video is digested once and cached.",
    accept: ".mp4,.webm,.mov",
    uploadLabel: "Upload video (.mp4 / .webm / .mov)",
    emptyLabel: "No videos uploaded yet.",
    activeLabel: "digesting…",
    workingLabel: "Watching the video and extracting checkpoints — this can take a few minutes…",
    noneFoundLabel: "No testable requirements found in this video.",
  },
} as const;

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
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  // Poll while any document is being parsed; parse always reaches a
  // terminal state server-side (done/error), so polling always stops.
  useEffect(() => {
    const anyActive = sows.some((s) => ACTIVE.has(s.parse_status));
    if (!anyActive) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(loadSows, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [sows, loadSows]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch(cfg.endpoint, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || "Upload failed");
      }
      await loadSows();
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
    if (sow.parse_status === "done" && !checkpoints[sow.id]) {
      try {
        const detail = await apiGet(`${cfg.endpoint}/${sow.id}`);
        setCheckpoints((prev) => ({ ...prev, [sow.id]: detail.checkpoints || [] }));
      } catch {
        setError("Could not load checkpoints for this document.");
      }
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
        <Button
          variant="outline"
          className="h-9 text-sm"
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? "Uploading…" : cfg.uploadLabel}
        </Button>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {error}
          </p>
        )}

        {sows.length === 0 ? (
          <p className="text-sm text-gray-400">{cfg.emptyLabel}</p>
        ) : (
          <div className="space-y-2">
            {sows.map((sow) => (
              <div key={sow.id} className="border border-gray-200 rounded-md">
                <button
                  onClick={() => toggleExpand(sow)}
                  className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-gray-50 transition-colors"
                >
                  <span className="text-sm text-gray-800 truncate mr-3">
                    {sow.file_name}
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
                      {ACTIVE.has(sow.parse_status) ? cfg.activeLabel : sow.parse_status}
                    </Badge>
                  </span>
                </button>

                {expanded === sow.id && (
                  <div className="border-t border-gray-100 px-3 py-2 space-y-2">
                    {sow.parse_status === "error" && (
                      <p className="text-sm text-red-600">
                        {sow.parse_error || "Parsing failed."} Re-upload the
                        file to retry.
                      </p>
                    )}
                    {ACTIVE.has(sow.parse_status) && (
                      <p className="text-sm text-gray-500">{cfg.workingLabel}</p>
                    )}
                    {sow.parse_status === "done" &&
                      (checkpoints[sow.id] ? (
                        checkpoints[sow.id].length === 0 ? (
                          <p className="text-sm text-gray-500">{cfg.noneFoundLabel}</p>
                        ) : (
                          checkpoints[sow.id].map((cp, i) => (
                            <div
                              key={i}
                              className="flex items-start justify-between gap-3 py-1.5"
                            >
                              <div className="min-w-0">
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
                                <p className="text-sm text-gray-600 mt-0.5">
                                  {cp.description}
                                  {cp.page ? (
                                    <span className="text-gray-400"> — {cp.page}</span>
                                  ) : null}
                                </p>
                                {cp.expected && (
                                  <p className="text-xs text-gray-400 mt-0.5">
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
                          ))
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
