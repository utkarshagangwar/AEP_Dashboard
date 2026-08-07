"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiFetch } from "@/utils/apiClient";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * "Test setup" — one popup, one decision.
 *
 * Replaces the earlier Environments dialog, which asked for an
 * environment label, a base URL, and a default login as three separate
 * concepts. That was confusing for a concrete reason, not just a
 * cosmetic one: a kind="bypass" credential profile already carries its
 * own logged-in landing URL (target_url), so asking for a Base URL
 * alongside it created two competing sources of truth for the same
 * thing, and it was not obvious which one a run would actually use.
 *
 * Here the login is the only required choice. When it carries its own
 * address, that address is shown read-only and no URL field appears —
 * the backend resolves the same way (app.services.start_context), and
 * `start_url` in the response is that resolved value, so what the user
 * reads here is literally what the run will use rather than a second
 * client-side guess at the precedence rules.
 *
 * Environment-specific URLs still exist for projects that need them,
 * moved behind Advanced so the common case stays a single dropdown.
 *
 * Same hand-rolled fixed/centered modal pattern as
 * CreateBypassProfileDialog — see the reasoning documented there.
 */

interface ProjectEnvironmentRow {
  id: string;
  environment: string;
  base_url: string | null;
}

interface TestSetup {
  project_id: string;
  default_credential_profile_id: string | null;
  default_credential_profile_name: string | null;
  default_credential_profile_kind: string | null;
  start_url: string | null;
  start_url_source: string;
  is_ready: boolean;
  reason: string | null;
  environments: ProjectEnvironmentRow[];
}

interface CredentialProfile {
  id: string;
  name: string;
  kind?: string | null;
  target_url?: string | null;
  project_id?: string | null;
}

const NO_LOGIN_VALUE = "__none__";

export default function ProjectTestSetupDialog({
  projectId,
  projectName,
  environments,
  onClose,
}: {
  projectId: string;
  projectName: string;
  /** The project's environment labels, from Project.environments. */
  environments: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [loginId, setLoginId] = useState<string>(NO_LOGIN_VALUE);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [envLabel, setEnvLabel] = useState<string>(environments[0] ?? "default");
  const [baseUrl, setBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const { data: setup, isLoading } = useQuery<TestSetup>({
    queryKey: ["project-test-setup", projectId],
    queryFn: () => apiGet(`/api/projects/${projectId}/test-setup`),
  });

  const { data: profiles = [] } = useQuery<CredentialProfile[]>({
    queryKey: ["ai-credential-profiles"],
    queryFn: () => apiGet("/api/ai-testing/credential-profiles"),
    staleTime: 60_000,
  });

  // Only this project's profiles plus unscoped global ones — offering
  // another project's login would invite authenticating a run against
  // the wrong application.
  const selectableProfiles = profiles.filter(
    (p) => !p.project_id || p.project_id === projectId
  );

  // Seed the form from the server once, when it arrives. Keyed on the
  // fetched object so a later refetch doesn't wipe in-progress edits.
  useEffect(() => {
    if (!setup) return;
    setLoginId(setup.default_credential_profile_id ?? NO_LOGIN_VALUE);
    const existing =
      setup.environments.find(
        (e) => e.environment.toLowerCase() === envLabel.toLowerCase()
      ) ?? setup.environments[0];
    if (existing) {
      setEnvLabel(existing.environment);
      setBaseUrl(existing.base_url ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setup]);

  const chosenProfile = selectableProfiles.find((p) => p.id === loginId) ?? null;
  // A login that brings its own address means no URL field is needed —
  // this is the whole simplification.
  const loginProvidesUrl = Boolean(chosenProfile?.target_url);
  const effectiveUrl = loginProvidesUrl
    ? chosenProfile?.target_url ?? null
    : baseUrl.trim() || null;

  const trimmedUrl = baseUrl.trim();
  // Mirrors the backend validator so feedback lands before the
  // round-trip. The backend check stays authoritative.
  const urlLooksValid =
    trimmedUrl === "" || /^https?:\/\/[^\s/]+/i.test(trimmedUrl);
  const canSave = urlLooksValid && !saving;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const resp = await apiFetch(`/api/projects/${projectId}/test-setup`, {
        method: "PUT",
        body: JSON.stringify({
          default_credential_profile_id:
            loginId === NO_LOGIN_VALUE ? null : loginId,
          // Only send the URL half when it is actually in play — either
          // the login carries no address, or the user opened Advanced to
          // override it deliberately.
          ...(!loginProvidesUrl || advancedOpen
            ? { environment: envLabel.trim(), base_url: trimmedUrl || null }
            : {}),
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      await queryClient.invalidateQueries({
        queryKey: ["project-test-setup", projectId],
      });
      setSaved(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save test setup");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="border-b px-6 py-4">
          <h2 className="text-base font-medium text-gray-900">
            Test setup — {projectName}
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            How tests for this project sign in, and where they start.
          </p>
        </div>

        <div className="space-y-4 px-6 py-5">
          {isLoading ? (
            <Skeleton className="h-40 w-full rounded-md" />
          ) : (
            <>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-gray-600">Login</label>
                <Select
                  value={loginId}
                  onValueChange={(v) => {
                    setLoginId(v ?? NO_LOGIN_VALUE);
                    setSaved(false);
                  }}
                >
                  <SelectTrigger className="h-9 w-full text-sm">
                    <SelectValue placeholder="None (no login)">
                      {(value: string | null) => {
                        if (!value || value === NO_LOGIN_VALUE)
                          return "None (no login)";
                        return (
                          selectableProfiles.find((p) => p.id === value)?.name ||
                          "None (no login)"
                        );
                      }}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_LOGIN_VALUE}>None (no login)</SelectItem>
                    {selectableProfiles.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-gray-500">
                  Used by any test that doesn&apos;t pick its own — which is
                  every skill created from a SOW.
                </p>
              </div>

              {loginProvidesUrl ? (
                <div className="rounded-md bg-blue-50 px-3 py-2.5">
                  <p className="text-xs text-blue-700">Tests will start at</p>
                  <p className="mt-0.5 break-all font-mono text-[13px] text-blue-900">
                    {chosenProfile?.target_url}
                  </p>
                  <p className="mt-1.5 text-xs text-blue-700">
                    Taken from this login — no URL needed.
                  </p>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-600">
                    Start URL
                  </label>
                  <Input
                    value={baseUrl}
                    onChange={(e) => {
                      setBaseUrl(e.target.value);
                      setSaved(false);
                    }}
                    placeholder="https://app.example.com/dashboard"
                    className="text-sm"
                  />
                  <p className="text-xs text-gray-500">
                    Use the page a signed-in user lands on, not the public
                    homepage — a homepage usually looks the same signed in or
                    out, which misleads the agent into thinking it is already
                    logged in.
                  </p>
                  {!urlLooksValid && (
                    <p className="text-xs text-red-600">
                      Must be an absolute http(s) URL.
                    </p>
                  )}
                </div>
              )}

              <div className="border-t pt-3">
                <button
                  type="button"
                  onClick={() => setAdvancedOpen((v) => !v)}
                  className="text-sm text-gray-600 hover:text-gray-900"
                >
                  {advancedOpen ? "▾" : "▸"} Advanced — per-environment start
                  URL
                </button>

                {advancedOpen && (
                  <div className="mt-3 space-y-3 rounded-md bg-gray-50 p-3">
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-gray-600">
                        Environment
                      </label>
                      <Select
                        value={envLabel}
                        onValueChange={(v) => setEnvLabel(v ?? envLabel)}
                      >
                        <SelectTrigger className="h-9 w-full text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(environments.length
                            ? environments
                            : ["default"]
                          ).map((env) => (
                            <SelectItem key={env} value={env}>
                              {env}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-gray-600">
                        Start URL for this environment
                      </label>
                      <Input
                        value={baseUrl}
                        onChange={(e) => {
                          setBaseUrl(e.target.value);
                          setSaved(false);
                        }}
                        placeholder="https://staging.example.com/dashboard"
                        className="text-sm"
                      />
                      <p className="text-xs text-gray-500">
                        Overrides the login&apos;s own URL for this environment
                        only. Leave blank to use the login&apos;s URL.
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {error && (
                <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
                  {error}
                </p>
              )}
              {saved && (
                <p className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
                  Saved. Tests will start at {effectiveUrl ?? "the configured URL"}.
                </p>
              )}
              {!saved && !error && setup && !setup.is_ready && !effectiveUrl && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                  {setup.reason}
                </p>
              )}
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t px-6 py-3.5">
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!canSave}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </div>
  );
}
