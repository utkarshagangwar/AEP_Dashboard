"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import PageContainer from "../../../components/PageContainer";
import { apiGet, apiFetch, apiPost, apiDelete } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";

// Phase 1 of SOW_FEATURE_PLAN.md: attach source material (meeting
// transcript, meeting recording, design reference) to a document and
// inspect the raw requirements ledger each source's extraction produces.
// Phase 3: trigger generation from that ledger and view the resulting
// versions/sections. Phase 4: coverage badges + gap panels per section.
// Phase 5: hand-edit a section's structured content in-place, and a
// client-side diff between the two most recent versions. Phase 6: export
// to md/docx/pdf and send-to-Vibe-Testing. Phase 7 (this addition):
// rewrite/patch -- regenerate only selected sections instead of
// everything. Editing, rewrite, export, and send-to-checkpoints all
// always target the CURRENT version (matches each endpoint's own scope)
// -- historical versions stay read-only.

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

const SOURCE_STATUS_COLORS = {
  pending: "#6B7280",
  processing: "#2563EB",
  done: "#16A34A",
  error: "#DC2626",
};
const SOURCE_STATUS_BG = {
  pending: "#F3F4F6",
  processing: "#DBEAFE",
  done: "#DCFCE7",
  error: "#FEE2E2",
};
const ACTIVE_SOURCE_STATUSES = new Set(["pending", "processing"]);

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

function Badge({ status, colors, bg }) {
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
        textTransform: "capitalize",
      }}
    >
      {status}
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
const btnStyle = {
  padding: "8px 14px",
  fontSize: 13,
  fontWeight: 600,
  color: "#fff",
  background: "#2563EB",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
};
const btnDisabledStyle = { ...btnStyle, background: "#93C5FD", cursor: "default" };

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
const smallBtnStyle = {
  padding: "3px 8px",
  fontSize: 11,
  fontWeight: 600,
  color: "#374151",
  background: "#F3F4F6",
  border: "1px solid #E5E7EB",
  borderRadius: 6,
  cursor: "pointer",
};
const smallDangerBtnStyle = { ...smallBtnStyle, color: "#DC2626" };

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
                  <button
                    onClick={() => removeColumn(i)}
                    style={{ ...smallDangerBtnStyle, marginTop: 4, width: "100%" }}
                  >
                    Remove column
                  </button>
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
                  <button onClick={() => removeRow(r)} style={smallDangerBtnStyle}>
                    Remove row
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={addRow} style={smallBtnStyle}>+ Row</button>
        <button onClick={addColumn} style={smallBtnStyle}>+ Column</button>
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
          <button onClick={() => onMove(index, -1)} disabled={index === 0} style={smallBtnStyle}>
            ↑
          </button>
          <button onClick={() => onMove(index, 1)} disabled={index === total - 1} style={smallBtnStyle}>
            ↓
          </button>
          <button onClick={() => onRemove(index)} style={smallDangerBtnStyle}>
            Remove
          </button>
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
        <button onClick={addBlock} style={smallBtnStyle}>+ Add block</button>
      </div>

      {error && <p style={{ fontSize: 12, color: "#DC2626", marginTop: 0 }}>{error}</p>}

      <div style={{ display: "flex", gap: 10 }}>
        <button
          onClick={() => onSave(localBlocks)}
          disabled={saving || localBlocks.length === 0}
          style={saving || localBlocks.length === 0 ? btnDisabledStyle : btnStyle}
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          onClick={onCancel}
          disabled={saving}
          style={{ ...smallBtnStyle, padding: "8px 14px", fontSize: 13 }}
        >
          Cancel
        </button>
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
  const { id } = useParams();
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
  const [factFilter, setFactFilter] = useState("");
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
  const { data: ledger, isLoading: ledgerLoading } = useQuery({
    queryKey: ledgerQueryKey,
    queryFn: () =>
      apiGet(
        `/api/v1/sow/documents/${id}/ledger${factFilter ? `?fact_type=${factFilter}` : ""}`
      ),
    refetchInterval: (query) => {
      const sourceList = sources || [];
      return sourceList.some((s) => ACTIVE_SOURCE_STATUSES.has(s.status)) ? 3000 : false;
    },
  });

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

  const deleteSourceMutation = useMutation({
    mutationFn: (sourceId) => apiDelete(`/api/v1/sow/documents/${id}/sources/${sourceId}`),
    onSuccess: () => invalidateAll(),
  });

  if (docLoading) {
    return (
      <AppShell noPadding>
        <PageContainer>
          <p style={{ fontSize: 13, color: "#6B7280" }}>Loading…</p>
        </PageContainer>
      </AppShell>
    );
  }
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
  const ledgerList = ledger || [];
  const hasReadySource = sourceList.some((s) => s.status === "done");
  const hasCurrentVersion = !!doc.current_version_id;
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
        <a
          href="/sow"
          style={{
            display: "inline-block",
            color: "#2563EB",
            fontSize: 12,
            fontWeight: 500,
            textDecoration: "none",
            marginBottom: 12,
          }}
        >
          ← Back to SOW
        </a>

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
          <Section
            title="Attach sources"
            description="Each source is extracted independently into the requirements ledger below. Attaching the same file again re-runs extraction (useful as a Retry after an error)."
          >
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
              {/* Transcript */}
              <div>
                <p style={{ fontSize: 13, fontWeight: 600, color: "#111827", margin: "0 0 8px" }}>
                  Meeting transcript
                </p>
                <label style={labelStyle}>Paste text</label>
                <textarea
                  value={transcriptText}
                  onChange={(e) => setTranscriptText(e.target.value)}
                  rows={4}
                  placeholder="Paste meeting notes/transcript…"
                  style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }}
                />
                <label style={labelStyle}>…or upload .txt / .md</label>
                <input
                  type="file"
                  accept=".txt,.md"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      transcriptUploadMutation.mutate({ file: f });
                      e.target.value = "";
                    }
                  }}
                  style={{ fontSize: 12, marginBottom: 10, display: "block" }}
                  disabled={transcriptUploadMutation.isPending}
                />
                <button
                  onClick={() =>
                    transcriptUploadMutation.mutate({ text: transcriptText })
                  }
                  disabled={!transcriptText.trim() || transcriptUploadMutation.isPending}
                  style={
                    !transcriptText.trim() || transcriptUploadMutation.isPending
                      ? btnDisabledStyle
                      : btnStyle
                  }
                >
                  {transcriptUploadMutation.isPending ? "Attaching…" : "Attach pasted text"}
                </button>
                {transcriptError && (
                  <p style={{ fontSize: 11, color: "#DC2626", margin: "6px 0 0" }}>
                    {transcriptError}
                  </p>
                )}
              </div>

              {/* Recording */}
              <div>
                <p style={{ fontSize: 13, fontWeight: 600, color: "#111827", margin: "0 0 8px" }}>
                  Meeting recording
                </p>
                <label style={labelStyle}>Context (optional)</label>
                <input
                  value={recordingLabel}
                  onChange={(e) => setRecordingLabel(e.target.value)}
                  placeholder="e.g. Sprint planning, July 18"
                  style={inputStyle}
                />
                <label style={labelStyle}>
                  Upload audio/video (.mp4 .webm .mov .mp3 .m4a .wav .ogg — up to 300MB / 60min
                  by default)
                </label>
                <input
                  type="file"
                  accept=".mp4,.webm,.mov,.mp3,.m4a,.wav,.ogg"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      recordingUploadMutation.mutate({ file: f, label: recordingLabel.trim() });
                      e.target.value = "";
                    }
                  }}
                  style={{ fontSize: 12, marginBottom: 10, display: "block" }}
                  disabled={recordingUploadMutation.isPending}
                />
                {recordingUploadMutation.isPending && (
                  <p style={{ fontSize: 12, color: "#2563EB", margin: "0 0 6px" }}>
                    Uploading & digesting — this can take a few minutes for longer
                    recordings…
                  </p>
                )}
                {recordingError && (
                  <p style={{ fontSize: 11, color: "#DC2626", margin: "6px 0 0" }}>
                    {recordingError}
                  </p>
                )}
              </div>

              {/* Design reference */}
              <div>
                <p style={{ fontSize: 13, fontWeight: 600, color: "#111827", margin: "0 0 8px" }}>
                  Design reference
                </p>
                <label style={labelStyle}>Page/screen label (optional)</label>
                <input
                  value={designLabel}
                  onChange={(e) => setDesignLabel(e.target.value)}
                  placeholder="e.g. Checkout screen"
                  style={inputStyle}
                />
                <label style={labelStyle}>Upload a PNG mockup/screenshot</label>
                <input
                  type="file"
                  accept=".png"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      designUploadMutation.mutate({ file: f, label: designLabel.trim() });
                      e.target.value = "";
                    }
                  }}
                  style={{ fontSize: 12, marginBottom: 10, display: "block" }}
                  disabled={designUploadMutation.isPending}
                />
                {designError && (
                  <p style={{ fontSize: 11, color: "#DC2626", margin: "6px 0 0" }}>
                    {designError}
                  </p>
                )}
              </div>
            </div>
          </Section>
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
                      <Badge status={s.status} colors={SOURCE_STATUS_COLORS} bg={SOURCE_STATUS_BG} />
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
                        <button
                          onClick={() => deleteSourceMutation.mutate(s.id)}
                          disabled={deleteSourceMutation.isPending}
                          style={{
                            fontSize: 12,
                            fontWeight: 500,
                            color: "#DC2626",
                            background: "transparent",
                            border: "none",
                            cursor: "pointer",
                          }}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section
          title="Requirements ledger (raw)"
          description="Every extracted fact across all sources — unstructured on purpose at this phase, so extraction quality can be checked directly before anything is drafted from it."
        >
          <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
            {[["", "All"], ...Object.entries(FACT_TYPE_LABELS)].map(([val, label]) => (
              <button
                key={val}
                onClick={() => setFactFilter(val)}
                style={{
                  padding: "5px 11px",
                  fontSize: 12,
                  fontWeight: factFilter === val ? 600 : 400,
                  border: "1px solid #E5E7EB",
                  borderRadius: 999,
                  background: factFilter === val ? "#111827" : "#fff",
                  color: factFilter === val ? "#fff" : "#6B7280",
                  cursor: "pointer",
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {ledgerLoading && <p style={{ fontSize: 13, color: "#6B7280" }}>Loading…</p>}
          {!ledgerLoading && ledgerList.length === 0 && (
            <p style={{ fontSize: 13, color: "#6B7280" }}>
              No ledger facts yet — attach a source above and wait for extraction to finish.
            </p>
          )}
          {!ledgerLoading && ledgerList.length > 0 && (
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
          )}
        </Section>

        <Section
          title="Generate SOW"
          description="Groups the ledger into sections and drafts the full document. A partial failure (some sections done, some errored) still produces a usable version — errored sections are flagged individually below, never silently dropped."
        >
          {canWrite && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
              <button
                onClick={() => {
                  // A full generation always creates a brand-new version
                  // from scratch (it does not patch the current one), so
                  // any hand-edits made in the Phase 5 editor won't carry
                  // forward -- warn before proceeding rather than let that
                  // be a silent surprise. This will stop being necessary
                  // once Phase 7's rewrite/patch flow can respect
                  // edited_by_human sections.
                  if (
                    currentVersionHasHumanEdits &&
                    !window.confirm(
                      "This version has hand-edited sections. Generating a new version starts " +
                        "fully fresh and will NOT carry those edits forward. Continue?"
                    )
                  ) {
                    return;
                  }
                  generateMutation.mutate();
                }}
                disabled={!hasReadySource || generationActive || generateMutation.isPending}
                style={
                  !hasReadySource || generationActive || generateMutation.isPending
                    ? btnDisabledStyle
                    : btnStyle
                }
              >
                {generationActive || generateMutation.isPending ? "Generating…" : "Generate SOW"}
              </button>
              {!hasReadySource && (
                <span style={{ fontSize: 12, color: "#6B7280" }}>
                  Attach at least one source and wait for extraction to finish first.
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
                <button
                  key={fmt}
                  onClick={() => downloadExport(fmt)}
                  disabled={exportingFormat !== null}
                  style={exportingFormat !== null ? { ...smallBtnStyle, opacity: 0.6 } : smallBtnStyle}
                >
                  {exportingFormat === fmt ? "Exporting…" : `.${fmt}`}
                </button>
              ))}
              {exportError && (
                <span style={{ fontSize: 12, color: "#DC2626" }}>{exportError}</span>
              )}

              {canWrite && (
                <>
                  <span style={{ width: 1, height: 18, background: "#E5E7EB", margin: "0 4px" }} />
                  <button
                    onClick={() => sendToCheckpointsMutation.mutate()}
                    disabled={sendToCheckpointsMutation.isPending}
                    style={
                      sendToCheckpointsMutation.isPending
                        ? { ...smallBtnStyle, opacity: 0.6 }
                        : smallBtnStyle
                    }
                  >
                    {sendToCheckpointsMutation.isPending ? "Sending…" : "Send to Vibe Testing"}
                  </button>
                  {sendToCheckpointsMutation.isSuccess && (
                    <span style={{ fontSize: 12, color: "#166534" }}>
                      {sendToCheckpointsMutation.data?.message}
                    </span>
                  )}
                  {sendToCheckpointsMutation.isError && (
                    <span style={{ fontSize: 12, color: "#DC2626" }}>
                      {sendToCheckpointsMutation.error?.message}
                    </span>
                  )}
                </>
              )}
            </div>
          )}

          {canWrite && isViewingCurrentVersion && versionDetail && (
            <div
              style={{
                marginBottom: 18,
                padding: "12px 14px",
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 8,
              }}
            >
              <p style={{ fontSize: 12, fontWeight: 600, color: "#374151", margin: "0 0 8px" }}>
                Rewrite (patch) — regenerate only selected sections; everything else in this
                version stays exactly as it is.
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
                        <input
                          type="checkbox"
                          checked={isTarget}
                          onChange={() => toggleRewriteTarget(s.section_key)}
                        />
                        {s.heading}
                      </label>
                      {s.edited_by_human && (
                        <span style={{ fontSize: 11, color: "#6B21A8" }}>hand-edited</span>
                      )}
                      {s.edited_by_human && isTarget && (
                        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#B45309" }}>
                          <input
                            type="checkbox"
                            checked={isOverridden}
                            onChange={() => toggleRewriteOverride(s.section_key)}
                          />
                          force-regenerate anyway
                        </label>
                      )}
                    </div>
                  );
                })}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
                <button
                  onClick={() => {
                    setRewriteError("");
                    rewriteMutation.mutate();
                  }}
                  disabled={rewriteTargets.size === 0 || generationActive || rewriteMutation.isPending}
                  style={
                    rewriteTargets.size === 0 || generationActive || rewriteMutation.isPending
                      ? btnDisabledStyle
                      : btnStyle
                  }
                >
                  {rewriteMutation.isPending ? "Rewriting…" : `Rewrite ${rewriteTargets.size || ""} section${rewriteTargets.size === 1 ? "" : "s"}`}
                </button>
                {rewriteError && <span style={{ fontSize: 12, color: "#DC2626" }}>{rewriteError}</span>}
              </div>
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
            <div style={{ display: "flex", gap: 24 }}>
              <div style={{ width: 200, flexShrink: 0 }}>
                <p
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "#6B7280",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    margin: "0 0 8px",
                  }}
                >
                  Versions
                </p>
                <button
                  onClick={() => setDiffMode((v) => !v)}
                  disabled={versionList.length < 2}
                  style={{
                    ...smallBtnStyle,
                    width: "100%",
                    marginBottom: 10,
                    ...(diffMode ? { background: "#2563EB", color: "#fff", borderColor: "#2563EB" } : {}),
                    ...(versionList.length < 2 ? { opacity: 0.5, cursor: "default" } : {}),
                  }}
                >
                  {diffMode ? "✓ Comparing versions" : "Compare with previous"}
                </button>
                {versionList.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setSelectedVersionId(v.id)}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      padding: "8px 10px",
                      marginBottom: 6,
                      fontSize: 12,
                      border: "1px solid " + (v.id === selectedVersionId ? "#2563EB" : "#E5E7EB"),
                      borderRadius: 8,
                      background: v.id === selectedVersionId ? "#EFF6FF" : "#fff",
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ fontWeight: 600, color: "#111827", marginBottom: 4 }}>
                      v{v.version_number} — {v.kind === "full_generation" ? "Full" : "Patch"}
                    </div>
                    <Badge status={v.status} colors={VERSION_STATUS_COLORS} bg={VERSION_STATUS_BG} />
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
                            <button
                              onClick={() => {
                                setEditSaveError("");
                                setEditingSectionKey(s.section_key);
                              }}
                              style={{ ...smallBtnStyle, marginLeft: "auto" }}
                            >
                              Edit
                            </button>
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
        </Section>
      </PageContainer>
    </AppShell>
  );
}
