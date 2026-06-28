"use client";

import { useRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
  return <QueryClientProvider client={queryClientRef.current}>{children}</QueryClientProvider>;
}
