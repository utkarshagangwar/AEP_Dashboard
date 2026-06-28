"use client";
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import AppShell from "../../../components/AppShell";
import { apiGet, apiPost, apiPut, apiDelete } from "../../../utils/apiClient";
import { getStoredUser } from "../../../utils/authStore";

const ROLES = ["admin", "qa_lead", "qa_engineer", "developer", "viewer"];
const ROLE_COLORS = {
  admin: "#7C3AED",
  qa_lead: "#2563EB",
  qa_engineer: "#0891B2",
  developer: "#16A34A",
  viewer: "#6B7280",
};
const ROLE_BG = {
  admin: "#EDE9FE",
  qa_lead: "#DBEAFE",
  qa_engineer: "#CFFAFE",
  developer: "#DCFCE7",
  viewer: "#F3F4F6",
};

export default function UsersPage() {
  const qc = useQueryClient();
  const [user, setUser] = useState(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [editUser, setEditUser] = useState(null);
  const [form, setForm] = useState({
    email: "",
    password: "",
    full_name: "",
    role: "viewer",
  });
  const [formError, setFormError] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState(null);

  useEffect(() => {
    // Middleware handles auth + role checks; this is a fallback
    const stored = getStoredUser();
    setUser(stored);
  }, []);

  const { data, isLoading, error } = useQuery({
    queryKey: ["users", search, roleFilter],
    queryFn: () =>
      apiGet(
        `/api/users?search=${encodeURIComponent(search)}${roleFilter ? `&role=${roleFilter}` : ""}&limit=50`,
      ),
  });

  const createMutation = useMutation({
    mutationFn: (body) => apiPost("/api/users", body),
    onSuccess: () => {
      qc.invalidateQueries(["users"]);
      setShowModal(false);
      setForm({ email: "", password: "", full_name: "", role: "viewer" });
    },
    onError: (e) => setFormError(e.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, ...body }) => apiPut(`/api/users/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries(["users"]);
      setEditUser(null);
    },
    onError: (e) => setFormError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => apiDelete(`/api/users/${id}`),
    onSuccess: () => {
      qc.invalidateQueries(["users"]);
      setDeleteConfirm(null);
    },
  });

  const users = data?.data || [];

  if (!user) return null;

  return (
    <AppShell>
      <div style={{ maxWidth: 1000 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 24,
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 22,
                fontWeight: 600,
                color: "#111827",
                letterSpacing: "-0.02em",
              }}
            >
              User Management
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6B7280" }}>
              Manage team access and roles
            </p>
          </div>
          <button
            onClick={() => {
              setShowModal(true);
              setFormError("");
            }}
            style={{
              padding: "9px 16px",
              background: "#2563EB",
              color: "#fff",
              border: "none",
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + Invite User
          </button>
        </div>

        <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or email…"
            style={{
              padding: "8px 12px",
              fontSize: 13,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              width: 260,
              color: "#111827",
            }}
            onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
            onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
          />
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            style={{
              padding: "8px 12px",
              fontSize: 13,
              border: "1px solid #E5E7EB",
              borderRadius: 8,
              outline: "none",
              color: "#111827",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            <option value="">All Roles</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>

        {error && (
          <div
            style={{
              background: "#FEF2F2",
              border: "1px solid #FECACA",
              borderRadius: 8,
              padding: "12px 16px",
              marginBottom: 20,
            }}
          >
            <p style={{ margin: 0, fontSize: 13, color: "#DC2626" }}>
              {error.message}
            </p>
          </div>
        )}

        <div
          style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 12,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "2fr 2.5fr 1.2fr 80px 120px",
              padding: "10px 20px",
              borderBottom: "1px solid #E5E7EB",
              background: "#F9FAFB",
            }}
          >
            {["Name", "Email", "Role", "Status", "Actions"].map((h) => (
              <span
                key={h}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#6B7280",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                {h}
              </span>
            ))}
          </div>
          {isLoading ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              Loading…
            </div>
          ) : !users.length ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "#9CA3AF",
                fontSize: 13,
              }}
            >
              No users found.
            </div>
          ) : (
            users.map((u, i) => (
              <div
                key={u.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "2fr 2.5fr 1.2fr 80px 120px",
                  padding: "14px 20px",
                  borderBottom:
                    i < users.length - 1 ? "1px solid #F3F4F6" : "none",
                  alignItems: "center",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: "50%",
                      background: ROLE_BG[u.role],
                      border: "1px solid #E5E7EB",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: ROLE_COLORS[u.role],
                      }}
                    >
                      {u.full_name
                        ?.split(" ")
                        .map((w) => w[0])
                        .join("")
                        .toUpperCase()
                        .slice(0, 2)}
                    </span>
                  </div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 13,
                      fontWeight: 600,
                      color: "#111827",
                    }}
                  >
                    {u.full_name}
                  </p>
                </div>
                <p style={{ margin: 0, fontSize: 13, color: "#6B7280" }}>
                  {u.email}
                </p>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    background: ROLE_BG[u.role],
                    color: ROLE_COLORS[u.role],
                    border: "1px solid #E5E7EB",
                    borderRadius: 999,
                    padding: "2px 8px",
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                  }}
                >
                  {u.role.replace("_", " ")}
                </span>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    fontSize: 11,
                    fontWeight: 600,
                    color: u.is_active ? "#16A34A" : "#DC2626",
                    background: u.is_active ? "#DCFCE7" : "#FEE2E2",
                    border: "1px solid #E5E7EB",
                    borderRadius: 999,
                    padding: "2px 8px",
                  }}
                >
                  <span
                    style={{
                      width: 4,
                      height: 4,
                      borderRadius: "50%",
                      background: u.is_active ? "#16A34A" : "#DC2626",
                    }}
                  />
                  {u.is_active ? "Active" : "Inactive"}
                </span>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    onClick={() => {
                      setEditUser({
                        ...u,
                        newRole: u.role,
                        is_active: u.is_active,
                      });
                      setFormError("");
                    }}
                    style={{
                      fontSize: 11,
                      padding: "4px 8px",
                      border: "1px solid #E5E7EB",
                      borderRadius: 6,
                      background: "#fff",
                      color: "#374151",
                      cursor: "pointer",
                    }}
                  >
                    Edit
                  </button>
                  {user.id !== u.id && (
                    <button
                      onClick={() => setDeleteConfirm(u)}
                      style={{
                        fontSize: 11,
                        padding: "4px 8px",
                        border: "1px solid #FECACA",
                        borderRadius: 6,
                        background: "#FEF2F2",
                        color: "#DC2626",
                        cursor: "pointer",
                      }}
                    >
                      Remove
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Create User Modal */}
        {showModal && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 50,
            }}
          >
            <div
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: 28,
                width: 440,
                boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
              }}
            >
              <h2
                style={{
                  margin: "0 0 20px",
                  fontSize: 16,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Invite Team Member
              </h2>
              {[
                {
                  field: "full_name",
                  label: "Full Name",
                  type: "text",
                  placeholder: "Jane Smith",
                },
                {
                  field: "email",
                  label: "Email",
                  type: "email",
                  placeholder: "jane@company.com",
                },
                {
                  field: "password",
                  label: "Temporary Password",
                  type: "password",
                  placeholder: "8+ characters",
                },
              ].map(({ field, label, type, placeholder }) => (
                <div key={field} style={{ marginBottom: 14 }}>
                  <label
                    style={{
                      display: "block",
                      fontSize: 13,
                      fontWeight: 500,
                      color: "#374151",
                      marginBottom: 6,
                    }}
                  >
                    {label} *
                  </label>
                  <input
                    type={type}
                    value={form[field]}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, [field]: e.target.value }))
                    }
                    placeholder={placeholder}
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      fontSize: 13,
                      border: "1px solid #E5E7EB",
                      borderRadius: 8,
                      outline: "none",
                      boxSizing: "border-box",
                    }}
                    onFocus={(e) => (e.target.style.borderColor = "#2563EB")}
                    onBlur={(e) => (e.target.style.borderColor = "#E5E7EB")}
                  />
                </div>
              ))}
              <div style={{ marginBottom: 20 }}>
                <label
                  style={{
                    display: "block",
                    fontSize: 13,
                    fontWeight: 500,
                    color: "#374151",
                    marginBottom: 6,
                  }}
                >
                  Role
                </label>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {ROLES.map((r) => (
                    <button
                      key={r}
                      onClick={() => setForm((f) => ({ ...f, role: r }))}
                      style={{
                        padding: "5px 10px",
                        fontSize: 11,
                        fontWeight: form.role === r ? 600 : 400,
                        border: `1px solid ${form.role === r ? ROLE_COLORS[r] : "#E5E7EB"}`,
                        borderRadius: 999,
                        background: form.role === r ? ROLE_BG[r] : "#fff",
                        color: form.role === r ? ROLE_COLORS[r] : "#6B7280",
                        cursor: "pointer",
                        textTransform: "uppercase",
                      }}
                    >
                      {r.replace("_", " ")}
                    </button>
                  ))}
                </div>
              </div>
              {formError && (
                <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                  {formError}
                </p>
              )}
              <div
                style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
              >
                <button
                  onClick={() => setShowModal(false)}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 500,
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    background: "#fff",
                    cursor: "pointer",
                    color: "#374151",
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={() => createMutation.mutate(form)}
                  disabled={createMutation.isPending}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 600,
                    background: "#2563EB",
                    color: "#fff",
                    border: "none",
                    borderRadius: 8,
                    cursor: "pointer",
                  }}
                >
                  {createMutation.isPending ? "Creating…" : "Create User"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Edit Modal */}
        {editUser && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 50,
            }}
          >
            <div
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: 28,
                width: 420,
                boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
              }}
            >
              <h2
                style={{
                  margin: "0 0 6px",
                  fontSize: 16,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Edit User
              </h2>
              <p style={{ margin: "0 0 20px", fontSize: 13, color: "#6B7280" }}>
                {editUser.email}
              </p>
              <div style={{ marginBottom: 16 }}>
                <label
                  style={{
                    display: "block",
                    fontSize: 13,
                    fontWeight: 500,
                    color: "#374151",
                    marginBottom: 8,
                  }}
                >
                  Role
                </label>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {ROLES.map((r) => (
                    <button
                      key={r}
                      onClick={() => setEditUser((u) => ({ ...u, newRole: r }))}
                      style={{
                        padding: "5px 10px",
                        fontSize: 11,
                        fontWeight: editUser.newRole === r ? 600 : 400,
                        border: `1px solid ${editUser.newRole === r ? ROLE_COLORS[r] : "#E5E7EB"}`,
                        borderRadius: 999,
                        background:
                          editUser.newRole === r ? ROLE_BG[r] : "#fff",
                        color:
                          editUser.newRole === r ? ROLE_COLORS[r] : "#6B7280",
                        cursor: "pointer",
                        textTransform: "uppercase",
                      }}
                    >
                      {r.replace("_", " ")}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ marginBottom: 20 }}>
                <label
                  style={{
                    display: "block",
                    fontSize: 13,
                    fontWeight: 500,
                    color: "#374151",
                    marginBottom: 8,
                  }}
                >
                  Account Status
                </label>
                <div style={{ display: "flex", gap: 8 }}>
                  {[true, false].map((active) => (
                    <button
                      key={String(active)}
                      onClick={() =>
                        setEditUser((u) => ({ ...u, is_active: active }))
                      }
                      style={{
                        flex: 1,
                        padding: "8px",
                        fontSize: 12,
                        fontWeight: editUser.is_active === active ? 600 : 400,
                        border: `1px solid ${editUser.is_active === active ? (active ? "#16A34A" : "#DC2626") : "#E5E7EB"}`,
                        borderRadius: 8,
                        background:
                          editUser.is_active === active
                            ? active
                              ? "#DCFCE7"
                              : "#FEE2E2"
                            : "#fff",
                        color:
                          editUser.is_active === active
                            ? active
                              ? "#16A34A"
                              : "#DC2626"
                            : "#374151",
                        cursor: "pointer",
                      }}
                    >
                      {active ? "Active" : "Inactive"}
                    </button>
                  ))}
                </div>
              </div>
              {formError && (
                <p style={{ fontSize: 13, color: "#DC2626", marginBottom: 12 }}>
                  {formError}
                </p>
              )}
              <div
                style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
              >
                <button
                  onClick={() => setEditUser(null)}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 500,
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    background: "#fff",
                    cursor: "pointer",
                    color: "#374151",
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={() =>
                    updateMutation.mutate({
                      id: editUser.id,
                      role: editUser.newRole,
                      is_active: editUser.is_active,
                    })
                  }
                  disabled={updateMutation.isPending}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 600,
                    background: "#2563EB",
                    color: "#fff",
                    border: "none",
                    borderRadius: 8,
                    cursor: "pointer",
                  }}
                >
                  {updateMutation.isPending ? "Saving…" : "Save Changes"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Delete Confirm */}
        {deleteConfirm && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 50,
            }}
          >
            <div
              style={{
                background: "#fff",
                border: "1px solid #E5E7EB",
                borderRadius: 12,
                padding: 28,
                width: 380,
                boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
              }}
            >
              <h2
                style={{
                  margin: "0 0 10px",
                  fontSize: 16,
                  fontWeight: 600,
                  color: "#111827",
                }}
              >
                Remove User
              </h2>
              <p style={{ fontSize: 13, color: "#6B7280", margin: "0 0 20px" }}>
                Remove <strong>{deleteConfirm.full_name}</strong> (
                {deleteConfirm.email}) from the platform? This cannot be undone.
              </p>
              <div
                style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
              >
                <button
                  onClick={() => setDeleteConfirm(null)}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 500,
                    border: "1px solid #E5E7EB",
                    borderRadius: 8,
                    background: "#fff",
                    cursor: "pointer",
                    color: "#374151",
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={() => deleteMutation.mutate(deleteConfirm.id)}
                  disabled={deleteMutation.isPending}
                  style={{
                    padding: "8px 16px",
                    fontSize: 13,
                    fontWeight: 600,
                    background: "#DC2626",
                    color: "#fff",
                    border: "none",
                    borderRadius: 8,
                    cursor: "pointer",
                  }}
                >
                  {deleteMutation.isPending ? "Removing…" : "Remove User"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
