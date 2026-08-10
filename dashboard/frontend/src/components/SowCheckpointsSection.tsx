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
import { confirmDialog } from "@/lib/confirm";
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
  // null/absent = fully specified and runnable. Set when the source document
  // named this requirement without specifying it well enough to execute —
  // the checkpoint is real, it just isn't ready. Optional because
  // checkpoints parsed before this shipped carry no such field.
  review_status?: "needs_review" | "needs_design_flow" | null;
  review_reason?: string | null;
  // ── TDD classification (backend app/services/tdd_extraction.py) ──
  // All optional: checkpoints extracted before the v2 pipeline carry none of
  // these, and are rendered as unclassified rather than assumed positive.
  //
  // test_type is the one that changes how a result must be read: a negative
  // checkpoint PASSES when the system refuses the action, so it can never be
  // shown in the same visual register as a happy path.
  test_type?: "positive" | "negative" | "edge" | null;
  category?: string | null;
  // "derived" = inferred from standard QA practice, not stated in the
  // document. A failure there may be a spec gap rather than a product defect.
  grounding?: "stated" | "derived" | null;
  behaviour_key?: string | null;
  priority?: string | null;
  // Variants this checkpoint's category requires that the extractor did not
  // produce — computed in Python, not claimed by the model.
  coverage_gap?: string[];
  // Other parts that stated this same behaviour and were merged into this
  // checkpoint. Shown so a merge is visible: without it a reader comparing
  // the per-part counts against the document total sees numbers that don't
  // add up and no explanation.
  merged_from_parts?: number[];
  // Lower-priority variants dropped to keep this behaviour under its ceiling.
  // Shown because a cap nobody can see reads as "the extractor found nothing
  // more" — which is a different problem with a different fix.
  capped_variants?: number;
}

interface ExcludedZone {
  heading?: string | null;
  zone_kind?: string | null;
  reason?: string | null;
  char_count?: number | null;
  classifier?: string | null;
}

interface PartCoverage {
  total_checkpoints?: number;
  by_test_type?: Record<string, number>;
  by_grounding?: Record<string, number>;
  negative_edge_ratio?: number;
  coverage_gaps?: { behaviour_key?: string | null; missing?: string[] }[];
  capped_variants?: number;
}

interface Part {
  part_number: number;
  total_parts: number;
  status: "pending" | "processing" | "done" | "error";
  error?: string | null;
  checkpoint_count: number;
  char_count: number;
  preview: string;
  // Testability-gate audit trail + coverage scorecard (migration 0043).
  // Empty/null on parts analyzed before it, which render as "no data" rather
  // than "nothing was excluded".
  excluded_zones?: ExcludedZone[];
  coverage?: PartCoverage | null;
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
    accept: ".mp4,.webm,.mov,.mkv",
    uploadLabel: "Upload video (.mp4 / .webm / .mov / .mkv)",
    emptyLabel: "No videos uploaded yet.",
    activeLabel: "digesting…",
    workingLabel: "Watching the video and extracting checkpoints — this can take a few minutes…",
    noneFoundLabel: "No testable requirements found in this video.",
    // Mirrors the backend's VISUAL_VIDEO_MAX_MB default. This posts to the
    // same /visual-audits/video endpoint as the Import SOW dialog, so a
    // lower number here would reject client-side a file the server accepts.
    maxSizeMB: 500 as number | null,
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

// A negative test passes when the system REFUSES, and an edge test passes
// when behaviour is merely DEFINED — neither is a happy path, and reading a
// result without knowing which is which inverts the conclusion. Hence the
// distinct tones rather than one neutral chip.
const TEST_TYPE_STYLES: Record<string, string> = {
  negative: "text-red-700 border-red-300 bg-red-50",
  edge: "text-amber-700 border-amber-300 bg-amber-50",
  positive: "text-green-700 border-green-300 bg-green-50",
};

const TEST_TYPE_LABELS: Record<string, string> = {
  negative: "⛔ Negative",
  edge: "🔍 Edge",
  positive: "✓ Positive",
};

const TEST_TYPE_TITLES: Record<string, string> = {
  negative:
    "Negative test — PASSES when the system correctly refuses or safely rejects the action. The action succeeding is a FAIL.",
  edge: "Edge-case test — PASSES when behaviour is defined and consistent, not necessarily when the action succeeds.",
  positive: "Positive test — PASSES when the stated outcome is observable.",
};

/** Checkpoints from before the v2 extractor carry no test_type; they render
 *  nothing rather than a guessed "Positive", because guessing here would be
 *  indistinguishable from a real classification. */
function TestTypeBadge({ cp }: { cp: Checkpoint }) {
  if (!cp.test_type) return null;
  const derived = cp.grounding === "derived";
  return (
    <Badge
      variant="outline"
      className={`${TEST_TYPE_STYLES[cp.test_type] || ""} shrink-0`}
      title={
        TEST_TYPE_TITLES[cp.test_type] +
        (derived
          ? " Expectation source: standard QA practice, NOT stated in the source document — confirm with the spec owner before raising a defect."
          : "")
      }
    >
      {TEST_TYPE_LABELS[cp.test_type] || cp.test_type}
      {derived ? " · derived" : ""}
    </Badge>
  );
}

/** Per-document roll-up over the parts' scorecards.
 *
 *  negative_edge_ratio is the headline: the defect this pipeline exists to
 *  fix produced ~0.0 by construction, and the spec's acceptance gate is 0.40.
 *  Showing it here is what makes a regression in extraction QUALITY (as
 *  opposed to extraction failure) visible without re-reading every skill.
 */
function CoverageSummary({ parts }: { parts: Part[] }) {
  const scored = parts.filter((p) => p.coverage && p.coverage.total_checkpoints);
  if (scored.length === 0) return null;

  let total = 0;
  let nonPositive = 0;
  let derived = 0;
  let gaps = 0;
  let capped = 0;
  for (const p of scored) {
    const c = p.coverage!;
    total += c.total_checkpoints || 0;
    nonPositive += (c.by_test_type?.negative || 0) + (c.by_test_type?.edge || 0);
    derived += c.by_grounding?.derived || 0;
    gaps += (c.coverage_gaps || []).length;
    capped += c.capped_variants || 0;
  }
  if (!total) return null;
  const ratio = nonPositive / total;
  const healthy = ratio >= 0.4;

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-gray-500">Coverage:</span>
      <Badge
        variant="outline"
        className={
          healthy
            ? "text-green-700 border-green-300 bg-green-50"
            : "text-amber-700 border-amber-300 bg-amber-50"
        }
        title={
          healthy
            ? "Negative + edge cases as a share of all checkpoints. At or above the 0.40 acceptance gate."
            : "Below the 0.40 acceptance gate — extraction has drifted towards happy-path-only output. Re-analyse, or check the extraction prompt/provider."
        }
      >
        {Math.round(ratio * 100)}% negative + edge
      </Badge>
      <span className="text-gray-400">
        {nonPositive} of {total} checkpoint{total === 1 ? "" : "s"}
      </span>
      {derived > 0 && (
        <span
          className="text-gray-400"
          title="Inferred from standard QA practice because the document is silent. A failure may be a spec gap rather than a product defect."
        >
          · {derived} derived
        </span>
      )}
      {capped > 0 && (
        <span
          className="text-gray-400"
          title="Lower-priority variants dropped to keep a behaviour under its ceiling. A deliberate cap, not a truncation — the worker log names each dropped test."
        >
          · {capped} capped
        </span>
      )}
      {gaps > 0 && (
        <Badge
          variant="outline"
          className="text-amber-700 border-amber-300 bg-amber-50"
          title="Behaviours whose category requires a variant the extractor did not produce. Flagged, not dropped."
        >
          {gaps} coverage gap{gaps === 1 ? "" : "s"}
        </Badge>
      )}
    </div>
  );
}

/** The testability gate's audit trail.
 *
 *  Rendered collapsed but never hidden: the alternative to showing what was
 *  excluded is a filter nobody can audit, and "the extractor quietly decided
 *  your requirements section was a glossary" is exactly the failure that
 *  would otherwise be impossible to notice.
 */
function ExcludedZonesPanel({ parts }: { parts: Part[] }) {
  const zones = parts.flatMap((p) =>
    (p.excluded_zones || []).map((z) => ({ ...z, part_number: p.part_number }))
  );
  if (zones.length === 0) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-gray-500 hover:text-gray-700">
        {zones.length} section{zones.length === 1 ? "" : "s"} skipped as non-testable
        (pricing, timelines, legal, glossary…) — click to review
      </summary>
      <ul className="mt-1.5 space-y-1 border-l-2 border-gray-200 pl-3">
        {zones.map((z, i) => (
          <li key={i} className="text-gray-500">
            <span className="font-medium text-gray-700">
              {z.heading || "(untitled section)"}
            </span>
            <span className="text-gray-400">
              {" "}
              — {z.zone_kind || "unclassified"}
              {z.char_count ? ` · ${z.char_count} chars` : ""}
              {z.classifier ? ` · ${z.classifier}` : ""}
              {parts.length > 1 ? ` · part ${z.part_number}` : ""}
            </span>
            {z.reason && <p className="text-gray-400">{z.reason}</p>}
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-gray-400">
        Skipped text is never sent for extraction. If a real requirement is listed here,
        re-analyse that part with <code>TDD_ZONING=0</code> to bypass the gate.
      </p>
    </details>
  );
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
    const ok = await confirmDialog({
      title: `Delete "${sow.file_name}"?`,
      body: "This also removes its extracted checkpoints.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
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
    <Card className="shadow-sm">
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
                    {/* Chromeless at rest so a row of these doesn't read as a
                        toolbar; the danger tone's hue vars carry the intent on
                        approach. */}
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`Delete ${sow.file_name}`}
                      title="Delete"
                      disabled={deletingId === sow.id}
                      className="text-gray-400 hover:text-destructive [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)]"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(sow);
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
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
                        {/* Parts analyze automatically, chained one after
                            another. The per-part buttons below stay as the
                            retry path for a part that failed. */}
                        {(() => {
                          const list = parts[sow.id] || [];
                          const running = list.find((p) => p.status === "processing");
                          const waiting = list.some((p) => p.status === "pending");
                          if (running) {
                            return (
                              <p className="text-xs text-blue-600">
                                Analyzing part {running.part_number} of {sow.total_parts}{" "}
                                automatically — the rest follow on their own.
                              </p>
                            );
                          }
                          if (waiting && sow.parse_status !== "error") {
                            return (
                              <p className="text-xs text-gray-500">
                                Queued — remaining parts start automatically.
                              </p>
                            );
                          }
                          return null;
                        })()}
                        {(() => {
                          const flagged = (checkpoints[sow.id] || []).filter(
                            (c) => c.review_status
                          ).length;
                          if (!flagged) return null;
                          return (
                            <p className="text-xs text-amber-700">
                              {flagged} checkpoint{flagged === 1 ? "" : "s"} need review —
                              the document does not specify them well enough to run.
                            </p>
                          );
                        })()}
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
                    {/* Extraction-quality summary and the testability gate's
                        audit trail, for single- and multi-part documents
                        alike. Both render only once there is data, so a
                        document analysed before migration 0043 looks exactly
                        as it did before. */}
                    <CoverageSummary parts={parts[sow.id] || []} />
                    <ExcludedZonesPanel parts={parts[sow.id] || []} />
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
                                    <TestTypeBadge cp={cp} />
                                    <span className="text-sm font-medium text-gray-800 truncate">
                                      {cp.title}
                                    </span>
                                    {cp.review_status && (
                                      // A flagged checkpoint is a real
                                      // requirement the document under-specified.
                                      // It is shown, not hidden — but it must
                                      // never read as ready to run.
                                      <Badge
                                        variant="outline"
                                        className="text-amber-700 border-amber-300 bg-amber-50 shrink-0"
                                        title={cp.review_reason || undefined}
                                      >
                                        {cp.review_status === "needs_design_flow"
                                          ? "needs design flow"
                                          : "needs review"}
                                      </Badge>
                                    )}
                                  </div>
                                  {cp.review_status && cp.review_reason && (
                                    <p className="mt-1 text-xs text-amber-700">
                                      {cp.review_reason}
                                    </p>
                                  )}
                                  {/* A derived expectation is reasoned from
                                      standard QA practice, not read out of the
                                      document. Saying so here is what lets a
                                      failure be triaged as a possible SPEC GAP
                                      instead of straight to a product defect. */}
                                  {cp.grounding === "derived" && (
                                    <p className="mt-1 text-xs text-gray-500">
                                      Expectation inferred from standard QA practice — the
                                      document does not state it. Confirm with the spec owner
                                      before raising a defect.
                                    </p>
                                  )}
                                  {(cp.capped_variants ?? 0) > 0 && (
                                    <p className="mt-1 text-xs text-gray-500">
                                      {cp.capped_variants} lower-priority variant
                                      {cp.capped_variants === 1 ? "" : "s"} of this behaviour
                                      were dropped to keep it under the per-behaviour limit —
                                      the worker log names each one.
                                    </p>
                                  )}
                                  {(cp.merged_from_parts?.length ?? 0) > 0 && (
                                    <p className="mt-1 text-xs text-gray-500">
                                      Also stated in part{" "}
                                      {cp.merged_from_parts!.join(", ")} — merged into this
                                      one checkpoint instead of duplicated.
                                    </p>
                                  )}
                                  {(cp.coverage_gap?.length ?? 0) > 0 && (
                                    <p className="mt-1 text-xs text-amber-700">
                                      Coverage gap: no {cp.coverage_gap!.join(" or ")} case was
                                      extracted for this behaviour, though its category requires
                                      one.
                                    </p>
                                  )}

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
