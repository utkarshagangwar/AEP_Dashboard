"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getStoredUser } from "../../utils/authStore";

// Stable sidebar destination. Existing /projects and /execute links remain
// canonical, so saved links and active executions keep their current scope.
export default function ScriptRunPage() {
  const router = useRouter();

  useEffect(() => {
    const user = getStoredUser();
    const permissions = user?.permissions || [];
    router.replace(
      user?.role === "admin" || permissions.includes("projects") ? "/projects" : "/execute"
    );
  }, [router]);

  return null;
}
