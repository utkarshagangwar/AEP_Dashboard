"use client";

/**
 * "Attach sources" — collapsed folder trigger that opens into a source-type
 * picker, then a per-type drop zone with a stage-then-confirm flow.
 *
 * Replaces the old always-visible 4-column upload grid. Behavior change
 * (approved): file pickers no longer upload on selection. A picked/dropped
 * file stages in a removable row; nothing hits the network until "Attach
 * pasted sources" is pressed. Paste-as-you-go for the transcript textarea is
 * unaffected — that still only fires on confirm, same as before.
 *
 * Zero backend/mutation changes. This component receives the four upload
 * mutations (and their paired text/label state) from the page exactly as
 * they were already defined, and calls `.mutate(...)` with the same payload
 * shapes the old inline handlers used. `sow/[id]/page.jsx` owns all of that
 * state; this file is presentation + local drawer/staging state only.
 */

import { useRef, useState } from "react";
import {
  FileText,
  Mic,
  Image as ImageIcon,
  FileCheck2,
  UploadCloud,
  X,
  Check as CheckIcon,
  ChevronDown,
  Loader2,
} from "lucide-react";

type SourceTypeKey = "transcript" | "recording" | "design" | "existing";

interface MutationLike {
  mutate: (variables: any, options?: { onSuccess?: () => void }) => void;
  isPending: boolean;
}

interface AttachSourcesFolderProps {
  attachedCount: number;
  transcriptText: string;
  setTranscriptText: (v: string) => void;
  transcriptError: string;
  transcriptUploadMutation: MutationLike;
  recordingLabel: string;
  setRecordingLabel: (v: string) => void;
  recordingError: string;
  recordingUploadMutation: MutationLike;
  designLabel: string;
  setDesignLabel: (v: string) => void;
  designError: string;
  designUploadMutation: MutationLike;
  existingSowError: string;
  existingSowUploadMutation: MutationLike;
}

const TYPES: Array<{
  key: SourceTypeKey;
  title: string;
  sub: string;
  accept: string;
  acceptLabel: string;
  icon: typeof FileText;
  hasPaste?: boolean;
}> = [
  {
    key: "transcript",
    title: "Meeting transcript",
    sub: "Paste or .txt / .md",
    accept: ".txt,.md",
    acceptLabel: ".txt .md",
    icon: FileText,
    hasPaste: true,
  },
  {
    key: "recording",
    title: "Meeting recording",
    sub: "Audio or video",
    accept: ".mp4,.webm,.mov,.mkv,.mp3,.m4a,.wav,.ogg",
    acceptLabel: ".mp4 .webm .mov .mkv .mp3 .m4a .wav .ogg",
    icon: Mic,
  },
  {
    key: "design",
    title: "Design reference",
    sub: "PNG mockup",
    accept: ".png",
    acceptLabel: ".png",
    icon: ImageIcon,
  },
  {
    key: "existing",
    title: "Existing SOW",
    sub: "docx, pdf, txt, md",
    accept: ".docx,.pdf,.txt,.md",
    acceptLabel: ".docx .pdf .txt .md",
    icon: FileCheck2,
  },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function AttachSourcesFolder({
  attachedCount,
  transcriptText,
  setTranscriptText,
  transcriptError,
  transcriptUploadMutation,
  recordingLabel,
  setRecordingLabel,
  recordingError,
  recordingUploadMutation,
  designLabel,
  setDesignLabel,
  designError,
  designUploadMutation,
  existingSowError,
  existingSowUploadMutation,
}: AttachSourcesFolderProps) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<SourceTypeKey | null>(null);
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const activeType = TYPES.find((t) => t.key === selected) || null;

  const mutationFor: Record<SourceTypeKey, MutationLike> = {
    transcript: transcriptUploadMutation,
    recording: recordingUploadMutation,
    design: designUploadMutation,
    existing: existingSowUploadMutation,
  };
  const errorFor: Record<SourceTypeKey, string> = {
    transcript: transcriptError,
    recording: recordingError,
    design: designError,
    existing: existingSowError,
  };

  const activeMutation = selected ? mutationFor[selected] : null;
  const activeError = selected ? errorFor[selected] : "";

  function resetStaging() {
    setStagedFile(null);
    setDragActive(false);
  }

  function selectType(key: SourceTypeKey) {
    setSelected(key);
    resetStaging();
  }

  function backToPicker() {
    setSelected(null);
    resetStaging();
  }

  function handleFile(f: File | null | undefined) {
    if (f) setStagedFile(f);
  }

  function handleConfirm() {
    if (!activeType || !activeMutation || activeMutation.isPending) return;

    if (activeType.key === "transcript") {
      if (!stagedFile && !transcriptText.trim()) return;
      transcriptUploadMutation.mutate(
        stagedFile ? { file: stagedFile } : { text: transcriptText },
        { onSuccess: backToPicker }
      );
      return;
    }
    if (!stagedFile) return;
    if (activeType.key === "recording") {
      recordingUploadMutation.mutate(
        { file: stagedFile, label: recordingLabel.trim() },
        { onSuccess: backToPicker }
      );
    } else if (activeType.key === "design") {
      designUploadMutation.mutate(
        { file: stagedFile, label: designLabel.trim() },
        { onSuccess: backToPicker }
      );
    } else if (activeType.key === "existing") {
      existingSowUploadMutation.mutate({ file: stagedFile }, { onSuccess: backToPicker });
    }
  }

  const canConfirm =
    !!activeType &&
    !activeMutation?.isPending &&
    (activeType.key === "transcript"
      ? !!stagedFile || !!transcriptText.trim()
      : !!stagedFile);

  return (
    <div className="attach-folder">
      <button
        type="button"
        className="attach-folder__trigger"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <div className="attach-folder__icon-wrap">
          <div className="attach-folder__folder">
            <div className="attach-folder__back" />
            <div className="attach-folder__front">
              <div className="attach-folder__tip" />
              <div className="attach-folder__cover" />
            </div>
          </div>
        </div>
        <div className="attach-folder__title">
          <p>Attach sources</p>
          <p>Transcripts, recordings, designs, or an existing SOW</p>
        </div>
        <span className="attach-folder__count">
          {attachedCount} attached
        </span>
        <ChevronDown
          size={18}
          className="attach-folder__chevron"
          style={{ transform: open ? "rotate(180deg)" : "none" }}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="attach-folder__panel">
          {!selected && (
            <div className="attach-folder__tiles">
              {TYPES.map((t) => {
                const Icon = t.icon;
                return (
                  <button
                    key={t.key}
                    type="button"
                    className="attach-folder__tile"
                    onClick={() => selectType(t.key)}
                  >
                    <Icon size={20} aria-hidden="true" />
                    <span className="attach-folder__tile-title">{t.title}</span>
                    <span className="attach-folder__tile-sub">{t.sub}</span>
                  </button>
                );
              })}
            </div>
          )}

          {selected && activeType && (
            <div className="attach-folder__fields">
              <div className="attach-folder__chips">
                {TYPES.map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    className={
                      "attach-folder__chip" +
                      (t.key === selected ? " attach-folder__chip--active" : "")
                    }
                    onClick={() => selectType(t.key)}
                  >
                    {t.title}
                  </button>
                ))}
              </div>

              {(activeType.key === "recording" || activeType.key === "design") && (
                <>
                  <label className="attach-folder__label">
                    {activeType.key === "recording" ? "Context (optional)" : "Page/screen label (optional)"}
                  </label>
                  <input
                    type="text"
                    className="attach-folder__input"
                    value={activeType.key === "recording" ? recordingLabel : designLabel}
                    onChange={(e) =>
                      activeType.key === "recording"
                        ? setRecordingLabel(e.target.value)
                        : setDesignLabel(e.target.value)
                    }
                    placeholder={
                      activeType.key === "recording"
                        ? "e.g. Sprint planning, July 18"
                        : "e.g. Checkout screen"
                    }
                    disabled={activeMutation?.isPending}
                  />
                </>
              )}

              {activeType.hasPaste && (
                <>
                  <label className="attach-folder__label">Paste text</label>
                  <textarea
                    className="attach-folder__textarea"
                    rows={4}
                    value={transcriptText}
                    onChange={(e) => setTranscriptText(e.target.value)}
                    placeholder="Paste meeting notes or transcript…"
                    disabled={activeMutation?.isPending}
                  />
                  <div className="attach-folder__divider">
                    <span />
                    <em>or drop a file</em>
                    <span />
                  </div>
                </>
              )}

              {!stagedFile && (
                <div
                  className={
                    "attach-folder__dropzone" + (dragActive ? " attach-folder__dropzone--active" : "")
                  }
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragActive(true);
                  }}
                  onDragLeave={() => setDragActive(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragActive(false);
                    handleFile(e.dataTransfer.files?.[0]);
                  }}
                  onClick={() => fileInputRef.current?.click()}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") fileInputRef.current?.click();
                  }}
                >
                  <UploadCloud size={24} aria-hidden="true" />
                  <p>
                    Drop your file here or <span className="attach-folder__browse">browse</span>
                  </p>
                  <p className="attach-folder__accept">{activeType.acceptLabel}</p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={activeType.accept}
                    style={{ display: "none" }}
                    onChange={(e) => {
                      handleFile(e.target.files?.[0]);
                      e.target.value = "";
                    }}
                  />
                </div>
              )}

              {stagedFile && (
                <div className="attach-folder__file-row">
                  <FileText size={18} aria-hidden="true" />
                  <div className="attach-folder__file-meta">
                    <p className="attach-folder__file-name">{stagedFile.name}</p>
                    <p className="attach-folder__file-sub">
                      {formatBytes(stagedFile.size)}
                      {activeMutation?.isPending ? " · attaching…" : " · ready to attach"}
                    </p>
                  </div>
                  {activeMutation?.isPending ? (
                    <Loader2 size={16} className="attach-folder__spin" aria-hidden="true" />
                  ) : (
                    <button
                      type="button"
                      className="attach-folder__remove"
                      onClick={() => setStagedFile(null)}
                      aria-label="Remove staged file"
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
              )}

              {activeError && <p className="attach-folder__error">{activeError}</p>}

              <div className="attach-folder__actions">
                <button type="button" className="attach-folder__cancel" onClick={backToPicker}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="attach-folder__confirm"
                  onClick={handleConfirm}
                  disabled={!canConfirm}
                >
                  {activeMutation?.isPending ? (
                    <>
                      <Loader2 size={14} className="attach-folder__spin" aria-hidden="true" />
                      Attaching…
                    </>
                  ) : (
                    <>
                      <CheckIcon size={14} aria-hidden="true" />
                      Attach pasted sources
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <style jsx>{`
        .attach-folder {
          background: #fff;
          border: 1px solid #e5e7eb;
          border-radius: 12px;
          margin-bottom: 20px;
          overflow: hidden;
        }
        .attach-folder__trigger {
          all: unset;
          box-sizing: border-box;
          display: flex;
          align-items: center;
          gap: 14px;
          width: 100%;
          cursor: pointer;
          padding: 16px 20px;
        }
        .attach-folder__title {
          flex: 1;
          min-width: 0;
        }
        .attach-folder__title p:first-child {
          margin: 0;
          font-size: 15px;
          font-weight: 600;
          color: #111827;
        }
        .attach-folder__title p:last-child {
          margin: 2px 0 0;
          font-size: 12.5px;
          color: #6b7280;
        }
        .attach-folder__count {
          font-size: 12px;
          color: #6b7280;
          border: 1px solid #e5e7eb;
          border-radius: 999px;
          padding: 3px 10px;
          white-space: nowrap;
          flex-shrink: 0;
        }
        .attach-folder__chevron {
          color: #6b7280;
          flex-shrink: 0;
          transition: transform 200ms cubic-bezier(0.22, 1, 0.36, 1);
        }

        /* ---------- folder icon ---------- */
        .attach-folder__icon-wrap {
          width: 46px;
          height: 42px;
          position: relative;
          flex-shrink: 0;
          perspective: 260px;
        }
        .attach-folder__folder {
          position: absolute;
          inset: 0;
          transform-style: preserve-3d;
        }
        .attach-folder__back {
          position: absolute;
          left: 1px;
          right: 1px;
          bottom: 2px;
          height: 30px;
          border-radius: 3px 9px 9px 9px;
          background: linear-gradient(155deg, #e8973a 0%, #c9701a 100%);
          box-shadow: inset 0 -3px 5px rgba(120, 60, 0, 0.25);
        }
        .attach-folder__back::before,
        .attach-folder__back::after {
          content: "";
          position: absolute;
          left: 14%;
          width: 72%;
          height: 9px;
          border-radius: 2px 2px 0 0;
          background: #fbf3e3;
          border: 1px solid #e9dcc0;
          border-bottom: none;
          transform-origin: 50% 100%;
          transition: transform 420ms cubic-bezier(0.22, 1, 0.36, 1);
        }
        .attach-folder__back::before {
          bottom: 24px;
        }
        .attach-folder__back::after {
          bottom: 21px;
        }
        .attach-folder__front {
          position: absolute;
          inset: 0;
          z-index: 1;
          transform-origin: 50% 100%;
          transition: transform 420ms cubic-bezier(0.22, 1, 0.36, 1);
        }
        .attach-folder__tip {
          position: absolute;
          top: 4px;
          left: 1px;
          width: 40%;
          height: 8px;
          border-radius: 4px 8px 0 0;
          background: #f6b44b;
        }
        .attach-folder__cover {
          position: absolute;
          left: 1px;
          right: 1px;
          bottom: 2px;
          top: 10px;
          border-radius: 3px 9px 9px 9px;
          background: linear-gradient(160deg, #fbc46b 0%, #f0a233 100%);
          box-shadow: 0 2px 3px rgba(120, 60, 0, 0.18);
        }

        /* Hover choreography: lid tips open, papers peek, folder floats. */
        .attach-folder__trigger:hover .attach-folder__back::before {
          transform: translateY(-3px) rotateX(-5deg) skewX(5deg);
        }
        .attach-folder__trigger:hover .attach-folder__back::after {
          transform: translateY(-5px) rotateX(-15deg) skewX(12deg);
        }
        .attach-folder__trigger:hover .attach-folder__front {
          transform: rotateX(-40deg) skewX(15deg);
          filter: drop-shadow(0 3px 2px rgba(120, 60, 0, 0.28));
        }
        .attach-folder__trigger:hover .attach-folder__folder {
          animation: attach-folder-float 1.6s cubic-bezier(0.22, 1, 0.36, 1) infinite;
        }
        @keyframes attach-folder-float {
          0% {
            transform: translateY(0);
          }
          50% {
            transform: translateY(-5px);
          }
          100% {
            transform: translateY(0);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .attach-folder__trigger:hover .attach-folder__folder {
            animation: none;
          }
          .attach-folder__back::before,
          .attach-folder__back::after,
          .attach-folder__front {
            transition: none;
          }
        }

        /* ---------- panel ---------- */
        .attach-folder__panel {
          border-top: 1px solid #e5e7eb;
          padding: 18px 20px 20px;
        }
        .attach-folder__tiles {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 10px;
        }
        .attach-folder__tile {
          all: unset;
          box-sizing: border-box;
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 6px;
          border: 1px solid #e5e7eb;
          border-radius: 10px;
          padding: 14px;
          cursor: pointer;
          color: #374151;
          transition: border-color 160ms ease, background-color 160ms ease;
        }
        .attach-folder__tile:hover {
          border-color: #9ca3af;
          background: #f9fafb;
        }
        .attach-folder__tile-title {
          font-size: 13.5px;
          font-weight: 600;
          color: #111827;
        }
        .attach-folder__tile-sub {
          font-size: 11.5px;
          color: #6b7280;
        }

        .attach-folder__chips {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 16px;
        }
        .attach-folder__chip {
          all: unset;
          box-sizing: border-box;
          font-size: 12.5px;
          padding: 7px 13px;
          border-radius: 999px;
          cursor: pointer;
          border: 1px solid #e5e7eb;
          color: #6b7280;
        }
        .attach-folder__chip--active {
          border-color: #111827;
          background: #111827;
          color: #fff;
        }

        .attach-folder__label {
          display: block;
          font-size: 12px;
          font-weight: 600;
          color: #374151;
          margin: 0 0 4px;
        }
        .attach-folder__input,
        .attach-folder__textarea {
          width: 100%;
          box-sizing: border-box;
          font-family: inherit;
          font-size: 13px;
          padding: 9px 11px;
          border: 1px solid #d1d5db;
          border-radius: 8px;
          margin-bottom: 12px;
          transition: border-color 140ms ease;
        }
        .attach-folder__input:focus,
        .attach-folder__textarea:focus {
          outline: none;
          border-color: #2563eb;
          box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
        }
        .attach-folder__textarea {
          resize: vertical;
          min-height: 84px;
        }

        .attach-folder__divider {
          display: flex;
          align-items: center;
          gap: 10px;
          margin: 4px 0 14px;
        }
        .attach-folder__divider span {
          flex: 1;
          height: 1px;
          background: #e5e7eb;
        }
        .attach-folder__divider em {
          font-style: normal;
          font-size: 11.5px;
          color: #9ca3af;
        }

        .attach-folder__dropzone {
          border: 1.5px dashed #d1d5db;
          border-radius: 10px;
          padding: 24px 16px;
          text-align: center;
          cursor: pointer;
          color: #6b7280;
          transition: border-color 160ms ease, background-color 160ms ease;
        }
        .attach-folder__dropzone:hover,
        .attach-folder__dropzone--active {
          border-color: #2563eb;
          background: rgba(37, 99, 235, 0.04);
          color: #2563eb;
        }
        .attach-folder__dropzone p {
          margin: 8px 0 0;
          font-size: 13.5px;
          color: #111827;
        }
        .attach-folder__browse {
          text-decoration: underline;
          font-weight: 600;
        }
        .attach-folder__accept {
          margin: 3px 0 0 !important;
          font-size: 11.5px !important;
          color: #9ca3af !important;
        }

        .attach-folder__file-row {
          display: flex;
          align-items: center;
          gap: 10px;
          border: 1px solid #e5e7eb;
          border-radius: 8px;
          padding: 10px 12px;
          color: #6b7280;
        }
        .attach-folder__file-meta {
          flex: 1;
          min-width: 0;
        }
        .attach-folder__file-name {
          margin: 0;
          font-size: 13px;
          font-weight: 500;
          color: #111827;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .attach-folder__file-sub {
          margin: 2px 0 0;
          font-size: 11.5px;
          color: #6b7280;
        }
        .attach-folder__remove {
          all: unset;
          box-sizing: border-box;
          width: 22px;
          height: 22px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          color: #6b7280;
          flex-shrink: 0;
        }
        .attach-folder__remove:hover {
          background: #f3f4f6;
          color: #111827;
        }
        .attach-folder__spin {
          animation: attach-folder-spin 800ms linear infinite;
          flex-shrink: 0;
          color: #2563eb;
        }
        @keyframes attach-folder-spin {
          to {
            transform: rotate(360deg);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .attach-folder__spin {
            animation-duration: 1600ms;
          }
        }

        .attach-folder__error {
          font-size: 11.5px;
          color: #dc2626;
          margin: 10px 0 0;
        }

        .attach-folder__actions {
          display: flex;
          justify-content: flex-end;
          gap: 8px;
          margin-top: 16px;
        }
        .attach-folder__cancel,
        .attach-folder__confirm {
          all: unset;
          box-sizing: border-box;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-family: inherit;
          font-size: 13.5px;
          font-weight: 500;
          border-radius: 8px;
          padding: 9px 16px;
          cursor: pointer;
        }
        .attach-folder__cancel {
          color: #6b7280;
          border: 1px solid #e5e7eb;
        }
        .attach-folder__cancel:hover {
          color: #111827;
          border-color: #9ca3af;
        }
        .attach-folder__confirm {
          background: #111827;
          color: #fff;
        }
        .attach-folder__confirm:hover:not(:disabled) {
          background: #333;
        }
        .attach-folder__confirm:disabled {
          background: #f3f4f6;
          color: #9ca3af;
          cursor: not-allowed;
        }
      `}</style>
    </div>
  );
}
