"use client";
import AppShell from "../../../components/AppShell";

export default function ReportDetailLoading() {
  return (
    <AppShell>
      <div style={{ maxWidth: 1100 }}>
        {/* Back link skeleton */}
        <div
          style={{
            height: 14,
            width: 80,
            background: "#F3F4F6",
            borderRadius: 4,
            marginBottom: 16,
          }}
        />
        {/* Title skeleton */}
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
            height: 12,
            width: 100,
            background: "#F3F4F6",
            borderRadius: 4,
            marginBottom: 28,
          }}
        />

        {/* Summary cards skeleton */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(5, 1fr)",
            gap: 14,
            marginBottom: 24,
          }}
        >
          {[0, 1, 2, 3, 4].map((i) => (
            <div
              key={i}
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: "14px 18px",
              }}
            >
              <div
                style={{
                  height: 10,
                  width: 60,
                  background: "#F3F4F6",
                  borderRadius: 4,
                  marginBottom: 10,
                }}
              />
              <div
                style={{
                  height: 24,
                  width: 40,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
            </div>
          ))}
        </div>

        {/* Metadata skeleton */}
        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            padding: "14px 20px",
            marginBottom: 24,
            display: "flex",
            gap: 32,
          }}
        >
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div
              key={i}
              style={{
                height: 12,
                width: 80 + i * 10,
                background: "#F3F4F6",
                borderRadius: 4,
              }}
            />
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
                display: "grid",
                gridTemplateColumns: "3fr 1fr 1fr 1fr 1.5fr 100px",
                padding: "12px 20px",
                borderBottom:
                  i < 4 ? "1px solid #F3F4F6" : "none",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div
                style={{
                  height: 12,
                  width: "80%",
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
              <div
                style={{
                  height: 18,
                  width: 50,
                  background: "#F3F4F6",
                  borderRadius: 999,
                }}
              />
              <div
                style={{
                  height: 12,
                  width: 40,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
              <div
                style={{
                  height: 12,
                  width: 20,
                  background: "#F3F4F6",
                  borderRadius: 4,
                }}
              />
              <div
                style={{
                  height: 12,
                  width: "60%",
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
