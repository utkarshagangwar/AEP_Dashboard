"use client";

/**
 * Figma Import — Phase 4b UI for the Vibe Testing tab.
 *
 * Paste a Figma file URL → list its frames → select → import. The backend
 * downloads the rendered PNGs (Celery) and they appear as reference designs
 * in the Visual Audit section. The Figma token lives server-side only.
 *
 * Feature-detected like the other Visual QA sections: probe 404 → render null.
 *
 * Also usable in `embedded` mode (see props below) — this is how the UI Test
 * flow's reference picker (VisualAuditSection.tsx) triggers a fresh Figma
 * import without leaving that flow. Previously a stale code comment in
 * ModeSelector.tsx claimed this was already reachable there; it wasn't —
 * VisualAuditSection only had "upload PNG" and "pick an already-completed
 * reference," with no path to a new Figma import at all. Embedding this
 * component (rather than re-implementing the same list/select/import/poll
 * logic a second time) closes that gap without duplicating it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface Frame {
  node_id: string;
  name: string;
  page?: string | null;
}

interface ImportedRef {
  id: string;
  file_name: string;
  parse_status: "not_required" | "pending" | "processing" | "done" | "error";
  parse_error?: string | null;
}

const ACTIVE = new Set(["pending", "processing"]);

interface FigmaImportSectionProps {
  // When true: skip the standalone Card/header chrome and this component's
  // own feature-detection probe (the parent has already confirmed the
  // backend flag is on), and render just the import controls so this can be
  // nested inside another section. Defaults to false — standalone behavior
  // is unchanged for any other caller.
  embedded?: boolean;
  // Fired once per import batch, the moment every imported frame in that
  // batch has reached a terminal parse_status (done/error) — lets an
  // embedding parent (e.g. VisualAuditSection) refresh its own reference
  // list and auto-select a newly-ready design instead of the caller having
  // to poll a second time.
  onImported?: (refs: ImportedRef[]) => void;
}

export default function FigmaImportSection({
  embedded = false,
  onImported,
}: FigmaImportSectionProps = {}) {
  // Embedded callers skip our own probe below (the parent already confirmed
  // the feature flag is on), so start "enabled" for them; standalone callers
  // start disabled until the probe resolves, same as before.
  const [enabled, setEnabled] = useState(embedded);
  const [fileInput, setFileInput] = useState("");
  const [fileKey, setFileKey] = useState<string | null>(null);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loadingFrames, setLoadingFrames] = useState(false);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState<ImportedRef[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Guards onImported so it fires exactly once per completed batch, not on
  // every subsequent render where "no active imports" still holds true.
  const notifiedRef = useRef(false);

  // Feature detection (same probe the other sections use) — skipped when
  // embedded, since the parent section already did this check itself.
  useEffect(() => {
    if (embedded) return;
    (async () => {
      try {
        await apiGet("/api/v1/visual-audits/references");
        setEnabled(true);
      } catch {
        setEnabled(false);
      }
    })();
  }, [embedded]);

  // Poll imported frames until all reach a terminal state
  const refreshImported = useCallback(async () => {
    try {
      const refs: ImportedRef[] = await apiGet("/api/v1/visual-audits/references");
      setImported((prev) =>
        prev.map((p) => refs.find((r) => r.id === p.id) ?? p)
      );
    } catch {
      // transient — keep last known state
    }
  }, []);

  useEffect(() => {
    const anyActive = imported.some((i) => ACTIVE.has(i.parse_status));
    if (!anyActive) {
      if (pollRef.current) clearInterval(pollRef.current);
      if (imported.length > 0 && !notifiedRef.current) {
        notifiedRef.current = true;
        onImported?.(imported);
      }
      return;
    }
    pollRef.current = setInterval(refreshImported, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [imported, refreshImported, onImported]);

  const handleListFrames = async () => {
    if (!fileInput.trim() || loadingFrames) return;
    setLoadingFrames(true);
    setError(null);
    setFrames([]);
    setSelected(new Set());
    setFileKey(null);
    try {
      const data = await apiGet(
        `/api/v1/visual-audits/figma/frames?file=${encodeURIComponent(fileInput.trim())}`
      );
      setFileKey(data.file_key);
      setFrames(data.frames || []);
      if (!data.frames?.length) {
        setError("No top-level frames found in this file.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not list frames");
    } finally {
      setLoadingFrames(false);
    }
  };

  const toggleFrame = (nodeId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else if (next.size < 20) next.add(nodeId);
      return next;
    });
  };

  const handleImport = async () => {
    if (!fileKey || selected.size === 0 || importing) return;
    setImporting(true);
    setError(null);
    // Re-arm the onImported guard so a second import batch in the same
    // mount (e.g. embedded panel, import once, then import again) still
    // notifies the parent when this new batch settles.
    notifiedRef.current = false;
    try {
      const chosen = frames.filter((f) => selected.has(f.node_id));
      const data: ImportedRef[] = await apiPost("/api/v1/visual-audits/figma/import", {
        file: fileKey,
        frames: chosen,
      });
      setImported(data);
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  if (!enabled) return null;

  // Shared between the standalone (own Card) and embedded (bare) renders so
  // the two never drift apart.
  const content = (
    <div className="space-y-4">
        <div className="flex gap-2">
          <input
            type="text"
            value={fileInput}
            onChange={(e) => {
              setFileInput(e.target.value);
              setError(null);
            }}
            placeholder="Figma file URL, e.g. https://www.figma.com/design/AbC123…/My-App"
            className="flex-1 rounded-md border border-gray-200 px-4 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent"
            onKeyDown={(e) => {
              if (e.key === "Enter") handleListFrames();
            }}
          />
          <Button
            variant="outline"
            className="h-9 text-sm flex-shrink-0"
            disabled={!fileInput.trim() || loadingFrames}
            onClick={handleListFrames}
          >
            {loadingFrames ? "Loading…" : "List frames"}
          </Button>
        </div>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {error}
          </p>
        )}

        {frames.length > 0 && (
          <>
            <div className="border border-gray-200 rounded-md divide-y divide-gray-100 max-h-64 overflow-y-auto">
              {frames.map((f) => (
                <label
                  key={f.node_id}
                  className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(f.node_id)}
                    onChange={() => toggleFrame(f.node_id)}
                    className="rounded border-gray-300"
                  />
                  <span className="text-sm text-gray-800 truncate">{f.name}</span>
                  {f.page && (
                    <span className="text-xs text-gray-400 ml-auto flex-shrink-0">
                      {f.page}
                    </span>
                  )}
                </label>
              ))}
            </div>
            <Button
              onClick={handleImport}
              disabled={selected.size === 0 || importing}
              className="w-full h-10 text-sm font-medium"
            >
              {importing
                ? "Importing…"
                : `Import ${selected.size} frame${selected.size === 1 ? "" : "s"} (max 20)`}
            </Button>
          </>
        )}

        {imported.length > 0 && (
          <div className="space-y-1.5 pt-2 border-t border-gray-100">
            {imported.map((ref) => (
              <div key={ref.id} className="flex items-center justify-between">
                <span className="text-sm text-gray-700 truncate mr-3">
                  {ref.file_name}
                </span>
                <span className="flex items-center gap-2 flex-shrink-0">
                  {ref.parse_status === "error" && ref.parse_error && (
                    <span
                      className="text-xs text-red-500 truncate max-w-[220px]"
                      title={ref.parse_error}
                    >
                      {ref.parse_error}
                    </span>
                  )}
                  <Badge
                    variant="outline"
                    className={
                      ref.parse_status === "done" || ref.parse_status === "not_required"
                        ? "text-green-600 border-green-300 bg-green-50"
                        : ref.parse_status === "error"
                        ? "text-red-600 border-red-300 bg-red-50"
                        : "text-blue-600 border-blue-300 bg-blue-50"
                    }
                  >
                    {ACTIVE.has(ref.parse_status) ? "downloading…" : ref.parse_status === "not_required" ? "ready" : ref.parse_status}
                  </Badge>
                </span>
              </div>
            ))}
            {imported.every((i) => !ACTIVE.has(i.parse_status)) && (
              <p className="text-xs text-gray-400 pt-1">
                {embedded
                  ? "Ready frames are now available in the reference picker above."
                  : "Ready frames are now available in the Visual Audit reference list above."}
              </p>
            )}
          </div>
        )}
    </div>
  );

  if (embedded) return content;

  return (
    <Card className="shadow-sm mt-6">
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">Figma Import</CardTitle>
            <p className="text-sm text-gray-500 mt-1">
              Pull design frames straight from Figma as visual-audit
              references — no manual exporting.
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
        {content}
      </CardContent>
    </Card>
  );
}
