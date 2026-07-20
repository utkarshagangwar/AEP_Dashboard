"use client";

import { useState } from "react";
import { apiPost } from "@/utils/apiClient";
import { Button } from "@/components/ui/button";
import { CredentialProfile } from "@/components/ai-testing/shared";

/**
 * Creates a "bypass" kind Credential Profile — instead of typing a
 * username/password into a login form, the AI runner calls the target
 * app's admin API-key login endpoint directly and injects the returned
 * token as a browser cookie, so the agent starts already authenticated
 * and never has to fight a CAPTCHA-gated login form.
 *
 * Reuses the same plain fixed/centered modal pattern already hand-rolled
 * elsewhere on this page (the "Log Defect" dialog) rather than introducing
 * a new components/ui/dialog.tsx primitive — this is a one-off form, not
 * a case that needs a fully reusable dialog primitive.
 *
 * The backend requires an admin role to create a bypass profile (it stores
 * a secret capable of impersonating any user on the target app) — a 403
 * here surfaces as a plain error message, same as any other failed request.
 */
export default function CreateBypassProfileDialog({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
  onClose: () => void;
  onCreated: (profile: CredentialProfile) => void;
}) {
  const [name, setName] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [bypassEndpoint, setBypassEndpoint] = useState("/admin-login-by-api-key");
  const [apiKey, setApiKey] = useState("");
  const [cookieName, setCookieName] = useState("authToken");
  const [cookieDomain, setCookieDomain] = useState("");
  const [allowedDomains, setAllowedDomains] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Allowed Domains defaults to the cookie domain the first time the user
  // types one, but only while they haven't touched Allowed Domains
  // themselves — once they edit it directly, stop overwriting it.
  const [allowedDomainsTouched, setAllowedDomainsTouched] = useState(false);

  function handleCookieDomainChange(v: string) {
    setCookieDomain(v);
    if (!allowedDomainsTouched) setAllowedDomains(v);
  }

  const valid =
    name.trim().length > 0 &&
    targetUrl.trim().length > 0 &&
    apiBaseUrl.trim().length > 0 &&
    apiKey.trim().length > 0 &&
    cookieDomain.trim().length > 0 &&
    allowedDomains.trim().length > 0;

  async function handleCreate() {
    if (!valid || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const profile: CredentialProfile = await apiPost(
        "/api/ai-testing/credential-profiles",
        {
          name: name.trim(),
          project_id: projectId || undefined,
          kind: "bypass",
          target_url: targetUrl.trim(),
          allowed_domains: allowedDomains
            .split(",")
            .map((d) => d.trim())
            .filter(Boolean),
          credentials: {
            api_base_url: apiBaseUrl.trim().replace(/\/$/, ""),
            bypass_endpoint: bypassEndpoint.trim() || "/admin-login-by-api-key",
            api_key: apiKey,
            cookie_name: cookieName.trim() || "authToken",
            cookie_domain: cookieDomain.trim(),
          },
        }
      );
      onCreated(profile);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create profile");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-900">
            Create bypass credential profile
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Injects an auth cookie via an admin API-key login call instead of
            typing into the login form — routes around CAPTCHA-gated logins.
            Requires an admin role.
          </p>
        </div>
        <div className="px-6 py-4 space-y-4">
          <Field label="Name" value={name} onChange={setName} placeholder="IG Login bypass" />
          <Field
            label="Target / App URL"
            value={targetUrl}
            onChange={setTargetUrl}
            placeholder="https://pre-prod.interviewgod.ai/dashboard"
            help="Where the agent lands right after the cookie is injected — use the actual logged-in destination (e.g. /dashboard), not the public marketing homepage. The homepage renders the same Sign In/Sign Up nav regardless of auth state, so landing there proves nothing and the run will look like the bypass silently failed."
          />
          <Field
            label="API Base URL"
            value={apiBaseUrl}
            onChange={setApiBaseUrl}
            placeholder="https://api.interviewgod.ai"
          />
          <Field
            label="Bypass Endpoint"
            value={bypassEndpoint}
            onChange={setBypassEndpoint}
            placeholder="/admin-login-by-api-key"
          />
          <Field
            label="X-API-Key"
            value={apiKey}
            onChange={setApiKey}
            placeholder="secret API key"
            type="password"
          />
          <Field
            label="Cookie Name"
            value={cookieName}
            onChange={setCookieName}
            placeholder="authToken"
          />
          <Field
            label="Cookie Domain"
            value={cookieDomain}
            onChange={handleCookieDomainChange}
            placeholder=".interviewgod.ai"
            help='Leading dot (e.g. ".interviewgod.ai") so the cookie also attaches to subdomains like www — without it, the cookie silently won’t attach and the bypass will look like it just didn’t work, with no error.'
          />
          <Field
            label="Allowed Domains"
            value={allowedDomains}
            onChange={(v) => {
              setAllowedDomains(v);
              setAllowedDomainsTouched(true);
            }}
            placeholder="interviewgod.ai, pre-prod.interviewgod.ai"
            help="Comma-separated. The AI agent is restricted to these domains once the authenticated session cookie is injected."
          />
          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </p>
          )}
        </div>
        <div className="px-6 py-4 border-t border-gray-100 flex gap-3 justify-end">
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={!valid || submitting}>
            {submitting ? "Creating…" : "Create profile"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  help,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  help?: string;
}) {
  return (
    <div>
      <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
      />
      {help && <p className="text-xs text-gray-400 mt-1">{help}</p>}
    </div>
  );
}
