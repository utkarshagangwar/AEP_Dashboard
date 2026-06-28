"use client";
import { useQuery } from "@tanstack/react-query";
import AppShell from "../../components/AppShell";
import { apiGet } from "../../utils/apiClient";

export default function ReportsLoading() {
  return (
    <AppShell>
      <div style={{ maxWidth: 1200 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 24,
          }}
        >
          <div>
            <div
              style={{
                height: 28,
                width: 200,
                background: "#F3F4F6",
                borderRadius: 6,
                marginBottom: 8,
              }}
            />
            <div
              style={{
                height: 14,
                width: 280,
                background: "#F3F4F6",
                borderRadius: 4,
              }}
            />
          </div>
        </div>

        {/* Summary skeleton */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 16,
            marginBottom: 24,
          }}
        >
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: "16px 20px",
              }}
            >
              <div
                style={{
                  height: 12,
                  width: 100,
                  background: "#F3F4F6",
                  borderRadius: 4,
                  marginBottom: 12,
                }}
              />
              <div
                style={{
                  height: 28,
                  width: 60,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
            </div>
          ))}
        </div>

        {/* Table skeleton */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            overflow: "hidden",
          }}
        >
          {[0, 1, 2, 3, 4].map((i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 20,
                padding: "14px 20px",
                borderBottom:
                  i < 4 ? "1px solid #F3F4F6" : "none",
                alignItems: "center",
              }}
            >
              <div
                style={{
                  height: 12,
                  width: 80,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
              <div
                style={{
                  height: 12,
                  width: 140,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
              <div
                style={{
                  height: 18,
                  width: 60,
                  background: "#F3F4F6",
                  borderRadius: 999,
                }}
              />
              <div
                style={{
                  height: 12,
                  width: 30,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
              <div
                style={{
                  height: 12,
                  width: 30,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
