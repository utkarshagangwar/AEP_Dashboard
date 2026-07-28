"use client";
import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import PageContainer from "../../../components/PageContainer";
import { apiGet, apiPut, apiDelete } from "../../../utils/apiClient";
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

const STATUS_COLORS = {
  ok: "bg-green-100 text-green-700 border-green-200",
  error: "bg-red-100 text-red-700 border-red-200",
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

function MetricCard({ label, value, danger }) {
  return (
    <div style={{ background: "var(--muted, #F9FAFB)" }} className="bg-gray-50 rounded-xl p-4">
      <p className="text-xs text-gray-500 mb-1.5">{label}</p>
      <p className={`text-2xl font-semibold ${danger ? "text-red-600" : "text-gray-900"}`}>
        {value}
      </p>
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
    },
    onError: (e) => setError(e.message),
  });

  const clearMutation = useMutation({
    mutationFn: () => apiDelete(`/api/ai-usage/keys/${encodeURIComponent(keyRow.key_label)}/limit`),
    onSuccess: () => {
      setEditing(false);
      onSaved();
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

function KeyUsageCard({ keys, onChanged }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-semibold">Per-key usage</CardTitle>
      </CardHeader>
      <Separator />
      {!keys?.length ? (
        <div className="p-8 text-center text-sm text-gray-400">No API keys configured</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-6">Provider</TableHead>
              <TableHead>Used this period</TableHead>
              <TableHead>Tokens</TableHead>
              <TableHead>Limit</TableHead>
              <TableHead>Remaining</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Resets</TableHead>
              <TableHead>Manage</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((k) => (
              <TableRow key={k.key_label}>
                <TableCell className="pl-6 text-xs text-gray-600 capitalize">{k.provider}</TableCell>
                <TableCell className="text-xs text-gray-700">
                  {k.limit_type === "budget_usd"
                    ? formatCost(k.cost_period_usd)
                    : `${k.calls_period} call${k.calls_period === 1 ? "" : "s"}`}
                </TableCell>
                <TableCell className="text-xs text-gray-700">{formatTokens(k.tokens_period)}</TableCell>
                <TableCell className="text-xs text-gray-500">
                  {k.limit_value === null || k.limit_value === undefined
                    ? "no fixed limit"
                    : k.limit_type === "budget_usd"
                    ? `$${Number(k.limit_value).toFixed(2)}`
                    : `${k.limit_value}/day`}
                  {k.limit_source === "auto" && (
                    <span className="text-gray-400"> (auto)</span>
                  )}
                </TableCell>
                <TableCell className="text-xs text-gray-700">
                  {k.remaining === null || k.remaining === undefined
                    ? "—"
                    : k.limit_type === "budget_usd"
                    ? `$${Number(k.remaining).toFixed(2)}`
                    : k.remaining}
                </TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={`text-[10px] h-4 px-1.5 ${QUOTA_COLORS[k.quota_status] || QUOTA_COLORS.unknown}`}
                  >
                    {k.quota_status}
                  </Badge>
                </TableCell>
                <TableCell className="text-[11px] text-gray-400">{k.resets_at || "—"}</TableCell>
                <TableCell>
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

function EventsTableSkeleton() {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-20" />
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
  const [page, setPage] = useState(1);
  const limit = 50;
  const qc = useQueryClient();

  useEffect(() => {
    setUser(getStoredUser());
  }, []);

  const { data: summary, isLoading: summaryLoading, refetch: refetchSummary } = useQuery({
    queryKey: ["ai-usage-summary"],
    queryFn: () => apiGet("/api/ai-usage/summary?window_days=30"),
    staleTime: 1000 * 30,
  });

  const { data: keyUsage, refetch: refetchKeys } = useQuery({
    queryKey: ["ai-usage-keys"],
    queryFn: () => apiGet("/api/ai-usage/keys"),
    staleTime: 1000 * 30,
  });

  const params = new URLSearchParams();
  if (providerFilter) params.set("provider", providerFilter);
  if (sourceFilter) params.set("source", sourceFilter);
  if (statusFilter) params.set("status", statusFilter);
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  params.set("limit", String(limit));
  params.set("offset", String((page - 1) * limit));

  const {
    data: events,
    isLoading: eventsLoading,
    error: eventsError,
    isFetching: eventsFetching,
  } = useQuery({
    queryKey: ["ai-usage-events", providerFilter, sourceFilter, statusFilter, fromDate, toDate, page],
    queryFn: () => apiGet(`/api/ai-usage?${params.toString()}`),
    staleTime: 1000 * 20,
  });

  const refreshAll = () => {
    refetchSummary();
    refetchKeys();
    qc.invalidateQueries(["ai-usage-events"]);
  };

  const onKeyChanged = () => {
    qc.invalidateQueries(["ai-usage-keys"]);
  };

  if (!user) return null;

  const total = events?.total || 0;
  const rows = events?.data || [];
  const totalPages = Math.max(1, Math.ceil(total / limit));

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
              Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[74px] rounded-xl" />)
            ) : (
              <>
                <MetricCard label="Calls (30d)" value={(summary?.total_calls ?? 0).toLocaleString()} />
                <MetricCard label="Tokens (30d)" value={formatTokens(summary?.total_tokens)} />
                <MetricCard label="Est. cost (30d)" value={formatCost(summary?.total_cost_usd)} />
                <MetricCard label="Failed calls" value={summary?.failed_calls ?? 0} danger={(summary?.failed_calls ?? 0) > 0} />
              </>
            )}
          </div>

          {/* Provider breakdown */}
          {summary?.by_provider?.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">By provider</CardTitle>
              </CardHeader>
              <Separator />
              <CardContent className="pt-4 space-y-2">
                {summary.by_provider.map((p) => {
                  const maxCalls = Math.max(...summary.by_provider.map((x) => x.calls), 1);
                  return (
                    <div key={p.provider} className="grid grid-cols-[90px_1fr_70px_80px] items-center gap-3 text-xs">
                      <span className="text-gray-600 capitalize">{p.provider}</span>
                      <div className="bg-gray-100 rounded h-1.5 overflow-hidden">
                        <div
                          className="bg-blue-500 h-full"
                          style={{ width: `${Math.max(4, (p.calls / maxCalls) * 100)}%` }}
                        />
                      </div>
                      <span className="text-right text-gray-700">{p.calls.toLocaleString()}</span>
                      <span className="text-right text-gray-500">{formatCost(p.cost_usd)}</span>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          )}

          {/* Per-key usage */}
          <KeyUsageCard keys={keyUsage} onChanged={onKeyChanged} />

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
                    onValueChange={(v) => {
                      setProviderFilter((v ?? "") === NO_FILTER ? "" : v ?? "");
                      setPage(1);
                    }}
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
                    onValueChange={(v) => {
                      setSourceFilter((v ?? "") === NO_FILTER ? "" : v ?? "");
                      setPage(1);
                    }}
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
                    onValueChange={(v) => {
                      setStatusFilter((v ?? "") === NO_FILTER ? "" : v ?? "");
                      setPage(1);
                    }}
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
                    onChange={(e) => {
                      setFromDate(e.target.value);
                      setPage(1);
                    }}
                    className="text-sm"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">To date</label>
                  <Input
                    type="date"
                    value={toDate}
                    onChange={(e) => {
                      setToDate(e.target.value);
                      setPage(1);
                    }}
                    className="text-sm"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {eventsError && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4">
              <p className="text-sm text-red-600 font-medium">
                Failed to load usage events: {eventsError.message}
              </p>
            </div>
          )}

          {/* Events table */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">
                Usage events {events ? `(${events.total.toLocaleString()} total)` : ""}
              </CardTitle>
            </CardHeader>
            <Separator />
            {eventsLoading ? (
              <EventsTableSkeleton />
            ) : !rows.length ? (
              <div className="p-8 text-center text-sm text-gray-400">No usage events found</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Key</TableHead>
                    <TableHead className="text-right">Tokens</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell className="text-xs text-gray-500 whitespace-nowrap">
                        {formatDateTime(e.created_at)}
                      </TableCell>
                      <TableCell className="text-xs text-gray-700">{e.source}</TableCell>
                      <TableCell className="text-xs text-gray-700 capitalize">{e.provider}</TableCell>
                      <TableCell className="text-xs text-gray-600 max-w-[160px] truncate">{e.model}</TableCell>
                      <TableCell className="text-[10px] font-mono text-gray-400 max-w-[120px] truncate">
                        {e.key_label || "—"}
                      </TableCell>
                      <TableCell className="text-xs text-gray-700 text-right">{formatTokens(e.total_tokens)}</TableCell>
                      <TableCell className="text-xs text-gray-500 text-right">{formatCost(e.cost_usd)}</TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={`text-[10px] h-4 px-1.5 ${STATUS_COLORS[e.status] || "bg-gray-100 text-gray-600 border-gray-200"}`}
                          title={e.error_message || undefined}
                        >
                          {e.status === "error" && e.http_status ? e.http_status : e.status}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}

            {total > limit && (
              <div className="flex items-center justify-between px-5 py-3 border-t">
                <p className="text-xs text-gray-500">
                  Showing {(page - 1) * limit + 1}–{Math.min(page * limit, total)} of {total}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                  >
                    Previous
                  </Button>
                  <span className="text-xs text-gray-500">
                    Page {page} of {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
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
