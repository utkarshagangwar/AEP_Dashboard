"use client";
import AppShell from "../../../components/AppShell";

export default function ReportDetailError({ error, reset }) {
  return (
    <AppShell>
      <div style={{ maxWidth: 1100 }}>
        <div
          style={{
            background: "#FEF2F2",
            border: "1px solid #FECACA",
            borderRadius: 12,
            padding: "24px 32px",
            textAlign: "center",
          }}
        >
          <h2
            style={{
              margin: "0 0 8px",
              fontSize: 16,
              fontWeight: 600,
              color: "#DC2626",
            }}
          >
            Failed to load report
          </h2>
          <p style={{ margin: "0 0 16px", fontSize: 13, color: "#7F1D1D" }}>
            {error?.message || "An unexpected error occurred."}
          </p>
          <button
            onClick={reset}
            style={{
              padding: "8px 20px",
              background: "#DC2626",
              color: "#fff",
              border: "none",
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </div>
    </AppShell>
  );
}
