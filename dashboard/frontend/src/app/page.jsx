"use client";
import { useEffect } from "react";
import GlobalLoader from "../components/GlobalLoader";
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

  // The redirect above fires on mount, so this is usually never painted —
  // GlobalLoader's own 180ms show-delay keeps it from flashing on the way out.
  return <GlobalLoader />;
}
