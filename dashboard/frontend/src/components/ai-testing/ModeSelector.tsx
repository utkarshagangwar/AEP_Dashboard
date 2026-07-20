"use client";

/**
 * Mode selector for the Vibe Testing "New test" tab — replaces the old
 * stacked-cards layout (Vibe UI test card, Autonomous QA card, SOW
 * Checkpoints card, Video Walkthrough card, Visual Audit card, Figma Import
 * card all rendered one after another) with a single "choose how to test"
 * step. Picking a mode just toggles which panel is visible in page.tsx —
 * this component owns no data/API logic of its own, it's purely the picker.
 *
 * Four modes map 1:1 to four distinct existing backend-backed features:
 *  - quick  → the plain-language goal test (unchanged)
 *  - visual → AutonomousQASection (URL + Figma + video + SOW + saved
 *             reference + credentials, all submitted together as one run)
 *  - sow    → SowCheckpointsSection variant="sow"
 *  - video  → SowCheckpointsSection variant="video"
 * Visual Audit and Figma Import are intentionally not represented here —
 * removed from the Vibe Testing page per product decision.
 */

import type { ReactNode } from "react";
import { Clapperboard, FileText, LayoutTemplate, Play } from "lucide-react";
import { cn } from "@/lib/utils";

export type TestMode = "quick" | "visual" | "sow" | "video";

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
    id: "quick",
    title: "New Vibe UI test",
    desc: "Describe a goal in plain English and let the AI plan and drive the browser. No design file needed.",
    icon: <Play className="h-4 w-4" />,
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
  {
    id: "sow",
    title: "SOW checkpoints",
    desc: "Parse a spec document into reusable functional and visual test skills.",
    icon: <FileText className="h-4 w-4" />,
    selectedClasses: "border-teal-200 bg-teal-50/80",
    accentClasses: "bg-teal-600",
    iconClasses: "bg-teal-100 text-teal-700",
    focusClasses: "focus-visible:ring-teal-500",
  },
  {
    id: "video",
    title: "Video walkthrough",
    desc: "Extract testable steps from a recorded product walkthrough.",
    icon: <Clapperboard className="h-4 w-4" />,
    selectedClasses: "border-amber-200 bg-amber-50/80",
    accentClasses: "bg-amber-500",
    iconClasses: "bg-amber-100 text-amber-700",
    focusClasses: "focus-visible:ring-amber-500",
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
