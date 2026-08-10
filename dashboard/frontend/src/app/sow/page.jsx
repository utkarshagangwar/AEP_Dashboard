"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import { usePageLoading } from "../../components/NavigationLoadingProvider";
import PageContainer from "../../components/PageContainer";
import { toastSuccess } from "../../lib/toast";
import { Button } from "../../components/ui/button";
import { DeleteIconButton } from "../../components/ui/delete-icon-button";
import { apiGet, apiPost, apiPatch, apiDelete } from "../../utils/apiClient";
import ImportSowDialog from "../../components/ImportSowDialog";
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

  // Import SOW: creates a document, attaches the uploaded file as an
  // "existing-sow" source, and files the platform evidence against the
  // project -- all inside ImportSowDialog, which owns that whole flow
  // (including the evidence requirement) so this page stays a library view.
  // The resulting document lands on /sow/{id} where extraction progress, the
  // requirements ledger and Generate work exactly as they do for a
  // meeting-sourced document; nothing about that page changed.
  const [showImportModal, setShowImportModal] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["sow-documents"],
    queryFn: () => apiGet("/api/sow/documents"),
  });

  // Loading is the app-wide overlay, not a box inside the page.
  usePageLoading(isLoading);

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
                onClick={() => setShowImportModal(true)}
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
                    {/* position: relative is what .cell-link's inset:0
                        resolves against -- without it the link would size
                        itself off the table instead of this cell. The row's
                        height still comes from the other cells (Status,
                        Project, Updated always render text), so taking the
                        link out of flow cannot collapse the row. */}
                    <td
                      style={{
                        position: "relative",
                        padding: "10px 16px",
                        fontSize: 13,
                        color: "#111827",
                      }}
                    >
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
                          <Button
                            variant="invert"
                            size="sm"
                            onClick={() =>
                              renameMutation.mutate({ id: doc.id, title: renameValue.trim() })
                            }
                            disabled={!renameValue.trim() || renameMutation.isPending}
                          >
                            Save
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setRenamingId(null);
                              setRenameError("");
                            }}
                          >
                            Cancel
                          </Button>
                          {renameError && (
                            <span style={{ fontSize: 11, color: "#DC2626" }}>{renameError}</span>
                          )}
                        </div>
                      ) : (
                        <a
                          // slug is the canonical URL identifier (migration
                          // 0048); doc.id fallback only guards a document
                          // fetched before the backend added it, which
                          // shouldn't happen post-deploy but costs nothing
                          // to keep the link from breaking if it ever does.
                          href={`/sow/${doc.slug || doc.id}`}
                          // .cell-link makes the whole TITLE cell the target
                          // and tints it on hover -- no inline `color` here
                          // on purpose, since an inline colour would outrank
                          // the stylesheet and kill the hover transition.
                          className="cell-link"
                        >
                          <span style={{ fontWeight: 500 }}>{doc.title}</span>
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
                        // One segmented control rather than two loose buttons:
                        // Rename and Delete are the same row's two options, so
                        // they read as a pair. `outline` (not `ghost`) because
                        // the group needs a resting left cap to be a group at
                        // all. Segment width and corner rounding come from
                        // `.btn-option-group` in app/global.css, not from
                        // classes here. Everything else about both buttons --
                        // rim, edge, hover reveal -- is the shared design.
                        <div className="btn-option-group">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setRenamingId(doc.id);
                              setRenameValue(doc.title);
                              setRenameError("");
                            }}
                          >
                            Rename
                          </Button>
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
              <Button
                variant="outline"
                size="lg"
                onClick={() => setShowModal(false)}
              >
                Cancel
              </Button>
              <Button
                variant="invert"
                size="lg"
                onClick={() =>
                  createMutation.mutate({
                    title: form.title.trim(),
                    project_id: form.project_id || null,
                  })
                }
                disabled={!form.title.trim() || createMutation.isPending}
              >
                {createMutation.isPending ? "Creating…" : "Create"}
              </Button>
            </div>
          </div>
        </div>
      )}

      <ImportSowDialog
        open={showImportModal}
        projects={projects}
        onClose={() => setShowImportModal(false)}
        onImported={(id) => {
          setShowImportModal(false);
          qc.invalidateQueries(["sow-documents"]);
          router.push(`/sow/${id}`);
        }}
      />

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
              <Button
                variant="outline"
                size="lg"
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="lg"
                onClick={() => deleteMutation.mutate(deleteTarget.id)}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? "Deleting…" : "Delete"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
