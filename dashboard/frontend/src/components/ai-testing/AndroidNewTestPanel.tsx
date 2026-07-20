"use client";

/**
 * Android app testing — New Test panel. Mirrors the plain "Quick" web goal
 * card's shape (goal textarea, target selection, optional credential
 * profile, submit) rather than AutonomousQASection's richer Visual QA form
 * — Android Vibe Testing has one mode, not four, for now.
 *
 * Reuses the existing Live Run / Results / Skills views entirely unchanged:
 * onRunStarted hands off (run_id, goal) exactly like page.tsx's
 * handleReplayStarted already does for a replayed skill, which is why this
 * component is passed that same handler as a prop rather than page.tsx
 * growing an Android-specific copy of it.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiGet } from "@/utils/apiClient";
import { CredentialProfile, EXAMPLE_GOALS, isGoalValid } from "@/components/ai-testing/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface AndroidAppBuild {
  id: string;
  name: string;
  project_id?: string | null;
  apk_filename: string;
  file_size?: number | null;
  package_name?: string | null;
  created_at: string;
}

interface DeviceProfileOption {
  id: string;
  label: string;
}

function ApkDropzone({
  uploading,
  error,
  onFile,
}: {
  uploading: boolean;
  error: string | null;
  onFile: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) onFile(f);
        }}
        className={`w-full flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors ${
          dragOver
            ? "border-blue-400 bg-blue-50"
            : error
              ? "border-red-300 bg-red-50"
              : "border-gray-200 bg-white hover:border-gray-300"
        }`}
      >
        {uploading ? (
          <span className="text-sm text-gray-500">Uploading…</span>
        ) : error ? (
          <span className="text-sm text-red-600 break-words">
            {error}
            <span className="block text-xs text-red-400 mt-1">Click to try again</span>
          </span>
        ) : (
          <span className="text-sm text-gray-500">
            Upload a new APK
            <span className="block text-xs text-gray-400 mt-1">.apk or .aab</span>
          </span>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".apk,.aab"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export default function AndroidNewTestPanel({
  onRunStarted,
}: {
  onRunStarted: (runId: string, goal: string) => void;
}) {
  const [goal, setGoal] = useState("");
  const [buildId, setBuildId] = useState("");
  const [deviceProfile, setDeviceProfile] = useState("");
  const [profileId, setProfileId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: builds = [], isLoading: buildsLoading } = useQuery<AndroidAppBuild[]>({
    queryKey: ["android-app-builds"],
    queryFn: () => apiGet("/api/v1/ai-testing/android/builds"),
    staleTime: 30_000,
  });

  const { data: deviceProfiles = [] } = useQuery<DeviceProfileOption[]>({
    queryKey: ["android-device-profiles"],
    queryFn: () => apiGet("/api/v1/ai-testing/android/device-profiles"),
    staleTime: 5 * 60_000,
  });

  const { data: credentialProfiles = [], isLoading: credProfilesLoading } = useQuery<
    CredentialProfile[]
  >({
    queryKey: ["ai-credential-profiles", "__unfiltered__"],
    queryFn: () => apiGet("/api/ai-testing/credential-profiles"),
    staleTime: 60_000,
  });
  // Bypass profiles inject a Playwright browser cookie — no Android
  // counterpart exists yet, so offering one here would silently do nothing.
  const androidCredentialProfiles = credentialProfiles.filter(
    (p) => (p.kind || "standard") !== "bypass"
  );

  // Pick a sensible default device once the (small, static) catalog loads,
  // so Submit isn't blocked on a choice most runs don't need to think about.
  useEffect(() => {
    if (!deviceProfile && deviceProfiles.length > 0) {
      setDeviceProfile(deviceProfiles[0].id);
    }
  }, [deviceProfiles, deviceProfile]);

  const handleFileSelected = async (file: File) => {
    setUploadError(null);
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("name", file.name.replace(/\.(apk|aab)$/i, ""));
      const resp = await apiFetch("/api/v1/ai-testing/android/builds", {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Upload failed (${resp.status})`);
      }
      const build: AndroidAppBuild = await resp.json();
      await queryClient.invalidateQueries({ queryKey: ["android-app-builds"] });
      setBuildId(build.id);
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleSubmit = async () => {
    if (!isGoalValid(goal) || !buildId || !deviceProfile || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const body: Record<string, unknown> = {
        goal: goal.trim(),
        platform: "android",
        android_app_build_id: buildId,
        device_profile: deviceProfile,
      };
      if (profileId) body.credential_profile_id = profileId;

      const resp = await apiFetch("/api/ai-testing/runs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error (${resp.status})`);
      }
      const data = await resp.json();
      onRunStarted(data.run_id, goal.trim());
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : "Failed to start test run");
    } finally {
      setSubmitting(false);
    }
  };

  const disabledReason = !isGoalValid(goal)
    ? goal.length === 0
      ? "Enter a goal to enable"
      : "Enter a goal with an action verb (log, verify, check…)"
    : !buildId
      ? "Select or upload an APK to enable"
      : !deviceProfile
        ? "Select a device to enable"
        : null;

  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">New Android Test</CardTitle>
            <p className="text-sm text-gray-500 mt-1">
              Describe a goal in plain language and let the AI drive the app on
              a real cloud device.
            </p>
          </div>
          <Badge
            variant="outline"
            className="text-green-600 border-green-300 bg-green-50 gap-1.5 flex-shrink-0"
          >
            <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
            Agent ready
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <textarea
          value={goal}
          onChange={(e) => {
            setGoal(e.target.value);
            setSubmitError(null);
          }}
          placeholder='Describe what to test, e.g. "Log in and verify the home feed loads with at least one item."'
          rows={4}
          className="w-full resize-none rounded-md border border-gray-200 px-4 py-3 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent"
        />

        <div className="flex flex-wrap gap-2 items-center">
          <span className="text-xs text-gray-400 uppercase tracking-wide">TRY</span>
          {EXAMPLE_GOALS.map((eg) => (
            <button
              key={eg}
              onClick={() => {
                setGoal(eg);
                setSubmitError(null);
              }}
              className="text-xs px-3 py-1.5 rounded-full border border-gray-200 text-gray-600 hover:bg-gray-100 transition-colors"
            >
              {eg}
            </button>
          ))}
        </div>

        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            App build
          </p>
          <div className="flex gap-3 flex-wrap items-start">
            {buildsLoading ? (
              <Skeleton className="h-9 w-48 rounded-md" />
            ) : builds.length > 0 ? (
              <Select
                value={buildId}
                onValueChange={(v) => setBuildId(v ?? "")}
                items={builds.map((b) => ({ value: b.id, label: b.name }))}
              >
                <SelectTrigger className="w-auto min-w-[200px] h-9 text-sm">
                  <SelectValue placeholder="Select an uploaded APK" />
                </SelectTrigger>
                <SelectContent>
                  {builds.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <div className="flex items-center h-9 px-3 rounded-md border border-dashed border-gray-200 text-xs text-gray-400">
                No APKs uploaded yet
              </div>
            )}
            <div className="w-56">
              <ApkDropzone uploading={uploading} error={uploadError} onFile={handleFileSelected} />
            </div>
          </div>
        </div>

        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            Device
          </p>
          <Select
            value={deviceProfile}
            onValueChange={(v) => setDeviceProfile(v ?? "")}
            items={deviceProfiles.map((d) => ({ value: d.id, label: d.label }))}
          >
            <SelectTrigger className="w-auto min-w-[220px] h-9 text-sm">
              <SelectValue placeholder="Device" />
            </SelectTrigger>
            <SelectContent>
              {deviceProfiles.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {!credProfilesLoading && androidCredentialProfiles.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              Credential profile (optional)
            </p>
            <Select
              value={profileId}
              onValueChange={(v) => setProfileId(v ?? "")}
              items={androidCredentialProfiles.map((p) => ({ value: p.id, label: p.name }))}
            >
              <SelectTrigger className="w-auto min-w-[180px] h-9 text-sm">
                <SelectValue placeholder="None" />
              </SelectTrigger>
              <SelectContent>
                {androidCredentialProfiles.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {submitError && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {submitError}
          </p>
        )}

        <div className="pt-1">
          <Button
            onClick={handleSubmit}
            disabled={!!disabledReason || submitting}
            className="w-full h-11 text-base font-medium"
          >
            <svg className="w-5 h-5 mr-2" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"
                clipRule="evenodd"
              />
            </svg>
            {submitting ? "Starting…" : "Run Test"}
          </Button>
          {disabledReason && (
            <p className="text-xs text-gray-400 text-center mt-2">{disabledReason}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
