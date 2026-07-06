"use client";

/**
 * Visual Audit — Phase 2 UI for the Vibe Testing tab.
 *
 * Self-contained and feature-detected: on mount it probes
 * GET /api/v1/visual-audits/references. If the backend feature flag
 * (VISUAL_AUDIT_ENABLED) is off, the API returns 404 and this component
 * renders NOTHING — the existing Vibe Testing UI is completely unchanged.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, apiGet, apiPost } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// ── Types ────────────────────────────────────────────────────────────────────

interface Reference {
  id: string;
  file_name: string;
  target_page?: string | null;
  // 'not_required' (direct upload) or 'done' (Figma import complete) = usable
  parse_status?: string;
}

interface Finding {
  engine: "pixel_diff" | "vision";
  severity: "critical" | "major" | "minor" | "info";
  element?: string | null;
  issue: string;
  expected?: string | null;
  actual?: string | null;
}

interface VisualRun {
  id: string;
  target_url: string;
  status: "pending" | "running" | "passed" | "failed" | "partial" | "error" | "cancelled";
  pixel_mismatch_pct?: number | null;
  summary?: string | null;
  error_message?: string | null;
  duration_ms?: number | null;
  created_at: string;
  findings: Finding[];
}

const TERMINAL = new Set(["passed", "failed", "partial", "error", "cancelled"]);

const SEVERITY_STYLES: Record<string, string> = {
  critical: "text-red-700 border-red-300 bg-red-50",
  major: "text-orange-700 border-orange-300 bg-orange-50",
  minor: "text-yellow-700 border-yellow-300 bg-yellow-50",
  info: "text-gray-600 border-gray-300 bg-gray-50",
};

const STATUS_STYLES: Record<string, string> = {
  passed: "text-green-600 border-green-300 bg-green-50",
  failed: "text-red-600 border-red-300 bg-red-50",
  partial: "text-yellow-700 border-yellow-300 bg-yellow-50",
  error: "text-red-600 border-red-300 bg-red-50",
  running: "text-blue-600 border-blue-300 bg-blue-50",
  pending: "text-gray-600 border-gray-300 bg-gray-50",
  cancelled: "text-gray-500 border-gray-300 bg-gray-50",
};

export default function VisualAuditSection() {
  const [enabled, setEnabled] = useState(false);
  const [references, setReferences] = useState<Reference[]>([]);
  const [selectedRef, setSelectedRef] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<VisualRun | null>(null);
  const [imageTab, setImageTab] = useState<"reference" | "screenshot" | "diff">("diff");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Feature detection + references ─────────────────────────────────────────
  const loadReferences = useCallback(async () => {
    try {
      const data: Reference[] = await apiGet("/api/v1/visual-audits/references");
      // Only offer references whose file is actually on disk (direct uploads
      // and completed Figma imports) — pending/failed imports are unusable.
      setReferences(
        data.filter(
          (r) => !r.parse_status || r.parse_status === "not_required" || r.parse_status === "done"
        )
      );
      setEnabled(true);
    } catch {
      // 404 → feature flag off or endpoint unavailable: render nothing.
      setEnabled(false);
    }
  }, []);

  useEffect(() => {
    loadReferences();
  }, [loadReferences]);

  // ── Poll active run until terminal ─────────────────────────────────────────
  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const data = await apiGet(`/api/v1/visual-audits/${run.id}`);
        setRun(data);
      } catch {
        // transient — keep polling; run always reaches a terminal state server-side
      }
    }, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [run]);

  // ── Actions ─────────────────────────────────────────────────────────────────
  const handleUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch("/api/v1/visual-audits/references", {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || "Upload failed");
      }
      const artifact = await res.json();
      await loadReferences();
      setSelectedRef(artifact.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const isValidUrl = (value: string) => {
    try {
      const u = new URL(value);
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  };

  const handleRun = async () => {
    if (!selectedRef || !isValidUrl(targetUrl) || submitting) return;
    setSubmitting(true);
    setError(null);
    setRun(null);
    try {
      const data = await apiPost("/api/v1/visual-audits", {
        target_url: targetUrl.trim(),
        artifact_id: selectedRef,
      });
      setRun(data);
      setImageTab("diff");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start audit");
    } finally {
      setSubmitting(false);
    }
  };

  if (!enabled) return null;

  const running = run !== null && !TERMINAL.has(run.status);

  return (
    <Card className="shadow-sm mt-6">
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">Visual Audit</CardTitle>
            <p className="text-sm text-gray-500 mt-1">
              Compare a live page against a design reference — deterministic
              pixel-diff plus AI structural review.
            </p>
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
        {/* Reference picker + upload */}
        <div className="flex gap-3 flex-wrap items-center">
          <Select value={selectedRef} onValueChange={(v) => setSelectedRef(v ?? "")}>
            <SelectTrigger className="w-auto min-w-[220px] h-9 text-sm">
              <SelectValue placeholder="Reference design (PNG)" />
            </SelectTrigger>
            <SelectContent>
              {references.map((r) => (
                <SelectItem key={r.id} value={r.id}>
                  {r.file_name}
                  {r.target_page ? ` — ${r.target_page}` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/png"
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
            {uploading ? "Uploading…" : "Upload PNG"}
          </Button>
        </div>

        {/* Target URL */}
        <input
          type="url"
          value={targetUrl}
          onChange={(e) => {
            setTargetUrl(e.target.value);
            setError(null);
          }}
          placeholder="Live page URL, e.g. https://staging.myapp.com/checkout"
          className="w-full rounded-md border border-gray-200 px-4 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent"
        />

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {error}
          </p>
        )}

        <Button
          onClick={handleRun}
          disabled={!selectedRef || !isValidUrl(targetUrl) || submitting || running}
          className="w-full h-10 text-sm font-medium"
        >
          {submitting
            ? "Starting…"
            : running
            ? "Audit running…"
            : "Run Visual Audit"}
        </Button>

        {/* Result */}
        {run && (
          <div className="space-y-3 pt-2 border-t border-gray-100">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className={STATUS_STYLES[run.status] || ""}>
                {run.status.toUpperCase()}
              </Badge>
              {typeof run.pixel_mismatch_pct === "number" && (
                <span className="text-xs text-gray-500">
                  {run.pixel_mismatch_pct}% pixel mismatch
                </span>
              )}
              {run.duration_ms != null && (
                <span className="text-xs text-gray-400">
                  {(run.duration_ms / 1000).toFixed(1)}s
                </span>
              )}
            </div>

            {run.summary && <p className="text-sm text-gray-600">{run.summary}</p>}
            {run.error_message && (
              <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
                {run.error_message}
              </p>
            )}

            {TERMINAL.has(run.status) && run.status !== "error" && run.status !== "cancelled" && (
              <>
                {/* Image tabs */}
                <div className="flex gap-2">
                  {(["reference", "screenshot", "diff"] as const).map((kind) => (
                    <button
                      key={kind}
                      onClick={() => setImageTab(kind)}
                      className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                        imageTab === kind
                          ? "border-gray-900 bg-gray-900 text-white"
                          : "border-gray-200 text-gray-600 hover:bg-gray-100"
                      }`}
                    >
                      {kind === "diff" ? "Diff overlay" : kind}
                    </button>
                  ))}
                </div>
                <AuthImage runId={run.id} kind={imageTab} />

                {/* Findings table */}
                {run.findings.length > 0 ? (
                  <div className="border border-gray-200 rounded-md overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                        <tr>
                          <th className="text-left px-3 py-2">Severity</th>
                          <th className="text-left px-3 py-2">Engine</th>
                          <th className="text-left px-3 py-2">Issue</th>
                          <th className="text-left px-3 py-2">Expected</th>
                          <th className="text-left px-3 py-2">Actual</th>
                        </tr>
                      </thead>
                      <tbody>
                        {run.findings.map((f, i) => (
                          <tr key={i} className="border-t border-gray-100">
                            <td className="px-3 py-2">
                              <Badge
                                variant="outline"
                                className={SEVERITY_STYLES[f.severity] || ""}
                              >
                                {f.severity}
                              </Badge>
                            </td>
                            <td className="px-3 py-2 text-gray-500 text-xs">
                              {f.engine === "pixel_diff" ? "Pixel diff" : "AI vision"}
                            </td>
                            <td className="px-3 py-2 text-gray-700">
                              {f.element ? `${f.element}: ` : ""}
                              {f.issue}
                            </td>
                            <td className="px-3 py-2 font-mono text-xs text-gray-600">
                              {f.expected || "—"}
                            </td>
                            <td className="px-3 py-2 font-mono text-xs text-gray-600">
                              {f.actual || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm text-green-600">
                    No discrepancies found above threshold.
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Fetches a run image with the JWT (plain <img src> can't send the header). */
function AuthImage({
  runId,
  kind,
}: {
  runId: string;
  kind: "reference" | "screenshot" | "diff";
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setSrc(null);
    setFailed(false);
    (async () => {
      try {
        const res = await apiFetch(`/api/v1/visual-audits/${runId}/images/${kind}`);
        if (!res.ok) throw new Error("image unavailable");
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setSrc(objectUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [runId, kind]);

  if (failed)
    return <p className="text-xs text-gray-400">Image not available for this run.</p>;
  if (!src)
    return <div className="h-48 bg-gray-100 rounded-md animate-pulse" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={`${kind} image`}
      className="w-full border border-gray-200 rounded-md"
    />
  );
}
