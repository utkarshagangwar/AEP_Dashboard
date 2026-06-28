"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { apiGet } from "@/utils/apiClient";

export interface AuthUser {
  id: string;
  email: string;
  role: string;
  full_name?: string;
  created_at: string;
}

interface AuthContextType {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  error: null,
  refresh: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchUser() {
    try {
      setLoading(true);
      setError(null);
      const data = await apiGet("/api/auth/me");
      setUser(data);
    } catch (err: any) {
      setError(err.message || "Failed to load user");
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchUser();
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, error, refresh: fetchUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
