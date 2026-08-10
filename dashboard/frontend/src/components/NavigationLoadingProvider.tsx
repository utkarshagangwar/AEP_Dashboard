"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname } from "next/navigation";

import GlobalLoader from "./GlobalLoader";

/**
 * The app's single loading overlay.
 *
 * Before this existed there were effectively two loaders: the fullscreen one in
 * each route's `loading.jsx`, which almost never appeared (every page is a
 * client component, so the router has nothing to suspend on), and a boxed
 * `<GlobalLoader fullscreen={false} />` rendered *inside* AppShell by each page
 * while its react-query fetch was in flight. The boxed one is what people
 * actually saw, and because it only mounts after the destination page has
 * mounted, the sequence was: click → nothing → new page's chrome/skeleton →
 * box appears. That gap is the lag this component removes.
 *
 * Two signals turn the overlay on, and it stays up while EITHER is true:
 *
 *   1. `navPending` — set synchronously from a capture-phase click on any
 *      internal link, i.e. in the same event that starts the navigation and
 *      before the router has done any work. Nothing of the next page can paint
 *      first, because the overlay is already up when the router begins.
 *   2. `pageLoading` — the destination page's own data-loading flag, reported
 *      through `usePageLoading`. Child effects run before parent effects, so
 *      the new page has already reported `true` by the time the pathname
 *      effect below clears `navPending`; the handoff has no gap.
 *
 * Mounted in Providers, i.e. outside AppShell, so it covers the sidebar too.
 */

type NavigationLoadingValue = {
  /** Turn the overlay on for a navigation this component can't observe (router.push). */
  startNavigation: () => void;
  report: (id: number, loading: boolean) => void;
};

const NavigationLoadingContext = createContext<NavigationLoadingValue | null>(null);

// Backstop only. The overlay's own 15s ceiling shows "taking unusually long"
// with a Reload; this exists so a navigation that never completes (blocked
// route, aborted push) can't leave the app permanently covered.
const NAV_PENDING_MAX_MS = 20000;

let nextLoaderId = 1;

export default function NavigationLoadingProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [navPending, setNavPending] = useState(false);
  // A Set rather than a boolean: a page and a nested section could both report,
  // and the overlay should lift only when the last of them is done.
  const [loadingIds, setLoadingIds] = useState<Set<number>>(() => new Set());

  const startNavigation = useCallback(() => setNavPending(true), []);

  const report = useCallback((id: number, loading: boolean) => {
    setLoadingIds((prev) => {
      if (loading === prev.has(id)) return prev;
      const next = new Set(prev);
      if (loading) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  // The destination page has mounted (and, if it fetches, already reported
  // itself as loading). The click-triggered flag has done its job.
  useEffect(() => {
    setNavPending(false);
  }, [pathname]);

  useEffect(() => {
    if (!navPending) return;
    const timer = window.setTimeout(() => setNavPending(false), NAV_PENDING_MAX_MS);
    return () => window.clearTimeout(timer);
  }, [navPending]);

  // Capture phase on document: this runs before Next's Link click handler, so
  // the overlay is committed as part of the same click that starts the route
  // change. One listener covers every internal link in the app — sidebar nav,
  // table rows, breadcrumbs — with no per-link wiring to keep in sync.
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      // Modified clicks open a new tab; this tab isn't going anywhere.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
        return;

      const target = event.target as Element | null;
      const anchor = target?.closest?.("a");
      if (!anchor) return;
      if (anchor.hasAttribute("download")) return;
      const anchorTarget = anchor.getAttribute("target");
      if (anchorTarget && anchorTarget !== "_self") return;

      const href = anchor.getAttribute("href");
      // Internal, same-origin, not a hash or protocol link.
      if (!href || !href.startsWith("/") || href.startsWith("//")) return;

      const url = new URL(href, window.location.href);
      // Re-clicking the current route navigates nowhere — an overlay there
      // would flash for no reason and then sit until the backstop fires.
      if (url.pathname === window.location.pathname) return;

      setNavPending(true);
    };

    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);

  // Back/forward: no click to intercept, so hook the history event instead.
  useEffect(() => {
    const onPopState = () => setNavPending(true);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const visible = navPending || loadingIds.size > 0;

  // Memoised, and both callbacks are stable: a fresh object here would change
  // the identity `usePageLoading` depends on, so its effect would re-run on
  // every provider render — cleanup reporting false, effect reporting true,
  // each flip re-rendering the provider. That's an infinite loop, and React
  // does report it as "Maximum update depth exceeded".
  const value = useMemo(
    () => ({ startNavigation, report }),
    [startNavigation, report],
  );

  return (
    <NavigationLoadingContext.Provider value={value}>
      {children}
      {visible && <GlobalLoader />}
    </NavigationLoadingContext.Provider>
  );
}

/**
 * Hand a page's loading flag to the global overlay.
 *
 * Replaces `{isLoading && <GlobalLoader fullscreen={false} />}`: the page no
 * longer renders a loader of its own, it just says whether it is loading and
 * the one overlay above the whole app responds.
 *
 *   const { data, isLoading } = useQuery(...)
 *   usePageLoading(isLoading)
 *
 * Must be called unconditionally, above any early return, like any other hook.
 */
export function usePageLoading(isLoading: boolean) {
  const ctx = useContext(NavigationLoadingContext);
  const idRef = useRef<number | null>(null);
  if (idRef.current === null) idRef.current = nextLoaderId++;

  useEffect(() => {
    const id = idRef.current as number;
    ctx?.report(id, isLoading);
    // Unmounting mid-load (navigating away from a page still fetching) must
    // release the overlay, or the next route inherits a stuck loader.
    return () => ctx?.report(id, false);
  }, [ctx, isLoading]);
}

/** Escape hatch for programmatic navigation (`router.push`), which has no click to catch. */
export function useStartNavigation() {
  const ctx = useContext(NavigationLoadingContext);
  return ctx?.startNavigation ?? (() => {});
}
