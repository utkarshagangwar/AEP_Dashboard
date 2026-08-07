"use client";

/**
 * Structured authoring fields for the "Functional Test" mode (New Vibe Test
 * Phase 1 — see Vibe_Test_Gaps_and_Implementation_Checklist.md). Replaces
 * the old single free-text goal textarea: preconditions, an ordered list of
 * atomic steps, one or more expected results, an optional test_type flag,
 * optional named data sets for data-driven runs, and an optional linked
 * requirement reference.
 *
 * Purely a controlled-state input component — it does not call the API
 * itself. It reports its current value (and whether that value is valid
 * enough to submit) to the parent via onChange on every edit; page.tsx owns
 * the actual POST /api/ai-testing/runs call and merges this payload with
 * the existing environment/credential fields.
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface FunctionalTestDataSet {
  name: string;
  values: Record<string, string>;
}

export interface FunctionalTestPayload {
  preconditions: string;
  steps: { text: string }[];
  expected_results: string[];
  test_data: FunctionalTestDataSet[];
  test_type: "happy" | "negative" | "edge";
  linked_requirement: string;
  viewport_preset: "desktop" | "tablet" | "mobile";
  isValid: boolean;
}

const TEST_TYPE_OPTIONS: { value: FunctionalTestPayload["test_type"]; label: string }[] = [
  { value: "happy", label: "Happy path" },
  { value: "negative", label: "Negative (invalid input)" },
  { value: "edge", label: "Edge case (boundary values)" },
];

// New Vibe Test Phase 2 (execution reliability) — lets a Functional Test
// deliberately exercise a mobile/tablet responsive breakpoint instead of
// always running at the fixed desktop viewport (backend default). Values
// must match app.services.ai_runner.VIEWPORT_PRESETS' keys exactly.
const VIEWPORT_OPTIONS: { value: FunctionalTestPayload["viewport_preset"]; label: string }[] = [
  { value: "desktop", label: "Desktop (1530×820)" },
  { value: "tablet", label: "Tablet (810×1080)" },
  { value: "mobile", label: "Mobile (390×844)" },
];

function ListEditor({
  label,
  placeholder,
  items,
  onChange,
  minRows = 1,
}: {
  label: string;
  placeholder: string;
  items: string[];
  onChange: (items: string[]) => void;
  minRows?: number;
}) {
  const rows = items.length > 0 ? items : Array(minRows).fill("");

  return (
    <div>
      <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
        {label}
      </label>
      <div className="space-y-2">
        {rows.map((val, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="text-xs text-gray-400 w-5 flex-shrink-0 text-right">
              {i + 1}.
            </span>
            <input
              value={val}
              onChange={(e) => {
                const next = [...rows];
                next[i] = e.target.value;
                onChange(next);
              }}
              placeholder={placeholder}
              className="flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
            />
            {rows.length > minRows && (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => onChange(rows.filter((_, j) => j !== i))}
                      className="shrink-0 text-gray-400 hover:text-destructive [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)]"
                      aria-label={`Remove ${label.toLowerCase()} ${i + 1}`}
                    >
                      ✕
                    </Button>
                  }
                />
                <TooltipContent>Remove {label.toLowerCase()}</TooltipContent>
              </Tooltip>
            )}
          </div>
        ))}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="xs"
        onClick={() => onChange([...rows, ""])}
        className="mt-1.5"
      >
        + Add {label.toLowerCase().replace(/s$/, "")}
      </Button>
    </div>
  );
}

export default function FunctionalTestFields({
  onChange,
}: {
  onChange: (payload: FunctionalTestPayload) => void;
}) {
  const [preconditions, setPreconditions] = useState("");
  const [steps, setSteps] = useState<string[]>([""]);
  const [expectedResults, setExpectedResults] = useState<string[]>([""]);
  const [testType, setTestType] = useState<FunctionalTestPayload["test_type"]>("happy");
  const [linkedRequirement, setLinkedRequirement] = useState("");
  const [viewportPreset, setViewportPreset] =
    useState<FunctionalTestPayload["viewport_preset"]>("desktop");
  const [dataSetsOpen, setDataSetsOpen] = useState(false);
  const [dataSets, setDataSets] = useState<FunctionalTestDataSet[]>([]);

  useEffect(() => {
    const cleanSteps = steps.map((s) => s.trim()).filter(Boolean);
    const cleanExpected = expectedResults.map((s) => s.trim()).filter(Boolean);
    onChange({
      preconditions: preconditions.trim(),
      steps: cleanSteps.map((text) => ({ text })),
      expected_results: cleanExpected,
      test_data: dataSets.filter((d) => d.name.trim()),
      test_type: testType,
      linked_requirement: linkedRequirement.trim(),
      viewport_preset: viewportPreset,
      isValid: cleanSteps.length > 0 && cleanExpected.length > 0,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    preconditions,
    steps,
    expectedResults,
    testType,
    linkedRequirement,
    viewportPreset,
    dataSets,
  ]);

  const addDataSet = () =>
    setDataSets((prev) => [...prev, { name: `Data set ${prev.length + 1}`, values: {} }]);

  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
          Preconditions <span className="normal-case text-gray-400">(optional)</span>
        </label>
        <textarea
          value={preconditions}
          onChange={(e) => setPreconditions(e.target.value)}
          placeholder='e.g. "User is logged in as a sales rep with at least one open deal."'
          rows={2}
          className="w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
        />
      </div>

      <ListEditor
        label="Steps"
        placeholder='e.g. "Click the Pipeline tab in the left nav"'
        items={steps}
        onChange={setSteps}
      />

      <ListEditor
        label="Expected results"
        placeholder='e.g. "Pipeline dashboard loads with at least one row"'
        items={expectedResults}
        onChange={setExpectedResults}
      />

      <div className="flex gap-3 flex-wrap items-end">
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
            Test type
          </label>
          <Select
            value={testType}
            onValueChange={(v) => setTestType((v as FunctionalTestPayload["test_type"]) ?? "happy")}
            items={TEST_TYPE_OPTIONS}
          >
            <SelectTrigger className="w-auto min-w-[180px] h-9 text-sm">
              <SelectValue placeholder="Test type" />
            </SelectTrigger>
            <SelectContent>
              {TEST_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
            Viewport
          </label>
          <Select
            value={viewportPreset}
            onValueChange={(v) =>
              setViewportPreset((v as FunctionalTestPayload["viewport_preset"]) ?? "desktop")
            }
            items={VIEWPORT_OPTIONS}
          >
            <SelectTrigger className="w-auto min-w-[190px] h-9 text-sm">
              <SelectValue placeholder="Viewport" />
            </SelectTrigger>
            <SelectContent>
              {VIEWPORT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex-1 min-w-[220px]">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
            Linked requirement <span className="normal-case text-gray-400">(optional)</span>
          </label>
          <input
            value={linkedRequirement}
            onChange={(e) => setLinkedRequirement(e.target.value)}
            placeholder='e.g. "REQ-102" or a SOW checkpoint title'
            className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
          />
        </div>
      </div>

      <div className="border-t border-gray-100 pt-3">
        <button
          type="button"
          onClick={() => setDataSetsOpen((v) => !v)}
          className="text-xs text-gray-600 hover:text-gray-900 font-medium flex items-center gap-1"
        >
          <span className="text-gray-400">{dataSetsOpen ? "▾" : "▸"}</span>
          Data-driven: run this test against multiple data sets{" "}
          {dataSets.length > 0 && (
            <span className="text-gray-400">({dataSets.length})</span>
          )}
        </button>

        {dataSetsOpen && (
          <div className="mt-3 space-y-3">
            {dataSets.map((ds, i) => (
              <div key={i} className="rounded-md border border-gray-200 p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <input
                    value={ds.name}
                    onChange={(e) => {
                      const next = [...dataSets];
                      next[i] = { ...next[i], name: e.target.value };
                      setDataSets(next);
                    }}
                    placeholder="Data set name"
                    className="flex-1 rounded-md border border-gray-200 px-2.5 py-1.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-gray-900"
                  />
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          onClick={() =>
                            setDataSets(dataSets.filter((_, j) => j !== i))
                          }
                          className="text-gray-400 hover:text-destructive [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)]"
                          aria-label={`Remove data set ${i + 1}`}
                        >
                          ✕
                        </Button>
                      }
                    />
                    <TooltipContent>Remove data set</TooltipContent>
                  </Tooltip>
                </div>
                {Object.entries(ds.values).map(([k, v], kvI) => (
                  <div key={kvI} className="flex items-center gap-2 pl-2">
                    <input
                      value={k}
                      onChange={(e) => {
                        const entries = Object.entries(ds.values);
                        entries[kvI] = [e.target.value, entries[kvI][1]];
                        const next = [...dataSets];
                        next[i] = { ...next[i], values: Object.fromEntries(entries) };
                        setDataSets(next);
                      }}
                      placeholder="field"
                      className="w-1/3 rounded-md border border-gray-200 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-gray-900"
                    />
                    <input
                      value={v}
                      onChange={(e) => {
                        const entries = Object.entries(ds.values);
                        entries[kvI] = [entries[kvI][0], e.target.value];
                        const next = [...dataSets];
                        next[i] = { ...next[i], values: Object.fromEntries(entries) };
                        setDataSets(next);
                      }}
                      placeholder="value"
                      className="flex-1 rounded-md border border-gray-200 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-gray-900"
                    />
                    <Button
                      type="button"
                      onClick={() => {
                        const entries = Object.entries(ds.values).filter(
                          (_, j) => j !== kvI
                        );
                        const next = [...dataSets];
                        next[i] = { ...next[i], values: Object.fromEntries(entries) };
                        setDataSets(next);
                      }}
                      variant="ghost"
                      size="icon-xs"
                      className="text-gray-400 hover:text-destructive [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)]"
                    >
                      ✕
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => {
                    const next = [...dataSets];
                    next[i] = { ...next[i], values: { ...next[i].values, "": "" } };
                    setDataSets(next);
                  }}
                  className="ml-2"
                >
                  + Add field
                </Button>
              </div>
            ))}
            <Button type="button" variant="outline" className="h-8 text-xs" onClick={addDataSet}>
              + Add data set
            </Button>
            <p className="text-xs text-gray-400">
              Submitting will run this test once per data set above.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
