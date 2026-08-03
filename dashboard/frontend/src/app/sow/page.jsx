"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import GlobalLoader from "../../components/GlobalLoader";
import PageContainer from "../../components/PageContainer";
import { toastSuccess } from "../../lib/toast";
import { Button } from "../../components/ui/button";
import { DeleteIconButton } from "../../components/ui/delete-icon-button";
import { apiGet, apiPost, apiPatch, apiDelete, apiFetch } from "../../utils/apiClient";
import { getStoredUser } from "../../utils/authStore";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

// Phase 0 of SOW_FEATURE_PLAN.md: document CRUD only. Source upload,
// generation, the editor, coverage panel, versions, export, and rewrite
// land in later phases (this page will grow a "New / Generate" and
// "Library" tab structure mirroring /ai-testing once Phase 1+ exists) --
// deliberately not stubbed here so nothing on screen looks finished before
// it is.

const STATUS_COLORS = {
  draft: "#6B7280",
  generating: "#2563EB",
  ready: "#16A34A",
  error: "#DC2626",
};
const STATUS_BG = {
  draft: "#F3F4F6",
  generating: "#DBEAFE",
  ready: "#DCFCE7",
  error: "#FEE2E2",
};

function StatusBadge({ status }) {
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: 11,
        fontWeight: 600,
        color: STATUS_COLORS[status] || "#6B7280",
        background: STATUS_BG[status] || "#F3F4F6",
        borderRadius: 999,
        padding: "2px 9px",
        textTransform: "capitalize",
      }}
    >
      {status}
    </span>
  );
}

// Import SOW: same upload validation the backend endpoint enforces
// (app/api/v1/sow.py::add_existing_sow_source's _EXISTING_SOW_EXTENSIONS) --
// mirrored here only so a wrong file type is rejected instantly in the UI
// instead of round-tripping to the server first.
const IMPORT_ACCEPT = ".docx,.pdf,.txt,.md";

export default function SowPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const user = typeof window !== "undefined" ? getStoredUser() : null;
  const canWrite =
    !!user && (user.role === "admin" || (user.permissions || []).includes("sow"));

  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({ title: "", project_id: "" });
  const [formError, setFormError] = useState("");
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState(null);

  // Import SOW: create a document, then attach the uploaded file as an
  // "existing-sow" source in one step -- the resulting document lands on
  // /sow/{id} where extraction progress, the requirements ledger, and the
  // existing Generate button all work exactly as they do for a meeting-
  // sourced document (nothing new on that page's generation/editor/export/
  // Send-to-Vibe-Testing flow -- this only adds a new way to seed the
  // ledger).
  const [showImportModal, setShowImportModal] = useState(false);
  const [importForm, setImportForm] = useState({ title: "", project_id: "", file: null });
  const [importError, setImportError] = useState("");
  const [importSubmitting, setImportSubmitting] = useState(false);

  function resetImportModal() {
    setShowImportModal(false);
    setImportForm({ title: "", project_id: "", file: null });
    setImportError("");
    setImportSubmitting(false);
  }

  async function submitImport() {
    setImportError("");
    if (!importForm.file) {
      setImportError("Choose a file to import.");
      return;
    }
    const title = importForm.title.trim() || importForm.file.name.replace(/\.[^./]+$/, "");
    setImportSubmitting(true);
    try {
      const doc = await apiPost("/api/sow/documents", {
        title,
        project_id: importForm.project_id || null,
      });

      const body = new FormData();
      body.append("file", importForm.file);
      const res = await apiFetch(
        `/api/v1/sow/documents/${doc.id}/sources/existing-sow`,
        { method: "POST", body }
      );
      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `Upload failed (${res.status})`);
      }

      qc.invalidateQueries(["sow-documents"]);
      resetImportModal();
      router.push(`/sow/${doc.id}`);
    } catch (e) {
      // The document may already have been created even if the file
      // upload step failed -- refresh the library list so it's visible
      // either way, and let the user retry the upload from the document
      // page's "Existing SOW document" source panel rather than losing
      // the reserved document.
      qc.invalidateQueries(["sow-documents"]);
      setImportError(e.message);
      setImportSubmitting(false);
    }
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ["sow-documents"],
    queryFn: () => apiGet("/api/sow/documents"),
  });

  const { data: projectsData } = useQuery({
    queryKey: ["projects-list"],
    queryFn: () => apiGet("/api/projects?limit=100"),
    enabled: !!canWrite,
  });

  const createMutation = useMutation({
    mutationFn: (body) => apiPost("/api/sow/documents", body),
    onSuccess: () => {
      qc.invalidateQueries(["sow-documents"]);
      setShowModal(false);
      setForm({ title: "", project_id: "" });
      setFormError("");
      toastSuccess("SOW created");
    },
    onError: (e) => setFormError(e.message),
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, title }) => apiPatch(`/api/sow/documents/${id}`, { title }),
    onSuccess: () => {
      qc.invalidateQueries(["sow-documents"]);
      setRenamingId(null);
      setRenameError("");
      toastSuccess("SOW renamed");
    },
    onError: (e) => setRenameError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => apiDelete(`/api/sow/documents/${id}`),
    onSuccess: () => {
      qc.invalidateQueries(["sow-documents"]);
      setDeleteTarget(null);
      toastSuccess("SOW deleted");
    },
  });

  const documents = data || [];
  const projects = projectsData?.data || [];

  function projectName(projectId) {
    if (!projectId) return "—";
    return projects.find((p) => p.id === projectId)?.name || "—";
  }

  return (
    <AppShell noPadding>
      <PageContainer>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 24,
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 22,
                fontWeight: 600,
                color: "#111827",
                letterSpacing: "-0.02em",
              }}
            >
              SOW
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Statement of Work creation and rewrite, generated from meeting
              discussions, walkthrough recordings, and design references.
            </p>
          </div>
          {canWrite && (
            <div style={{ display: "flex", gap: 8 }}>
              {/* Outline rather than invert: this sits directly beside
                  "+ New SOW", and two identical dark pills would flatten the
                  primary/secondary distinction between them. Still moved onto
                  the shared Button so the vocabulary matches. */}
              <Button
                variant="outline"
                size="lg"
                onClick={() => {
                  resetImportModal();
                  setShowImportModal(true);
                }}
              >
                Import SOW
              </Button>
              <Button
                variant="invert"
                size="lg"
                onClick={() => {
                  setShowModal(true);
                  setFormError("");
                }}
              >
                + New SOW
              </Button>
            </div>
          )}
        </div>

        {isLoading && <GlobalLoader />}
        {error && (
          <p style={{ fontSize: 13, color: "#DC2626" }}>{error.message}</p>
        )}

        {!isLoading && !error && documents.length === 0 && (
          <div
            style={{
              border: "1px dashed #E5E7EB",
              borderRadius: 10,
              padding: "48px 24px",
              textAlign: "center",
              background: "#fff",
            }}
          >
            <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "#111827" }}>
              No SOW documents yet
            </p>
            <p style={{ margin: "6px 0 0", fontSize: 13, color: "#6B7280" }}>
              {canWrite
                ? "Create one to reserve a document, then attach meeting notes, a walkthrough recording, and design references once generation is available."
                : "Ask an admin to grant you the SOW permission to create one."}
            </p>
          </div>
        )}

        {!isLoading && !error && documents.length > 0 && (
          <div
            style={{
              background: "#fff",
              border: "1px solid #E5E7EB",
              borderRadius: 10,
              overflow: "hidden",
            }}
          >
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "#F9FAFB", borderBottom: "1px solid #E5E7EB" }}>
                  {["Title", "Status", "Project", "Updated", ""].map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: "left",
                        padding: "10px 16px",
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
                {documents.map((doc) => (
                  <tr key={doc.id} style={{ borderBottom: "1px solid #F3F4F6" }}>
                    <td style={{ padding: "10px 16px", fontSize: 13, color: "#111827" }}>
                      {renamingId === doc.id ? (
                        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                          <input
                            autoFocus
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            style={{
                              fontSize: 13,
                              padding: "5px 8px",
                              border: "1px solid #D1D5DB",
                              borderRadius: 6,
                              width: 220,
                            }}
                          />
                          <button
                            onClick={() =>
                              renameMutation.mutate({ id: doc.id, title: renameValue.trim() })
                            }
                            disabled={!renameValue.trim() || renameMutation.isPending}
                            style={{
                              fontSize: 12,
                              fontWeight: 600,
                              color: "#2563EB",
                              background: "transparent",
                              border: "none",
                              cursor: "pointer",
                            }}
                          >
                            Save
                          </button>
                          <button
                            onClick={() => {
                              setRenamingId(null);
                              setRenameError("");
                            }}
                            style={{
                              fontSize: 12,
                              color: "#6B7280",
                              background: "transparent",
                              border: "none",
                              cursor: "pointer",
                            }}
                          >
                            Cancel
                          </button>
                          {renameError && (
                            <span style={{ fontSize: 11, color: "#DC2626" }}>{renameError}</span>
                          )}
                        </div>
                      ) : (
                        <a
                          href={`/sow/${doc.id}`}
                          style={{ fontWeight: 500, color: "#111827", textDecoration: "none" }}
                          onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
                          onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
                        >
                          {doc.title}
                        </a>
                      )}
                    </td>
                    <td style={{ padding: "10px 16px" }}>
                      <StatusBadge status={doc.status} />
                    </td>
                    <td style={{ padding: "10px 16px", fontSize: 13, color: "#6B7280" }}>
                      {projectName(doc.project_id)}
                    </td>
                    <td style={{ padding: "10px 16px", fontSize: 13, color: "#6B7280" }}>
                      {doc.updated_at ? new Date(doc.updated_at).toLocaleString() : "—"}
                    </td>
                    <td style={{ padding: "10px 16px", textAlign: "right" }}>
                      {canWrite && renamingId !== doc.id && (
                        <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
                          <button
                            onClick={() => {
                              setRenamingId(doc.id);
                              setRenameValue(doc.title);
                              setRenameError("");
                            }}
                            style={{
                              fontSize: 12,
                              fontWeight: 500,
                              color: "#374151",
                              background: "transparent",
                              border: "none",
                              cursor: "pointer",
                            }}
                          >
                            Rename
                          </button>
                          <DeleteIconButton
                            onClick={() => setDeleteTarget(doc)}
                            aria-label={`Delete ${doc.title || "document"}`}
                          />
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PageContainer>

      {/* Create modal */}
      {showModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(17,24,39,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={() => setShowModal(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "#fff",
              borderRadius: 12,
              padding: 24,
              width: 420,
              maxWidth: "90vw",
            }}
          >
            <h2 style={{ margin: "0 0 4px", fontSize: 16, fontWeight: 600, color: "#111827" }}>
              New SOW document
            </h2>
            <p style={{ margin: "0 0 16px", fontSize: 12, color: "#6B7280" }}>
              This reserves an empty document. Meeting notes, a walkthrough
              recording, and design references are attached in a later step.
            </p>

            <label style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>Title</label>
            <input
              autoFocus
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="e.g. Checkout Redesign — SOW"
              style={{
                display: "block",
                width: "100%",
                marginTop: 4,
                marginBottom: 14,
                fontSize: 13,
                padding: "8px 10px",
                border: "1px solid #D1D5DB",
                borderRadius: 8,
                boxSizing: "border-box",
              }}
            />

            <label style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>
              Project (optional)
            </label>
            <div style={{ marginTop: 4, marginBottom: 16 }}>
              <Select
                value={form.project_id || "none"}
                onValueChange={(v) =>
                  setForm((f) => ({ ...f, project_id: v === "none" ? "" : v }))
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="No project" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No project</SelectItem>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {formError && (
              <p style={{ fontSize: 12, color: "#DC2626", margin: "0 0 10px" }}>{formError}</p>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                onClick={() => setShowModal(false)}
                style={{
                  padding: "8px 14px",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  background: "#fff",
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={() =>
                  createMutation.mutate({
                    title: form.title.trim(),
                    project_id: form.project_id || null,
                  })
                }
                disabled={!form.title.trim() || createMutation.isPending}
                style={{
                  padding: "8px 14px",
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#fff",
                  background: !form.title.trim() ? "#93C5FD" : "#2563EB",
                  border: "none",
                  borderRadius: 8,
                  cursor: !form.title.trim() ? "default" : "pointer",
                }}
              >
                {createMutation.isPending ? "Creating…" : "Create"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Import SOW modal */}
      {showImportModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(17,24,39,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={() => !importSubmitting && resetImportModal()}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "#fff",
              borderRadius: 12,
              padding: 24,
              width: 440,
              maxWidth: "90vw",
            }}
          >
            <h2 style={{ margin: "0 0 4px", fontSize: 16, fontWeight: 600, color: "#111827" }}>
              Import SOW
            </h2>
            <p style={{ margin: "0 0 16px", fontSize: 12, color: "#6B7280" }}>
              Upload an existing SOW/requirements document (.docx, .pdf, .txt, or .md). It's
              parsed into this document's requirements ledger — click Generate on the document
              page to produce an editable, structured SOW from it, then refine it further with
              meeting sources or send it to Vibe Testing whenever you're ready.
            </p>

            <label style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>
              Title (optional — defaults to the file name)
            </label>
            <input
              autoFocus
              value={importForm.title}
              onChange={(e) => setImportForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="e.g. Checkout Redesign — SOW"
              style={{
                display: "block",
                width: "100%",
                marginTop: 4,
                marginBottom: 14,
                fontSize: 13,
                padding: "8px 10px",
                border: "1px solid #D1D5DB",
                borderRadius: 8,
                boxSizing: "border-box",
              }}
            />

            <label style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>
              Project (optional)
            </label>
            <div style={{ marginTop: 4, marginBottom: 14 }}>
              <Select
                value={importForm.project_id || "none"}
                onValueChange={(v) =>
                  setImportForm((f) => ({ ...f, project_id: v === "none" ? "" : v }))
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="No project" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No project</SelectItem>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <label style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>File</label>
            <input
              type="file"
              accept={IMPORT_ACCEPT}
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setImportForm((form) => ({ ...form, file: f }));
              }}
              style={{ fontSize: 12, marginTop: 4, marginBottom: 14, display: "block" }}
              disabled={importSubmitting}
            />

            {importError && (
              <p style={{ fontSize: 12, color: "#DC2626", margin: "0 0 10px" }}>{importError}</p>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                onClick={resetImportModal}
                disabled={importSubmitting}
                style={{
                  padding: "8px 14px",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  background: "#fff",
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  cursor: importSubmitting ? "default" : "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={submitImport}
                disabled={!importForm.file || importSubmitting}
                style={{
                  padding: "8px 14px",
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#fff",
                  background: !importForm.file || importSubmitting ? "#93C5FD" : "#2563EB",
                  border: "none",
                  borderRadius: 8,
                  cursor: !importForm.file || importSubmitting ? "default" : "pointer",
                }}
              >
                {importSubmitting ? "Importing…" : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation */}
      {deleteTarget && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(17,24,39,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={() => setDeleteTarget(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "#fff",
              borderRadius: 12,
              padding: 24,
              width: 380,
              maxWidth: "90vw",
            }}
          >
            <h2 style={{ margin: "0 0 8px", fontSize: 16, fontWeight: 600, color: "#111827" }}>
              Delete "{deleteTarget.title}"?
            </h2>
            <p style={{ margin: "0 0 18px", fontSize: 13, color: "#6B7280" }}>
              This hides the document from the list. It is not permanently
              erased — an admin can recover it directly if this was a
              mistake.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                onClick={() => setDeleteTarget(null)}
                style={{
                  padding: "8px 14px",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  background: "#fff",
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => deleteMutation.mutate(deleteTarget.id)}
                disabled={deleteMutation.isPending}
                style={{
                  padding: "8px 14px",
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#fff",
                  background: "#DC2626",
                  border: "none",
                  borderRadius: 8,
                  cursor: "pointer",
                }}
              >
                {deleteMutation.isPending ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
