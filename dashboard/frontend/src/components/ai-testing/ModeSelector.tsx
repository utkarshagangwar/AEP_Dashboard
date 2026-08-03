"use client";

/**
 * Mode selector for the Vibe Testing "New test" tab — replaces the old
 * stacked-cards layout (UI test, Functional Test, and Visual and design QA)
 * with a single "choose how to test"
 * step. Picking a mode just toggles which panel is visible in page.tsx —
 * this component owns no data/API logic of its own, it's purely the picker.
 *
 * Three modes map 1:1 to existing backend-backed features:
 *  - ui         → VisualAuditSection (reference design + target URL,
 *                 backed by visual_judge.judge() / visual_runs). Replaces
 *                 the old single free-text "quick" goal box's UI-testing
 *                 half — see Vibe_Test_Gaps_and_Implementation_Checklist.md
 *                 Phase 1. A leaner, single-reference-vs-one-URL check;
 *                 "Visual and design QA" below remains the richer combined
 *                 Figma+video+SOW Autonomous QA audit — different tool.
 *  - functional → FunctionalTestPanel (preconditions + ordered steps +
 *                 expected results + optional data sets, compiled into a
 *                 goal server-side and run by the existing Hands agent).
 *                 Replaces the old "quick" goal box's functional half.
 *  - visual     → AutonomousQASection (URL + Figma + video + SOW + saved
 *                 reference + credentials, all submitted together as one
 *                 run) — unchanged.
 * Figma Import (standalone) is intentionally not represented here — removed
 * from the Vibe Testing page per product decision; still reachable inside
 * the "ui" mode's reference picker via VisualAuditSection.
 */

import type { ReactNode } from "react";
import { LayoutTemplate, ListChecks, ScanEye } from "lucide-react";
import { cn } from "@/lib/utils";

export type TestMode = "ui" | "functional" | "visual";

interface ModeCardConfig {
  id: TestMode;
  title: string;
  desc: string;
  icon: ReactNode;
  selectedClasses: string;
  accentClasses: string;
  iconClasses: string;
  focusClasses: string;
}

const CARDS: ModeCardConfig[] = [
  {
    id: "ui",
    title: "UI Test",
    desc: "Compare a live page against one design reference — pixel-diff plus AI structural review. No steps to script.",
    icon: <ScanEye className="h-4 w-4" />,
    selectedClasses: "border-purple-200 bg-purple-50/80",
    accentClasses: "bg-purple-600",
    iconClasses: "bg-purple-100 text-purple-700",
    focusClasses: "focus-visible:ring-purple-500",
  },
  {
    id: "functional",
    title: "Functional Test",
    desc: "Author preconditions, ordered steps, and expected results — the AI drives the browser and checks each one.",
    icon: <ListChecks className="h-4 w-4" />,
    selectedClasses: "border-indigo-200 bg-indigo-50/80",
    accentClasses: "bg-indigo-600",
    iconClasses: "bg-indigo-100 text-indigo-700",
    focusClasses: "focus-visible:ring-indigo-500",
  },
  {
    id: "visual",
    title: "Visual and design QA",
    desc: "Combine a live site, Figma file, walkthrough video, spec doc, and saved references into one audit run.",
    icon: <LayoutTemplate className="h-4 w-4" />,
    selectedClasses: "border-violet-200 bg-violet-50/80",
    accentClasses: "bg-violet-600",
    iconClasses: "bg-violet-100 text-violet-700",
    focusClasses: "focus-visible:ring-violet-500",
  },
];

export default function ModeSelector({
  mode,
  onModeChange,
}: {
  mode: TestMode;
  onModeChange: (mode: TestMode) => void;
}) {
  return (
    <section aria-labelledby="test-mode-title" className="mb-6">
      <div className="mb-3 flex items-center gap-3">
        <span className="font-mono text-xs text-gray-400">01</span>
        <h2
          id="test-mode-title"
          className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500"
        >
          Choose a test mode
        </h2>
      </div>
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {CARDS.map((card) => {
          const selected = mode === card.id;
          return (
            <button
              key={card.id}
              type="button"
              onClick={() => onModeChange(card.id)}
              aria-pressed={selected}
              className={cn(
                "group relative flex min-h-[92px] overflow-hidden rounded-xl border p-3.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
                selected
                  ? card.selectedClasses
                  : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50",
                card.focusClasses
              )}
            >
              <span
                className={cn(
                  "absolute inset-y-0 left-0 w-1",
                  selected ? card.accentClasses : "bg-transparent"
                )}
              />
              <span className="flex flex-1 items-start gap-3 pl-1">
                <span
                  className={cn(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                    selected ? card.iconClasses : "bg-gray-100 text-gray-500"
                  )}
                >
                  {card.icon}
                </span>
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-semibold tracking-[-0.01em] text-gray-900">
                    {card.title}
                  </span>
                  <span className="max-w-md text-xs leading-4 text-gray-500">
                    {card.desc}
                  </span>
                </span>
              </span>
              {selected && (
                <span
                  className={cn(
                    "absolute right-3 top-3 h-2 w-2 rounded-full",
                    card.accentClasses
                  )}
                  aria-hidden="true"
                />
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
