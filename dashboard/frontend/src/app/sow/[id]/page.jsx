"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import PageContainer from "../../../components/PageContainer";
import { usePageLoading } from "../../../components/NavigationLoadingProvider";
import { toastSuccess } from "../../../lib/toast";
import { confirmDialog } from "../../../lib/confirm";
import { Checkbox } from "../../../components/ui/checkbox";
import { DeleteIconButton } from "../../../components/ui/delete-icon-button";
import { ErrorAlert } from "../../../components/ui/error-state";
import { Button } from "../../../components/ui/button";
import { BackButton } from "../../../components/ui/back-button";
import SowExtractionProgress from "../../../components/SowExtractionProgress";
import AttachSourcesFolder from "../../../components/AttachSourcesFolder";
import { apiGet, apiFetch, apiPost, apiDelete } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";

// Phase 1 of SOW_FEATURE_PLAN.md: attach source material (meeting
// transcript, meeting recording, design reference) to a document and
// inspect the raw requirements ledger each source's extraction produces.
// Phase 3: trigger generation from that ledger and view the resulting
// versions/sections. Phase 4: coverage badges + gap panels per section.
// Phase 5: hand-edit a section's structured content in-place, and a
// client-side diff between the two most recent versions. Phase 6: export
// to md/docx/pdf and send-to-Vibe-Testing. Phase 7:
// rewrite/patch -- regenerate only selected sections instead of
// everything. Editing, rewrite, export, and send-to-checkpoints all
// always target the CURRENT version (matches each endpoint's own scope)
// -- historical versions stay read-only.
// Import SOW (this addition): a fourth "Existing SOW document" source type
// alongside transcript/recording/design -- an uploaded pre-existing SOW
// feeds the same ledger-extraction -> Generate -> editor/export/Send-to-
// Vibe-Testing pipeline every other source already uses, so nothing below
// this comment changes behavior for the three existing source types.

// Mirrors backend/app/services/sow_patch.py::non_patchable_section_keys()
// -- framing sections are drafted from the WHOLE document's facts, not
// one section's assigned subset, and the templated trailing sections have
// no facts at all, so "regenerate just this section" isn't meaningful for
// either. If this drifts from the backend, the backend's own 400
// rejection is still a correct safety net -- this is only about not
// offering an option that would just bounce.
const NON_PATCHABLE_SECTION_KEYS = new Set([
  "project-overview",
  "scope-of-work",
  "out-of-scope",
  "assumptions",
  "dependencies",
  "exclusions",
  "sign-off-acceptance-criteria",
]);

// done_with_errors: some parts of a chunked document extracted and some did
// not. Deliberately amber rather than green — partial results ARE saved now,
// and the only thing that makes that safe is that a partial source can never
// be mistaken at a glance for a complete one. error_message names the
// failing parts.
const SOURCE_STATUS_COLORS = {
  pending: "#6B7280",
  processing: "#2563EB",
  done: "#16A34A",
  done_with_errors: "#B45309",
  error: "#DC2626",
};
const SOURCE_STATUS_BG = {
  pending: "#F3F4F6",
  processing: "#DBEAFE",
  done: "#DCFCE7",
  done_with_errors: "#FEF3C7",
  error: "#FEE2E2",
};
const SOURCE_STATUS_LABELS = {
  done_with_errors: "Done with errors",
};
const ACTIVE_SOURCE_STATUSES = new Set(["pending", "processing"]);

// Progress stage tokens written by the ledger workers
// (backend/app/models/sow.py::SOW_SOURCE_PROGRESS_STAGES). Any value not
// listed here falls back to "Working" rather than rendering a raw token --
// a newer worker introducing a stage this build doesn't know about must
// degrade, never show machine text to the user.
const SOURCE_STAGE_LABELS = {
  reading: "Reading file",
  chunking: "Splitting into parts",
  extracting: "Extracting facts",
  saving: "Saving to ledger",
};

// The turtle that walks the capsule below. Drawn from the supplied artwork:
// ribbed dark-gold dome, lighter gold rim band, green head and three feet
// that alternate as it moves.
//
// aria-hidden and presentational: the percentage beside it and the "part N of
// M" caption underneath carry the actual meaning, and a screen reader
// announcing a turtle would be noise on top of a number it already has.
function CapsuleTurtle() {
  return (
    <span className="sow-capsule__turtle">
      <svg viewBox="0 0 36 23" width="19" height="12" aria-hidden="true" focusable="false">
        <ellipse className="sow-turtle__foot" cx="8" cy="20" rx="3.1" ry="2.1" fill="#679b46" />
        <ellipse
          className="sow-turtle__foot sow-turtle__foot--b"
          cx="16.5" cy="20.4" rx="3.1" ry="2.1" fill="#679b46"
        />
        <ellipse
          className="sow-turtle__foot sow-turtle__foot--c"
          cx="24" cy="20" rx="2.9" ry="2" fill="#679b46"
        />
        <ellipse className="sow-turtle__head" cx="30" cy="13.6" rx="5.2" ry="4.4" fill="#679b46" />
        <circle cx="32.4" cy="12.4" r="0.75" fill="#2f4d1f" />
        <path d="M4 18.4 A12 11.4 0 0 1 26 18.4 Z" fill="#b57400" />
        <path
          d="M15 7.2 L15 18.4 M9.4 9.6 L7.2 18.4 M20.6 9.6 L22.8 18.4"
          stroke="#fcad00"
          strokeWidth="1"
          fill="none"
          strokeLinecap="round"
        />
        <path d="M4 18.4 A12 11.4 0 0 1 26 18.4 Z" fill="none" stroke="#fcad00" strokeWidth="1.1" />
        <rect x="3.4" y="17.6" width="23.2" height="2.5" rx="1.25" fill="#fcad00" />
      </svg>
    </span>
  );
}

// Live progress cell for the Attached sources table.
//
// Replaces a status badge that could only ever read "Processing", with no
// way to tell a working extraction from a dead worker. Three render modes,
// picked by what the backend can honestly report:
//   1. not processing            -> the plain badge, exactly as before.
//   2. processing, total > 1     -> filling capsule + percentage + "part N
//                                   of M", with the turtle riding the fill's
//                                   leading edge.
//   3. processing, no total      -> sweeping capsule + stage label (single
//                                   indivisible LLM call: a recording, a
//                                   design image, or file-read before
//                                   chunking). No fake percentage is
//                                   invented for these, and no turtle --
//                                   a mascot at a position on a bar with no
//                                   denominator would be claiming progress
//                                   nothing is measuring.
// A source mid-flight when this shipped, or one whose worker never reported,
// has progress_stage === null and lands in mode 3 with a generic label.
function SourceProgressCell({ source }) {
  const isActive = ACTIVE_SOURCE_STATUSES.has(source.status);
  if (!isActive) {
    return (
      <Badge
        status={source.status}
        colors={SOURCE_STATUS_COLORS}
        bg={SOURCE_STATUS_BG}
        labels={SOURCE_STATUS_LABELS}
      />
    );
  }

  const stageLabel =
    SOURCE_STAGE_LABELS[source.progress_stage] ||
    (source.status === "pending" ? "Queued" : "Working");

  const total = Number(source.progress_total) || 0;
  const current = Number(source.progress_current) || 0;
  // Only a total above 1 is a real denominator -- 0/null means "no divisible
  // unit", and 1 means a single chunk, where a bar jumping 0->100 says less
  // than the stage name does.
  const hasBar = total > 1;
  // Clamped both ends: a retry racing a stale poll could momentarily yield
  // current > total, which would otherwise overflow the bar track.
  const pct = hasBar ? Math.min(100, Math.max(0, Math.round((current / total) * 100))) : null;

  return (
    <div style={{ minWidth: 170 }}>
      {hasBar ? (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              className="sow-capsule"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${stageLabel}: ${pct}% complete`}
            >
              <div className="sow-capsule__fill" style={{ width: `${pct}%` }}>
                <span className="sow-capsule__sheen" aria-hidden="true" />
                <CapsuleTurtle />
              </div>
            </div>
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: "#92400E",
                minWidth: 32,
                textAlign: "right",
              }}
            >
              {pct}%
            </span>
          </div>
          <div style={{ fontSize: 11, color: "#6B7280", marginTop: 6 }}>
            {stageLabel} · part {Math.min(current + 1, total)} of {total}
          </div>
        </>
      ) : (
        <>
          {/* No aria-valuenow: this is a genuinely indeterminate bar, and the
              ARIA spec's own signal for that is the absence of the value,
              not a made-up one. */}
          <div
            className="sow-capsule sow-capsule--indeterminate"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${stageLabel} — in progress`}
          >
            <div className="sow-capsule__sweep" />
          </div>
          <div style={{ fontSize: 11, color: "#6B7280", marginTop: 6 }}>{stageLabel}…</div>
        </>
      )}
    </div>
  );
}

const FACT_TYPE_LABELS = {
  feature: "Feature",
  decision: "Decision",
  ui_element: "UI element",
  open_question: "Open question",
};

// Generation job / version / section status vocab (backend/app/models/sow.py)
const GENERATION_ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

const JOB_STATUS_COLORS = {
  queued: "#6B7280",
  running: "#2563EB",
  done: "#16A34A",
  done_with_errors: "#B45309",
  error: "#DC2626",
};
const JOB_STATUS_BG = {
  queued: "#F3F4F6",
  running: "#DBEAFE",
  done: "#DCFCE7",
  done_with_errors: "#FEF3C7",
  error: "#FEE2E2",
};

const VERSION_STATUS_COLORS = { ...JOB_STATUS_COLORS, pending: "#6B7280", generating: "#2563EB" };
const VERSION_STATUS_BG = { ...JOB_STATUS_BG, pending: "#F3F4F6", generating: "#DBEAFE" };

const SECTION_STATUS_COLORS = { pending: "#6B7280", generating: "#2563EB", done: "#16A34A", error: "#DC2626" };
const SECTION_STATUS_BG = { pending: "#F3F4F6", generating: "#DBEAFE", done: "#DCFCE7", error: "#FEE2E2" };

// Phase 4: coverage score thresholds (Pass 3 completeness audit --
// app/services/sow_audit.py). Null means "never audited" -- either the
// audit pass failed (transient, logged server-side) or this is a framing/
// templated section that's narrative by design and intentionally never
// audited (see sow_audit.py's module docstring) -- both render the same
// way here since neither is actionable from this read-only view.
function coverageStyle(score) {
  if (score >= 90) return { color: "#166534", bg: "#DCFCE7", label: `${score}% coverage` };
  if (score >= 70) return { color: "#92400E", bg: "#FEF3C7", label: `${score}% coverage` };
  return { color: "#991B1B", bg: "#FEE2E2", label: `${score}% coverage` };
}

function CoverageBadge({ score }) {
  if (score === null || score === undefined) return null;
  const { color, bg, label } = coverageStyle(score);
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: 11,
        fontWeight: 600,
        color,
        background: bg,
        borderRadius: 999,
        padding: "2px 9px",
      }}
    >
      {label}
    </span>
  );
}

function Badge({ status, colors, bg, labels }) {
  // A label overrides the raw token so a machine value like
  // "done_with_errors" reads as "Done with errors" instead of relying on
  // capitalize, which would leave the underscores visible.
  const label = labels?.[status];
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: 11,
        fontWeight: 600,
        color: colors[status] || "#6B7280",
        background: bg[status] || "#F3F4F6",
        borderRadius: 999,
        padding: "2px 9px",
        textTransform: label ? "none" : "capitalize",
      }}
    >
      {label || status}
    </span>
  );
}

function Section({ title, description, children }) {
  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid #E5E7EB",
        borderRadius: 10,
        padding: 20,
        marginBottom: 20,
      }}
    >
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "#111827" }}>{title}</h2>
      {description && (
        <p style={{ margin: "4px 0 16px", fontSize: 12, color: "#6B7280" }}>{description}</p>
      )}
      {children}
    </div>
  );
}

// Same chrome as Section, plus a click-to-toggle header and an optional
// count pill. Kept as a separate component rather than a flag on Section so
// every existing Section call site keeps its exact current markup and
// behaviour -- nothing else on this page becomes collapsible by accident.
//
// `open`/`onToggle` are controlled by the parent so collapse state survives
// re-renders driven by the 3s polling queries.
function CollapsibleSection({ title, description, badge, open, onToggle, children }) {
  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid #E5E7EB",
        borderRadius: 10,
        padding: 20,
        marginBottom: 20,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        // .section-toggle replaces the old underline-on-hover: the whole
        // header band is the toggle, so it tints as a surface. `padding`,
        // `width` and `color` all live in the class -- setting any of them
        // inline here would outrank it and break the effect.
        className="section-toggle"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <span
          aria-hidden="true"
          className="section-toggle-caret"
          style={{
            fontSize: 11,
            display: "inline-block",
            transform: open ? "rotate(90deg)" : "none",
          }}
        >
          ▶
        </span>
        {/* color: inherit so the heading deepens with the button's hover
            state instead of pinning itself to a fixed near-black. */}
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "inherit" }}>{title}</h2>
        {badge != null && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: "#374151",
              background: "#F3F4F6",
              borderRadius: 999,
              padding: "2px 9px",
            }}
          >
            {badge}
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#2563EB" }}>
          {open ? "Collapse" : "Expand"}
        </span>
      </button>
      {open && (
        <>
          {description && (
            <p style={{ margin: "8px 0 16px", fontSize: 12, color: "#6B7280" }}>{description}</p>
          )}
          {children}
        </>
      )}
    </div>
  );
}

const labelStyle = { fontSize: 12, fontWeight: 600, color: "#374151", display: "block", marginBottom: 4 };
const inputStyle = {
  width: "100%",
  fontSize: 13,
  padding: "8px 10px",
  border: "1px solid #D1D5DB",
  borderRadius: 8,
  boxSizing: "border-box",
  marginBottom: 10,
};

// ── Phase 5: structured block editor ─────────────────────────────────────
// Mirrors backend/app/services/sow_drafting.py's _validate_block contract
// exactly -- every block type/field editable here is one _validate_block
// accepts, so nothing a user can produce through this UI will ever get
// rejected by the server-side re-validation in PATCH .../sections/{key}.
const BLOCK_ELEMENT_TYPES = [
  "button", "dropdown", "filter", "checkbox", "toggle", "slider",
  "three_dot_menu", "tab", "modal", "other",
];
const BLOCK_TYPE_LABELS = {
  heading: "Heading",
  paragraph: "Paragraph",
  control_spec: "Control",
  bullet_list: "Bullet list",
  table: "Table",
  callout: "Callout",
};

function defaultBlock(type) {
  switch (type) {
    case "heading":
      return { type: "heading", level: 3, text: "" };
    case "control_spec":
      return { type: "control_spec", element_type: "button", label: "", behavior: "", fact_index: null };
    case "bullet_list":
      return { type: "bullet_list", items: [""] };
    case "table":
      return { type: "table", headers: ["Column 1"], rows: [[""]] };
    case "callout":
      return { type: "callout", tone: "info", text: "" };
    case "paragraph":
    default:
      return { type: "paragraph", text: "" };
  }
}

const editorFieldStyle = { ...inputStyle, marginBottom: 0 };

function TableBlockEditor({ block, onChange }) {
  const headers = block.headers || [];
  const rows = block.rows || [];

  const setHeader = (i, val) => {
    const next = [...headers];
    next[i] = val;
    onChange({ ...block, headers: next });
  };
  const setCell = (r, c, val) => {
    const next = rows.map((row) => [...row]);
    next[r][c] = val;
    onChange({ ...block, rows: next });
  };
  const addColumn = () =>
    onChange({
      ...block,
      headers: [...headers, `Column ${headers.length + 1}`],
      rows: rows.map((row) => [...row, ""]),
    });
  const removeColumn = (i) =>
    onChange({
      ...block,
      headers: headers.filter((_, x) => x !== i),
      rows: rows.map((row) => row.filter((_, x) => x !== i)),
    });
  const addRow = () => onChange({ ...block, rows: [...rows, headers.map(() => "")] });
  const removeRow = (r) => onChange({ ...block, rows: rows.filter((_, x) => x !== r) });

  return (
    <div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 8 }}>
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={i} style={{ padding: 2, minWidth: 120 }}>
                  <input
                    value={h}
                    onChange={(e) => setHeader(i, e.target.value)}
                    style={{ ...editorFieldStyle, fontWeight: 600, fontSize: 12 }}
                  />
                  <Button
                    variant="destructive"
                    size="xs"
                    onClick={() => removeColumn(i)}
                    className="mt-1 w-full"
                  >
                    Remove column
                  </Button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} style={{ padding: 2 }}>
                    <input
                      value={cell}
                      onChange={(e) => setCell(r, c, e.target.value)}
                      style={{ ...editorFieldStyle, fontSize: 12 }}
                    />
                  </td>
                ))}
                <td style={{ padding: 2 }}>
                  <Button variant="destructive" size="xs" onClick={() => removeRow(r)}>
                    Remove row
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant="outline" size="xs" onClick={addRow}>+ Row</Button>
        <Button variant="outline" size="xs" onClick={addColumn}>+ Column</Button>
      </div>
    </div>
  );
}

function BlockEditorCard({ block, index, total, onChange, onMove, onRemove }) {
  return (
    <div
      style={{
        border: "1px solid #E5E7EB",
        borderRadius: 8,
        padding: 12,
        marginBottom: 8,
        background: "#FAFAFA",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <span style={{ fontSize: 11, fontWeight: 700, color: "#6B7280", textTransform: "uppercase" }}>
          {BLOCK_TYPE_LABELS[block.type] || block.type}
        </span>
        <div style={{ display: "flex", gap: 4 }}>
          <Button variant="outline" size="xs" onClick={() => onMove(index, -1)} disabled={index === 0}>
            ↑
          </Button>
          <Button variant="outline" size="xs" onClick={() => onMove(index, 1)} disabled={index === total - 1}>
            ↓
          </Button>
          <Button variant="destructive" size="xs" onClick={() => onRemove(index)}>
            Remove
          </Button>
        </div>
      </div>

      {block.type === "heading" && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select
            value={block.level || 2}
            onChange={(e) => onChange({ ...block, level: Number(e.target.value) })}
            style={{ ...editorFieldStyle, width: 80 }}
          >
            {[1, 2, 3, 4].map((l) => (
              <option key={l} value={l}>H{l}</option>
            ))}
          </select>
          <input
            value={block.text || ""}
            onChange={(e) => onChange({ ...block, text: e.target.value })}
            style={{ ...editorFieldStyle, flex: 1 }}
          />
        </div>
      )}

      {block.type === "paragraph" && (
        <textarea
          value={block.text || ""}
          onChange={(e) => onChange({ ...block, text: e.target.value })}
          rows={3}
          style={{ ...editorFieldStyle, resize: "vertical", fontFamily: "inherit" }}
        />
      )}

      {block.type === "control_spec" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <select
              value={block.element_type || "other"}
              onChange={(e) => onChange({ ...block, element_type: e.target.value })}
              style={{ ...editorFieldStyle, width: 160 }}
            >
              {BLOCK_ELEMENT_TYPES.map((t) => (
                <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
              ))}
            </select>
            <input
              placeholder="Label"
              value={block.label || ""}
              onChange={(e) => onChange({ ...block, label: e.target.value })}
              style={{ ...editorFieldStyle, flex: 1 }}
            />
          </div>
          <textarea
            placeholder="Behavior"
            value={block.behavior || ""}
            onChange={(e) => onChange({ ...block, behavior: e.target.value })}
            rows={2}
            style={{ ...editorFieldStyle, resize: "vertical", fontFamily: "inherit" }}
          />
        </div>
      )}

      {block.type === "bullet_list" && (
        <textarea
          value={(block.items || []).join("\n")}
          onChange={(e) => onChange({ ...block, items: e.target.value.split("\n") })}
          rows={Math.max(3, (block.items || []).length)}
          placeholder="One item per line"
          style={{ ...editorFieldStyle, resize: "vertical", fontFamily: "inherit" }}
        />
      )}

      {block.type === "table" && <TableBlockEditor block={block} onChange={onChange} />}

      {block.type === "callout" && (
        <div>
          <select
            value={block.tone || "info"}
            onChange={(e) => onChange({ ...block, tone: e.target.value })}
            style={{ ...editorFieldStyle, width: 140, marginBottom: 8 }}
          >
            <option value="info">Info</option>
            <option value="warning">Warning</option>
          </select>
          <textarea
            value={block.text || ""}
            onChange={(e) => onChange({ ...block, text: e.target.value })}
            rows={2}
            style={{ ...editorFieldStyle, resize: "vertical", fontFamily: "inherit" }}
          />
        </div>
      )}
    </div>
  );
}

function SectionEditor({ blocks, onSave, onCancel, saving, error }) {
  const [localBlocks, setLocalBlocks] = useState(blocks);
  const [addType, setAddType] = useState("paragraph");

  const updateBlock = (index, next) =>
    setLocalBlocks((prev) => prev.map((b, i) => (i === index ? next : b)));
  const moveBlock = (index, dir) =>
    setLocalBlocks((prev) => {
      const target = index + dir;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  const removeBlock = (index) => setLocalBlocks((prev) => prev.filter((_, i) => i !== index));
  const addBlock = () => setLocalBlocks((prev) => [...prev, defaultBlock(addType)]);

  return (
    <div>
      {localBlocks.map((block, i) => (
        <BlockEditorCard
          key={i}
          block={block}
          index={i}
          total={localBlocks.length}
          onChange={(next) => updateBlock(i, next)}
          onMove={moveBlock}
          onRemove={removeBlock}
        />
      ))}

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 14 }}>
        <select
          value={addType}
          onChange={(e) => setAddType(e.target.value)}
          style={{ ...editorFieldStyle, width: 160 }}
        >
          {Object.entries(BLOCK_TYPE_LABELS).map(([val, label]) => (
            <option key={val} value={val}>{label}</option>
          ))}
        </select>
        <Button variant="outline" size="xs" onClick={addBlock}>+ Add block</Button>
      </div>

      {error && <p style={{ fontSize: 12, color: "#DC2626", marginTop: 0 }}>{error}</p>}

      <div style={{ display: "flex", gap: 10 }}>
        <Button
          variant="invert"
          size="lg"
          onClick={() => onSave(localBlocks)}
          disabled={saving || localBlocks.length === 0}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button variant="outline" size="lg" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ── Phase 5: version diff (client-side, no diff service -- plan §6: "client-
// side text diff over each section's rendered content_blocks is sufficient").
// Standard O(n*m) LCS line diff. Capped at _DIFF_MAX_LINES per side so a
// pathologically large section can't freeze the tab computing an O(n*m)
// table -- falls back to "changed, too large to show inline" instead.
const _DIFF_MAX_LINES = 1500;

function diffLines(oldText, newText) {
  const a = (oldText || "").split("\n");
  const b = (newText || "").split("\n");
  if (a.length > _DIFF_MAX_LINES || b.length > _DIFF_MAX_LINES) return null;
  const n = a.length;
  const m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const result = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      result.push({ type: "same", text: a[i] });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ type: "removed", text: a[i] });
      i++;
    } else {
      result.push({ type: "added", text: b[j] });
      j++;
    }
  }
  while (i < n) { result.push({ type: "removed", text: a[i] }); i++; }
  while (j < m) { result.push({ type: "added", text: b[j] }); j++; }
  return result;
}

const DIFF_LINE_STYLE = {
  same: { color: "#374151", background: "transparent" },
  added: { color: "#166534", background: "#DCFCE7" },
  removed: { color: "#991B1B", background: "#FEE2E2" },
};

function SectionDiffCard({ sectionKey, oldSection, newSection }) {
  const heading = newSection?.heading || oldSection?.heading || sectionKey;
  const onlyInNew = !oldSection && !!newSection;
  const onlyInOld = !!oldSection && !newSection;
  const oldText = oldSection?.rendered_markdown || "";
  const newText = newSection?.rendered_markdown || "";
  const unchanged = !onlyInNew && !onlyInOld && oldText === newText;
  const lines = !onlyInNew && !onlyInOld && !unchanged ? diffLines(oldText, newText) : null;

  let badgeLabel = "Unchanged";
  let badgeColor = "#6B7280";
  let badgeBg = "#F3F4F6";
  if (onlyInNew) { badgeLabel = "Added"; badgeColor = "#166534"; badgeBg = "#DCFCE7"; }
  else if (onlyInOld) { badgeLabel = "Removed"; badgeColor = "#991B1B"; badgeBg = "#FEE2E2"; }
  else if (!unchanged) { badgeLabel = "Changed"; badgeColor = "#92400E"; badgeBg = "#FEF3C7"; }

  return (
    <div style={{ border: "1px solid #E5E7EB", borderRadius: 8, padding: 16, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: "#111827" }}>{heading}</span>
        <span
          style={{
            display: "inline-block",
            fontSize: 11,
            fontWeight: 600,
            color: badgeColor,
            background: badgeBg,
            borderRadius: 999,
            padding: "2px 9px",
          }}
        >
          {badgeLabel}
        </span>
      </div>

      {unchanged && (
        <p style={{ fontSize: 12, color: "#9CA3AF", margin: 0 }}>No changes in this section.</p>
      )}

      {(onlyInNew || onlyInOld) && (
        <pre
          style={{
            margin: 0,
            fontFamily: "inherit",
            fontSize: 13,
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            ...(onlyInOld ? { color: "#991B1B", textDecoration: "line-through" } : { color: "#374151" }),
          }}
        >
          {onlyInNew ? newText : oldText}
        </pre>
      )}

      {!onlyInNew && !onlyInOld && !unchanged && lines === null && (
        <p style={{ fontSize: 12, color: "#6B7280", margin: 0 }}>
          This section changed but is too large to diff inline — open each version individually
          to compare.
        </p>
      )}

      {!onlyInNew && !onlyInOld && !unchanged && lines !== null && (
        <pre
          style={{
            margin: 0,
            fontFamily: "inherit",
            fontSize: 12.5,
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {lines.map((l, i) => (
            <div key={i} style={{ ...DIFF_LINE_STYLE[l.type], padding: "0 4px" }}>
              {l.type === "added" ? "+ " : l.type === "removed" ? "- " : "  "}
              {l.text}
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}

export default function SowDocumentPage() {
  // `id` here is whatever identifier is in the URL -- the document's
  // current slug (the normal case), an old/renamed-away slug, or a raw
  // UUID. The backend resolves any of those; the effect below is what
  // canonicalizes the URL itself once the real document loads.
  const { id } = useParams();
  const router = useRouter();
  const qc = useQueryClient();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite =
    !!user && (user.role === "admin" || (user.permissions || []).includes("sow"));

  const [transcriptText, setTranscriptText] = useState("");
  const [transcriptError, setTranscriptError] = useState("");
  const [recordingLabel, setRecordingLabel] = useState("");
  const [recordingError, setRecordingError] = useState("");
  const [designLabel, setDesignLabel] = useState("");
  const [designError, setDesignError] = useState("");
  const [existingSowError, setExistingSowError] = useState("");
  const [factFilter, setFactFilter] = useState("");
  // Requirements ledger collapse state. Starts open so a small ledger reads
  // exactly as it did before; the effect below closes it once for a large
  // one. Once the user touches the toggle their choice is final for the
  // session -- see ledgerAutoCollapsedRef.
  const [ledgerOpen, setLedgerOpen] = useState(true);
  // Guards the auto-collapse so it fires at most once per document. Without
  // it, every 3s poll that re-reports a large ledger would slam the section
  // shut again the moment the user opened it.
  const ledgerAutoCollapsedRef = useRef(false);
  // Imported documents can be long. Keep the document immediately available
  // after the ledger without forcing every downstream control below it off
  // screen; the user explicitly opens it when they want the complete source.
  const [importedSowOpen, setImportedSowOpen] = useState(false);
  const [generateError, setGenerateError] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState(null);

  // Latest generation job (404 = "never generated yet", not a real error --
  // swallowed below rather than surfaced as an error banner). Polled while
  // active so the Generate button, document status, and version list all
  // converge on the final state without a manual refresh.
  const { data: job } = useQuery({
    queryKey: ["sow-generation", id],
    queryFn: () => apiGet(`/api/v1/sow/documents/${id}/generation`),
    retry: false,
    refetchInterval: (query) => {
      const j = query.state.data;
      return j && GENERATION_ACTIVE_JOB_STATUSES.has(j.status) ? 3000 : false;
    },
  });

  const { data: doc, isLoading: docLoading, error: docError } = useQuery({
    queryKey: ["sow-document", id],
    queryFn: () => apiGet(`/api/sow/documents/${id}`),
    // Reads query.state.data (not the outer `doc` binding, which doesn't
    // exist yet at this point in the module -- avoids a TDZ self-reference)
    // plus the already-declared `job` query for the same "still working" signal.
    refetchInterval: (query) => {
      const d = query.state.data;
      const activeByDoc = d?.status === "generating";
      const activeByJob = job && GENERATION_ACTIVE_JOB_STATUSES.has(job.status);
      return activeByDoc || activeByJob ? 3000 : false;
    },
  });
  const generationActive = doc?.status === "generating" || (job && GENERATION_ACTIVE_JOB_STATUSES.has(job.status));

  const { data: versions } = useQuery({
    queryKey: ["sow-versions", id],
    queryFn: () => apiGet(`/api/v1/sow/documents/${id}/versions`),
    refetchInterval: () => (generationActive ? 4000 : false),
  });
  const versionList = versions || [];

  // Auto-select the most recent version (list is already ordered by
  // version_number desc) the first time the list loads, and again whenever
  // a newer version appears (e.g. right after a generation run completes) --
  // but never override a version the user deliberately clicked on.
  useEffect(() => {
    if (versionList.length === 0) return;
    setSelectedVersionId((current) => {
      if (current && versionList.some((v) => v.id === current)) return current;
      return versionList[0].id;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionList.map((v) => v.id).join(",")]);

  const { data: versionDetail, isLoading: versionDetailLoading } = useQuery({
    queryKey: ["sow-version-detail", id, selectedVersionId],
    queryFn: () => apiGet(`/api/v1/sow/documents/${id}/versions/${selectedVersionId}`),
    enabled: !!selectedVersionId,
  });

  // Phase 5: diff the selected version against the one immediately before
  // it (by version_number). There's no `parent_version_id`-based "the
  // version this patched" yet -- every version so far is a full_generation
  // (Phase 7's patch/rewrite flow hasn't landed), so "previous by number"
  // is the only meaningful comparison available today.
  const [diffMode, setDiffMode] = useState(false);
  const selectedVersionIndex = versionList.findIndex((v) => v.id === selectedVersionId);
  const previousVersion =
    selectedVersionIndex >= 0 && selectedVersionIndex + 1 < versionList.length
      ? versionList[selectedVersionIndex + 1]
      : null;
  const { data: previousVersionDetail, isLoading: previousVersionDetailLoading } = useQuery({
    queryKey: ["sow-version-detail", id, previousVersion?.id],
    queryFn: () => apiGet(`/api/v1/sow/documents/${id}/versions/${previousVersion.id}`),
    enabled: diffMode && !!previousVersion,
  });

  const generateMutation = useMutation({
    mutationFn: () => apiPost(`/api/v1/sow/documents/${id}/generate`, {}),
    onSuccess: () => {
      setGenerateError("");
      qc.invalidateQueries({ queryKey: ["sow-generation", id] });
      qc.invalidateQueries({ queryKey: ["sow-document", id] });
      qc.invalidateQueries({ queryKey: ["sow-versions", id] });
      toastSuccess("Generation started");
    },
    onError: (e) => setGenerateError(e.message),
  });

  // Phase 5: hand-edit a section's structured content. Always targets the
  // CURRENT version (matches the backend's own scope -- see
  // patch_section's docstring) -- the frontend only ever shows an Edit
  // button when isViewingCurrentVersion is true, computed further down
  // once `doc` is available.
  const [editingSectionKey, setEditingSectionKey] = useState(null);
  const [editSaveError, setEditSaveError] = useState("");

  const patchSectionMutation = useMutation({
    mutationFn: async ({ sectionKey, blocks }) => {
      const res = await apiFetch(
        `/api/v1/sow/documents/${id}/sections/${encodeURIComponent(sectionKey)}`,
        { method: "PATCH", body: JSON.stringify({ content_blocks: blocks }) }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Save failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setEditingSectionKey(null);
      setEditSaveError("");
      qc.invalidateQueries({ queryKey: ["sow-version-detail", id, selectedVersionId] });
      qc.invalidateQueries({ queryKey: ["sow-versions", id] });
    },
    onError: (e) => setEditSaveError(e.message),
  });

  // Phase 6: export + send-to-checkpoints. Both always act on the
  // document's CURRENT version (matching the backend's own scope), not
  // whichever version happens to be selected in the picker above.
  const [exportingFormat, setExportingFormat] = useState(null);
  const [exportError, setExportError] = useState("");

  async function downloadExport(format) {
    setExportError("");
    setExportingFormat(format);
    try {
      const res = await apiFetch(`/api/v1/sow/documents/${id}/export`, {
        method: "POST",
        body: JSON.stringify({ format }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Export failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const safeTitle = (doc?.title || "sow-document").replace(/[^A-Za-z0-9._-]+/g, "-");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${safeTitle}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(e.message);
    } finally {
      setExportingFormat(null);
    }
  }

  const sendToCheckpointsMutation = useMutation({
    mutationFn: () => apiPost(`/api/v1/sow/documents/${id}/send-to-checkpoints`, {}),
  });

  // Phase 7: rewrite/patch -- regenerate only selected sections. Only
  // ever offered while viewing the current version (same reasoning as
  // the Phase 5 editor gating -- the endpoint always targets
  // current_version_id regardless of what's in the URL).
  const [rewriteTargets, setRewriteTargets] = useState(() => new Set());
  const [rewriteOverrides, setRewriteOverrides] = useState(() => new Set());
  const [rewriteError, setRewriteError] = useState("");
  // Collapsed by default. Expanded, the full section checklist reads like a
  // list of sections that NEED rewriting — it is only a picker, and on a
  // freshly imported SOW nothing needs rewriting at all. It opens when the
  // user asks for it, or when the affected-sections banner sends them here.
  const [rewriteOpen, setRewriteOpen] = useState(false);
  // Open by default -- this panel holds the actual document (or the
  // Skills/TDDs extraction action) and collapsing it on load would hide the
  // page's primary content the moment someone lands here. Collapsible only
  // so a user who's done with it can put it away, same as Imported SOW.
  const [versionsOpen, setVersionsOpen] = useState(true);
  // The version PICKER, distinct from `versionsOpen` above (which is the
  // whole Generate/Skills section). Collapsed by default and shaped like the
  // Rewrite panel: on the overwhelmingly common single-version document the
  // list is one row that only restates what is already on screen, and the
  // 200px column it used to occupy was taken out of the document's width on
  // every page load to show it. The header still names the selected version,
  // so collapsing costs no information.
  const [versionPickerOpen, setVersionPickerOpen] = useState(false);

  function toggleRewriteTarget(key) {
    setRewriteTargets((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  function toggleRewriteOverride(key) {
    setRewriteOverrides((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const rewriteMutation = useMutation({
    mutationFn: () =>
      apiPost(`/api/v1/sow/documents/${id}/rewrite`, {
        target_sections: Array.from(rewriteTargets),
        override_manual_edits: Array.from(rewriteOverrides),
      }),
    onSuccess: () => {
      setRewriteError("");
      setRewriteTargets(new Set());
      setRewriteOverrides(new Set());
      qc.invalidateQueries({ queryKey: ["sow-generation", id] });
      qc.invalidateQueries({ queryKey: ["sow-document", id] });
      qc.invalidateQueries({ queryKey: ["sow-versions", id] });
    },
    onError: (e) => setRewriteError(e.message),
  });

  const { data: sources, isLoading: sourcesLoading } = useQuery({
    queryKey: ["sow-sources", id],
    queryFn: () => apiGet(`/api/v1/sow/documents/${id}/sources`),
    refetchInterval: (query) => {
      const list = query.state.data || [];
      return list.some((s) => ACTIVE_SOURCE_STATUSES.has(s.status)) ? 3000 : false;
    },
  });

  const ledgerQueryKey = ["sow-ledger", id, factFilter];
  // Read through apiFetch rather than apiGet so the X-Total-Count header is
  // available: the badge below must report how many facts EXIST, not how many
  // this response happened to carry. Counting the array made a document with
  // more facts than the page limit report the limit as its total, which read
  // as "extraction lost the rest of my document" when nothing had been lost.
  const { data: ledgerPage, isLoading: ledgerLoading } = useQuery({
    queryKey: ledgerQueryKey,
    queryFn: async () => {
      const res = await apiFetch(
        `/api/v1/sow/documents/${id}/ledger${factFilter ? `?fact_type=${factFilter}` : ""}`
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          typeof body?.detail === "string" ? body.detail : "Could not load the requirements ledger"
        );
      }
      const facts = await res.json();
      const header = res.headers.get("X-Total-Count");
      const parsed = Number(header);
      return {
        facts,
        // Fall back to the array length if the header is missing (an older
        // backend, or a proxy that stripped it) -- never render "undefined".
        total: Number.isFinite(parsed) && parsed >= 0 ? parsed : facts.length,
      };
    },
    refetchInterval: (query) => {
      const sourceList = sources || [];
      return sourceList.some((s) => ACTIVE_SOURCE_STATUSES.has(s.status)) ? 3000 : false;
    },
  });

  // Collapse the ledger once, the first time it is observed to be large.
  // 40 rows is roughly one screenful at this row height -- below that the
  // section is easier to read open, above it the sections underneath
  // (Generate SOW, versions, section editor) get pushed out of reach.
  const LEDGER_AUTO_COLLAPSE_THRESHOLD = 40;
  useEffect(() => {
    if (ledgerAutoCollapsedRef.current) return;
    if ((ledgerPage?.facts || []).length > LEDGER_AUTO_COLLAPSE_THRESHOLD) {
      ledgerAutoCollapsedRef.current = true;
      setLedgerOpen(false);
    }
  }, [ledgerPage]);

  function invalidateAll() {
    qc.invalidateQueries({ queryKey: ["sow-sources", id] });
    qc.invalidateQueries({ queryKey: ["sow-ledger", id] });
  }

  const transcriptUploadMutation = useMutation({
    mutationFn: async ({ file, text }) => {
      const form = new FormData();
      if (file) form.append("file", file);
      else form.append("text", text);
      const res = await apiFetch(`/api/v1/sow/documents/${id}/sources/transcript`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Upload failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setTranscriptText("");
      setTranscriptError("");
      invalidateAll();
    },
    onError: (e) => setTranscriptError(e.message),
  });

  const recordingUploadMutation = useMutation({
    mutationFn: async ({ file, label }) => {
      const form = new FormData();
      form.append("file", file);
      if (label) form.append("context_label", label);
      const res = await apiFetch(`/api/v1/sow/documents/${id}/sources/recording`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Upload failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setRecordingLabel("");
      setRecordingError("");
      invalidateAll();
    },
    onError: (e) => setRecordingError(e.message),
  });

  const designUploadMutation = useMutation({
    mutationFn: async ({ file, label }) => {
      const form = new FormData();
      form.append("file", file);
      if (label) form.append("target_page", label);
      const res = await apiFetch(`/api/v1/sow/documents/${id}/sources/design`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Upload failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setDesignLabel("");
      setDesignError("");
      invalidateAll();
    },
    onError: (e) => setDesignError(e.message),
  });

  // Import SOW (SOW tab): attach a pre-existing SOW/requirements document
  // as a fourth source type, same shape as transcript/recording/design --
  // its facts land in the ledger below exactly like any other source, and
  // from there Generate/editor/export/Send-to-Vibe-Testing are all the
  // existing, unmodified flows.
  const existingSowUploadMutation = useMutation({
    mutationFn: async ({ file }) => {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch(`/api/v1/sow/documents/${id}/sources/existing-sow`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Upload failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: () => {
      setExistingSowError("");
      invalidateAll();
    },
    onError: (e) => setExistingSowError(e.message),
  });

  const deleteSourceMutation = useMutation({
    mutationFn: (sourceId) => apiDelete(`/api/v1/sow/documents/${id}/sources/${sourceId}`),
    onSuccess: () => invalidateAll(),
  });

  // The browser tab/history entry otherwise just shows the app-wide default
  // (or, before that existed, nothing) -- never which document you're on.
  // Runs before the loading-gate return below so hook order stays fixed
  // regardless of docLoading; restoring the previous title on unmount keeps
  // it from leaking onto whatever page you navigate to next.
  useEffect(() => {
    if (!doc?.title) return undefined;
    const previous = document.title;
    document.title = `${doc.title} — SOW`;
    return () => {
      document.title = previous;
    };
  }, [doc?.title]);

  // Canonicalize the URL to the document's current slug. Fires whenever the
  // URL segment isn't that slug -- a stale slug from before a rename (the
  // backend still resolves it via slug history, but the address bar should
  // catch up), or a raw id link (e.g. an old bookmark, or an audit-log
  // entry). replace(), not push(): this is correcting the current entry,
  // not creating a new page in history the back button would stop on.
  useEffect(() => {
    if (!doc?.slug || doc.slug === id) return;
    router.replace(`/sow/${doc.slug}`);
  }, [doc?.slug, id, router]);

  // The app-wide overlay carries the loading state; nothing renders behind it.
  usePageLoading(docLoading);
  if (docLoading) return null;
  if (docError || !doc) {
    return (
      <AppShell noPadding>
        <PageContainer>
          <p style={{ fontSize: 13, color: "#DC2626" }}>
            {docError?.message || "Document not found."}
          </p>
        </PageContainer>
      </AppShell>
    );
  }

  const sourceList = sources || [];
  const ledgerList = ledgerPage?.facts || [];
  // How many facts EXIST for the current filter, from the server, vs how many
  // this page carries. These differ only when a document exceeds the page
  // limit; when they do, the UI says so rather than quietly showing fewer.
  const ledgerTotal = ledgerPage?.total ?? ledgerList.length;
  const ledgerTruncated = ledgerTotal > ledgerList.length;
  // A partially extracted source still produced facts, so it can still be
  // generated from — the user is told it was partial, and blocking Generate
  // on it would strand them with a ledger they cannot use.
  const hasReadySource = sourceList.some(
    (s) => s.status === "done" || s.status === "done_with_errors"
  );
  // Sections a newly attached source affects, computed by the backend after
  // extraction. Advisory: it pre-ticks the Rewrite dialog, nothing more.
  const pendingSectionKeys = doc?.pending_section_keys || [];
  // Generate is offered before the first version, and afterwards only once
  // new source material has arrived — regenerating an unchanged document
  // redrafts every section from the same ledger and discards hand edits.
  const canGenerate = doc?.can_generate !== false;
  // An imported SOW that nothing new has been added to. Its version was built
  // verbatim from the uploaded file, so there is nothing to "generate" — the
  // document already exists and the useful next step is extracting skills
  // from it. Derived from data the page already has rather than a new field:
  // a version exists, and no source has landed since it was built.
  const hasCurrentVersion = !!doc.current_version_id;
  const isImportedBaseline = hasCurrentVersion && !canGenerate;
  const selectedVersion = versionList.find((v) => v.id === selectedVersionId) || null;
  // The PATCH endpoint always edits document.current_version_id regardless
  // of which version_id is in the URL -- editing while viewing an older
  // version would silently edit the CURRENT version's same-keyed section
  // instead of the one on screen. Gate the Edit button on this so that
  // trap can't happen; historical versions stay read-only.
  const isViewingCurrentVersion = !!doc.current_version_id && selectedVersionId === doc.current_version_id;
  const currentVersionHasHumanEdits =
    isViewingCurrentVersion && (versionDetail?.sections || []).some((s) => s.edited_by_human);

  return (
    <AppShell noPadding>
      <PageContainer>
        <BackButton
          href="/sow"
          label="Back to SOW"
          className="mb-3"
        />

        <div style={{ marginBottom: 24 }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, color: "#111827" }}>
            {doc.title}
          </h1>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
            Status: <span style={{ fontWeight: 600 }}>{doc.status}</span> — attach meeting
            notes, a recording, and design references below, then inspect what the AI
            extracted in the requirements ledger.
          </p>
        </div>

        {canWrite && (
          <AttachSourcesFolder
            attachedCount={sourceList.length}
            transcriptText={transcriptText}
            setTranscriptText={setTranscriptText}
            transcriptError={transcriptError}
            transcriptUploadMutation={transcriptUploadMutation}
            recordingLabel={recordingLabel}
            setRecordingLabel={setRecordingLabel}
            recordingError={recordingError}
            recordingUploadMutation={recordingUploadMutation}
            designLabel={designLabel}
            setDesignLabel={setDesignLabel}
            designError={designError}
            designUploadMutation={designUploadMutation}
            existingSowError={existingSowError}
            existingSowUploadMutation={existingSowUploadMutation}
          />
        )}

        <Section title="Attached sources" description={sourcesLoading ? "Loading…" : null}>
          {sourceList.length === 0 && !sourcesLoading && (
            <p style={{ fontSize: 13, color: "#6B7280" }}>
              No sources attached yet. Add a transcript, recording, or design reference above.
            </p>
          )}
          {sourceList.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #E5E7EB" }}>
                  {["File", "Type", "Status", "Facts", ""].map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: "left",
                        padding: "8px 12px",
                        fontSize: 11,
                        fontWeight: 600,
                        color: "#6B7280",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sourceList.map((s) => (
                  <tr key={s.id} style={{ borderBottom: "1px solid #F3F4F6" }}>
                    <td style={{ padding: "8px 12px", fontSize: 13, color: "#111827" }}>
                      {s.file_name || "—"}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "#6B7280" }}>
                      {(s.artifact_type || "").replace(/_/g, " ")}
                    </td>
                    <td style={{ padding: "8px 12px" }}>
                      <SourceProgressCell source={s} />
                      {s.status === "error" && s.error_message && (
                        <div style={{ fontSize: 11, color: "#DC2626", marginTop: 4, maxWidth: 260 }}>
                          {s.error_message}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 13, color: "#374151" }}>
                      {s.ledger_fact_count ?? "—"}
                    </td>
                    <td style={{ padding: "8px 12px", textAlign: "right" }}>
                      {canWrite && (
                        // A one-segment option group: the row has a single
                        // action, so it renders as a rounded square at the
                        // shared 72x28 instead of the bigger standalone
                        // circular slot. Matches Reports and Vibe Testing →
                        // Results. See `.btn-option-group` in global.css.
                        <div className="btn-option-group">
                          <DeleteIconButton
                            onClick={() => deleteSourceMutation.mutate(s.id)}
                            disabled={deleteSourceMutation.isPending}
                            label="Remove"
                            aria-label="Remove attached source"
                          />
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <CollapsibleSection
          title="Requirements ledger (raw)"
          description="A searchable index of every requirement and UI control found across all sources — it does not replace your document. It exists so you can check nothing was missed, and it is what lets a later transcript or design update only the sections it actually affects."
          badge={ledgerLoading ? null : `${ledgerTotal} fact${ledgerTotal === 1 ? "" : "s"}`}
          open={ledgerOpen}
          onToggle={() => setLedgerOpen((v) => !v)}
        >
          <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
            {[["", "All"], ...Object.entries(FACT_TYPE_LABELS)].map(([val, label]) => (
              <button
                key={val}
                onClick={() => setFactFilter(val)}
                className="chip-toggle"
                aria-pressed={factFilter === val}
                style={{
                  padding: "5px 11px",
                  fontSize: 12,
                  fontWeight: factFilter === val ? 600 : 400,
                  border: "1px solid #E5E7EB",
                  borderRadius: 999,
                  background: factFilter === val ? "#111827" : undefined,
                  color: factFilter === val ? "#fff" : "#6B7280",
                  cursor: "pointer",
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Only ever shown when the server really did return fewer rows than
              exist. Silence here is a positive assertion that the table below
              is the complete ledger. */}
          {ledgerTruncated && (
            <p
              style={{
                fontSize: 12,
                color: "#B45309",
                background: "#FEF3C7",
                border: "1px solid #FCD34D",
                borderRadius: 6,
                padding: "8px 12px",
                marginBottom: 12,
              }}
            >
              Showing the first {ledgerList.length} of {ledgerTotal} facts. All{" "}
              {ledgerTotal} are stored and every one of them is used for
              generation and rewrites — only this preview table is capped.
            </p>
          )}

          {ledgerLoading && <p style={{ fontSize: 13, color: "#6B7280" }}>Loading…</p>}
          {!ledgerLoading && ledgerList.length === 0 && (
            <p style={{ fontSize: 13, color: "#6B7280" }}>
              No ledger facts yet — attach a source above and wait for extraction to finish.
            </p>
          )}
          {!ledgerLoading && ledgerList.length > 0 && (
            // Scroll container, not page growth: a 360-fact ledger otherwise
            // pushes Generate SOW and every version/section panel below it
            // several screens down. maxHeight is capped in px rather than vh
            // so the box is a predictable size regardless of viewport.
            // The header row is sticky inside it so column meaning survives
            // scrolling.
            <div
              style={{
                maxHeight: 420,
                overflowY: "auto",
                border: "1px solid #F3F4F6",
                borderRadius: 8,
              }}
            >
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #E5E7EB" }}>
                  {["Type", "Element", "Label", "Location", "Notes"].map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: "left",
                        padding: "8px 12px",
                        fontSize: 11,
                        fontWeight: 600,
                        color: "#6B7280",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        position: "sticky",
                        top: 0,
                        background: "#fff",
                        zIndex: 1,
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ledgerList.map((f) => (
                  <tr key={f.id} style={{ borderBottom: "1px solid #F3F4F6" }}>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "#6B7280" }}>
                      {FACT_TYPE_LABELS[f.fact_type] || f.fact_type}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "#6B7280" }}>
                      {f.element_type ? f.element_type.replace(/_/g, " ") : "—"}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 13, color: "#111827", fontWeight: 500 }}>
                      {f.label}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "#6B7280" }}>
                      {f.location || "—"}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "#374151", maxWidth: 320 }}>
                      {f.behavior_notes || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </CollapsibleSection>

        {isImportedBaseline && (
          <CollapsibleSection
            title="Imported SOW"
            description="The original imported document, preserved verbatim. This is the source used for Skills/TDD extraction; it has not been regenerated or rewritten."
            badge={versionDetail ? `${versionDetail.sections.length} section${versionDetail.sections.length === 1 ? "" : "s"}` : null}
            open={importedSowOpen}
            onToggle={() => setImportedSowOpen((open) => !open)}
          >
            {versionDetailLoading && (
              <p style={{ fontSize: 13, color: "#6B7280", margin: 0 }}>Loading imported SOW…</p>
            )}
            {!versionDetailLoading && !versionDetail && (
              <ErrorAlert
                title="Couldn’t show the imported SOW"
                message="The imported document is still available, but its display version could not be loaded. Try again in a moment."
                action={{ label: "Reload", onClick: () => qc.invalidateQueries({ queryKey: ["sow-version-detail", id, selectedVersionId] }) }}
              />
            )}
            {!versionDetailLoading && versionDetail && (
              <pre
                style={{
                  maxHeight: 620,
                  overflow: "auto",
                  margin: 0,
                  padding: "14px 16px",
                  background: "#F9FAFB",
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  fontFamily: "inherit",
                  fontSize: 13,
                  lineHeight: 1.65,
                  color: "#1F2937",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {versionDetail.sections.map((section) => section.rendered_markdown).join("\n\n")}
              </pre>
            )}
          </CollapsibleSection>
        )}

        <CollapsibleSection
          title={isImportedBaseline ? "Skills & TDDs" : "Generate SOW"}
          description={
            isImportedBaseline
              ? "This SOW was imported and is shown below exactly as you wrote it — no AI rewrote it. Extract Skills/TDDs turns it into runnable Vibe Testing skills. Attach a meeting transcript, recording or design reference to unlock Generate SOW, which rewrites only the sections that new material affects."
              : "Groups the ledger into sections and drafts the full document. A partial failure (some sections done, some errored) still produces a usable version — errored sections are flagged individually below, never silently dropped."
          }
          badge={versionList.length > 0 ? `${versionList.length} version${versionList.length === 1 ? "" : "s"}` : null}
          open={versionsOpen}
          onToggle={() => setVersionsOpen((v) => !v)}
        >
          {/* The primary action for an imported SOW is extracting skills, not
              generating a document that already exists. Generate only earns
              its place once new source material arrives to fold in. */}
          {canWrite && isImportedBaseline && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
              <Button
                variant="invert"
                size="lg"
                onClick={() => sendToCheckpointsMutation.mutate()}
                disabled={sendToCheckpointsMutation.isPending}
              >
                {sendToCheckpointsMutation.isPending
                  ? "Extracting…"
                  : "Extract Skills / TDDs"}
              </Button>
              <span style={{ fontSize: 12, color: "#6B7280" }}>
                Sends this document to Vibe Testing and extracts a runnable skill per
                feature. Ambiguous, incomplete, or conflicting requirements stay in
                review and are never saved as runnable Skills/TDDs.
              </span>
              {sendToCheckpointsMutation.isSuccess && (
                <span style={{ fontSize: 12, color: "#166534" }}>
                  {sendToCheckpointsMutation.data?.message}
                </span>
              )}
              {sendToCheckpointsMutation.isError && (
                <ErrorAlert
                  title="Skills/TDD extraction could not start"
                  message={sendToCheckpointsMutation.error?.message || "Try again after checking the document."}
                  action={{ label: "Try again", onClick: () => sendToCheckpointsMutation.mutate() }}
                />
              )}
              {/* Live step-by-step progress for the artifact this document was
                  just sent as. Renders whatever the pipeline actually did —
                  see SowExtractionProgress. Full width beneath the button row
                  rather than inline: the timeline grows to dozens of rows on a
                  multi-part document. */}
              <div style={{ flexBasis: "100%" }}>
                <SowExtractionProgress
                  artifactId={sendToCheckpointsMutation.data?.artifact_id || null}
                />
              </div>
            </div>
          )}

          {canWrite && !isImportedBaseline && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
              <Button
                variant="invert"
                size="lg"
                onClick={async () => {
                  // A full generation always creates a brand-new version
                  // from scratch (it does not patch the current one), so
                  // any hand-edits made in the Phase 5 editor won't carry
                  // forward -- warn before proceeding rather than let that
                  // be a silent surprise. This will stop being necessary
                  // once Phase 7's rewrite/patch flow can respect
                  // edited_by_human sections.
                  if (currentVersionHasHumanEdits) {
                    const ok = await confirmDialog({
                      title: "Regenerate this SOW from scratch?",
                      body: "This version has hand-edited sections. A new version starts fully fresh and will NOT carry those edits forward.",
                      tone: "neutral",
                      confirmLabel: "Continue",
                    });
                    if (!ok) return;
                  }
                  generateMutation.mutate();
                }}
                disabled={
                  !hasReadySource ||
                  !canGenerate ||
                  generationActive ||
                  generateMutation.isPending
                }
              >
                {generationActive || generateMutation.isPending ? "Generating…" : "Generate SOW"}
              </Button>
              {!hasReadySource && (
                <span style={{ fontSize: 12, color: "#6B7280" }}>
                  Attach at least one source and wait for extraction to finish first.
                </span>
              )}
              {hasReadySource && !canGenerate && (
                <span style={{ fontSize: 12, color: "#6B7280" }}>
                  Already generated. Attach a new source to regenerate, or use Rewrite
                  to redo individual sections.
                </span>
              )}
              {generateError && (
                <span style={{ fontSize: 12, color: "#DC2626" }}>{generateError}</span>
              )}
            </div>
          )}

          {hasCurrentVersion && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
                marginBottom: 18,
                padding: "10px 14px",
                background: "#F9FAFB",
                border: "1px solid #E5E7EB",
                borderRadius: 8,
              }}
            >
              <span style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginRight: 4 }}>
                Export current version:
              </span>
              {["md", "docx", "pdf"].map((fmt) => (
                <Button
                  key={fmt}
                  variant="outline"
                  size="xs"
                  onClick={() => downloadExport(fmt)}
                  disabled={exportingFormat !== null}
                >
                  {exportingFormat === fmt ? "Exporting…" : `.${fmt}`}
                </Button>
              ))}
              {exportError && (
                <span style={{ fontSize: 12, color: "#DC2626" }}>{exportError}</span>
              )}

              {canWrite && (
                <>
                  <span style={{ width: 1, height: 18, background: "#E5E7EB", margin: "0 4px" }} />
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => sendToCheckpointsMutation.mutate()}
                    disabled={sendToCheckpointsMutation.isPending}
                  >
                    {sendToCheckpointsMutation.isPending
                      ? "Extracting…"
                      : "Extract Skills / TDDs"}
                  </Button>
                  {sendToCheckpointsMutation.isSuccess && (
                    <span style={{ fontSize: 12, color: "#166534" }}>
                      {sendToCheckpointsMutation.data?.message}
                    </span>
                  )}
                  {sendToCheckpointsMutation.isError && (
                    <ErrorAlert
                      title="Skills/TDD extraction could not start"
                      message={sendToCheckpointsMutation.error?.message || "Try again after checking the document."}
                      action={{ label: "Try again", onClick: () => sendToCheckpointsMutation.mutate() }}
                    />
                  )}
                </>
              )}
            </div>
          )}

          {/* A newly attached source lands facts in the ledger, but nothing
              connects them to the sections that already exist. The backend
              works that out after extraction and reports the affected keys
              here. Pressing the button only PRE-TICKS them below — no tokens
              are spent until the user runs the rewrite themselves. */}
          {canWrite && isViewingCurrentVersion && pendingSectionKeys.length > 0 && (
            <div
              style={{
                marginBottom: 18,
                padding: "12px 14px",
                background: "#FEF3C7",
                border: "1px solid #FCD34D",
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontSize: 13, color: "#78350F" }}>
                New source material affects{" "}
                <strong>
                  {pendingSectionKeys.length} section
                  {pendingSectionKeys.length === 1 ? "" : "s"}
                </strong>
                {doc?.pending_new_fact_count
                  ? ` (${doc.pending_new_fact_count} new requirement${
                      doc.pending_new_fact_count === 1 ? "" : "s"
                    })`
                  : ""}
                . Everything else stays exactly as it is.
              </span>
              <Button
                variant="outline"
                size="xs"
                onClick={() => {
                  setRewriteTargets(new Set(pendingSectionKeys));
                  setRewriteOpen(true); // the panel is collapsed by default
                  document
                    .getElementById("sow-rewrite-panel")
                    ?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              >
                Review affected sections
              </Button>
            </div>
          )}

          {canWrite && isViewingCurrentVersion && versionDetail && (
            <div
              id="sow-rewrite-panel"
              style={{
                marginBottom: 18,
                padding: "12px 14px",
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 8,
              }}
            >
              {/* Header is always rendered: it carries the panel's id anchor
                  and states what the tool does without implying the sections
                  below are outstanding work. */}
              <button
                type="button"
                onClick={() => setRewriteOpen((open) => !open)}
                aria-expanded={rewriteOpen}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  width: "100%",
                  padding: 0,
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span style={{ fontSize: 12, color: "#6B7280", width: 10 }}>
                  {rewriteOpen ? "▾" : "▸"}
                </span>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>
                  Rewrite sections (optional)
                </span>
                {pendingSectionKeys.length > 0 && (
                  <span style={{ fontSize: 11, color: "#B45309" }}>
                    {pendingSectionKeys.length} affected by new source
                  </span>
                )}
                {rewriteTargets.size > 0 && (
                  <span style={{ fontSize: 11, color: "#374151" }}>
                    {rewriteTargets.size} selected
                  </span>
                )}
              </button>

              {rewriteOpen && (
                <>
              <p style={{ fontSize: 12, color: "#6B7280", margin: "8px 0 8px 18px" }}>
                Pick the sections to regenerate — everything else in this version stays
                exactly as it is. Nothing is rewritten until you press the button.
              </p>
              {versionDetail.sections
                .filter((s) => !NON_PATCHABLE_SECTION_KEYS.has(s.section_key))
                .map((s) => {
                  const isTarget = rewriteTargets.has(s.section_key);
                  const isOverridden = rewriteOverrides.has(s.section_key);
                  return (
                    <div
                      key={s.section_key}
                      style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}
                    >
                      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#111827" }}>
                        <Checkbox
                          checked={isTarget}
                          onCheckedChange={() => toggleRewriteTarget(s.section_key)}
                        />
                        {s.heading}
                      </label>
                      {pendingSectionKeys.includes(s.section_key) && (
                        <span style={{ fontSize: 11, color: "#B45309" }}>
                          affected by new source
                        </span>
                      )}
                      {s.edited_by_human && (
                        <span style={{ fontSize: 11, color: "#6B21A8" }}>hand-edited</span>
                      )}
                      {s.edited_by_human && isTarget && (
                        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#B45309" }}>
                          <Checkbox
                            checked={isOverridden}
                            onCheckedChange={() => toggleRewriteOverride(s.section_key)}
                          />
                          force-regenerate anyway
                        </label>
                      )}
                    </div>
                  );
                })}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
                <Button
                  variant="invert"
                  size="lg"
                  onClick={() => {
                    setRewriteError("");
                    rewriteMutation.mutate();
                  }}
                  disabled={rewriteTargets.size === 0 || generationActive || rewriteMutation.isPending}
                >
                  {rewriteMutation.isPending ? "Rewriting…" : `Rewrite ${rewriteTargets.size || ""} section${rewriteTargets.size === 1 ? "" : "s"}`}
                </Button>
                {rewriteError && <span style={{ fontSize: 12, color: "#DC2626" }}>{rewriteError}</span>}
              </div>
                </>
              )}
            </div>
          )}

          {job && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 18,
                padding: "10px 14px",
                background: "#F9FAFB",
                border: "1px solid #E5E7EB",
                borderRadius: 8,
              }}
            >
              <Badge status={job.status} colors={JOB_STATUS_COLORS} bg={JOB_STATUS_BG} />
              <span style={{ fontSize: 12, color: "#374151" }}>
                {job.stage_progress || job.stage || "Working…"}
              </span>
              {job.error_message && (
                <span style={{ fontSize: 12, color: "#DC2626" }}>{job.error_message}</span>
              )}
            </div>
          )}

          {versionList.length === 0 && (
            <p style={{ fontSize: 13, color: "#6B7280" }}>
              No versions yet — generate the document to produce the first one.
            </p>
          )}

          {versionList.length > 0 && (
            <div>
              {/* Same shape as the Rewrite panel above: a bordered band whose
                  header is the toggle.

                  It wraps the version PICKER AND THE DOCUMENT ITSELF, not just
                  the picker. Collapsing only the picker would have split one
                  thing across two boxes — a closed "Versions" band with the
                  version's own contents still spilling out underneath it,
                  belonging to nothing on screen. "Versions" here means the
                  chosen version and what is in it. */}
              <div
                style={{
                  padding: "12px 14px",
                  background: "#fff",
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                }}
              >
                <button
                  type="button"
                  onClick={() => setVersionPickerOpen((open) => !open)}
                  aria-expanded={versionPickerOpen}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    width: "100%",
                    padding: 0,
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <span style={{ fontSize: 12, color: "#6B7280", width: 10 }}>
                    {versionPickerOpen ? "▾" : "▸"}
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>
                    Versions
                  </span>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: "#374151",
                      background: "#F3F4F6",
                      borderRadius: 999,
                      padding: "2px 9px",
                    }}
                  >
                    {versionList.length} version{versionList.length === 1 ? "" : "s"}
                  </span>
                  {/* Collapsed, this line is the only thing telling you which
                      version the document below belongs to — without it the
                      closed state would be ambiguous on a multi-version doc. */}
                  {selectedVersion && (
                    <span style={{ fontSize: 11, color: "#6B7280" }}>
                      v{selectedVersion.version_number} selected
                    </span>
                  )}
                  {diffMode && (
                    <span style={{ fontSize: 11, color: "#B45309" }}>comparing</span>
                  )}
                </button>

                {versionPickerOpen && (
                  // The original two-column layout, unchanged, now living
                  // inside the collapsible body: picker rail on the left, the
                  // version's own content on the right.
                  <div style={{ display: "flex", gap: 24, marginTop: 14 }}>
                    <div style={{ width: 200, flexShrink: 0 }}>
                      {/* On/off carried by the two designs themselves — ink when
                          engaged, white when not — rather than by the blue fill this
                          used to swap in. */}
                      <Button
                        variant={diffMode ? "invert" : "outline"}
                        size="xs"
                        onClick={() => setDiffMode((v) => !v)}
                        aria-pressed={diffMode}
                        disabled={versionList.length < 2}
                        className="mb-2.5 w-full"
                      >
                        {diffMode ? "✓ Comparing versions" : "Compare with previous"}
                      </Button>
                      {versionList.map((v) => (
                        <button
                          key={v.id}
                          onClick={() => setSelectedVersionId(v.id)}
                          className="chip-toggle"
                          aria-pressed={v.id === selectedVersionId}
                          style={{
                            display: "block",
                            width: "100%",
                            textAlign: "left",
                            padding: "8px 10px",
                            marginBottom: 6,
                            fontSize: 12,
                            border:
                              "1px solid " + (v.id === selectedVersionId ? "#2563EB" : "#E5E7EB"),
                            borderRadius: 8,
                            background: v.id === selectedVersionId ? "#EFF6FF" : undefined,
                            cursor: "pointer",
                          }}
                        >
                          <div style={{ fontWeight: 600, color: "#111827", marginBottom: 4 }}>
                            v{v.version_number} — {v.kind === "full_generation" ? "Full" : "Patch"}
                          </div>
                          <Badge
                            status={v.status}
                            colors={VERSION_STATUS_COLORS}
                            bg={VERSION_STATUS_BG}
                          />
                        </button>
                      ))}
                    </div>

                    <div style={{ flex: 1, minWidth: 0 }}>
              {diffMode ? (
                <>
                  {!previousVersion && (
                    <p style={{ fontSize: 13, color: "#6B7280" }}>
                      This is the earliest version — nothing to compare against.
                    </p>
                  )}
                  {previousVersion && (previousVersionDetailLoading || versionDetailLoading) && (
                    <p style={{ fontSize: 13, color: "#6B7280" }}>Loading comparison…</p>
                  )}
                  {previousVersion &&
                    !previousVersionDetailLoading &&
                    !versionDetailLoading &&
                    versionDetail &&
                    previousVersionDetail && (
                      <>
                        <p style={{ fontSize: 12, color: "#6B7280", margin: "0 0 14px" }}>
                          Comparing v{selectedVersion?.version_number} against v
                          {previousVersion.version_number}
                        </p>
                        {(() => {
                          const oldByKey = Object.fromEntries(
                            previousVersionDetail.sections.map((s) => [s.section_key, s])
                          );
                          const newByKey = Object.fromEntries(
                            versionDetail.sections.map((s) => [s.section_key, s])
                          );
                          const orderedKeys = [
                            ...versionDetail.sections.map((s) => s.section_key),
                            ...previousVersionDetail.sections
                              .map((s) => s.section_key)
                              .filter((k) => !newByKey[k]),
                          ];
                          return orderedKeys.map((key) => (
                            <SectionDiffCard
                              key={key}
                              sectionKey={key}
                              oldSection={oldByKey[key] || null}
                              newSection={newByKey[key] || null}
                            />
                          ));
                        })()}
                      </>
                    )}
                </>
              ) : (
                <>
                {versionDetailLoading && (
                  <p style={{ fontSize: 13, color: "#6B7280" }}>Loading version…</p>
                )}
                {!versionDetailLoading && selectedVersion?.error_message && (
                  <p style={{ fontSize: 12, color: "#DC2626", marginTop: 0 }}>
                    {selectedVersion.error_message}
                  </p>
                )}
                {!versionDetailLoading && versionDetail && (
                  <>
                    {versionDetail.generated_by_model && (
                      <p style={{ fontSize: 11, color: "#9CA3AF", margin: "0 0 14px" }}>
                        Generated by {versionDetail.generated_by_model}
                      </p>
                    )}
                    {versionDetail.sections.map((s) => (
                      <div
                        key={s.id}
                        style={{
                          border: "1px solid #E5E7EB",
                          borderRadius: 8,
                          padding: 16,
                          marginBottom: 14,
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 10,
                            marginBottom: s.status === "error" ? 6 : 10,
                            flexWrap: "wrap",
                          }}
                        >
                          <span style={{ fontSize: 14, fontWeight: 600, color: "#111827" }}>
                            {s.heading}
                          </span>
                          <Badge status={s.status} colors={SECTION_STATUS_COLORS} bg={SECTION_STATUS_BG} />
                          <CoverageBadge score={s.coverage_score} />
                          {s.edited_by_human && (
                            <span style={{ fontSize: 11, fontWeight: 600, color: "#6B21A8" }}>
                              ✎ hand-edited
                            </span>
                          )}
                          {canWrite && isViewingCurrentVersion && editingSectionKey !== s.section_key && (
                            <Button
                              variant="outline"
                              size="xs"
                              onClick={() => {
                                setEditSaveError("");
                                setEditingSectionKey(s.section_key);
                              }}
                              className="ml-auto"
                            >
                              Edit
                            </Button>
                          )}
                        </div>
                        {s.status === "error" && s.error_message && (
                          <p style={{ fontSize: 12, color: "#DC2626", margin: "0 0 10px" }}>
                            {s.error_message}
                          </p>
                        )}

                        {editingSectionKey === s.section_key ? (
                          <SectionEditor
                            blocks={s.content_blocks}
                            saving={patchSectionMutation.isPending}
                            error={editSaveError}
                            onCancel={() => {
                              setEditingSectionKey(null);
                              setEditSaveError("");
                            }}
                            onSave={(blocks) =>
                              patchSectionMutation.mutate({ sectionKey: s.section_key, blocks })
                            }
                          />
                        ) : (
                          <>
                            <pre
                              style={{
                                margin: 0,
                                fontFamily: "inherit",
                                fontSize: 13,
                                lineHeight: 1.6,
                                color: "#374151",
                                whiteSpace: "pre-wrap",
                                wordBreak: "break-word",
                              }}
                            >
                              {s.rendered_markdown}
                            </pre>
                            {Array.isArray(s.coverage_gaps) && s.coverage_gaps.length > 0 && (
                              <div
                                style={{
                                  marginTop: 12,
                                  padding: "10px 12px",
                                  background: "#FEF3C7",
                                  border: "1px solid #FDE68A",
                                  borderRadius: 8,
                                }}
                              >
                                <p
                                  style={{
                                    margin: "0 0 6px",
                                    fontSize: 11,
                                    fontWeight: 700,
                                    color: "#92400E",
                                    textTransform: "uppercase",
                                    letterSpacing: "0.05em",
                                  }}
                                >
                                  Audit found {s.coverage_gaps.length} gap
                                  {s.coverage_gaps.length === 1 ? "" : "s"} — review before trusting
                                  this section for vibe testing
                                </p>
                                <ul style={{ margin: 0, paddingLeft: 18 }}>
                                  {s.coverage_gaps.map((g, i) => (
                                    <li key={i} style={{ fontSize: 12, color: "#78350F", marginBottom: 4 }}>
                                      <strong>{g.label}</strong>
                                      {g.element_type ? ` (${g.element_type.replace(/_/g, " ")})` : ""}
                                      {g.reason ? ` — ${g.reason}` : ""}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    ))}
                  </>
                )}
                </>
              )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </CollapsibleSection>
      </PageContainer>
    </AppShell>
  );
}
