"use client";
import { useState } from "react";
import { Terminal, ArrowRight, Loader2 } from "lucide-react";
import { PasswordRevealSwitch } from "../../components/ui/password-reveal-switch";
import { setAccessToken } from "../../lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin() {
    setError("");
    setLoading(true);
    try {
      // Client-side validation
      if (!email.trim()) {
        setError("Email is required");
        setLoading(false);
        return;
      }
      if (!password) {
        setError("Password is required");
        setLoading(false);
        return;
      }

      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Login failed");
      // Access token lives in memory only (never localStorage — XSS
      // protection). The refresh token never reaches client JS at all: the
      // /api/auth/login proxy strips it and sets it as an httpOnly cookie.
      setAccessToken(data.access_token);
      // Set a separate, short-lived, non-httpOnly cookie purely so Edge
      // middleware can gate route access without a network round trip. Its
      // lifetime matches the 24h session, not the (short) access token TTL —
      // apiClient's refresh flow re-syncs it on every silent refresh.
      document.cookie = `aep_token=${data.access_token}; path=/; max-age=${24 * 60 * 60}; SameSite=Lax`;

      // Fetch full user profile from /me endpoint
      const meRes = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${data.access_token}` },
      });
      if (meRes.ok) {
        const user = await meRes.json();
        localStorage.setItem("aep_user", JSON.stringify(user));
      } else {
        // Fallback: decode user info from JWT payload
        const payload = JSON.parse(atob(data.access_token.split(".")[1]));
        localStorage.setItem(
          "aep_user",
          JSON.stringify({
            id: payload.sub,
            email: payload.email,
            role: payload.role,
            full_name: payload.email,
          }),
        );
      }
      window.location.href = "/dashboard";
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-6">
      <div className="w-full max-w-[400px] animate-fade-in-up">
        {/* Brand */}
        <div className="text-center mb-8">
          <img
            src="/spider-logo.png"
            alt="AEP logo"
            width={84}
            height={53}
            className="inline-block object-contain mb-2"
          />
          <h1 className="text-2xl font-semibold text-gray-900 tracking-[-0.02em] m-0">
            Automation Execution Platform (AEP)
          </h1>
          <p className="text-[13px] text-gray-500 mt-1.5">
            Sign in to your workspace
          </p>
        </div>

        {/* Card — no <form> tag; uses state + onClick per spec */}
        <div className="rounded-xl overflow-hidden shadow-xl shadow-gray-950/10 ring-1 ring-black/5">
          {/* Terminal-style header */}
          <div className="flex items-center gap-2 bg-gray-900 px-4 py-2.5">
            <Terminal className="w-4 h-4 text-blue-400" aria-hidden="true" />
            <span className="text-sm font-medium text-white">Sign in</span>
          </div>

          {/* Terminal-style body */}
          <div className="bg-gray-950 px-4 py-5 [font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace]">
            <div className="mb-4">
              <label htmlFor="login-email" className="sr-only">
                Email address
              </label>
              <div className="flex items-center gap-2.5">
                <span className="text-sm text-emerald-400 shrink-0">
                  email:
                </span>
                <input
                  id="login-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleLogin();
                  }}
                  placeholder="you@company.com"
                  autoComplete="email"
                  className="flex-1 min-w-0 bg-transparent text-sm text-white placeholder:text-gray-400 outline-none caret-emerald-400 rounded-sm focus:ring-1 focus:ring-emerald-400/50"
                />
              </div>
            </div>

            <div>
              <label htmlFor="login-password" className="sr-only">
                Password
              </label>
              <div className="flex items-center gap-2.5">
                <span className="text-sm text-emerald-400 shrink-0">
                  password:
                </span>
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleLogin();
                  }}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  className="flex-1 min-w-0 bg-transparent text-sm text-white placeholder:text-gray-400 outline-none caret-emerald-400 rounded-sm focus:ring-1 focus:ring-emerald-400/50"
                />
                <PasswordRevealSwitch
                  revealed={showPassword}
                  onRevealedChange={setShowPassword}
                  labelClassName="text-gray-400"
                />
              </div>
            </div>

            {error && (
              <p role="alert" className="mt-4 text-sm text-red-300">
                ✗ {error}
              </p>
            )}
          </div>
        </div>

        {/* Sign-in CTA — gradient glow ring on hover/focus */}
        <button
          type="button"
          onClick={handleLogin}
          disabled={loading}
          className="group relative w-full mt-5 inline-block p-px rounded-xl bg-gray-800 shadow-lg shadow-gray-950/10 transition-transform duration-300 ease-out motion-reduce:transition-none hover:scale-[1.02] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:scale-100 focus-visible:ring-2 focus-visible:ring-blue-400/60"
        >
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-xl bg-gradient-to-r from-teal-400 via-blue-500 to-purple-500 opacity-0 transition-opacity duration-500 motion-reduce:transition-none group-hover:opacity-100 group-focus-visible:opacity-100"
          />
          <span className="relative z-10 flex items-center justify-center gap-2 rounded-[11px] bg-gray-950 px-6 py-3 text-sm font-semibold text-white">
            {loading ? (
              <>
                <Loader2
                  className="w-4 h-4 motion-safe:animate-spin"
                  aria-hidden="true"
                />
                Signing in…
              </>
            ) : (
              <>
                Sign in
                <ArrowRight
                  className="w-4 h-4 transition-transform duration-300 motion-reduce:transition-none group-hover:translate-x-1"
                  aria-hidden="true"
                />
              </>
            )}
          </span>
        </button>

        <p className="text-center mt-5 text-xs text-gray-400">
          QA Team Internal Tool · v1.0.0
        </p>
      </div>
    </div>
  );
}
