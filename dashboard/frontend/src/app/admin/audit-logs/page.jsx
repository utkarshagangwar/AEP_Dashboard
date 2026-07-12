"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import PageContainer from "../../../components/PageContainer";
import { apiGet } from "../../../utils/apiClient";
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

const ACTION_OPTIONS = [
  "login",
  "logout",
  "create_user",
  "update_user",
  "create_project",
  "update_project",
  "delete_project",
  "create_test_suite",
  "update_test_suite",
  "delete_test_suite",
  "trigger_run",
  "cancel_run",
  "defect_created",
  "defect_updated",
];

const ACTION_COLORS = {
  login: "bg-blue-100 text-blue-700 border-blue-200",
  logout: "bg-gray-100 text-gray-600 border-gray-200",
  create_user: "bg-green-100 text-green-700 border-green-200",
  update_user: "bg-yellow-100 text-yellow-700 border-yellow-200",
  create_project: "bg-green-100 text-green-700 border-green-200",
  update_project: "bg-yellow-100 text-yellow-700 border-yellow-200",
  delete_project: "bg-red-100 text-red-700 border-red-200",
  create_test_suite: "bg-green-100 text-green-700 border-green-200",
  update_test_suite: "bg-yellow-100 text-yellow-700 border-yellow-200",
  delete_test_suite: "bg-red-100 text-red-700 border-red-200",
  trigger_run: "bg-purple-100 text-purple-700 border-purple-200",
  cancel_run: "bg-red-100 text-red-700 border-red-200",
  defect_created: "bg-orange-100 text-orange-700 border-orange-200",
  defect_updated: "bg-yellow-100 text-yellow-700 border-yellow-200",
};

const NO_FILTER = "__all__";

function formatDateTime(dateStr) {
  try {
    const d = new Date(dateStr);
    return d.toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

function AuditTableSkeleton({ rows = 10 }) {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-12" />
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  );
}

export default function AuditLogsPage() {
  const [user, setUser] = useState(null);
  const [actionFilter, setActionFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);
  const limit = 50;

  useEffect(() => {
    // Middleware handles auth + role checks; this is a fallback
    const stored = getStoredUser();
    setUser(stored);
  }, []);

  const params = new URLSearchParams();
  if (actionFilter) params.set("action", actionFilter);
  if (userFilter) params.set("user_id", userFilter);
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  params.set("limit", String(limit));
  params.set("offset", String((page - 1) * limit));

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["audit-logs", actionFilter, userFilter, fromDate, toDate, page],
    queryFn: () => apiGet(`/api/audit-logs?${params.toString()}`),
    staleTime: 1000 * 30,
  });

  const total = data?.total || 0;
  const logs = data?.data || [];
  const totalPages = Math.max(1, Math.ceil(total / limit));

  if (!user) return null;

  return (
    <AppShell noPadding>
      <PageContainer>
        <div className="max-w-[1400px] space-y-6">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight">
                Audit Logs
              </h1>
              <p className="text-sm text-gray-500 mt-1">
                All platform activity — {total.toLocaleString()} total events
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              {isFetching ? "Refreshing…" : "Refresh"}
            </Button>
          </div>

          {/* Filters */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Filters</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="pt-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">Action</label>
                  <Select
                    value={actionFilter || NO_FILTER}
                    onValueChange={(v) => {
                      setActionFilter((v ?? "") === NO_FILTER ? "" : v ?? "");
                      setPage(1);
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="All actions">
                        {(value) =>
                          !value || value === NO_FILTER ? "All actions" : value
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_FILTER}>All actions</SelectItem>
                      {ACTION_OPTIONS.map((a) => (
                        <SelectItem key={a} value={a}>
                          {a}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">From Date</label>
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
                  <label className="text-xs font-medium text-gray-500">To Date</label>
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
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-500">User ID</label>
                  <Input
                    placeholder="UUID"
                    value={userFilter}
                    onChange={(e) => {
                      setUserFilter(e.target.value);
                      setPage(1);
                    }}
                    className="text-sm"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Error banner */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4">
              <p className="text-sm text-red-600 font-medium">
                Failed to load audit logs: {error.message}
              </p>
            </div>
          )}

          {/* Audit log table */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">
                Audit Entries {data ? `(${data.total} total)` : ""}
              </CardTitle>
            </CardHeader>
            <Separator />
            {isLoading ? (
              <AuditTableSkeleton />
            ) : !logs.length ? (
              <div className="p-8 text-center text-sm text-gray-400">
                No audit log entries found
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>User</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Entity Type</TableHead>
                    <TableHead>Entity ID</TableHead>
                    <TableHead>IP Address</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.map((entry) => (
                    <TableRow key={entry.id}>
                      <TableCell className="text-xs text-gray-500 whitespace-nowrap">
                        {formatDateTime(entry.created_at)}
                      </TableCell>
                      <TableCell>
                        <div>
                          <p className="text-xs font-medium text-gray-900">
                            {entry.user_name || "—"}
                          </p>
                          <p className="text-[10px] text-gray-400">
                            {entry.user_email || "—"}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={`text-[10px] h-4 px-1.5 ${
                            ACTION_COLORS[entry.action] ||
                            "bg-gray-100 text-gray-600 border-gray-200"
                          }`}
                        >
                          {entry.action}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-gray-500">
                        {entry.resource_type || "—"}
                      </TableCell>
                      <TableCell className="text-[10px] text-gray-400 font-mono max-w-[120px] truncate">
                        {entry.resource_id || "—"}
                      </TableCell>
                      <TableCell className="text-xs text-gray-500 font-mono">
                        {entry.ip_address || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}

            {/* Pagination */}
            {total > limit && (
              <div className="flex items-center justify-between px-5 py-3 border-t">
                <p className="text-xs text-gray-500">
                  Showing {(page - 1) * limit + 1}–{Math.min(page * limit, total)} of{" "}
                  {total}
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
