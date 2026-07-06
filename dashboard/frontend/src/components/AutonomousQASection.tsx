"use client";

/**
 * Autonomous Visual QA — hero section for the Vibe Testing tab, implementing
 * the AEP_Architecture_Revision2 design: engine status cards, a single
 * "New Autonomous Visual QA Run" form (live URL + environment + three design
 * upload dropzones + credential profiles + audit pipeline), and inline run
 * results.
 *
 * Feature-detected like the other Visual QA sections: on mount it probes
 * GET /api/v1/visual-audits/references. If the backend flag
 * (VISUAL_AUDIT_ENABLED) is off the probe 404s and this renders nothing —
 * the existing Vibe Testing UI is completely unchanged.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, apiGet, apiPost } from "@/utils/apiClient";
import { FindingCard } from "@/components/ai-testing/shared";
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
  status:
    | "pending"
    | "running"
    | "passed"
    | "failed"
    | "partial"
    | "error"
    | "cancelled";
  pixel_mismatch_pct?: number | null;
  summary?: string | null;
  error_message?: string | null;
  duration_ms?: number | null;
  created_at: string;
  findings: Finding[];
}

interface StepDecision {
  step: "hands" | "judge" | "self_execute";
  invoked: boolean;
  model_provider?: string | null;
  model_name?: string | null;
  is_deterministic: boolean;
  rationale: string;
  sequence: number;
}

interface OrchestratorRun {
  id: string;
  goal?: string | null;
  target_url?: string | null;
  artifact_id?: string | null;
  environment?: string | null;
  status:
    | "pending"
    | "planning"
    | "running"
    | "passed"
    | "failed"
    | "partial"
    | "error"
    | "cancelled";
  ai_test_run_id?: string | null;
  visual_run_id?: string | null;
  self_execute_answer?: string | null;
  summary?: string | null;
  error_message?: string | null;
  duration_ms?: number | null;
  created_at: string;
  decisions: StepDecision[];
}

interface Environment {
  id: string;
  name: string;
}

interface CredentialProfile {
  id: string;
  name: string;
}

interface UploadState {
  status: "idle" | "uploading" | "done" | "error";
  fileName?: string;
  detail?: string;
}

const TERMINAL = new Set(["passed", "failed", "partial", "error", "cancelled"]);

const STATUS_STYLES: Record<string, string> = {
  passed: "text-green-600 border-green-300 bg-green-50",
  failed: "text-red-600 border-red-300 bg-red-50",
  partial: "text-yellow-700 border-yellow-300 bg-yellow-50",
  error: "text-red-600 border-red-300 bg-red-50",
  running: "text-blue-600 border-blue-300 bg-blue-50",
  planning: "text-blue-600 border-blue-300 bg-blue-50",
  pending: "text-gray-600 border-gray-300 bg-gray-50",
  cancelled: "text-gray-500 border-gray-300 bg-gray-50",
};

// ── Engine status cards — live once a run exists, static "Ready" before ─────

interface EngineCard {
  label: string;
  name: string;
  role: string;
  status: string;
  dotColor: string;
}

const IDLE_ENGINES: EngineCard[] = [
  { label: "THE BRAIN", name: "Gemini 3.5 Flash", role: "Context engine", status: "Ready", dotColor: "bg-teal-500" },
  { label: "THE HANDS", name: "browser-use", role: "Execution agent", status: "Ready", dotColor: "bg-teal-500" },
  { label: "THE JUDGE", name: "Vision + Pixel-diff", role: "Visual audit engine", status: "Ready", dotColor: "bg-teal-500" },
  { label: "THE LINE", name: "Redis + Celery", role: "Task queue", status: "Ready", dotColor: "bg-teal-500" },
  { label: "MEMORY BANK", name: "Postgres", role: "Design rules store", status: "Active", dotColor: "bg-teal-500" },
];

function buildEngineCards(run: OrchestratorRun | null): EngineCard[] {
  if (!run) return IDLE_ENGINES;

  const done = TERMINAL.has(run.status);
  const hands = run.decisions.find((d) => d.step === "hands");
  const judge = run.decisions.find((d) => d.step === "judge");
  const brainDecision = run.decisions.find((d) => !d.is_deterministic);

  const subAgentCard = (
    label: string,
    decision: StepDecision | undefined,
    idleName: string,
    idleRole: string
  ): EngineCard => {
    if (!decision) {
      return { label, name: idleName, role: idleRole, status: "—", dotColor: "bg-gray-300" };
    }
    if (!decision.invoked) {
      return { label, name: "Skipped", role: decision.rationale, status: "Skipped", dotColor: "bg-gray-400" };
    }
    const modelLabel =
      decision.model_provider && decision.model_name
        ? `${decision.model_provider}/${decision.model_name}`
        : idleName;
    return {
      label,
      name: modelLabel,
      role: decision.rationale,
      status: done ? "Done" : "Running…",
      dotColor: done ? "bg-teal-500" : "bg-blue-500",
    };
  };

  return [
    {
      label: "THE BRAIN",
      name: brainDecision
        ? `${brainDecision.model_provider ?? ""}/${brainDecision.model_name ?? ""}`
        : "Rule-based routing",
      role: brainDecision
        ? brainDecision.rationale
        : "Deterministic rules resolved this task — no classifier call needed.",
      status:
        run.status === "planning"
          ? "Routing…"
          : done
            ? "Done"
            : run.status === "running"
              ? "Working…"
              : "Ready",
      dotColor: done ? "bg-teal-500" : "bg-blue-500",
    },
    subAgentCard("THE HANDS", hands, "browser-use", "Execution agent"),
    subAgentCard("THE JUDGE", judge, "Vision + Pixel-diff", "Visual audit engine"),
    { label: "THE LINE", name: "Redis + Celery", role: "Task queue", status: "Ready", dotColor: "bg-teal-500" },
    { label: "MEMORY BANK", name: "Postgres", role: "Design rules store", status: "Active", dotColor: "bg-teal-500" },
  ];
}

const PIPELINE_STEPS = [
  "Brain digests files",
  "Line queues tasks",
  "Hands navigate site",
  "Judge audits visuals",
  "Discrepancy report",
];

// ── Dropzone ─────────────────────────────────────────────────────────────────

function Dropzone({
  label,
  hint,
  accept,
  state,
  onFile,
}: {
  label: string;
  hint: string;
  accept: string;
  state: UploadState;
  onFile: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  return (
    <div className="flex-1 min-w-[200px] flex flex-col">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
        {label}
      </p>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) onFile(f);
        }}
        className={`w-full flex-1 flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
          dragOver
            ? "border-blue-400 bg-blue-50"
            : state.status === "done"
              ? "border-green-300 bg-green-50"
              : state.status === "error"
                ? "border-red-300 bg-red-50"
                : "border-gray-200 bg-white hover:border-gray-300"
        }`}
      >
        {state.status === "uploading" ? (
          <span className="text-sm text-gray-500">Uploading…</span>
        ) : state.status === "done" ? (
          <span className="text-sm text-green-700 break-all">
            ✓ {state.fileName}
            {state.detail ? (
              <span className="block text-xs text-green-600 mt-1">
                {state.detail}
              </span>
            ) : null}
          </span>
        ) : state.status === "error" ? (
          <span className="text-sm text-red-600 break-words">
            {state.detail || "Upload failed"}
            <span className="block text-xs text-red-400 mt-1">
              Click to try again
            </span>
          </span>
        ) : (
          <span className="text-sm text-gray-500">
            {hint}
            <span className="block text-xs text-gray-400 mt-1.5">{accept}</span>
          </span>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

// ── Main section ─────────────────────────────────────────────────────────────

export default function AutonomousQASection() {
  const [enabled, setEnabled] = useState(false);
  const [references, setReferences] = useState<Reference[]>([]);
  const [selectedRef, setSelectedRef] = useState("");
  const [goal, setGoal] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environment, setEnvironment] = useState("");
  const [profiles, setProfiles] = useState<CredentialProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [figmaUpload, setFigmaUpload] = useState<UploadState>({ status: "idle" });
  const [videoUpload, setVideoUpload] = useState<UploadState>({ status: "idle" });
  const [sowUpload, setSowUpload] = useState<UploadState>({ status: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<OrchestratorRun | null>(null);
  const [visualDetail, setVisualDetail] = useState<VisualRun | null>(null);
  const [images, setImages] = useState<Record<string, string>>({});
  const [imageTab, setImageTab] = useState<"reference" | "screenshot" | "diff">(
    "diff"
  );
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Feature detection + data loads ─────────────────────────────────────────

  const loadReferences = useCallback(async () => {
    try {
      const data: Reference[] = await apiGet("/api/v1/visual-audits/references");
      setReferences(
        data.filter(
          (r) =>
            !r.parse_status ||
            r.parse_status === "not_required" ||
            r.parse_status === "done"
        )
      );
      setEnabled(true);
      return true;
    } catch {
      setEnabled(false); // 404 → feature flag off: render nothing
      return false;
    }
  }, []);

  useEffect(() => {
    (async () => {
      const on = await loadReferences();
      if (!on) return;
      // Environments/profiles are cosmetic context for the run — non-fatal.
      try {
        setEnvironments(await apiGet("/api/ai-testing/environments"));
      } catch {}
      try {
        setProfiles(await apiGet("/api/ai-testing/credential-profiles"));
      } catch {}
    })();
  }, [loadReferences]);

  // ── Poll active run until terminal ─────────────────────────────────────────

  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        setRun(await apiGet(`/api/v1/orchestrator/runs/${run.id}`));
      } catch {
        // transient — run always reaches a terminal state server-side
      }
    }, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [run]);

  // ── Load the underlying Judge sub-run (findings + mismatch %) once the
  // orchestrator run is terminal and actually invoked the Judge ─────────────

  useEffect(() => {
    if (!run || !TERMINAL.has(run.status) || !run.visual_run_id) {
      setVisualDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await apiGet(`/api/v1/visual-audits/${run.visual_run_id}`);
        if (!cancelled) setVisualDetail(data);
      } catch {}
    })();
    return () => {
      cancelled = true;
    };
  }, [run?.visual_run_id, run?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load result images once the Judge sub-run id is known ──────────────────

  useEffect(() => {
    if (!run || !TERMINAL.has(run.status) || !run.visual_run_id) return;
    const visualRunId = run.visual_run_id;
    let cancelled = false;
    (async () => {
      const next: Record<string, string> = {};
      for (const kind of ["reference", "screenshot", "diff"]) {
        try {
          const res = await apiFetch(`/api/v1/visual-audits/${visualRunId}/images/${kind}`);
          if (res.ok) next[kind] = URL.createObjectURL(await res.blob());
        } catch {}
      }
      if (!cancelled) setImages(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [run?.visual_run_id, run?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Uploads ─────────────────────────────────────────────────────────────────

  const upload = async (
    endpoint: string,
    file: File,
    setState: (s: UploadState) => void,
    onDone?: (artifact: { id: string; parse_status?: string }) => void
  ) => {
    setState({ status: "uploading" });
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch(endpoint, { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || `Upload failed (${res.status})`);
      }
      const artifact = await res.json();
      setState({
        status: "done",
        fileName: file.name,
        detail:
          artifact.parse_status === "pending" ||
          artifact.parse_status === "processing"
            ? "Queued for AI digestion"
            : undefined,
      });
      onDone?.(artifact);
    } catch (err) {
      setState({
        status: "error",
        detail: err instanceof Error ? err.message : "Upload failed",
      });
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

  // ── Run ─────────────────────────────────────────────────────────────────────

  const canRun =
    (goal.trim().length >= 5 || isValidUrl(targetUrl) || !!selectedRef) && !submitting;

  const handleRun = async () => {
    if (!canRun) return;
    setSubmitting(true);
    setError(null);
    setRun(null);
    setVisualDetail(null);
    setImages({});
    try {
      const envName = environments.find((e) => e.id === environment)?.name;
      const data = await apiPost("/api/v1/orchestrator/runs", {
        ...(goal.trim() ? { goal: goal.trim() } : {}),
        ...(isValidUrl(targetUrl) ? { target_url: targetUrl.trim() } : {}),
        ...(selectedRef ? { artifact_id: selectedRef } : {}),
        ...(envName ? { environment: envName } : {}),
        ...(selectedProfile ? { credential_profile_id: selectedProfile } : {}),
      });
      setRun(data);
      setImageTab("diff");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start run");
    } finally {
      setSubmitting(false);
    }
  };

  if (!enabled) return null;

  const running = run !== null && !TERMINAL.has(run.status);
  const runHint =
    !goal.trim() && !isValidUrl(targetUrl) && !selectedRef
      ? "Enter a goal, a live URL, or pick a design reference to enable"
      : null;

  return (
    <div className="space-y-6">
      {/* ── Engine status cards — live routing decisions once a run exists ── */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {buildEngineCards(run).map((e) => (
          <Card key={e.label} className="shadow-sm">
            <CardContent className="p-4">
              <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">
                {e.label}
              </p>
              <p className="text-sm font-bold text-gray-900 mt-1.5 break-words">{e.name}</p>
              <p className="text-xs text-gray-500 mt-0.5">{e.role}</p>
              <p className="flex items-center gap-1.5 text-xs text-gray-600 mt-2">
                <span className={`w-2 h-2 rounded-full inline-block ${e.dotColor}`} />
                {e.status}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── New Autonomous Visual QA Run ────────────────────────────────── */}
      <Card className="shadow-sm">
        <CardHeader className="pb-4">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-xl">
                New Autonomous Visual QA Run
              </CardTitle>
              <p className="text-sm text-gray-500 mt-1">
                Upload your design handoffs and a live URL — the AI will audit
                the site autonomously.
              </p>
            </div>
            <Badge
              variant="outline"
              className="text-teal-700 border-teal-300 bg-teal-50 gap-1.5 flex-shrink-0"
            >
              <span className="w-2 h-2 rounded-full bg-teal-500 inline-block" />
              Agent ready
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Goal — optional, enables prompt-only browser testing with no
              design reference at all */}
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              Goal (optional — describe what to test)
            </p>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. Log in and verify the dashboard loads"
              rows={2}
              className="w-full rounded-md border border-gray-200 px-4 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent resize-none"
            />
          </div>

          {/* URL + environment */}
          <div className="flex gap-4 flex-wrap">
            <div className="flex-1 min-w-[260px]">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Live Site URL
              </p>
              <input
                type="url"
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                placeholder="https://your-app.com"
                className="w-full h-11 rounded-md border border-gray-200 px-4 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent"
              />
            </div>
            <div className="flex-1 min-w-[220px]">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Target Environment
              </p>
              <Select value={environment} onValueChange={(v) => setEnvironment(v ?? "")}>
                <SelectTrigger className="w-full !h-11 rounded-md border-gray-200 text-sm">
                  <SelectValue placeholder="Environment" />
                </SelectTrigger>
                <SelectContent>
                  {environments.map((env) => (
                    <SelectItem key={env.id} value={env.id}>
                      {env.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Dropzones */}
          <div className="flex gap-4 flex-wrap">
            <Dropzone
              label="Figma File"
              hint="Drop Figma export or click to upload"
              accept=".png"
              state={figmaUpload}
              onFile={(f) =>
                upload(
                  "/api/v1/visual-audits/references",
                  f,
                  setFigmaUpload,
                  (artifact) => {
                    setSelectedRef(artifact.id);
                    loadReferences();
                  }
                )
              }
            />
            <Dropzone
              label="Design Video"
              hint="Drop walkthrough video or click to upload"
              accept=".mp4,.mov,.webm"
              state={videoUpload}
              onFile={(f) => upload("/api/v1/visual-audits/video", f, setVideoUpload)}
            />
            <Dropzone
              label="SOW / Spec Doc"
              hint="Drop spec document or click to upload"
              accept=".pdf,.md,.txt"
              state={sowUpload}
              onFile={(f) => upload("/api/v1/visual-audits/sow", f, setSowUpload)}
            />
          </div>

          {/* Existing reference picker (Memory Bank) */}
          {references.length > 0 && (
            <div className="flex gap-3 items-center flex-wrap">
              <span className="text-xs text-gray-400 uppercase tracking-wide">
                or use a saved design
              </span>
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
            </div>
          )}

          {/* Credential profiles */}
          {profiles.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Credential Profile
              </p>
              <div className="flex gap-2 flex-wrap">
                {profiles.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() =>
                      setSelectedProfile((cur) => (cur === p.id ? "" : p.id))
                    }
                    className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm transition-colors ${
                      selectedProfile === p.id
                        ? "border-gray-900 bg-gray-900 text-white"
                        : "border-gray-200 text-gray-700 hover:bg-gray-50"
                    }`}
                  >
                    <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
                      <circle
                        cx="8"
                        cy="5.5"
                        r="2.5"
                        stroke="currentColor"
                        strokeWidth="1.25"
                      />
                      <path
                        d="M2.5 13c0-2.485 2.462-4.5 5.5-4.5s5.5 2.015 5.5 4.5"
                        stroke="currentColor"
                        strokeWidth="1.25"
                        strokeLinecap="round"
                      />
                    </svg>
                    {p.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Audit pipeline */}
          <div>
            <p className="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none">
                <rect
                  x="4"
                  y="4"
                  width="8"
                  height="8"
                  rx="1.5"
                  stroke="currentColor"
                  strokeWidth="1.25"
                />
                <path
                  d="M6 1.5v2M10 1.5v2M6 12.5v2M10 12.5v2M1.5 6h2M1.5 10h2M12.5 6h2M12.5 10h2"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  strokeLinecap="round"
                />
              </svg>
              Audit Pipeline
            </p>
            <div className="flex items-center gap-2 flex-wrap">
              {PIPELINE_STEPS.map((step, i) => (
                <div key={step} className="flex items-center gap-2">
                  <span className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-700 whitespace-nowrap">
                    {step}
                  </span>
                  {i < PIPELINE_STEPS.length - 1 && (
                    <span className="text-gray-300">→</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </p>
          )}

          {/* Run button */}
          <div>
            <Button
              onClick={handleRun}
              disabled={!canRun || running}
              className="w-full h-12 text-base font-medium"
            >
              <svg className="w-5 h-5 mr-2" viewBox="0 0 20 20" fill="currentColor">
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"
                  clipRule="evenodd"
                />
              </svg>
              {submitting
                ? "Starting…"
                : running
                  ? "Audit in progress…"
                  : "Run Autonomous QA"}
            </Button>
            {runHint && (
              <p className="text-xs text-gray-400 text-center mt-2">{runHint}</p>
            )}
          </div>

          {/* ── Run result ─────────────────────────────────────────────── */}
          {run && (
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-4 space-y-3">
              <div className="flex items-center gap-3 flex-wrap">
                <Badge
                  variant="outline"
                  className={STATUS_STYLES[run.status] || STATUS_STYLES.pending}
                >
                  {running && (
                    <span className="w-3 h-3 mr-1.5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin inline-block" />
                  )}
                  {run.status}
                </Badge>
                {visualDetail?.pixel_mismatch_pct != null && (
                  <span className="text-sm text-gray-600">
                    Pixel mismatch: {visualDetail.pixel_mismatch_pct}%
                  </span>
                )}
                {run.duration_ms != null && (
                  <span className="text-xs text-gray-400">
                    {Math.round(run.duration_ms / 1000)}s
                  </span>
                )}
              </div>
              {run.summary && (
                <p className="text-sm text-gray-700">{run.summary}</p>
              )}
              {run.self_execute_answer && (
                <p className="text-sm text-gray-700 whitespace-pre-wrap">
                  {run.self_execute_answer}
                </p>
              )}
              {run.error_message && (
                <p className="text-sm text-red-600">{run.error_message}</p>
              )}

              {(visualDetail?.findings.length ?? 0) > 0 && (
                <div className="space-y-2">
                  {(visualDetail?.findings ?? []).map((f, i) => (
                    <FindingCard key={i} finding={f} />
                  ))}
                </div>
              )}

              {Object.keys(images).length > 0 && (
                <div>
                  <div className="flex gap-2 mb-2">
                    {(["reference", "screenshot", "diff"] as const).map(
                      (kind) =>
                        images[kind] && (
                          <button
                            key={kind}
                            type="button"
                            onClick={() => setImageTab(kind)}
                            className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                              imageTab === kind
                                ? "border-gray-900 bg-gray-900 text-white"
                                : "border-gray-200 text-gray-600 hover:bg-gray-100"
                            }`}
                          >
                            {kind === "reference"
                              ? "Reference"
                              : kind === "screenshot"
                                ? "Live screenshot"
                                : "Diff overlay"}
                          </button>
                        )
                    )}
                  </div>
                  {images[imageTab] && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={images[imageTab]}
                      alt={imageTab}
                      className="w-full h-auto rounded-lg border border-gray-200"
                    />
                  )}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
