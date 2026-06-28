"use client";

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/utils/apiClient";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import {
  Activity,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  FolderOpen,
  Clock,
  TrendingUp,
  TrendingDown,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────
interface KPICard {
  value: number;
  change_pct: number | null;
  label: string;
}

interface PassRateDay {
  date: string;
  pass_rate: number;
  total: number;
  passed: number;
}

interface RunsByProject {
  project_name: string;
  run_count: number;
}

interface RecentRun {
  id: string;
  project_name: string | null;
  suite_name: string | null;
  status: string;
  passed: number;
  failed: number;
  total: number;
  duration_ms: number | null;
  created_at: string;
}

interface TopDefect {
  id: string;
  title: string;
  severity: string;
  assigned_to_name: string | null;
  created_at: string;
}

interface DashboardData {
  total_runs_today: KPICard;
  pass_rate_7d: KPICard;
  open_defects: KPICard;
  critical_defects: KPICard;
  active_projects: KPICard;
  avg_execution_duration: KPICard;
  pass_rate_by_day: PassRateDay[];
  runs_by_project: RunsByProject[];
  recent_runs: RecentRun[];
  top_defects: TopDefect[];
}

// ─── Status styling ───────────────────────────────────────────────────────────
const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string }> = {
  passed: { bg: "bg-emerald-50", text: "text-emerald-700", dot: "bg-emerald-500" },
  failed: { bg: "bg-red-50", text: "text-red-700", dot: "bg-red-500" },
  running: { bg: "bg-blue-50", text: "text-blue-700", dot: "bg-blue-500" },
  queued: { bg: "bg-amber-50", text: "text-amber-700", dot: "bg-amber-500" },
  pending: { bg: "bg-gray-50", text: "text-gray-600", dot: "bg-gray-400" },
  cancelled: { bg: "bg-gray-50", text: "text-gray-600", dot: "bg-gray-400" },
  error: { bg: "bg-red-50", text: "text-red-700", dot: "bg-red-500" },
};

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-100 text-red-700 border-red-200",
  high: "bg-orange-100 text-orange-700 border-orange-200",
  medium: "bg-yellow-100 text-yellow-700 border-yellow-200",
  low: "bg-gray-100 text-gray-600 border-gray-200",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────
function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const remainingSec = seconds % 60;
  if (minutes === 0) return `${remainingSec}s`;
  return `${minutes}m ${remainingSec}s`;
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  } catch {
    return dateStr;
  }
}

function ChangeIndicator({ value, label }: { value: number | null; label: string }) {
  const isPositive = value !== null && value > 0;
  const isZero = value === 0 || value === null;
  const trend = isZero ? "stable" : isPositive ? "up" : "down";

  const tooltipText = (() => {
    if (value === null) return null;
    if (value === 0) return `No change vs previous period`;
    const direction = isPositive ? "increased" : "decreased";
    const abs = Math.abs(value).toFixed(1);
    return `${label} ${direction} by ${abs}% vs previous period`;
  })();

  const indicator = (
    <span
      className={`inline-flex items-center gap-0.5 text-xs font-medium ${
        isZero
          ? "text-gray-500"
          : isPositive
            ? "text-emerald-600"
            : "text-red-600"
      }`}
    >
      {isZero ? null : isPositive ? (
        <TrendingUp className="h-3 w-3" />
      ) : (
        <TrendingDown className="h-3 w-3" />
      )}
      {isZero ? "0%" : `${Math.abs(value!).toFixed(1)}%`}
    </span>
  );

  if (!tooltipText) return <span className="text-xs text-gray-400">—</span>;

  return (
    <Tooltip>
      <TooltipTrigger render={<span className="cursor-help" />}>
        {indicator}
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[220px]">
        {tooltipText}
      </TooltipContent>
    </Tooltip>
  );
}

// ─── Status Pill ──────────────────────────────────────────────────────────────
function StatusPill({ status }: { status: string }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.pending;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide ${style.bg} ${style.text}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
      {status}
    </span>
  );
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────
function KPICardComponent({
  icon: Icon,
  label,
  value,
  changePct,
  accent,
  loading,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | number;
  changePct: number | null;
  accent: string;
  loading: boolean;
}) {
  return (
    <Card className="relative overflow-hidden transition-[transform,shadow] duration-200 hover:shadow-md hover:scale-[1.02] hover:-translate-y-0.5 cursor-default">
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
              {label}
            </p>
            {loading ? (
              <Skeleton className="h-8 w-20 mt-1" />
            ) : (
              <p className={`text-2xl font-bold tracking-tight ${accent}`}>
                {value}
              </p>
            )}
            <div className="mt-1.5">
              {loading ? (
                <Skeleton className="h-4 w-16" />
              ) : (
                <ChangeIndicator value={changePct} label={label} />
              )}
            </div>
          </div>
          <div
            className={`p-2.5 rounded-xl ${accent.replace("text-", "bg-").replace("600", "100").replace("700", "100").replace("500", "100")}`}
          >
            <Icon className={`h-5 w-5 ${accent}`} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Skeleton Loaders ─────────────────────────────────────────────────────────
function KPIsSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i}>
          <CardContent className="p-5">
            <Skeleton className="h-3 w-24 mb-3" />
            <Skeleton className="h-8 w-16 mb-2" />
            <Skeleton className="h-4 w-12" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ChartSkeleton({ variant = "line" }: { variant?: "line" | "bar" }) {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-44" />
      </CardHeader>
      <CardContent>
        <div className="h-[280px] w-full flex flex-col">
          {/* Y-axis labels + chart area */}
          <div className="flex-1 flex">
            {/* Y-axis ticks */}
            <div className="w-8 flex flex-col justify-between py-1">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-2.5 w-6 ml-auto" />
              ))}
            </div>
            {/* Chart area with grid lines */}
            <div className="flex-1 relative border-l border-b border-gray-100">
              {/* Horizontal grid lines */}
              {[0, 1, 2, 3, 4].map((i) => (
                <div
                  key={i}
                  className="absolute w-full border-t border-dashed border-gray-100"
                  style={{ top: `${(i / 4) * 100}%` }}
                />
              ))}
              {/* Chart content */}
              {variant === "bar" ? (
                <div className="absolute inset-0 flex items-end justify-around px-3 pb-1">
                  {[0.6, 0.85, 0.45, 0.7, 0.55, 0.9, 0.35].map((h, i) => (
                    <Skeleton
                      key={i}
                      className="w-full max-w-[28px] rounded-t-sm"
                      style={{ height: `${h * 100}%` }}
                    />
                  ))}
                </div>
              ) : (
                <svg
                  className="absolute inset-0 w-full h-full"
                  viewBox="0 0 400 240"
                  preserveAspectRatio="none"
                >
                  <path
                    d="M0,180 Q50,160 100,140 T200,100 T300,60 T400,40"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    className="text-gray-200"
                    strokeDasharray="6 4"
                  />
                  {[0, 100, 200, 300, 400].map((cx) => (
                    <circle
                      key={cx}
                      cx={cx}
                      cy={180 - (cx / 400) * 140}
                      r="5"
                      className="fill-gray-200"
                    />
                  ))}
                </svg>
              )}
            </div>
          </div>
          {/* X-axis labels */}
          <div className="flex ml-8 mt-1.5 justify-around">
            {Array.from({ length: 7 }).map((_, i) => (
              <Skeleton key={i} className="h-2.5 w-8" />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-12" />
        </div>
      ))}
    </div>
  );
}

// ─── Main Dashboard Page ──────────────────────────────────────────────────────
export default function DashboardPage() {
  const { data, isLoading, error } = useQuery<DashboardData>({
    queryKey: ["dashboard-stats"],
    queryFn: () => apiGet("/api/v1/dashboard/stats"),
    staleTime: 1000 * 60 * 2, // 2 min
  });

  const kpis = data
    ? [
        {
          icon: Activity,
          label: "Total Runs Today",
          value: data.total_runs_today.value,
          changePct: data.total_runs_today.change_pct,
          accent: "text-blue-600",
        },
        {
          icon: CheckCircle2,
          label: "Pass Rate (7d)",
          value: `${data.pass_rate_7d.value}%`,
          changePct: data.pass_rate_7d.change_pct,
          accent: "text-emerald-600",
        },
        {
          icon: AlertTriangle,
          label: "Open Defects",
          value: data.open_defects.value,
          changePct: data.open_defects.change_pct,
          accent: "text-amber-600",
        },
        {
          icon: XCircle,
          label: "Critical Defects",
          value: data.critical_defects.value,
          changePct: data.critical_defects.change_pct,
          accent: "text-red-600",
        },
        {
          icon: FolderOpen,
          label: "Active Projects",
          value: data.active_projects.value,
          changePct: data.active_projects.change_pct,
          accent: "text-purple-600",
        },
        {
          icon: Clock,
          label: "Avg Execution Duration",
          value: formatDuration(data.avg_execution_duration.value),
          changePct: data.avg_execution_duration.change_pct,
          accent: "text-slate-600",
        },
      ]
    : [];

  return (
    <TooltipProvider>
    <div className="max-w-[1200px] space-y-8">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">
          QA execution overview — all products
        </p>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4">
          <p className="text-sm text-red-600 font-medium">
            Failed to load dashboard stats: {(error as Error).message}
          </p>
        </div>
      )}

      {/* Section 1: KPI Cards */}
      {isLoading ? (
        <KPIsSkeleton />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {kpis.map((kpi, i) => (
            <div
              key={kpi.label}
              className="animate-fade-in-up"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <KPICardComponent
                icon={kpi.icon}
                label={kpi.label}
                value={kpi.value}
                changePct={kpi.changePct}
                accent={kpi.accent}
                loading={false}
              />
            </div>
          ))}
        </div>
      )}

      {/* Section 2: Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Line chart — Pass Rate per day */}
        {isLoading ? (
          <ChartSkeleton variant="line" />
        ) : (
          <Card className="animate-fade-in-up" style={{ animationDelay: '400ms' }}>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">
                Pass Rate % — Last 14 Days
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[280px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={data?.pass_rate_by_day || []}
                    margin={{ top: 5, right: 10, left: -10, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11, fill: "#9ca3af" }}
                      tickFormatter={(v: string) => {
                        const d = new Date(v);
                        return `${d.getDate()}/${d.getMonth() + 1}`;
                      }}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#9ca3af" }}
                      tickFormatter={(v: number) => `${v}%`}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        borderRadius: "8px",
                        border: "1px solid #e5e7eb",
                        fontSize: "12px",
                      }}
                      formatter={(value: number) => [`${value.toFixed(1)}%`, "Pass Rate"]}
                      labelFormatter={(label: string) => {
                        const d = new Date(label);
                        return d.toLocaleDateString("en-GB", {
                          day: "numeric",
                          month: "short",
                          year: "numeric",
                        });
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="pass_rate"
                      stroke="#2563eb"
                      strokeWidth={2}
                      dot={{ r: 3, fill: "#2563eb" }}
                      activeDot={{ r: 5 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Bar chart — Runs per project */}
        {isLoading ? (
          <ChartSkeleton variant="bar" />
        ) : (
          <Card className="animate-fade-in-up" style={{ animationDelay: '520ms' }}>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">
                Runs per Project — Last 30 Days
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[280px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={data?.runs_by_project || []}
                    margin={{ top: 5, right: 10, left: -10, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="project_name"
                      tick={{ fontSize: 11, fill: "#9ca3af" }}
                    />
                    <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }} />
                    <RechartsTooltip
                      contentStyle={{
                        borderRadius: "8px",
                        border: "1px solid #e5e7eb",
                        fontSize: "12px",
                      }}
                    />
                    <Bar
                      dataKey="run_count"
                      fill="#6366f1"
                      radius={[4, 4, 0, 0]}
                      name="Runs"
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Section 3 & 4: Recent Runs Table + Open Defects Widget */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Runs Table */}
        <Card className="lg:col-span-2 animate-fade-in-up" style={{ animationDelay: '640ms' }}>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">Recent Runs</CardTitle>
          </CardHeader>
          <Separator />
          {isLoading ? (
            <TableSkeleton rows={5} />
          ) : !data?.recent_runs?.length ? (
            <div className="p-8 text-center text-sm text-gray-400">
              No runs found
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Project</TableHead>
                  <TableHead>Suite</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Passed</TableHead>
                  <TableHead className="text-right">Failed</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.recent_runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="font-medium text-gray-900 text-xs">
                      {run.project_name || "—"}
                    </TableCell>
                    <TableCell className="text-xs text-gray-500">
                      {run.suite_name || "—"}
                    </TableCell>
                    <TableCell>
                      <StatusPill status={run.status} />
                    </TableCell>
                    <TableCell className="text-right text-xs text-emerald-600 font-medium">
                      {run.passed}
                    </TableCell>
                    <TableCell className="text-right text-xs text-red-600 font-medium">
                      {run.failed}
                    </TableCell>
                    <TableCell className="text-right text-xs text-gray-500">
                      {formatDuration(run.duration_ms)}
                    </TableCell>
                    <TableCell className="text-xs text-gray-400">
                      {formatDate(run.created_at)}
                    </TableCell>
                    <TableCell>
                      <a
                        href={`/reports/${run.id}`}
                        className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                      >
                        View Report
                      </a>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>

        {/* Open Defects Widget */}
        <Card className="animate-fade-in-up" style={{ animationDelay: '760ms' }}>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-semibold">Open Defects</CardTitle>
            <a
              href="/defects"
              className="text-xs text-blue-600 hover:text-blue-700 font-medium"
            >
              View All →
            </a>
          </CardHeader>
          <Separator />
          {isLoading ? (
            <TableSkeleton rows={5} />
          ) : !data?.top_defects?.length ? (
            <div className="p-8 text-center text-sm text-gray-400">
              No open defects 🎉
            </div>
          ) : (
            <div className="divide-y">
              {data.top_defects.map((defect) => (
                <div key={defect.id} className="p-4 space-y-1.5">
                  <p className="text-xs font-medium text-gray-900 leading-snug line-clamp-2">
                    {defect.title}
                  </p>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant="outline"
                      className={`text-[10px] h-4 px-1.5 ${SEVERITY_STYLES[defect.severity] || ""}`}
                    >
                      {defect.severity}
                    </Badge>
                    {defect.assigned_to_name && (
                      <span className="text-[11px] text-gray-500">
                        → {defect.assigned_to_name}
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] text-gray-400">
                    {formatDate(defect.created_at)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
    </TooltipProvider>
  );
}
