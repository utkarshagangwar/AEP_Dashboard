"use client";

/**
 * UI naming reference — what the AI actually read off this project's evidence.
 *
 * WHY IT IS ON SCREEN AT ALL. The reference (app/services/ui_inventory.py) is
 * what makes a generated test say "Apply Now" instead of the document's
 * "Submit Application". If the vision pass misreads the UI, or manages 2
 * screens out of 12, nothing announces it: extraction silently falls back to
 * the document's wording and the cost shows up weeks later as a failed run
 * that looks like a product bug. This panel is the only place that difference
 * is visible before then.
 *
 * Read-only on purpose. There is no rebuild button because the reference
 * rebuilds itself whenever the project's evidence changes — a manual rebuild
 * would only re-run a vision call that is already current. When it is wrong,
 * the fix is better evidence, not another build.
 *
 * Feature-detected like the other Vibe Testing surfaces: the endpoint 404s
 * when the flag is off, and this renders nothing rather than an error.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { apiGet } from "@/utils/apiClient";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface InventoryScreen {
  screen?: string;
  controls?: string[];
  fields?: string[];
  nav?: string[];
  messages?: string[];
}

interface UiInventory {
  project_id: string;
  screens: InventoryScreen[];
  screen_count: number;
  label_count: number;
  rendered_text?: string | null;
  built_by_model?: string | null;
  build_error?: string | null;
  source_artifact_count: number;
  updated_at?: string | null;
}

// A reference built from screenshots taken months ago describes a product
// that may since have moved on, and a confidently wrong label is worse than
// no label: the test looks grounded and the failure reads as a product bug.
// Age alone cannot prove staleness, so this warns rather than invalidates.
const STALE_AFTER_DAYS = 90;

function daysSince(iso?: string | null): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 86_400_000);
}

function LabelRow({ heading, items }: { heading: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="flex gap-2 text-xs">
      <span className="w-24 flex-shrink-0 text-gray-400">{heading}</span>
      <span className="min-w-0 text-gray-700">{items.join(", ")}</span>
    </div>
  );
}

export default function ProjectUiInventoryPanel({ projectId }: { projectId: string }) {
  const [expanded, setExpanded] = useState(false);

  const { data, isLoading, isError } = useQuery<UiInventory>({
    queryKey: ["project-ui-inventory", projectId],
    queryFn: () => apiGet(`/api/v1/visual-audits/projects/${projectId}/ui-inventory`),
    enabled: !!projectId,
    retry: false,
  });

  // 404 = Vibe Testing is off on this server. Render nothing, exactly as the
  // other feature-detected sections do — an error box for a feature that was
  // deliberately disabled is noise.
  if (isError) return null;

  const age = daysSince(data?.updated_at);
  const stale = age !== null && age > STALE_AFTER_DAYS;
  const built = !!data && data.screen_count > 0;

  return (
    // Geometry matches the Environments/Suites sections above it exactly
    // (18px 24px, 12px radius) — those are inline-styled on the page, so the
    // arbitrary value is what keeps this from sitting a few pixels off.
    <div className="mb-6 rounded-xl border border-gray-200 bg-white px-6 py-[18px]">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-gray-900">UI naming reference</h2>
        {built && (
          <span className="flex items-center gap-2">
            {stale && (
              <Badge
                variant="outline"
                className="text-amber-700 border-amber-300 bg-amber-50"
                title={`Built ${age} days ago. If the UI has changed since, upload newer screenshots — the reference rebuilds itself when the evidence changes.`}
              >
                {age} days old
              </Badge>
            )}
            <span className="text-xs text-gray-400">
              {data!.screen_count} screen{data!.screen_count === 1 ? "" : "s"} ·{" "}
              {data!.label_count} label{data!.label_count === 1 ? "" : "s"}
              {data!.updated_at
                ? ` · built ${new Date(data!.updated_at).toLocaleDateString("en-GB")}`
                : ""}
            </span>
          </span>
        )}
      </div>

      <p className="mb-3 text-sm text-gray-500">
        What this project&apos;s screens, buttons and fields are actually called, read
        from the screenshots and walkthroughs uploaded with its SOWs. Tests generated
        for this project use these names instead of the wording in the document.
      </p>

      {isLoading ? (
        <Skeleton className="h-16 w-full rounded-md" />
      ) : !built ? (
        <p className="text-sm text-gray-400">
          {data?.build_error
            ? data.build_error
            : "No reference yet — it is built the first time a SOW is imported for this project."}
          {data?.build_error ? (
            <>
              {" "}
              Until it is built, tests name controls the way the document does, which
              is what makes them fail on wording rather than on behaviour.
            </>
          ) : null}
        </p>
      ) : (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 rounded-md text-xs font-medium text-gray-600 transition-colors hover:text-gray-900 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            {expanded ? "Hide" : "Show"} what was read
          </button>

          {expanded && (
            <div className="mt-3 space-y-3 border-l-2 border-gray-100 pl-3">
              {data!.screens.map((screen, i) => (
                <div key={i} className="space-y-1">
                  <p className="text-xs font-medium text-gray-800">
                    {screen.screen || "Unnamed screen"}
                  </p>
                  <LabelRow heading="nav" items={screen.nav} />
                  <LabelRow heading="buttons/links" items={screen.controls} />
                  <LabelRow heading="fields" items={screen.fields} />
                  <LabelRow heading="messages" items={screen.messages} />
                </div>
              ))}
              <p className="text-xs text-gray-400">
                Read from {data!.source_artifact_count} evidence file
                {data!.source_artifact_count === 1 ? "" : "s"}
                {data!.built_by_model ? ` via ${data!.built_by_model}` : ""}. A name
                missing here is not a missing feature — extraction falls back to the
                document&apos;s wording for anything the reference does not cover.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
