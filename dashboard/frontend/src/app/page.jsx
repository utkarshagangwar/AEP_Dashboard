"use client";
import { useEffect } from "react";
import { getStoredUser } from "../utils/authStore";

export default function RootPage() {
  useEffect(() => {
    // The access token itself now lives in memory only, so it's already
    // gone by the time this runs on a fresh load — the cached user profile
    // (still in localStorage) is the signal here instead. Middleware is the
    // real gate; this is just picking an initial redirect target.
    const user = getStoredUser();
    window.location.href = user ? "/dashboard" : "/login";
  }, []);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
        background: "#F9FAFB",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            width: 24,
            height: 24,
            border: "2px solid #2563EB",
            borderTopColor: "transparent",
            borderRadius: "50%",
            animation: "spin 0.8s linear infinite",
          }}
        />
        <span
          style={{
            fontFamily: "Inter, sans-serif",
            color: "#6B7280",
            fontSize: 14,
          }}
        >
          Loading…
        </span>
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
