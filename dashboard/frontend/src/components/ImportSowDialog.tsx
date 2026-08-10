"use client";

/**
 * Import SOW — document + the platform evidence the extractor needs to write
 * runnable tests.
 *
 * WHY EVIDENCE IS HERE AND NOT LATER. Extraction only ever saw text, so a
 * checkpoint said "click Submit Application" because that is what the
 * document called the button, while the product says "Apply Now". The test
 * then fails for a reason that is neither a product defect nor a spec gap.
 * Screenshots and a walkthrough give the extractor the real screen, control
 * and field names up front — before any skill is written — which is far
 * cheaper than having an agent navigate the live product per test, and needs
 * no credentials or deployed environment at extraction time.
 *
 * EVIDENCE IS CONTEXT, NOT A SOURCE. It uploads to the PROJECT's artifact
 * store (/api/v1/visual-audits/*), never to this document's SOW sources. That
 * boundary is deliberate: a SOW source feeds the requirements ledger and the
 * regeneration/rewrite pipeline, and platform evidence must never rewrite the
 * SOW's own content. It only tells the AI what the platform looks like.
 *
 * ATTACHED TO THE PROJECT, NOT THE IMPORT. One platform serves many SOWs.
 * Per-import evidence would mean re-uploading the same screenshots every time
 * with no single place to refresh them, so a project that already carries
 * evidence satisfies the requirement and this dialog says so instead of
 * asking again.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Image as ImageIcon, Video, X } from "lucide-react";
import { apiGet, apiPost, apiFetch } from "@/utils/apiClient";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// Mirrors app/api/v1/sow.py::add_existing_sow_source's accepted extensions —
// duplicated here only so a wrong file type is rejected instantly instead of
// round-tripping to the server. The server stays authoritative.
const DOC_ACCEPT = ".docx,.pdf,.txt,.md";
const DOC_MAX_MB = 15;

// Screenshots are validated by PNG magic bytes server-side
// (visual_audit.upload_reference), so anything else is rejected there no
// matter what the file is named. Saying "PNG" here is honesty, not a
// preference.
const SHOT_ACCEPT = "image/png";
const SHOT_MAX_MB = 10;

// .mkv is stored as-is and converted to MP4 server-side before analysis
// (Gemini's Files API has no Matroska type) — see
// video_ingest._prepare_for_upload. Nothing extra is asked of the uploader.
const VIDEO_ACCEPT = ".mp4,.webm,.mov,.mkv";
// Mirrors the backend's VISUAL_VIDEO_MAX_MB default (visual_audit.py). The
// server streams the upload to disk with a running cap, so this is a fast
// client-side reject, not the real gate.
const VIDEO_MAX_MB = 500;

interface ProjectRow {
  id: string;
  name: string;
}

interface EvidenceRow {
  id: string;
  file_name: string;
  project_id?: string | null;
  created_at?: string;
}

function mb(bytes: number) {
  return bytes / (1024 * 1024);
}

function fileSizeLabel(file: File) {
  const size = mb(file.size);
  return size < 0.1 ? "<0.1 MB" : `${size.toFixed(1)} MB`;
}

export default function ImportSowDialog({
  open,
  projects,
  onClose,
  onImported,
}: {
  open: boolean;
  projects: ProjectRow[];
  onClose: () => void;
  onImported: (documentId: string) => void;
}) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [projectId, setProjectId] = useState("");
  const [doc, setDoc] = useState<File | null>(null);
  const [shots, setShots] = useState<File[]>([]);
  const [video, setVideo] = useState<File | null>(null);
  const [platformName, setPlatformName] = useState("");
  const [error, setError] = useState("");
  // Named stage rather than a bare spinner: this submits up to four requests
  // and a failure on the third is unreadable if the UI only said "Working…".
  const [stage, setStage] = useState("");
  const submitting = stage !== "";

  const docInputRef = useRef<HTMLInputElement>(null);
  const shotInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);
  const firstFocusRef = useRef<HTMLButtonElement>(null);
  const titleId = "import-sow-title";

  function reset() {
    setTitle("");
    setProjectId("");
    setDoc(null);
    setShots([]);
    setVideo(null);
    setPlatformName("");
    setError("");
    setStage("");
  }

  function close() {
    if (submitting) return;
    reset();
    onClose();
  }

  // Escape closes, matching every other dismissible surface in the app. The
  // previous version of this modal could only be dismissed by mouse.
  // Depends on `submitting` because close() is a no-op mid-import: an escape
  // key must not abandon a dialog that is halfway through uploading.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, submitting]);

  // Focus lands on the first control rather than on the document body, so the
  // dialog is operable from the keyboard the moment it opens.
  useEffect(() => {
    if (open) firstFocusRef.current?.focus();
  }, [open]);

  /** What this project already has on file. Both endpoints 404 when the
   *  Vibe Testing feature flag is off — that is reported as "unavailable"
   *  rather than as an empty result, because the two mean opposite things
   *  for a requirement the user is being asked to satisfy. */
  const evidenceQuery = useQuery({
    queryKey: ["project-evidence", projectId],
    enabled: open && !!projectId,
    queryFn: async () => {
      const [refs, videos] = await Promise.all([
        apiGet("/api/v1/visual-audits/references"),
        apiGet("/api/v1/visual-audits/video"),
      ]);
      const mine = (rows: EvidenceRow[]) =>
        (rows || []).filter((r) => String(r.project_id || "") === projectId);
      return { screenshots: mine(refs), videos: mine(videos) };
    },
    retry: false,
  });

  const existing = evidenceQuery.data;
  const existingCount =
    (existing?.screenshots.length || 0) + (existing?.videos.length || 0);
  // A 404 here is the feature flag, not a missing project. Enforcing a
  // requirement nobody can satisfy would block every import outright, so the
  // requirement is waived and the reason is stated on screen.
  const evidenceUnavailable = evidenceQuery.isError;

  const staged = shots.length + (video ? 1 : 0);
  const evidenceSatisfied =
    evidenceUnavailable || existingCount > 0 || staged > 0;
  const needsPlatformName = !!video && !platformName.trim();

  const derivedTitle = useMemo(
    () => (doc ? doc.name.replace(/\.[^./]+$/, "") : ""),
    [doc]
  );

  const canSubmit =
    !!doc && !!projectId && evidenceSatisfied && !needsPlatformName && !submitting;

  function addShots(files: FileList | null) {
    if (!files?.length) return;
    const next: File[] = [];
    for (const file of Array.from(files)) {
      if (file.type !== "image/png" && !file.name.toLowerCase().endsWith(".png")) {
        setError(`"${file.name}" is not a PNG. Screenshots must be PNG files.`);
        continue;
      }
      if (mb(file.size) > SHOT_MAX_MB) {
        setError(`"${file.name}" is over the ${SHOT_MAX_MB}MB limit.`);
        continue;
      }
      // Same name AND same size is the only cheap duplicate signal available
      // client-side; the server dedupes properly by sha256.
      if (shots.some((s) => s.name === file.name && s.size === file.size)) continue;
      next.push(file);
    }
    if (next.length) setShots((prev) => [...prev, ...next]);
    if (shotInputRef.current) shotInputRef.current.value = "";
  }

  function setVideoFile(file: File | null) {
    if (!file) return;
    const ext = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
    if (!VIDEO_ACCEPT.split(",").includes(ext)) {
      setError(`"${file.name}" must be a .mp4, .webm, .mov or .mkv file.`);
    } else if (mb(file.size) > VIDEO_MAX_MB) {
      setError(`"${file.name}" is over the ${VIDEO_MAX_MB}MB limit.`);
    } else {
      setError("");
      setVideo(file);
    }
    if (videoInputRef.current) videoInputRef.current.value = "";
  }

  async function upload(path: string, body: FormData, what: string) {
    const res = await apiFetch(path, { method: "POST", body });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(
        typeof detail?.detail === "string"
          ? `${what}: ${detail.detail}`
          : `${what} failed (${res.status})`
      );
    }
    return res.json().catch(() => null);
  }

  async function submit() {
    setError("");
    if (!doc) return setError("Choose a SOW document to import.");
    if (!projectId) return setError("Select the project this SOW belongs to.");
    if (mb(doc.size) > DOC_MAX_MB) {
      return setError(`The document is over the ${DOC_MAX_MB}MB limit.`);
    }
    if (!evidenceSatisfied) {
      return setError(
        "Add at least one screenshot or a walkthrough video so the extractor knows what the platform actually looks like."
      );
    }
    if (needsPlatformName) {
      return setError("Name the platform the walkthrough shows.");
    }

    try {
      // Evidence first, deliberately. It attaches to the project, which
      // already exists, so a failure here leaves nothing half-created — as
      // opposed to a document that exists without the evidence it was
      // required to have.
      for (let i = 0; i < shots.length; i++) {
        setStage(`Uploading screenshot ${i + 1} of ${shots.length}…`);
        const body = new FormData();
        body.append("file", shots[i]);
        body.append("project_id", projectId);
        await upload("/api/v1/visual-audits/references", body, "Screenshot upload");
      }

      if (video) {
        setStage("Uploading walkthrough…");
        const body = new FormData();
        body.append("file", video);
        body.append("project_id", projectId);
        body.append("platform_name", platformName.trim());
        await upload("/api/v1/visual-audits/video", body, "Walkthrough upload");
      }

      setStage("Creating document…");
      const created = await apiPost("/api/sow/documents", {
        title: title.trim() || derivedTitle,
        project_id: projectId,
      });

      setStage("Attaching document…");
      const body = new FormData();
      body.append("file", doc);
      await upload(
        `/api/v1/sow/documents/${created.id}/sources/existing-sow`,
        body,
        "Document upload"
      );

      // slug is the canonical URL identifier (migration 0048); id fallback
      // only guards a response that predates the backend adding it.
      reset();
      onImported(created.slug || created.id);
    } catch (e) {
      // The document may already exist even though attaching the file
      // failed. Refresh the library so it is visible rather than stranded
      // invisibly — the user can retry the upload from the document page's
      // "Existing SOW document" source panel instead of losing it.
      qc.invalidateQueries({ queryKey: ["sow-documents"] });
      setError(e instanceof Error ? e.message : "Import failed");
      setStage("");
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[calc(100vh-2rem)] w-full max-w-xl flex-col rounded-lg bg-white shadow-xl duration-150 animate-in fade-in-0 zoom-in-95 motion-reduce:animate-none"
      >
        <div className="border-b px-6 py-4">
          <h2 id={titleId} className="text-base font-medium text-gray-900">
            Import SOW
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Upload a requirements document and show the AI what the platform looks
            like, so the tests it writes name real screens and real buttons.
          </p>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {/* 1 — the document. First because it is the object being imported,
              and because the title below defaults from its file name. */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-600">
              SOW document <span className="text-red-500">*</span>
            </label>
            <input
              ref={docInputRef}
              type="file"
              accept={DOC_ACCEPT}
              className="sr-only"
              disabled={submitting}
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                if (f && mb(f.size) > DOC_MAX_MB) {
                  setError(`The document is over the ${DOC_MAX_MB}MB limit.`);
                  return;
                }
                setError("");
                setDoc(f);
              }}
            />
            {doc ? (
              // h-9 (not vertical padding) so the filled state is exactly as
              // tall as the empty-state button, the Title input and the
              // Project select — the three rows read as one stack of
              // same-sized fields whether or not a file is attached.
              <div className="flex h-9 items-center justify-between gap-3 rounded-lg border border-gray-200 px-3">
                <span className="min-w-0 truncate text-sm text-gray-800">{doc.name}</span>
                <span className="flex flex-shrink-0 items-center gap-2">
                  <span className="text-xs text-gray-400">{fileSizeLabel(doc)}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Remove document"
                    disabled={submitting}
                    className="text-gray-400 hover:text-destructive"
                    onClick={() => setDoc(null)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </span>
              </div>
            ) : (
              <Button
                ref={firstFocusRef}
                type="button"
                variant="outline"
                className="h-9 w-full justify-start text-sm font-normal text-gray-500"
                disabled={submitting}
                onClick={() => docInputRef.current?.click()}
              >
                Choose a .docx, .pdf, .txt or .md file
              </Button>
            )}
          </div>

          {/* 2 — title, after the file it defaults from. */}
          <div className="space-y-1.5">
            <label htmlFor="import-sow-name" className="text-xs font-medium text-gray-600">
              Title
            </label>
            <Input
              id="import-sow-name"
              className="h-9 text-sm"
              value={title}
              disabled={submitting}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={derivedTitle || "Defaults to the file name"}
            />
          </div>

          {/* 3 — project. Required now: evidence is stored against it. */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-600">
              Project <span className="text-red-500">*</span>
            </label>
            {/* null, not "", so Base UI treats it as unset and renders the
                placeholder — there is no "No project" option any more, since
                evidence has nowhere to live without one. */}
            <Select
              value={projectId || null}
              onValueChange={(v) => setProjectId(v || "")}
              disabled={submitting}
              // Without `items`, Select.Value has no way to look up a label
              // for a plain string value and falls back to printing the raw
              // value itself -- so the trigger showed the project's UUID
              // instead of its name. This is what lets it resolve the name.
              items={projects.map((p) => ({ value: p.id, label: p.name }))}
            >
              {/* data-[size=default]:h-9 as well as h-9: SelectTrigger's own
                  height is the arbitrary variant `data-[size=default]:h-8`,
                  which twMerge cannot see as conflicting with a plain `h-9`,
                  so the bare override lost and this field rendered 32px next
                  to the 36px document and title rows. */}
              <SelectTrigger className="h-9 w-full text-sm data-[size=default]:h-9">
                <SelectValue placeholder="Select a project" />
              </SelectTrigger>
              <SelectContent>
                {projects.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-gray-500">
              Platform evidence is stored on the project, so every SOW you import
              for it reuses the same screenshots.
            </p>
          </div>

          {/* 4 — evidence. */}
          <div className="space-y-2.5 rounded-lg border border-gray-200 bg-gray-50/60 p-3.5">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-xs font-medium text-gray-700">
                Platform evidence <span className="text-red-500">*</span>
              </p>
              {!!projectId && !evidenceQuery.isLoading && !evidenceUnavailable && (
                <span className="text-xs text-gray-400">
                  {existingCount > 0
                    ? `${existingCount} already on this project`
                    : "none on this project yet"}
                </span>
              )}
            </div>

            {!projectId ? (
              <p className="text-xs text-gray-500">
                Select a project above first — evidence is filed against the project,
                not this document.
              </p>
            ) : evidenceQuery.isLoading ? (
              <Skeleton className="h-16 w-full rounded-md" />
            ) : evidenceUnavailable ? (
              <p className="text-xs text-gray-500">
                Evidence upload is unavailable — the Vibe Testing feature is turned
                off on this server. You can still import, but the extractor will name
                controls the way your document does rather than the way the product
                does.
              </p>
            ) : (
              <>
                {existingCount > 0 && (
                  <div className="flex items-start gap-2 rounded-md bg-green-50 px-3 py-2">
                    <Check className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-green-600" />
                    <p className="text-xs text-green-800">
                      This project already has{" "}
                      {existing!.screenshots.length > 0 && (
                        <>
                          {existing!.screenshots.length} screenshot
                          {existing!.screenshots.length === 1 ? "" : "s"}
                        </>
                      )}
                      {existing!.screenshots.length > 0 && existing!.videos.length > 0
                        ? " and "
                        : ""}
                      {existing!.videos.length > 0 && (
                        <>
                          {existing!.videos.length} walkthrough
                          {existing!.videos.length === 1 ? "" : "s"}
                        </>
                      )}
                      . Add more below if the UI has changed since.
                    </p>
                  </div>
                )}

                <p className="text-xs text-gray-500">
                  Screens the SOW describes — a signed-in view of each main page, or a
                  walkthrough recording. The extractor reads the real button and field
                  names off these instead of guessing from the document&apos;s wording.
                </p>

                <input
                  ref={shotInputRef}
                  type="file"
                  accept={SHOT_ACCEPT}
                  multiple
                  className="sr-only"
                  disabled={submitting}
                  onChange={(e) => addShots(e.target.files)}
                />
                <input
                  ref={videoInputRef}
                  type="file"
                  accept={VIDEO_ACCEPT}
                  className="sr-only"
                  disabled={submitting}
                  onChange={(e) => setVideoFile(e.target.files?.[0] || null)}
                />

                <div className="grid gap-2.5 sm:grid-cols-2">
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() => shotInputRef.current?.click()}
                    className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-gray-300 bg-white px-3 py-3.5 text-center transition-colors hover:border-gray-400 hover:bg-gray-50 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                  >
                    <ImageIcon className="h-4 w-4 text-gray-400" />
                    <span className="text-xs font-medium text-gray-700">
                      Add screenshots
                    </span>
                    <span className="text-[11px] text-gray-400">
                      PNG, up to {SHOT_MAX_MB}MB each
                    </span>
                  </button>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() => videoInputRef.current?.click()}
                    className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-gray-300 bg-white px-3 py-3.5 text-center transition-colors hover:border-gray-400 hover:bg-gray-50 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                  >
                    <Video className="h-4 w-4 text-gray-400" />
                    <span className="text-xs font-medium text-gray-700">
                      Add a walkthrough
                    </span>
                    <span className="text-[11px] text-gray-400">
                      MP4 / WebM / MOV / MKV, up to {VIDEO_MAX_MB}MB
                    </span>
                  </button>
                </div>

                {(shots.length > 0 || video) && (
                  <ul className="space-y-1">
                    {shots.map((f, i) => (
                      <li
                        key={`${f.name}-${f.size}-${i}`}
                        className="flex items-center justify-between gap-3 rounded-md bg-white px-2.5 py-1.5"
                      >
                        <span className="min-w-0 truncate text-xs text-gray-700">
                          {f.name}
                        </span>
                        <span className="flex flex-shrink-0 items-center gap-2">
                          <span className="text-[11px] text-gray-400">
                            {fileSizeLabel(f)}
                          </span>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-xs"
                            aria-label={`Remove ${f.name}`}
                            disabled={submitting}
                            className="text-gray-400 hover:text-destructive"
                            onClick={() =>
                              setShots((prev) => prev.filter((_, idx) => idx !== i))
                            }
                          >
                            <X className="h-3.5 w-3.5" />
                          </Button>
                        </span>
                      </li>
                    ))}
                    {video && (
                      <li className="flex items-center justify-between gap-3 rounded-md bg-white px-2.5 py-1.5">
                        <span className="min-w-0 truncate text-xs text-gray-700">
                          {video.name}
                        </span>
                        <span className="flex flex-shrink-0 items-center gap-2">
                          <span className="text-[11px] text-gray-400">
                            {fileSizeLabel(video)}
                          </span>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-xs"
                            aria-label="Remove walkthrough"
                            disabled={submitting}
                            className="text-gray-400 hover:text-destructive"
                            onClick={() => setVideo(null)}
                          >
                            <X className="h-3.5 w-3.5" />
                          </Button>
                        </span>
                      </li>
                    )}
                  </ul>
                )}

                {video && (
                  <div className="space-y-1.5 pt-0.5">
                    <label
                      htmlFor="import-sow-platform"
                      className="text-xs font-medium text-gray-600"
                    >
                      Platform / product name <span className="text-red-500">*</span>
                    </label>
                    <Input
                      id="import-sow-platform"
                      className="h-9 text-sm"
                      value={platformName}
                      disabled={submitting}
                      onChange={(e) => setPlatformName(e.target.value)}
                      placeholder="e.g. Acme Recruiting Portal"
                    />
                    <p className="text-xs text-gray-500">
                      Required for a walkthrough — without a declared product the model
                      infers what it is watching, and has been observed pulling
                      checkpoints from unrelated on-screen text.
                    </p>
                  </div>
                )}

                {video && (
                  <p className="text-xs text-gray-500">
                    Heads up: a walkthrough is also digested into its own checkpoints
                    and skills, which costs AI credits. Neither it nor the screenshots
                    ever change this SOW&apos;s content.
                  </p>
                )}
              </>
            )}
          </div>

          {error && (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700"
            >
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t px-6 py-4">
          <span className="min-w-0 truncate text-xs text-gray-500">{stage}</span>
          <span className="flex flex-shrink-0 gap-2">
            <Button variant="outline" size="lg" onClick={close} disabled={submitting}>
              Cancel
            </Button>
            <Button variant="invert" size="lg" onClick={submit} disabled={!canSubmit}>
              {submitting ? "Importing…" : "Import"}
            </Button>
          </span>
        </div>
      </div>
    </div>
  );
}
