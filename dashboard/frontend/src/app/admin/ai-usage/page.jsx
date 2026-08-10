"use client";
import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isAfter,
  isBefore,
  isSameDay,
  isSameMonth,
  isWithinInterval,
  startOfDay,
  startOfMonth,
  startOfWeek,
  subMonths,
} from "date-fns";
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import AppShell from "../../../components/AppShell";
import { usePageLoading } from "../../../components/NavigationLoadingProvider";
import PageContainer from "../../../components/PageContainer";
import { apiGet, apiPut, apiDelete } from "../../../utils/apiClient";
import { toastSuccess } from "../../../lib/toast";
import { getStoredUser } from "../../../utils/authStore";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const NO_FILTER = "__all__";

const PROVIDER_OPTIONS = ["google", "axon", "openrouter", "anthropic", "openai"];
const SOURCE_OPTIONS = ["hands", "judge", "brain", "sow_ledger", "orchestrator", "video_ingest"];

// Providers ordered by how much anyone actually watches them day to day —
// AXON is the platform's default/highest-volume provider (see
// app.services.ai_runner._axon_client), so its row leads the table.
const PROVIDER_SORT_PRIORITY = { axon: 0 };

// Consistent, roomier padding to match every other data table in the app
// (e.g. components/ai-testing/ResultsTab.tsx's `px-4 py-3` cells) — the
// shadcn Table primitive's own defaults (h-10 px-2 / p-2) read as cramped
// next to them. h-auto overrides TableHead's fixed h-10 so py-3 actually
// has room to take effect instead of being clipped.
const HEAD_CELL = "h-auto px-4 py-3";
const BODY_CELL = "px-4 py-3";

const STATUS_COLORS = {
  ok: "bg-green-100 text-green-700 border-green-200",
  error: "bg-red-100 text-red-700 border-red-200",
  partial: "bg-yellow-100 text-yellow-700 border-yellow-200",
};

const QUOTA_COLORS = {
  ok: "bg-green-100 text-green-700 border-green-200",
  exhausted: "bg-red-100 text-red-700 border-red-200",
  unknown: "bg-gray-100 text-gray-500 border-gray-200",
};

function formatDateTime(dateStr) {
  try {
    const d = new Date(dateStr);
    return d.toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

function formatCost(v) {
  if (v === null || v === undefined) return "—";
  return `$${Number(v).toFixed(4)}`;
}

function formatTokens(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString();
}

function StatusBadge({ status, httpStatus, errorMessage }) {
  return (
    <Badge
      variant="outline"
      className={`text-[10px] h-4 px-1.5 ${STATUS_COLORS[status] || "bg-gray-100 text-gray-600 border-gray-200"}`}
      title={errorMessage || undefined}
    >
      {status === "error" && httpStatus ? httpStatus : status}
    </Badge>
  );
}

// Ported from app/dashboard/page.jsx's StatCard — hover lift, diagonal sheen
// sweep, border/shadow shift. Duplicated rather than imported: StatCard is
// a local, unexported component there (and that file is mid-redesign in
// this working tree), so this keeps the two pages decoupled while matching
// dashboard's metric-tile look exactly, per request.
function StatCard({ label, value, sub, accent }) {
  return (
    <div className="group/stat relative overflow-hidden rounded-xl border border-gray-200 bg-white px-6 py-5 transition-all duration-500 [transition-timing-function:cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-1 hover:border-gray-300 hover:shadow-lg motion-reduce:transition-none motion-reduce:hover:translate-y-0">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover/stat:opacity-100 motion-reduce:transition-none"
        style={{
          background:
            "linear-gradient(120deg, transparent 40%, rgba(255,255,255,0.8) 50%, transparent 60%)",
        }}
      />
      <div className="relative">
        <p className="m-0 mb-1 text-xs font-medium uppercase tracking-[0.06em] text-gray-500">
          {label}
        </p>
        <p
          className="m-0 mb-1 text-[28px] font-semibold tracking-[-0.02em]"
          style={{ color: accent || "#111827" }}
        >
          {value ?? "—"}
        </p>
        {sub && <p className="m-0 text-xs text-gray-500">{sub}</p>}
      </div>
    </div>
  );
}

function KeyLimitEditor({ keyRow, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [limitType, setLimitType] = useState(keyRow.limit_type || "requests_per_day");
  const [limitValue, setLimitValue] = useState(keyRow.limit_value ?? "");
  const [error, setError] = useState("");

  const saveMutation = useMutation({
    mutationFn: () =>
      apiPut(`/api/ai-usage/keys/${encodeURIComponent(keyRow.key_label)}/limit`, {
        limit_type: limitType,
        limit_value: Number(limitValue),
      }),
    onSuccess: () => {
      setEditing(false);
      setError("");
      onSaved();
      toastSuccess("Limit saved");
    },
    onError: (e) => setError(e.message),
  });

  const clearMutation = useMutation({
    mutationFn: () => apiDelete(`/api/ai-usage/keys/${encodeURIComponent(keyRow.key_label)}/limit`),
    onSuccess: () => {
      setEditing(false);
      onSaved();
      toastSuccess("Limit cleared");
    },
  });

  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" className="h-6 text-[11px] px-2" onClick={() => setEditing(true)}>
          {keyRow.limit_source === "manual" ? "Edit limit" : "Set limit"}
        </Button>
        {keyRow.limit_source === "manual" && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-[11px] px-2"
            disabled={clearMutation.isPending}
            onClick={() => clearMutation.mutate()}
          >
            Clear
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5">
        <Select value={limitType} onValueChange={setLimitType}>
          <SelectTrigger className="h-7 text-[11px] w-[130px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="requests_per_day">Requests / day</SelectItem>
            <SelectItem value="budget_usd">Budget ($)</SelectItem>
          </SelectContent>
        </Select>
        <Input
          type="number"
          min="0"
          step="any"
          value={limitValue}
          onChange={(e) => setLimitValue(e.target.value)}
          className="h-7 text-[11px] w-[80px]"
        />
        <Button
          size="sm"
          className="h-7 text-[11px] px-2"
          disabled={saveMutation.isPending || !limitValue}
          onClick={() => saveMutation.mutate()}
        >
          Save
        </Button>
        <Button variant="outline" size="sm" className="h-7 text-[11px] px-2" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
      {error && <p className="text-[11px] text-red-600">{error}</p>}
    </div>
  );
}

// ── Custom period picker for Per-key usage ──────────────────────────────────
//
// A purpose-built popover + calendar, not the browser's native <input
// type="date"> used by the Events filters below — deliberately a different
// control for a different job: picking a whole month (or a custom range)
// to inspect historical usage, with quick presets for the common cases.

const PERIOD_PRESETS = [
  { key: "this_month", label: "This month" },
  { key: "last_month", label: "Last month" },
  { key: "last_3_months", label: "Last 3 months" },
  { key: "all_time", label: "All time" },
];

// start/end are Date objects in "local" terms, both INCLUSIVE — end is the
// last day whose data should count, matching the existing to_date
// convention in this same page's Events filter (see buildCallsParams) and
// the /keys endpoint's period_end handling (it adds one day itself to turn
// an inclusive end date into an exclusive SQL upper bound). start:null
// means "let the backend's own current-calendar-month default decide"
// (see ai_usage.compute_key_usage) rather than duplicating that boundary
// calculation here and risking the two drifting apart at a month edge.
function periodFromPreset(key) {
  const now = new Date();
  switch (key) {
    case "last_month": {
      const lm = subMonths(now, 1);
      return { preset: "last_month", start: startOfMonth(lm), end: endOfMonth(lm), label: format(lm, "MMMM yyyy") };
    }
    case "last_3_months": {
      const start = startOfMonth(subMonths(now, 2));
      return { preset: "last_3_months", start, end: null, label: "Last 3 months" };
    }
    case "all_time":
      return { preset: "all_time", start: new Date(2000, 0, 1), end: null, label: "All time" };
    case "this_month":
    default:
      return { preset: "this_month", start: null, end: null, label: "This month" };
  }
}

function periodToParams(period) {
  const params = {};
  if (period.start) params.period_start = format(period.start, "yyyy-MM-dd");
  // period_end is exclusive on the backend — the picker's `end` is the
  // last INCLUDED day, so bump it forward one day when both are set (an
  // open-ended preset/range has end:null and needs no upper bound at all).
  if (period.end) params.period_end = format(period.end, "yyyy-MM-dd");
  return params;
}

function CalendarMonth({ viewMonth, rangeStart, rangeEnd, hoverEnd, onPickDay, onHoverDay }) {
  const monthStart = startOfMonth(viewMonth);
  const monthEnd = endOfMonth(viewMonth);
  const gridStart = startOfWeek(monthStart, { weekStartsOn: 0 });
  const gridEnd = endOfWeek(monthEnd, { weekStartsOn: 0 });
  const days = eachDayOfInterval({ start: gridStart, end: gridEnd });
  const today = startOfDay(new Date());
  const previewEnd = rangeStart && !rangeEnd ? hoverEnd : rangeEnd;

  return (
    <div>
      <div className="grid grid-cols-7 gap-y-1 text-center text-[10px] font-medium uppercase text-gray-400 mb-1">
        {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((d) => (
          <span key={d}>{d}</span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-y-1">
        {days.map((day) => {
          const inMonth = isSameMonth(day, viewMonth);
          const isStart = rangeStart && isSameDay(day, rangeStart);
          const isEnd = previewEnd && isSameDay(day, previewEnd);
          const inRange =
            rangeStart &&
            previewEnd &&
            !isBefore(previewEnd, rangeStart) &&
            isWithinInterval(day, { start: rangeStart, end: previewEnd });
          return (
            <button
              key={day.toISOString()}
              type="button"
              disabled={isAfter(day, today)}
              onClick={() => onPickDay(day)}
              onMouseEnter={() => onHoverDay?.(day)}
              className={[
                "relative h-7 w-7 mx-auto text-[11px] rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-30",
                !inMonth && "text-gray-300",
                inMonth && !isStart && !isEnd && "text-gray-700 hover:bg-gray-100",
                inRange && !isStart && !isEnd ? "bg-blue-50" : "",
                (isStart || isEnd) && "bg-blue-600 text-white font-semibold hover:bg-blue-600",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              {format(day, "d")}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function PeriodRangePicker({ period, onChange }) {
  const [open, setOpen] = useState(false);
  const [viewMonth, setViewMonth] = useState(() => period.start || new Date());
  const [pendingStart, setPendingStart] = useState(null);
  const [hoverDay, setHoverDay] = useState(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
        setPendingStart(null);
        setHoverDay(null);
      }
    }
    function onKeyDown(e) {
      if (e.key === "Escape") {
        setOpen(false);
        setPendingStart(null);
        setHoverDay(null);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function applyPreset(key) {
    onChange(periodFromPreset(key));
    setPendingStart(null);
    setOpen(false);
  }

  function pickDay(day) {
    if (!pendingStart) {
      setPendingStart(day);
      return;
    }
    let start = pendingStart;
    let end = day;
    if (isBefore(end, start)) [start, end] = [end, start];
    onChange({
      preset: "custom",
      start,
      end,
      label: isSameDay(start, end) ? format(start, "d MMM yyyy") : `${format(start, "d MMM")} – ${format(end, "d MMM yyyy")}`,
    });
    setPendingStart(null);
    setOpen(false);
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md border border-gray-200 bg-white text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50 transition-colors"
      >
        <CalendarDays className="size-3.5 text-gray-400" />
        {period.label}
        <ChevronDown className="size-3 text-gray-400" />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1.5 flex overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg">
          <div className="w-36 border-r border-gray-100 p-2">
            {PERIOD_PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => applyPreset(p.key)}
                className={`w-full rounded-md px-2.5 py-1.5 text-left text-xs transition-colors ${
                  period.preset === p.key ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-50"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="p-3 w-[240px]">
            <div className="flex items-center justify-between mb-2">
              <button
                type="button"
                onClick={() => setViewMonth((m) => subMonths(m, 1))}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <ChevronLeft className="size-3.5" />
              </button>
              <span className="text-xs font-medium text-gray-900">{format(viewMonth, "MMMM yyyy")}</span>
              <button
                type="button"
                onClick={() => setViewMonth((m) => addMonths(m, 1))}
                disabled={isSameMonth(viewMonth, new Date())}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-30 disabled:hover:bg-transparent"
              >
                <ChevronRight className="size-3.5" />
              </button>
            </div>
            <CalendarMonth
              viewMonth={viewMonth}
              // While a start is pending, highlight it live against the
              // hovered day; once a full custom range is committed,
              // reopening the picker re-highlights that saved range.
              rangeStart={pendingStart || (period.preset === "custom" ? period.start : null)}
              rangeEnd={period.preset === "custom" && !pendingStart ? period.end : null}
              hoverEnd={hoverDay}
              onPickDay={pickDay}
              onHoverDay={setHoverDay}
            />
            <p className="mt-2 text-[10px] text-gray-400">
              {pendingStart ? "Pick an end date" : "Pick a start date, or use a quick range on the left"}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function KeyUsageCardSkeleton() {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

function KeyUsageCard({ keys, isLoading, period, onPeriodChange, onChanged }) {
  const sortedKeys = keys
    ? [...keys].sort(
        (a, b) => (PROVIDER_SORT_PRIORITY[a.provider] ?? 99) - (PROVIDER_SORT_PRIORITY[b.provider] ?? 99)
      )
    : keys;
  const totalCostForPeriod = (keys || []).reduce((sum, k) => sum + (k.cost_period_usd || 0), 0);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="text-sm font-semibold">Per-key usage</CardTitle>
          <p className="text-[11px] text-gray-400 mt-1">
            Total cost for {period.label.toLowerCase()}:{" "}
            <span className="font-medium text-gray-600">
              {isLoading ? "…" : formatCost(totalCostForPeriod)}
            </span>
          </p>
        </div>
        <PeriodRangePicker period={period} onChange={onPeriodChange} />
      </CardHeader>
      <Separator />
      {isLoading ? (
        <KeyUsageCardSkeleton />
      ) : !sortedKeys?.length ? (
        <div className="p-8 text-center text-sm text-gray-400">No API keys configured</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className={HEAD_CELL}>Provider</TableHead>
              <TableHead className={HEAD_CELL}>Used this period</TableHead>
              <TableHead className={`${HEAD_CELL} text-right`}>Input tokens</TableHead>
              <TableHead className={`${HEAD_CELL} text-right`}>Output tokens</TableHead>
              <TableHead className={HEAD_CELL}>Limit</TableHead>
              <TableHead className={HEAD_CELL}>Remaining</TableHead>
              <TableHead className={HEAD_CELL}>Status</TableHead>
              <TableHead className={HEAD_CELL}>Resets</TableHead>
              <TableHead className={HEAD_CELL}>Manage</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedKeys.map((k) => (
              <TableRow key={k.key_label}>
                <TableCell className={`${BODY_CELL} text-xs text-gray-600 capitalize`}>{k.provider}</TableCell>
                <TableCell className={`${BODY_CELL} text-xs text-gray-700`}>
                  {k.calls_period.toLocaleString()} call{k.calls_period === 1 ? "" : "s"}
                </TableCell>
                <TableCell className={`${BODY_CELL} text-xs text-gray-700 text-right`}>
                  {formatTokens(k.prompt_tokens_period)}
                </TableCell>
                <TableCell className={`${BODY_CELL} text-xs text-gray-700 text-right`}>
                  {formatTokens(k.completion_tokens_period)}
                </TableCell>
                <TableCell className={`${BODY_CELL} text-xs text-gray-500`}>
                  {k.limit_value === null || k.limit_value === undefined
                    ? "no fixed limit"
                    : k.limit_type === "budget_usd"
                    ? `$${Number(k.limit_value).toFixed(2)}`
                    : `${k.limit_value}/day`}
                  {k.limit_source === "auto" && (
                    <span className="text-gray-400"> (auto)</span>
                  )}
                </TableCell>
                <TableCell className={`${BODY_CELL} text-xs text-gray-700`}>
                  {k.remaining === null || k.remaining === undefined
                    ? "—"
                    : k.limit_type === "budget_usd"
                    ? `$${Number(k.remaining).toFixed(2)}`
                    : k.remaining}
                </TableCell>
                <TableCell className={BODY_CELL}>
                  <Badge
                    variant="outline"
                    className={`text-[10px] h-4 px-1.5 ${QUOTA_COLORS[k.quota_status] || QUOTA_COLORS.unknown}`}
                  >
                    {k.quota_status}
                  </Badge>
                </TableCell>
                <TableCell className={`${BODY_CELL} text-[11px] text-gray-400`}>{k.resets_at || "—"}</TableCell>
                <TableCell className={BODY_CELL}>
                  <KeyLimitEditor keyRow={k} onSaved={onChanged} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

// ── Usage events, grouped by the task that made them ────────────────────────
//
// Every AI call is grouped under the one user-triggered job that caused it
// (a Vibe Test run, a SOW import, a Visual Audit, ...) rather than shown as
// a flat, undifferentiated list — see app.services.ai_usage's "Task
// grouping" section on the backend. A group is aggregates only; its
// individual calls are fetched on demand (react-query, cached) the first
// time its row is expanded, so this page stays fast with thousands of calls.

const TASK_ROW_GRID = "grid-cols-[24px_1fr_120px_70px_90px_90px_90px_90px]";

function groupKey(g) {
  return g.is_legacy ? `legacy:${g.legacy_source}` : `${g.run_type}:${g.run_id}`;
}

function buildCallsParams(group, filters) {
  const params = new URLSearchParams();
  if (filters.statusFilter) params.set("status", filters.statusFilter);
  if (filters.fromDate) params.set("from_date", filters.fromDate);
  if (filters.toDate) params.set("to_date", filters.toDate);
  if (group.is_legacy) {
    params.set("no_task", "true");
    params.set("source", group.legacy_source);
  } else {
    params.set("run_type", group.run_type);
    params.set("run_id", group.run_id);
    if (filters.sourceFilter) params.set("source", filters.sourceFilter);
  }
  if (filters.providerFilter) params.set("provider", filters.providerFilter);
  params.set("limit", "200");
  return params;
}

function TaskCallsList({ group, filters }) {
  const params = buildCallsParams(group, filters);
  const { data, isLoading, error } = useQuery({
    queryKey: ["ai-usage-task-calls", groupKey(group), params.toString()],
    queryFn: () => apiGet(`/api/ai-usage?${params.toString()}`),
    staleTime: 1000 * 20,
  });

  if (isLoading) {
    return (
      <div className="space-y-2 py-3">
        {Array.from({ length: Math.min(3, group.call_count) }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-full" />
        ))}
      </div>
    );
  }
  if (error) {
    return <p className="text-xs text-red-600 py-3">Failed to load calls: {error.message}</p>;
  }

  const rows = data?.data || [];
  const total = data?.total ?? rows.length;

  return (
    <div className="pb-3">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className={HEAD_CELL}>Time</TableHead>
            <TableHead className={HEAD_CELL}>Source</TableHead>
            <TableHead className={HEAD_CELL}>Provider</TableHead>
            <TableHead className={HEAD_CELL}>Model</TableHead>
            <TableHead className={`${HEAD_CELL} text-right`}>Input</TableHead>
            <TableHead className={`${HEAD_CELL} text-right`}>Output</TableHead>
            <TableHead className={`${HEAD_CELL} text-right`}>Cost</TableHead>
            <TableHead className={HEAD_CELL}>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((e) => (
            <TableRow key={e.id}>
              <TableCell className={`${BODY_CELL} text-xs text-gray-500 whitespace-nowrap`}>
                {formatDateTime(e.created_at)}
              </TableCell>
              <TableCell className={`${BODY_CELL} text-xs text-gray-700`}>{e.source}</TableCell>
              <TableCell className={`${BODY_CELL} text-xs text-gray-700 capitalize`}>{e.provider}</TableCell>
              <TableCell className={`${BODY_CELL} text-xs text-gray-600 max-w-[160px] truncate`}>
                {e.model}
              </TableCell>
              <TableCell className={`${BODY_CELL} text-xs text-gray-700 text-right`}>
                {formatTokens(e.prompt_tokens)}
              </TableCell>
              <TableCell className={`${BODY_CELL} text-xs text-gray-700 text-right`}>
                {formatTokens(e.completion_tokens)}
              </TableCell>
              <TableCell className={`${BODY_CELL} text-xs text-gray-500 text-right`}>
                {formatCost(e.cost_usd)}
              </TableCell>
              <TableCell className={BODY_CELL}>
                <StatusBadge status={e.status} httpStatus={e.http_status} errorMessage={e.error_message} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {total > rows.length && (
        <p className="text-[11px] text-gray-400 px-4 pt-2">
          Showing the first {rows.length.toLocaleString()} of {total.toLocaleString()} calls in this task.
        </p>
      )}
    </div>
  );
}

function TaskGroupRow({ group, filters, expanded, onToggle }) {
  return (
    <div className="border-b last:border-0">
      <button
        type="button"
        onClick={onToggle}
        className={`w-full grid ${TASK_ROW_GRID} items-center gap-3 px-4 py-3 text-left hover:bg-muted/50 transition-colors`}
      >
        {expanded ? (
          <ChevronDown className="size-3.5 text-gray-400" />
        ) : (
          <ChevronRight className="size-3.5 text-gray-400" />
        )}
        <div className="min-w-0">
          <p className="text-xs font-medium text-gray-900 truncate">{group.label}</p>
          <p className="text-[10px] text-gray-400 truncate">
            {group.sources.join(", ") || "—"} · last call {formatDateTime(group.last_seen)}
          </p>
        </div>
        <Badge variant="outline" className="text-[10px] h-4 px-1.5 justify-self-start">
          {group.task_kind_label}
        </Badge>
        <span className="text-xs text-gray-700 text-right">
          {group.call_count.toLocaleString()} call{group.call_count === 1 ? "" : "s"}
        </span>
        <span className="text-xs text-gray-700 text-right">{formatTokens(group.prompt_tokens)} in</span>
        <span className="text-xs text-gray-700 text-right">{formatTokens(group.completion_tokens)} out</span>
        <span className="text-xs text-gray-500 text-right">{formatCost(group.cost_usd)}</span>
        <span className="justify-self-end">
          <StatusBadge status={group.status} />
        </span>
      </button>
      {expanded && (
        <div className="bg-muted/20 border-t">
          <TaskCallsList group={group} filters={filters} />
        </div>
      )}
    </div>
  );
}

function TaskGroupsSkeleton() {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-4 w-4" />
          <Skeleton className="h-4 w-64" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-14" />
        </div>
      ))}
    </div>
  );
}

export default function AIUsagePage() {
  const [user, setUser] = useState(null);
  const [providerFilter, setProviderFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [taskPage, setTaskPage] = useState(1);
  const [expandedTasks, setExpandedTasks] = useState(() => new Set());
  const [keyPeriod, setKeyPeriod] = useState(() => periodFromPreset("this_month"));
  const taskLimit = 20;
  const qc = useQueryClient();

  useEffect(() => {
    setUser(getStoredUser());
  }, []);

  const { data: summary, isLoading: summaryLoading, refetch: refetchSummary } = useQuery({
    queryKey: ["ai-usage-summary"],
    queryFn: () => apiGet("/api/ai-usage/summary?window_days=30"),
    staleTime: 1000 * 30,
  });

  // "By provider" is deliberately NOT scoped to the 30-day window above —
  // it's a running cumulative total (window_days=0 means all-time; see
  // usage_summary's own docstring), independent of whatever lookback the
  // summary cards happen to use.
  const { data: allTimeSummary, isLoading: allTimeSummaryLoading, refetch: refetchAllTimeSummary } = useQuery({
    queryKey: ["ai-usage-summary-all-time"],
    queryFn: () => apiGet("/api/ai-usage/summary?window_days=0"),
    staleTime: 1000 * 30,
  });

  const keyPeriodParams = new URLSearchParams(periodToParams(keyPeriod));
  const { data: keyUsage, isLoading: keysLoading, refetch: refetchKeys } = useQuery({
    queryKey: ["ai-usage-keys", keyPeriodParams.toString()],
    queryFn: () => apiGet(`/api/ai-usage/keys${keyPeriodParams.toString() ? `?${keyPeriodParams}` : ""}`),
    staleTime: 1000 * 30,
  });

  const filters = { providerFilter, sourceFilter, statusFilter, fromDate, toDate };

  const taskParams = new URLSearchParams();
  if (providerFilter) taskParams.set("provider", providerFilter);
  if (sourceFilter) taskParams.set("source", sourceFilter);
  if (statusFilter) taskParams.set("status", statusFilter);
  if (fromDate) taskParams.set("from_date", fromDate);
  if (toDate) taskParams.set("to_date", toDate);
  taskParams.set("limit", String(taskLimit));
  taskParams.set("offset", String((taskPage - 1) * taskLimit));

  const {
    data: taskGroups,
    isLoading: tasksLoading,
    error: tasksError,
    isFetching: tasksFetching,
  } = useQuery({
    queryKey: ["ai-usage-tasks", providerFilter, sourceFilter, statusFilter, fromDate, toDate, taskPage],
    queryFn: () => apiGet(`/api/ai-usage/tasks?${taskParams.toString()}`),
    staleTime: 1000 * 20,
  });

  // App-wide overlay for the FIRST load only, across all four of this
  // page's independent queries. Deliberately NOT "isLoading of whichever
  // query is currently fetching" — after the first paint, a period-picker
  // or filter change re-triggers isLoading for just ONE of these four
  // (e.g. picking "Last month" only reloads keyUsage), and that should
  // fall through to that section's own skeleton (KeyUsageCardSkeleton,
  // TaskGroupsSkeleton, ...) rather than blanking the whole page — sections
  // the admin isn't even looking at (e.g. the task list) shouldn't
  // disappear because the Per-key usage filter changed.
  const anyInitialLoading = summaryLoading || allTimeSummaryLoading || keysLoading || tasksLoading;
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  useEffect(() => {
    if (!anyInitialLoading && !hasLoadedOnce) setHasLoadedOnce(true);
  }, [anyInitialLoading, hasLoadedOnce]);
  const showGlobalLoader = !hasLoadedOnce && anyInitialLoading;
  usePageLoading(showGlobalLoader);

  const refreshAll = () => {
    refetchSummary();
    refetchAllTimeSummary();
    refetchKeys();
    qc.invalidateQueries(["ai-usage-tasks"]);
    qc.invalidateQueries(["ai-usage-task-calls"]);
  };

  const onKeyChanged = () => {
    qc.invalidateQueries(["ai-usage-keys"]);
  };

  // Any filter/page change invalidates which calls belong under which
  // group's expansion, so collapse everything rather than risk a stale
  // calls list under a still-expanded row.
  const resetAndSetPage = (setter) => (value) => {
    setter(value);
    setTaskPage(1);
    setExpandedTasks(new Set());
  };

  const toggleTask = (key) => {
    setExpandedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (!user || showGlobalLoader) return null;

  const taskTotal = taskGroups?.total || 0;
  const taskRows = taskGroups?.data || [];
  const taskTotalPages = Math.max(1, Math.ceil(taskTotal / taskLimit));

  return (
    <AppShell noPadding>
      <PageContainer>
        <div className="max-w-[1400px] space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight">AI Usage</h1>
              <p className="text-sm text-gray-500 mt-1">
                API calls, tokens, and cost across every provider and key — last 30 days
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={refreshAll}>
              Refresh
            </Button>
          </div>

          {/* Summary cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {summaryLoading ? (
              Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[104px] rounded-xl" />)
            ) : (
              <>
                <StatCard label="Calls (30d)" value={(summary?.total_calls ?? 0).toLocaleString()} />
                <StatCard
                  label="Tokens (30d)"
                  value={formatTokens(summary?.total_tokens)}
                  sub={`Input ${formatTokens(summary?.total_prompt_tokens)} · Output ${formatTokens(summary?.total_completion_tokens)}`}
                />
                <StatCard label="Est. cost (30d)" value={formatCost(summary?.total_cost_usd)} />
                <StatCard
                  label="Failed calls"
                  value={summary?.failed_calls ?? 0}
                  accent={(summary?.failed_calls ?? 0) > 0 ? "#DC2626" : undefined}
                />
              </>
            )}
          </div>

          {/* Provider breakdown — all-time, independent of the 30d window above */}
          {allTimeSummary?.by_provider?.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">By provider</CardTitle>
                <p className="text-[11px] text-gray-400">Total usage to date, not limited to the last 30 days</p>
              </CardHeader>
              <Separator />
              <CardContent className="pt-4 space-y-2">
                <div className="grid grid-cols-[90px_1fr_75px_75px_60px_80px] items-center gap-3 text-[10px] text-gray-400 uppercase tracking-wide">
                  <span>Provider</span>
                  <span></span>
                  <span className="text-right">Input</span>
                  <span className="text-right">Output</span>
                  <span className="text-right">Calls</span>
                  <span className="text-right">Cost</span>
                </div>
                {allTimeSummary.by_provider.map((p) => {
                  const maxCalls = Math.max(...allTimeSummary.by_provider.map((x) => x.calls), 1);
                  return (
                    <div key={p.provider} className="grid grid-cols-[90px_1fr_75px_75px_60px_80px] items-center gap-3 text-xs">
                      <span className="text-gray-600 capitalize">{p.provider}</span>
                      <div className="bg-gray-100 rounded h-1.5 overflow-hidden">
                        <div
                          className="bg-blue-500 h-full"
                          style={{ width: `${Math.max(4, (p.calls / maxCalls) * 100)}%` }}
                        />
                      </div>
                      <span className="text-right text-gray-700">{formatTokens(p.prompt_tokens)}</span>
                      <span className="text-right text-gray-700">{formatTokens(p.completion_tokens)}</span>
                      <span className="text-right text-gray-700">{p.calls.toLocaleString()}</span>
                      <span className="text-right text-gray-500">{formatCost(p.cost_usd)}</span>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          )}

          {/* Per-key usage */}
          <KeyUsageCard
            keys={keyUsage}
            isLoading={keysLoading}
            period={keyPeriod}
            onPeriodChange={setKeyPeriod}
            onChanged={onKeyChanged}
          />

          {/* Filters */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Filters</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="pt-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">Provider</label>
                  <Select
                    value={providerFilter || NO_FILTER}
                    onValueChange={resetAndSetPage((v) => setProviderFilter((v ?? "") === NO_FILTER ? "" : v ?? ""))}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="All providers">
                        {(value) =>
                          !value || value === NO_FILTER ? "All providers" : value
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_FILTER}>All providers</SelectItem>
                      {PROVIDER_OPTIONS.map((p) => (
                        <SelectItem key={p} value={p}>
                          {p}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">Source</label>
                  <Select
                    value={sourceFilter || NO_FILTER}
                    onValueChange={resetAndSetPage((v) => setSourceFilter((v ?? "") === NO_FILTER ? "" : v ?? ""))}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="All sources">
                        {(value) =>
                          !value || value === NO_FILTER ? "All sources" : value
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_FILTER}>All sources</SelectItem>
                      {SOURCE_OPTIONS.map((s) => (
                        <SelectItem key={s} value={s}>
                          {s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">Status</label>
                  <Select
                    value={statusFilter || NO_FILTER}
                    onValueChange={resetAndSetPage((v) => setStatusFilter((v ?? "") === NO_FILTER ? "" : v ?? ""))}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="All statuses">
                        {(value) =>
                          !value || value === NO_FILTER ? "All statuses" : value
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_FILTER}>All statuses</SelectItem>
                      <SelectItem value="ok">ok</SelectItem>
                      <SelectItem value="error">error</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">From date</label>
                  <Input
                    type="date"
                    value={fromDate}
                    onChange={(e) => resetAndSetPage(setFromDate)(e.target.value)}
                    className="text-sm"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">To date</label>
                  <Input
                    type="date"
                    value={toDate}
                    onChange={(e) => resetAndSetPage(setToDate)(e.target.value)}
                    className="text-sm"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {tasksError && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4">
              <p className="text-sm text-red-600 font-medium">
                Failed to load usage events: {tasksError.message}
              </p>
            </div>
          )}

          {/* Usage events, grouped by task */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">
                Usage events by task {taskGroups ? `(${taskTotal.toLocaleString()} task${taskTotal === 1 ? "" : "s"})` : ""}
              </CardTitle>
            </CardHeader>
            <Separator />
            {!tasksLoading && taskRows.length > 0 && (
              <div
                className={`grid ${TASK_ROW_GRID} items-center gap-3 px-4 py-2 border-b bg-muted/30 text-[10px] font-medium uppercase tracking-wide text-gray-400`}
              >
                <span></span>
                <span>Task</span>
                <span>Type</span>
                <span className="text-right">Calls</span>
                <span className="text-right">Input</span>
                <span className="text-right">Output</span>
                <span className="text-right">Cost</span>
                <span className="justify-self-end">Status</span>
              </div>
            )}
            {tasksLoading ? (
              <TaskGroupsSkeleton />
            ) : !taskRows.length ? (
              <div className="p-8 text-center text-sm text-gray-400">No usage events found</div>
            ) : (
              <div>
                {taskRows.map((g) => {
                  const key = groupKey(g);
                  return (
                    <TaskGroupRow
                      key={key}
                      group={g}
                      filters={filters}
                      expanded={expandedTasks.has(key)}
                      onToggle={() => toggleTask(key)}
                    />
                  );
                })}
              </div>
            )}

            {taskTotal > taskLimit && (
              <div className="flex items-center justify-between px-5 py-3 border-t">
                <p className="text-xs text-gray-500">
                  {tasksFetching && <span className="inline-block size-3 mr-1.5 align-[-1px] animate-spin rounded-full border-2 border-gray-300 border-t-gray-500" />}
                  Showing {(taskPage - 1) * taskLimit + 1}–{Math.min(taskPage * taskLimit, taskTotal)} of {taskTotal} tasks
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setTaskPage((p) => Math.max(1, p - 1));
                      setExpandedTasks(new Set());
                    }}
                    disabled={taskPage === 1}
                  >
                    Previous
                  </Button>
                  <span className="text-xs text-gray-500">
                    Page {taskPage} of {taskTotalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setTaskPage((p) => Math.min(taskTotalPages, p + 1));
                      setExpandedTasks(new Set());
                    }}
                    disabled={taskPage >= taskTotalPages}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </div>
      </PageContainer>
    </AppShell>
  );
}
