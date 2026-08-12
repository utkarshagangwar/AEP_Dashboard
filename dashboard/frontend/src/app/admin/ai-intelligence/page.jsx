"use client";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  FileQuestion,
  Gauge,
  GitCompare,
  ImageOff,
  Lock,
  Palette,
  Search,
  Workflow,
} from "lucide-react";
import AppShell from "../../../components/AppShell";
import PageContainer from "../../../components/PageContainer";
import { usePageLoading } from "../../../components/NavigationLoadingProvider";
import { apiFetch, apiGet, apiPatch, apiPost } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";
import { toastError, toastSuccess } from "../../../lib/toast";
import {
  SegmentedTabs,
  SegmentedTabsList,
  SegmentedTabsIndicator,
  SegmentedTabsTab,
  SegmentedTabsPanel,
} from "@/components/ui/segmented-tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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

const API = "/api/v1/project-intelligence";
const NO_FILTER = "__all__";

// ── Small shared bits ────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const cfg = {
    pending: { variant: "outline", className: "border-amber-300 text-amber-700 bg-amber-50", label: "Pending" },
    verified: { variant: "outline", className: "border-emerald-300 text-emerald-700 bg-emerald-50", label: "Verified" },
    rejected: { variant: "outline", className: "border-red-300 text-red-700 bg-red-50", label: "Rejected" },
    superseded: { variant: "outline", className: "border-gray-300 text-gray-500 bg-gray-50 line-through", label: "Superseded" },
  }[status] || { variant: "outline", className: "", label: status || "—" };
  return (
    <Badge variant={cfg.variant} className={`text-[10px] h-4 ${cfg.className}`}>
      {cfg.label}
    </Badge>
  );
}

function TierBadge({ tier }) {
  if (tier === null || tier === undefined) return null;
  const strong = tier <= 3;
  const medium = tier === 4;
  return (
    <Badge
      variant="outline"
      className={`text-[10px] h-4 ${
        strong
          ? "border-blue-200 text-blue-700 bg-blue-50"
          : medium
          ? "border-amber-200 text-amber-700 bg-transparent"
          : "border-gray-200 text-gray-500 bg-gray-50"
      }`}
      title={
        strong
          ? "Stable anchor (data-testid/id/name) — a rename can be asserted"
          : medium
          ? "Partial anchor (aria-label/position) — treated cautiously"
          : "Text-only identity — a rename can only ever be a candidate"
      }
    >
      tier {tier}
    </Badge>
  );
}

const DRIFT_TYPE_CFG = {
  label_changed: { label: "Label changed", className: "border-blue-200 text-blue-700 bg-blue-50" },
  candidate_rename: { label: "Candidate rename", className: "border-purple-200 text-purple-700 bg-purple-50" },
  behavior_changed: { label: "Behaviour changed", className: "border-amber-200 text-amber-700 bg-amber-50" },
};

function DriftTypeBadge({ type }) {
  const cfg = DRIFT_TYPE_CFG[type] || { label: type, className: "border-gray-200 text-gray-500 bg-gray-50" };
  return (
    <Badge variant="outline" className={`text-[10px] h-4 ${cfg.className}`}>
      {cfg.label}
    </Badge>
  );
}

function SeverityBadge({ severity }) {
  const cls = {
    high: "border-red-300 text-red-700 bg-red-50",
    medium: "border-amber-300 text-amber-700 bg-amber-50",
    low: "border-gray-300 text-gray-500 bg-gray-50",
  }[severity] || "border-gray-200 text-gray-500 bg-gray-50";
  return (
    <Badge variant="outline" className={`text-[10px] h-4 capitalize ${cls}`}>
      {severity || "—"} severity
    </Badge>
  );
}

function EmptyState({ icon: Icon = FileQuestion, title, hint }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-14 px-6 border border-dashed border-gray-200 rounded-2xl text-gray-400">
      <Icon className="size-7 mb-3 opacity-60" strokeWidth={1.3} />
      <p className="text-[13.5px] text-gray-700 mb-1">{title}</p>
      {hint && <p className="text-xs max-w-[38ch] leading-relaxed">{hint}</p>}
    </div>
  );
}

function PhaseTwoPlaceholder({ title, hint }) {
  return (
    <EmptyState
      icon={Lock}
      title={title}
      hint={hint || "This view is part of Project Intelligence's Change Detection & Healing phase, which builds on the catalog above — it isn't wired up yet."}
    />
  );
}

function ListSkeleton({ rows = 4 }) {
  return (
    <div className="space-y-2.5 p-1">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-14 w-full rounded-xl" />
      ))}
    </div>
  );
}

// ── Review action controls (Approve / Edit / Reject), shared across tabs ──

function useReviewAction(entityType, onDone) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, action, reason, edit }) =>
      apiPost(`${API}/${entityType}/${entityId}/review-action`, { action, reason, edit }),
    onSuccess: (_, vars) => {
      toastSuccess(
        vars.action === "approve" ? "Approved" : vars.action === "reject" ? "Rejected" : "Saved and verified"
      );
      qc.invalidateQueries({ queryKey: [entityType] });
      qc.invalidateQueries({ queryKey: ["pi-review-queue"] });
      onDone?.();
    },
    onError: (e) => toastError(e.message, "Review action failed"),
  });
}

// ── Drift flag actions (Apply / Reject) — deliberately separate from
// useReviewAction above: ledger healing is a dedicated, non-bulk operation
// with its own endpoints (see api/v1/project_intelligence.py) ────────────

function useDriftAction(onDone) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["pi-drift-flags"] });
    qc.invalidateQueries({ queryKey: ["pi-drift-count"] });
  };
  const apply = useMutation({
    mutationFn: ({ flagId, label, behaviorNotes }) =>
      apiPost(`${API}/drift-flags/${flagId}/apply`, {
        label: label || undefined,
        behavior_notes: behaviorNotes || undefined,
        confirm_pairing: true,
      }),
    onSuccess: () => {
      toastSuccess("Applied");
      invalidate();
      onDone?.();
    },
    onError: (e) => toastError(e.message, "Apply failed"),
  });
  const reject = useMutation({
    mutationFn: ({ flagId, reason }) => apiPost(`${API}/drift-flags/${flagId}/reject`, { reason }),
    onSuccess: () => {
      toastSuccess("Rejected");
      invalidate();
      onDone?.();
    },
    onError: (e) => toastError(e.message, "Reject failed"),
  });
  return { apply, reject };
}

function RejectInline({ onConfirm, pending }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open) {
    return (
      <Button variant="destructive" size="sm" onClick={() => setOpen(true)}>
        Reject
      </Button>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <Input
        autoFocus
        placeholder="Reason for rejecting (required)…"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        className="h-7 text-xs w-[220px]"
      />
      <Button
        variant="destructive"
        size="sm"
        disabled={!reason.trim() || pending}
        onClick={() => onConfirm(reason.trim())}
      >
        Confirm
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </div>
  );
}

// ── Tab: Screen Catalog ───────────────────────────────────────────────────

function ScreenCatalogTab({ projectId, canReview }) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  if (statusFilter) params.set("status", statusFilter);
  params.set("limit", "300");

  const { data: screens, isLoading } = useQuery({
    queryKey: ["screen", projectId, statusFilter],
    queryFn: () => apiGet(`${API}/screens?${params.toString()}`),
    staleTime: 1000 * 20,
  });

  const review = useReviewAction("screen");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return screens || [];
    return (screens || []).filter(
      (s) => s.route?.toLowerCase().includes(q) || s.title?.toLowerCase().includes(q)
    );
  }, [screens, search]);

  return (
    <div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[220px] max-w-[340px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-gray-400" />
          <Input
            placeholder="Search screens by route or title…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 text-sm"
          />
        </div>
        <Select value={statusFilter || NO_FILTER} onValueChange={(v) => setStatusFilter(v === NO_FILTER ? "" : v)}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_FILTER}>All statuses</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="verified">Verified</SelectItem>
            <SelectItem value="rejected">Rejected</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <span className="text-xs text-gray-400">{isLoading ? "" : `${filtered.length} screen(s)`}</span>
      </div>

      {isLoading ? (
        <ListSkeleton rows={6} />
      ) : !filtered.length ? (
        <EmptyState
          icon={Workflow}
          title="No screens observed yet"
          hint="Screens are catalogued automatically as RF suites and Vibe Test runs execute against this project. Nothing to review until a run has happened with Project Intelligence enabled."
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((s) => (
            <Card key={s.id} className="hover:shadow-md transition-shadow">
              <CardContent className="pt-2">
                <p className="font-mono text-[10.5px] text-gray-400 mb-0.5 truncate" title={s.route}>
                  {s.route}
                </p>
                <p className="text-[13px] font-semibold mb-1 truncate">{s.title || "(untitled)"}</p>
                {s.description && (
                  <p className="text-[11.5px] text-gray-500 leading-relaxed mb-2 line-clamp-2">{s.description}</p>
                )}
                <div className="flex items-center justify-between mt-2">
                  <StatusBadge status={s.status} />
                  {s.status === "pending" && canReview && (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="success"
                        size="xs"
                        disabled={review.isPending}
                        onClick={() => review.mutate({ entityId: s.id, action: "approve" })}
                      >
                        <CheckCircle2 className="size-3" /> Approve
                      </Button>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Tab: Component Map ───────────────────────────────────────────────────

function ComponentMapTab({ projectId, canReview }) {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  params.set("limit", "1000");

  const { data: components, isLoading: compsLoading } = useQuery({
    queryKey: ["component", projectId],
    queryFn: () => apiGet(`${API}/components?${params.toString()}`),
    staleTime: 1000 * 20,
  });
  const screenParams = new URLSearchParams();
  if (projectId) screenParams.set("project_id", projectId);
  screenParams.set("limit", "300");
  const { data: screens, isLoading: screensLoading } = useQuery({
    queryKey: ["screen", projectId, ""],
    queryFn: () => apiGet(`${API}/screens?${screenParams.toString()}`),
    staleTime: 1000 * 20,
  });

  const review = useReviewAction("component");

  const grouped = useMemo(() => {
    const screensById = new Map((screens || []).map((s) => [s.id, s]));
    const byScreen = new Map();
    for (const c of components || []) {
      const key = c.screen_id;
      if (!byScreen.has(key)) byScreen.set(key, []);
      byScreen.get(key).push(c);
    }
    return [...byScreen.entries()]
      .map(([screenId, comps]) => ({ screen: screensById.get(screenId), comps }))
      .sort((a, b) => (b.comps.length || 0) - (a.comps.length || 0));
  }, [components, screens]);

  const isLoading = compsLoading || screensLoading;

  if (isLoading) return <ListSkeleton rows={5} />;
  if (!grouped.length) {
    return (
      <EmptyState
        title="No components observed yet"
        hint="Controls are catalogued per screen the first time a run interacts with them."
      />
    );
  }

  return (
    <div className="space-y-3">
      {grouped.map(({ screen, comps }) => (
        <details key={screen?.id || "unknown"} className="border border-gray-200 rounded-xl overflow-hidden bg-white" open={grouped.length <= 3}>
          <summary className="flex items-center gap-2 px-4 py-3 cursor-pointer text-[13px] font-semibold select-none">
            <ChevronRight className="size-3.5 text-gray-400 transition-transform [details[open]_&]:rotate-90" />
            <span className="font-mono text-[11px] text-gray-400">{screen?.route || "(unknown screen)"}</span>
            {screen?.title && <span>— {screen.title}</span>}
            <span className="ml-auto text-[11px] font-normal text-gray-400">{comps.length} component(s)</span>
          </summary>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="h-auto px-4 py-2 text-[10px]">Type</TableHead>
                <TableHead className="h-auto px-4 py-2 text-[10px]">Label</TableHead>
                <TableHead className="h-auto px-4 py-2 text-[10px]">Locator</TableHead>
                <TableHead className="h-auto px-4 py-2 text-[10px]">Identity</TableHead>
                <TableHead className="h-auto px-4 py-2 text-[10px]">Success / Fail</TableHead>
                <TableHead className="h-auto px-4 py-2 text-[10px]">Status</TableHead>
                {canReview && <TableHead className="h-auto px-4 py-2 text-[10px]">Review</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {comps.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="px-4 py-2.5">
                    <Badge variant="secondary" className="text-[10px] h-4">{c.component_type}</Badge>
                  </TableCell>
                  <TableCell className="px-4 py-2.5 text-xs">
                    {c.previous_label && c.previous_label !== c.label ? (
                      <>
                        <span className="line-through text-gray-400 mr-1">{c.previous_label}</span>
                        <b>{c.label}</b>
                      </>
                    ) : (
                      c.label
                    )}
                  </TableCell>
                  <TableCell className="px-4 py-2.5">
                    <code className="text-[10.5px] text-gray-500">{c.locator || "—"}</code>
                  </TableCell>
                  <TableCell className="px-4 py-2.5">
                    <TierBadge tier={c.identity_tier} />
                  </TableCell>
                  <TableCell className="px-4 py-2.5 text-xs whitespace-nowrap">
                    <span className="text-emerald-600">{c.success_count} ✓</span>{" "}
                    <span className="text-red-500">{c.fail_count} ✗</span>
                  </TableCell>
                  <TableCell className="px-4 py-2.5">
                    <StatusBadge status={c.status} />
                  </TableCell>
                  {canReview && (
                    <TableCell className="px-4 py-2.5">
                      {c.status === "pending" && (
                        <Button
                          variant="success"
                          size="xs"
                          disabled={review.isPending}
                          onClick={() => review.mutate({ entityId: c.id, action: "approve" })}
                        >
                          Approve
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </details>
      ))}
    </div>
  );
}

// ── Tab: Behaviour Notes ─────────────────────────────────────────────────

function BehaviorNotesTab({ projectId, canReview }) {
  const [searchQuery, setSearchQuery] = useState("");
  const trimmedQuery = searchQuery.trim();

  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  params.set("limit", "200");

  const { data: notes, isLoading } = useQuery({
    queryKey: ["behavior_note", projectId],
    queryFn: () => apiGet(`${API}/behavior-notes?${params.toString()}`),
    staleTime: 1000 * 20,
  });
  const screenParams = new URLSearchParams();
  if (projectId) screenParams.set("project_id", projectId);
  screenParams.set("limit", "300");
  const { data: screens } = useQuery({
    queryKey: ["screen", projectId, ""],
    queryFn: () => apiGet(`${API}/screens?${screenParams.toString()}`),
    staleTime: 1000 * 20,
  });
  const screensById = useMemo(() => new Map((screens || []).map((s) => [s.id, s])), [screens]);

  // Phase 5 (Scale) — semantic search, falling back to a plain client-side
  // substring filter of the already-fetched notes when the backend
  // returns no results (either genuinely no semantic match, or
  // PI_SEMANTIC_SEARCH_ENABLED is off / pgvector isn't installed — the
  // frontend can't tell those apart from an empty array alone, and
  // doesn't need to: either way, falling back to a full-text-ish filter
  // over data already on screen keeps search useful regardless).
  const searchParams = new URLSearchParams();
  if (projectId) searchParams.set("project_id", projectId);
  searchParams.set("q", trimmedQuery);
  searchParams.set("limit", "20");
  const { data: semanticResults, isFetching: isSearching } = useQuery({
    queryKey: ["behavior_note_search", projectId, trimmedQuery],
    queryFn: () => apiGet(`${API}/behavior-notes/search?${searchParams.toString()}`),
    enabled: !!projectId && trimmedQuery.length > 0,
    staleTime: 1000 * 10,
  });

  const displayedNotes = useMemo(() => {
    if (!trimmedQuery) return notes || [];
    if (semanticResults?.length) return semanticResults;
    const needle = trimmedQuery.toLowerCase();
    return (notes || []).filter((n) => (n.description || "").toLowerCase().includes(needle));
  }, [trimmedQuery, semanticResults, notes]);

  const review = useReviewAction("behavior_note");

  if (isLoading) return <ListSkeleton rows={4} />;
  if (!notes?.length) {
    return (
      <EmptyState
        title="No behaviour notes yet"
        hint="A short plain-language note is generated the first time each screen is observed, when Project Intelligence's behaviour-note capture is enabled."
      />
    );
  }

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2 mb-1">
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search behaviour notes (semantic — describe what you're looking for)"
          className="max-w-md text-sm"
        />
        {trimmedQuery && (
          <span className="text-xs text-gray-400">
            {isSearching ? "searching…" : `${displayedNotes.length} match(es)`}
          </span>
        )}
      </div>
      {trimmedQuery && !displayedNotes.length && !isSearching && (
        <EmptyState
          title="No matches"
          hint="Try a shorter or differently-worded search, or clear the search box to see every note."
        />
      )}
      {displayedNotes.map((n) => {
        const screen = screensById.get(n.screen_id);
        return (
          <Card key={n.id}>
            <CardContent className="pt-2 flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="font-mono text-[11px] text-gray-400">{screen?.route || "—"}</p>
                <p className="text-[12.5px] text-gray-700 leading-relaxed mt-1.5">{n.description}</p>
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  <Badge variant="outline" className="text-[10px] h-4">{n.source_type}</Badge>
                  {n.confidence != null && (
                    <Badge variant="outline" className="text-[10px] h-4">
                      confidence {Math.round(n.confidence * 100)}%
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex flex-col items-end gap-2 flex-shrink-0">
                <StatusBadge status={n.status} />
                {n.status === "pending" && canReview && (
                  <Button
                    variant="success"
                    size="xs"
                    disabled={review.isPending}
                    onClick={() => review.mutate({ entityId: n.id, action: "approve" })}
                  >
                    Approve
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

// ── Tab: Navigation Flow ─────────────────────────────────────────────────

function FlowStateRow({ state }) {
  return (
    <div className="flex items-start gap-3 border border-gray-200 rounded-xl px-4 py-3 bg-white">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <code className="text-[11px] text-gray-400">{state.id}</code>
          <span className="text-[13px] font-semibold">{state.name || "(unnamed)"}</span>
        </div>
        <p className="text-[11.5px] text-gray-500 mt-1">
          requires:{" "}
          {state.requires?.length ? state.requires.map((r) => (
            <code key={r} className="text-[10.5px] bg-gray-100 rounded px-1 py-0.5 mr-1">{r}</code>
          )) : <span className="italic">nothing — entry state</span>}
        </p>
        {state.pages?.length > 0 && (
          <p className="text-[11.5px] text-gray-500 mt-1">
            pages: {state.pages.map((p) => (
              <code key={p} className="text-[10.5px] bg-gray-100 rounded px-1 py-0.5 mr-1">{p}</code>
            ))}
          </p>
        )}
        {state.locked_behaviours?.length > 0 && (
          <p className="text-[11.5px] mt-1.5 flex items-start gap-1.5 text-amber-700">
            <Lock className="size-3 mt-0.5 flex-shrink-0" />
            <span>{state.locked_behaviours.join(", ")}</span>
          </p>
        )}
      </div>
    </div>
  );
}

function NavigationFlowTab({ projectId, canReview }) {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  params.set("limit", "10");

  const { data: flows, isLoading } = useQuery({
    queryKey: ["flow", projectId],
    queryFn: () => apiGet(`${API}/flows?${params.toString()}`),
    staleTime: 1000 * 15,
  });

  const review = useReviewAction("flow");

  const verified = useMemo(() => (flows || []).find((f) => f.status === "verified"), [flows]);
  const pending = useMemo(() => (flows || []).find((f) => f.status === "pending"), [flows]);

  if (isLoading) return <ListSkeleton rows={5} />;

  if (!verified && !pending) {
    return (
      <EmptyState
        icon={Workflow}
        title="No flow model yet"
        hint="A flow model is proposed automatically once enough screens and navigation edges have been observed for this project. Nothing here to review until then."
      />
    );
  }

  const shown = pending || verified;
  const states = shown?.model_json?.states || [];
  const entryId = shown?.model_json?.entry_state;

  return (
    <div>
      {pending && (
        <div className="flex items-center gap-2.5 rounded-xl px-4 py-2.5 mb-4 text-[12.5px] bg-amber-50 border border-amber-200 text-amber-800">
          <Workflow className="size-4 flex-shrink-0" />
          <span>
            <b>v{pending.version} — pending approval.</b> Approving this replaces the flow model{" "}
            <code className="bg-black/5 rounded px-1">get_flow_model()</code> serves for this project.
          </span>
        </div>
      )}
      {!pending && verified && (
        <div className="flex items-center gap-2.5 rounded-xl px-4 py-2.5 mb-4 text-[12.5px] bg-emerald-50 border border-emerald-200 text-emerald-800">
          <CheckCircle2 className="size-4 flex-shrink-0" />
          <span><b>v{verified.version} — live.</b> This is the model currently served to flow validation.</span>
        </div>
      )}

      <div className="space-y-2">
        {states.map((s) => (
          <FlowStateRow key={s.id} state={{ ...s, __entry: s.id === entryId }} />
        ))}
      </div>

      {pending && canReview && (
        <div className="flex items-center gap-2 mt-4">
          <Button
            variant="success"
            size="sm"
            disabled={review.isPending}
            onClick={() => review.mutate({ entityId: pending.id, action: "approve" })}
          >
            <CheckCircle2 className="size-3.5" /> Approve v{pending.version}
          </Button>
          <RejectInline
            pending={review.isPending}
            onConfirm={(reason) => review.mutate({ entityId: pending.id, action: "reject", reason })}
          />
        </div>
      )}

      <p className="text-[11px] text-gray-400 mt-4">
        This is the object stored in <code className="bg-gray-100 rounded px-1">pi_flows.model_json</code> and served by{" "}
        <code className="bg-gray-100 rounded px-1">flow_validation.get_flow_model()</code>.
      </p>
    </div>
  );
}

// ── Tab: Review Queue ─────────────────────────────────────────────────────

const ENTITY_LABEL = {
  screen: "Screen",
  component: "Component",
  behavior_note: "Behaviour note",
  flow: "Flow model",
  design_pattern: "Design pattern",
};

function QueueRow({ item, canReview, onDone }) {
  const review = useReviewAction(item.entity_type, onDone);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(
    item.entity_type === "screen"
      ? item.detail?.title || ""
      : item.entity_type === "component"
      ? item.detail?.label || ""
      : item.entity_type === "behavior_note"
      ? item.detail?.description || ""
      : ""
  );

  const editField =
    item.entity_type === "screen" ? "title" : item.entity_type === "component" ? "label" : "description";

  return (
    <div className="border border-gray-200 rounded-xl px-4 py-3 bg-white">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap mb-0.5">
            <span className="text-[10.5px] font-bold uppercase tracking-wide text-gray-400">
              {ENTITY_LABEL[item.entity_type] || item.entity_type}
            </span>
          </div>
          <p className="text-[13px] font-medium">{item.summary}</p>
          {editing ? (
            <div className="flex items-center gap-1.5 mt-2">
              <Input
                autoFocus
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                className="h-7 text-xs flex-1 max-w-[360px]"
              />
              <Button
                variant="default"
                size="sm"
                disabled={!editValue.trim() || review.isPending}
                onClick={() =>
                  review.mutate({
                    entityId: item.entity_id,
                    action: "edit",
                    edit: { [editField]: editValue.trim() },
                  })
                }
              >
                Save &amp; verify
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <p className="text-[11.5px] text-gray-400 mt-1">
              submitted {new Date(item.submitted_at).toLocaleString()}
            </p>
          )}
        </div>
        {canReview && !editing && (
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <Button
              variant="success"
              size="sm"
              disabled={review.isPending}
              onClick={() => review.mutate({ entityId: item.entity_id, action: "approve" })}
            >
              <CheckCircle2 className="size-3.5" /> Approve
            </Button>
            {item.entity_type !== "flow" && item.entity_type !== "design_pattern" && (
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
            <RejectInline
              pending={review.isPending}
              onConfirm={(reason) => review.mutate({ entityId: item.entity_id, action: "reject", reason })}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function ReviewQueueTab({ projectId, canReview }) {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  params.set("limit", "200");

  const { data: items, isLoading, refetch } = useQuery({
    queryKey: ["pi-review-queue", projectId],
    queryFn: () => apiGet(`${API}/review-queue?${params.toString()}`),
    staleTime: 1000 * 15,
  });

  if (isLoading) return <ListSkeleton rows={5} />;
  if (!items?.length) {
    return (
      <EmptyState
        icon={CheckCircle2}
        title="Nothing waiting for review"
        hint="Every screen, component, behaviour note, and flow version Project Intelligence has observed is already verified, rejected, or hasn't been proposed yet."
      />
    );
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <QueueRow
          key={`${item.entity_type}:${item.entity_id}`}
          item={item}
          canReview={canReview}
          onDone={refetch}
        />
      ))}
    </div>
  );
}

// ── Tab: Change & Drift Log (Phase 2) ────────────────────────────────────

function DriftFlagRow({ flag, canReview, onDone }) {
  const { apply, reject } = useDriftAction(onDone);
  const [label, setLabel] = useState(flag.proposed_label || "");
  const [notes, setNotes] = useState(flag.proposed_behavior_notes || "");
  const [editingCorrection, setEditingCorrection] = useState(false);

  const isPending = flag.status === "pending";
  const hasLedgerMatch = !!flag.ledger_fact_id;
  const applyBlocked = !flag.heal_enabled && hasLedgerMatch;

  return (
    <Card>
      <CardContent className="pt-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex-1 min-w-[260px]">
            <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
              <DriftTypeBadge type={flag.drift_type} />
              <SeverityBadge severity={flag.severity} />
              <StatusBadge status={flag.status} />
              {flag.identity_tier != null && <TierBadge tier={flag.identity_tier} />}
            </div>
            <p className="font-mono text-[10.5px] text-gray-400 mb-1 truncate" title={flag.screen_route}>
              {flag.screen_route}
            </p>
            <p className="text-[13px] text-gray-800 leading-relaxed mb-2">{flag.description}</p>

            {flag.drift_type === "candidate_rename" ? (
              <div className="text-[12px] text-gray-600 flex items-center gap-2 flex-wrap mb-1">
                <span className="line-through text-gray-400">{flag.candidate_component_label}</span>
                <ChevronRight className="size-3 text-gray-300" />
                <span className="font-medium">{flag.component_label}</span>
                <span className="text-[10px] text-gray-400">— not yet confirmed</span>
              </div>
            ) : flag.drift_type === "label_changed" ? (
              <div className="text-[12px] text-gray-600 flex items-center gap-2 flex-wrap mb-1">
                <span className="line-through text-gray-400">
                  {flag.ledger_current_label || flag.component_label}
                </span>
                <ChevronRight className="size-3 text-gray-300" />
                <span className="font-medium">{flag.proposed_label}</span>
              </div>
            ) : null}

            {hasLedgerMatch ? (
              isPending && canReview && editingCorrection ? (
                <div className="space-y-1.5 mt-2 max-w-[420px]">
                  <Input
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="Corrected label"
                    className="h-7 text-xs"
                  />
                  <Input
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="Corrected behaviour notes (optional)"
                    className="h-7 text-xs"
                  />
                </div>
              ) : (
                <p className="text-[11px] text-gray-400 mt-1">
                  Would correct 1 ledger fact
                  {flag.proposed_behavior_notes ? ` — proposed: "${flag.proposed_behavior_notes}"` : ""}
                  {isPending && canReview && (
                    <button
                      type="button"
                      className="ml-1.5 underline decoration-dotted text-gray-500 hover:text-gray-700"
                      onClick={() => setEditingCorrection(true)}
                    >
                      edit before applying
                    </button>
                  )}
                </p>
              )
            ) : (
              isPending && (
                <p className="text-[11px] text-gray-400 mt-1">
                  No matching ledger fact found — applying this reviews the flag but writes nothing to
                  the ledger.
                </p>
              )
            )}
          </div>

          {isPending && canReview && (
            <div className="flex flex-col items-end gap-1.5 shrink-0">
              {applyBlocked && (
                <span className="text-[10px] text-amber-600 max-w-[190px] text-right leading-snug">
                  Ledger healing is off (PI_HEAL_LEDGER) — reviewable, not applicable yet.
                </span>
              )}
              <div className="flex items-center gap-1.5">
                <Button
                  variant="success"
                  size="xs"
                  disabled={apply.isPending || applyBlocked}
                  onClick={() => apply.mutate({ flagId: flag.id, label, behaviorNotes: notes })}
                >
                  <CheckCircle2 className="size-3" /> Apply
                </Button>
                <RejectInline
                  pending={reject.isPending}
                  onConfirm={(reason) => reject.mutate({ flagId: flag.id, reason })}
                />
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function DriftLogTab({ projectId, canReview }) {
  const [statusFilter, setStatusFilter] = useState("pending");

  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  if (statusFilter) params.set("status", statusFilter);
  params.set("limit", "200");

  const { data: flags, isLoading, refetch } = useQuery({
    queryKey: ["pi-drift-flags", projectId, statusFilter],
    queryFn: () => apiGet(`${API}/drift-flags?${params.toString()}`),
    staleTime: 1000 * 15,
    enabled: !!projectId,
  });

  return (
    <div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <Select value={statusFilter || NO_FILTER} onValueChange={(v) => setStatusFilter(v === NO_FILTER ? "" : v)}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_FILTER}>All statuses</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="verified">Applied</SelectItem>
            <SelectItem value="rejected">Rejected</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <span className="text-xs text-gray-400">{isLoading ? "" : `${(flags || []).length} flag(s)`}</span>
      </div>

      {isLoading ? (
        <ListSkeleton rows={4} />
      ) : !(flags || []).length ? (
        <EmptyState
          icon={GitCompare}
          title="No drift detected"
          hint="Renames, broken locators, and behaviour changes are flagged automatically once a run revisits a
          screen Project Intelligence has already catalogued — nothing to review until that happens."
        />
      ) : (
        <div className="space-y-2.5">
          {flags.map((f) => (
            <DriftFlagRow key={f.id} flag={f} canReview={canReview} onDone={refetch} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Tab: Design Library (Phase 3) ────────────────────────────────────────

const PATTERN_TYPE_CFG = {
  color: { label: "Colour", className: "border-pink-200 text-pink-700 bg-pink-50" },
  typography: { label: "Typography", className: "border-indigo-200 text-indigo-700 bg-indigo-50" },
  layout: { label: "Layout", className: "border-teal-200 text-teal-700 bg-teal-50" },
  component_style: { label: "Component style", className: "border-orange-200 text-orange-700 bg-orange-50" },
};

function PatternTypeBadge({ type }) {
  const cfg = PATTERN_TYPE_CFG[type] || { label: type, className: "border-gray-200 text-gray-500 bg-gray-50" };
  return (
    <Badge variant="outline" className={`text-[10px] h-4 ${cfg.className}`}>
      {cfg.label}
    </Badge>
  );
}

// Same technique as components/ai-testing/shared.tsx's AuthImage — plain
// <img src="..."> can't carry the auth header apiFetch attaches, so the
// bytes are fetched once and turned into a local object URL.
function DesignPatternScreenshot({ patternId }) {
  const [src, setSrc] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let objectUrl = null;
    let cancelled = false;
    setSrc(null);
    setFailed(false);
    (async () => {
      try {
        const res = await apiFetch(`${API}/design-patterns/${patternId}/screenshot`);
        if (!res.ok) throw new Error("image unavailable");
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setSrc(objectUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [patternId]);

  if (failed) {
    return (
      <div className="flex flex-col items-center justify-center h-32 bg-gray-50 rounded-lg text-gray-300">
        <ImageOff className="size-5 mb-1" />
        <span className="text-[10px] text-gray-400">Screenshot expired</span>
      </div>
    );
  }
  if (!src) return <div className="h-32 bg-gray-100 rounded-lg animate-pulse" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt="Design pattern evidence" className="w-full h-32 object-cover rounded-lg border border-gray-200" />
  );
}

function DesignPatternCard({ pattern, canReview }) {
  const review = useReviewAction("design_pattern");
  return (
    <Card>
      <CardContent className="pt-2">
        {pattern.evidence_ref && <DesignPatternScreenshot patternId={pattern.id} />}
        <div className="flex items-center gap-1.5 mt-2 mb-1 flex-wrap">
          <PatternTypeBadge type={pattern.pattern_type} />
          <StatusBadge status={pattern.status} />
        </div>
        {pattern.screen_route && (
          <p className="font-mono text-[10.5px] text-gray-400 truncate" title={pattern.screen_route}>
            {pattern.screen_route}
          </p>
        )}
        {pattern.description && (
          <p className="text-[12px] text-gray-700 leading-relaxed mt-1">{pattern.description}</p>
        )}
        <pre className="text-[10.5px] text-gray-500 bg-gray-50 rounded-md p-2 mt-1.5 overflow-x-auto whitespace-pre-wrap break-all">
          {JSON.stringify(pattern.value, null, 2)}
        </pre>
        {pattern.status === "pending" && canReview && (
          <div className="flex items-center gap-1.5 mt-2">
            <Button
              variant="success"
              size="xs"
              disabled={review.isPending}
              onClick={() => review.mutate({ entityId: pattern.id, action: "approve" })}
            >
              <CheckCircle2 className="size-3" /> Approve
            </Button>
            <RejectInline
              pending={review.isPending}
              onConfirm={(reason) => review.mutate({ entityId: pattern.id, action: "reject", reason })}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DesignLibraryTab({ projectId, canReview }) {
  const [patternType, setPatternType] = useState("");

  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  if (patternType) params.set("pattern_type", patternType);
  params.set("limit", "200");

  const { data: patterns, isLoading } = useQuery({
    queryKey: ["design_pattern", projectId, patternType],
    queryFn: () => apiGet(`${API}/design-patterns?${params.toString()}`),
    staleTime: 1000 * 20,
    enabled: !!projectId,
  });

  return (
    <div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <Select value={patternType || NO_FILTER} onValueChange={(v) => setPatternType(v === NO_FILTER ? "" : v)}>
          <SelectTrigger className="w-[170px]">
            <SelectValue placeholder="All pattern types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_FILTER}>All pattern types</SelectItem>
            <SelectItem value="color">Colour</SelectItem>
            <SelectItem value="typography">Typography</SelectItem>
            <SelectItem value="layout">Layout</SelectItem>
            <SelectItem value="component_style">Component style</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <span className="text-xs text-gray-400">{isLoading ? "" : `${(patterns || []).length} pattern(s)`}</span>
      </div>

      {isLoading ? (
        <ListSkeleton rows={4} />
      ) : !(patterns || []).length ? (
        <EmptyState
          icon={Palette}
          title="No design patterns observed yet"
          hint="Colour, typography, layout, and component-style conventions are recorded from the scheduled crawler's screenshots — nothing here until crawling has been enabled for this project and at least one crawl has run."
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {patterns.map((p) => (
            <DesignPatternCard key={p.id} pattern={p} canReview={canReview} />
          ))}
        </div>
      )}
    </div>
  );
}

// Phase 4 — AI Context Feedback Loop (spec §20). Read-only before/after
// comparison: services/pi_context.py already states the one approximation
// it makes (step count) in its own "note" field, surfaced verbatim below
// rather than re-worded here, so the caveat can't drift out of sync with
// what the backend is actually computing.
function ContextEffectivenessTab({ projectId }) {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);

  const { data, isLoading } = useQuery({
    queryKey: ["pi-context-effectiveness", projectId],
    queryFn: () => apiGet(`${API}/context-effectiveness?${params.toString()}`),
    staleTime: 1000 * 30,
  });

  if (isLoading) return <ListSkeleton rows={3} />;

  const withCtx = data?.with_context || { run_count: 0, avg_steps: 0, avg_tokens: 0, avg_cost_usd: 0 };
  const withoutCtx = data?.without_context || { run_count: 0, avg_steps: 0, avg_tokens: 0, avg_cost_usd: 0 };

  if (!withCtx.run_count && !withoutCtx.run_count) {
    return (
      <EmptyState
        icon={Gauge}
        title="No AI run usage data yet"
        hint="This compares average steps, tokens, and cost per run for runs that received an auto-generated context brief against runs that did not. Nothing to compare until AI test runs have executed."
      />
    );
  }

  const rows = [
    { label: "Runs measured", withCtx: withCtx.run_count, withoutCtx: withoutCtx.run_count },
    { label: "Avg. steps / run", withCtx: withCtx.avg_steps, withoutCtx: withoutCtx.avg_steps },
    { label: "Avg. tokens / run", withCtx: withCtx.avg_tokens, withoutCtx: withoutCtx.avg_tokens },
    {
      label: "Avg. cost / run (USD)",
      withCtx: `$${Number(withCtx.avg_cost_usd).toFixed(4)}`,
      withoutCtx: `$${Number(withoutCtx.avg_cost_usd).toFixed(4)}`,
    },
  ];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Context brief — before / after</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead></TableHead>
                <TableHead>Without context brief</TableHead>
                <TableHead>With context brief</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.label}>
                  <TableCell className="text-gray-500">{r.label}</TableCell>
                  <TableCell>{r.withoutCtx}</TableCell>
                  <TableCell>{r.withCtx}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      {data?.note && <p className="text-xs text-gray-400">{data.note}</p>}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────

const TABS = [
  { key: "flow", label: "Navigation Flow" },
  { key: "screens", label: "Screen Catalog" },
  { key: "components", label: "Component Map" },
  { key: "behaviour", label: "Behaviour Notes" },
  { key: "drift", label: "Change & Drift Log" },
  { key: "design", label: "Design Library" },
  { key: "context", label: "AI Context Feedback" },
  { key: "sources", label: "Source Documents" },
  { key: "queue", label: "Review Queue" },
];

export default function AIIntelligencePage() {
  const [user, setUser] = useState(null);
  const [projectId, setProjectId] = useState("");
  const [tab, setTab] = useState("flow");

  useEffect(() => {
    setUser(getStoredUser());
  }, []);

  const { data: projectsResp, isLoading: projectsLoading } = useQuery({
    queryKey: ["pi-projects"],
    queryFn: () => apiGet("/api/v1/projects?limit=200"),
    staleTime: 1000 * 60,
  });
  const projects = projectsResp?.data || [];

  useEffect(() => {
    if (!projectId && projects.length > 0) setProjectId(projects[0].id);
  }, [projects, projectId]);

  const { data: queueCount } = useQuery({
    queryKey: ["pi-review-queue-count", projectId],
    queryFn: () =>
      apiGet(`${API}/review-queue?${projectId ? `project_id=${projectId}&` : ""}limit=200`),
    staleTime: 1000 * 15,
    enabled: !!user,
  });
  const { data: driftCount } = useQuery({
    queryKey: ["pi-drift-count", projectId],
    queryFn: () =>
      apiGet(
        `${API}/drift-flags?${projectId ? `project_id=${projectId}&` : ""}status=pending&limit=200`
      ),
    staleTime: 1000 * 15,
    enabled: !!user && !!projectId,
  });

  usePageLoading(!user || projectsLoading);

  if (!user) return null;

  const isAdmin = user.role === "admin";
  const permissions = user.permissions || [];
  const canBrowse = isAdmin || permissions.includes("project_intelligence");
  const canReview = isAdmin || permissions.includes("project_intelligence_review");

  if (!canBrowse) {
    return (
      <AppShell noPadding>
        <PageContainer>
          <EmptyState
            icon={Lock}
            title="You don't have access to Project Intelligence"
            hint="Ask an admin to grant the “project_intelligence” permission from the Users admin page."
          />
        </PageContainer>
      </AppShell>
    );
  }

  return (
    <AppShell noPadding>
      <PageContainer>
        <div className="max-w-[1400px] space-y-5">
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight flex items-center gap-2">
                <BrainCircuit className="size-6 text-gray-400" />
                AI Intelligence
              </h1>
              <p className="text-sm text-gray-500 mt-1 max-w-[64ch]">
                What AEP has learned about how this product actually behaves — generated from test runs
                and documents, nothing counted as fact until a reviewer confirms it.
              </p>
            </div>
            <Select value={projectId} onValueChange={setProjectId}>
              <SelectTrigger className="w-[220px]">
                <SelectValue placeholder="Select a project" />
              </SelectTrigger>
              <SelectContent>
                {projects.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {!canReview && (
            <p className="text-xs text-gray-400">
              You can browse everything here, but approving, editing, or rejecting requires the
              additional “project_intelligence_review” permission.
            </p>
          )}

          {!projectId ? (
            <EmptyState title="No projects yet" hint="Create a project first — Project Intelligence catalogs per project." />
          ) : (
            <SegmentedTabs value={tab} onValueChange={setTab}>
              <SegmentedTabsList className="w-fit rounded-full mb-5 flex-wrap h-auto">
                <SegmentedTabsIndicator />
                {TABS.map((t) => (
                  <SegmentedTabsTab
                    key={t.key}
                    value={t.key}
                    className="w-auto px-4"
                    badge={
                      t.key === "queue" && queueCount?.length
                        ? queueCount.length
                        : t.key === "drift" && driftCount?.length
                        ? driftCount.length
                        : undefined
                    }
                  >
                    {t.label}
                  </SegmentedTabsTab>
                ))}
              </SegmentedTabsList>

              <SegmentedTabsPanel value="flow">
                <NavigationFlowTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="screens">
                <ScreenCatalogTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="components">
                <ComponentMapTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="behaviour">
                <BehaviorNotesTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="drift">
                <DriftLogTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="design">
                <DesignLibraryTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="context">
                <ContextEffectivenessTab projectId={projectId} />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="sources">
                <PhaseTwoPlaceholder
                  title="Source Documents arrives in Phase 2"
                  hint="Cross-referencing screens against the SOW requirements ledger — which document referenced which screen, and what's never been observed — lands with ledger-heal in Phase 2."
                />
              </SegmentedTabsPanel>
              <SegmentedTabsPanel value="queue">
                <ReviewQueueTab projectId={projectId} canReview={canReview} />
              </SegmentedTabsPanel>
            </SegmentedTabs>
          )}
        </div>
      </PageContainer>
    </AppShell>
  );
}
