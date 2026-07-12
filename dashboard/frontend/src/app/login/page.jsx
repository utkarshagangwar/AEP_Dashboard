"use client";
import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";

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
      localStorage.setItem("aep_access_token", data.access_token);
      if (data.refresh_token) {
        localStorage.setItem("aep_refresh_token", data.refresh_token);
      }
      // Set cookie for middleware auth
      document.cookie = `aep_token=${data.access_token}; path=/; max-age=${7 * 24 * 60 * 60}; SameSite=Lax`;

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
    <div
      style={{
        minHeight: "100vh",
        background: "#F9FAFB",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
      }}
    >
      <div style={{ width: "100%", maxWidth: 400 }}>
        {/* Brand */}
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <img
              src="/spider-logo.png"
              alt="AEP logo"
              width={84}
              height={53}
              style={{ flexShrink: 0, objectFit: "contain" }}
            />
          </div>
          <h1
            style={{
              fontSize: 24,
              fontWeight: 600,
              color: "#111827",
              margin: 0,
              letterSpacing: "-0.02em",
            }}
          >
            Automation Execution Platform (AEP)
          </h1>
          <p style={{ fontSize: 13, color: "#6B7280", marginTop: 6 }}>
            Sign in to your workspace
          </p>
        </div>

        {/* Card — no <form> tag; uses state + onClick per spec */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            padding: 32,
          }}
        >
          <div>
            <div style={{ marginBottom: 16 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  marginBottom: 6,
                }}
              >
                Email address
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleLogin();
                }}
                placeholder="you@company.com"
                autoComplete="email"
                style={{
                  width: "100%",
                  padding: "9px 12px",
                  fontSize: 14,
                  border: "1px solid #E5E7EB",
                  borderRadius: 8,
                  outline: "none",
                  color: "#111827",
                  background: "#fff",
                  boxSizing: "border-box",
                  transition: "border-color 0.15s",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
              />
            </div>

            <div style={{ marginBottom: 24 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#374151",
                  marginBottom: 6,
                }}
              >
                Password
              </label>
              <div style={{ position: "relative" }}>
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleLogin();
                  }}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  style={{
                    width: "100%",
                    padding: "9px 40px 9px 12px",
                    fontSize: 14,
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    outline: "none",
                    color: "#111827",
                    background: "#fff",
                    boxSizing: "border-box",
                    transition: "border-color 0.15s",
                  }}
                  onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                  onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  tabIndex={-1}
                  style={{
                    position: "absolute",
                    right: 10,
                    top: "50%",
                    transform: "translateY(-50%)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "none",
                    border: "none",
                    padding: 4,
                    cursor: "pointer",
                    color: "#6B7280",
                  }}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {error && (
              <div
                style={{
                  background: "#FEF2F2",
                  border: "1px solid #FECACA",
                  borderRadius: 8,
                  padding: "10px 12px",
                  marginBottom: 16,
                }}
              >
                <p style={{ fontSize: 13, color: "#DC2626", margin: 0 }}>
                  {error}
                </p>
              </div>
            )}

            <button
              type="button"
              onClick={handleLogin}
              disabled={loading}
              style={{
                width: "100%",
                padding: "10px 16px",
                background: loading ? "#93C5FD" : "#2563EB",
                color: "#fff",
                border: "none",
                borderRadius: 8,
                fontSize: 14,
                fontWeight: 600,
                cursor: loading ? "not-allowed" : "pointer",
                transition: "background 0.15s",
              }}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </div>
        </div>

        <p
          style={{
            textAlign: "center",
            marginTop: 20,
            fontSize: 12,
            color: "#9CA3AF",
          }}
        >
          QA Team Internal Tool · v1.0.0
        </p>
      </div>
    </div>
  );
}
