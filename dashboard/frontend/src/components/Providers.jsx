"use client";

import { useRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

// The pre-emptive "redeem the refresh cookie before anything else fires"
// logic that used to live here has moved to module-load time in
// utils/apiClient.js. A useEffect here runs too late to help: React fires
// child mount effects (including every page's useQuery) before this
// component's own effect, so by the time this ran, the first wave of
// queries had already gone out token-less. See apiClient.js for the fix.

export default function Providers({ children }) {
  const queryClientRef = useRef(null);
  if (!queryClientRef.current) {
    queryClientRef.current = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 1000 * 60 * 5,
          cacheTime: 1000 * 60 * 30,
          retry: 1,
          refetchOnWindowFocus: false,
        },
      },
    });
  }

  return (
    <QueryClientProvider client={queryClientRef.current}>
      {/* Short delay rather than 0: these tooltips sit on table-row action
          icons, and an instant popup on every pointer cross is noise. */}
      <TooltipProvider delay={350}>
        {children}
        {/* Bottom-right so it never covers the sidebar nav or a page's
            top-right primary actions. */}
        <Toaster position="bottom-right" />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
