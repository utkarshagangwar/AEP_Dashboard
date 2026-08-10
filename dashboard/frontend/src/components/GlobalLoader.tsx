"use client";

import { useEffect, useRef, useState } from "react";

import LoaderMascot from "./LoaderMascot";
import { Button } from "./ui/button";

// Zero: the loader must be fully visible on the frame the navigation starts.
// This used to be 90ms, which was an anti-strobe measure for sub-100ms
// transitions — but it also meant the mascot arrived after the backdrop, which
// read as lag on exactly the fast navigations it was meant to protect. The
// short fade below is enough to keep the appearance from being a hard cut.
const CONTENT_DELAY_MS = 0;
// Past this, stop implying progress and offer a way out. PRODUCT.md: never
// claim progress that isn't actually happening.
const CEILING_MS = 15000;

/**
 * The app's one loading state.
 *
 * Composition is the two approved loader designs and nothing else: the mascot
 * (global_loader_1) over the travelling capsule bar (global_loader_2). The
 * earlier drop-in entrance, heartbeat pulse dot, and four checklist tick
 * badges are gone — they animated a four-stage "time machine" whose stages
 * corresponded to nothing real, which is precisely the progress-theatre
 * PRODUCT.md rules out. The bar reads as indeterminate, which is the truth.
 *
 * Two things this deliberately does NOT do, both of which it used to:
 *
 * 1. It does not render `null` for the first N milliseconds. That gap meant a
 *    route's loading boundary painted nothing at all while the router worked,
 *    so the outgoing page showed through — the visible flash before the loader
 *    appeared. The opaque backdrop is now up on the first frame and only the
 *    contents are delayed, which keeps the anti-strobe benefit without the gap.
 * 2. It does not fetch the mascot over the network. See LoaderMascot.
 *
 * It is always fullscreen. The `fullscreen={false}` region variant is gone:
 * pages used it to render a loader *inside* AppShell, which meant the app's
 * chrome and the page's empty skeleton painted first and the loader appeared
 * in a box a beat later. Loading is now one overlay over everything, driven by
 * NavigationLoadingProvider — pages report `usePageLoading(isLoading)` instead
 * of rendering a loader themselves.
 */
export default function GlobalLoader() {
  const [pastCeiling, setPastCeiling] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  const mascotRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(media.matches);
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const ceilingTimer = window.setTimeout(() => setPastCeiling(true), CEILING_MS);
    return () => window.clearTimeout(ceilingTimer);
  }, []);

  // prefers-reduced-motion does not pause SMIL, so the mascot's jump has to be
  // stopped explicitly. Paused at time 0 it holds its resting pose, which is
  // the honest still frame of this animation.
  useEffect(() => {
    const svgEl = mascotRef.current;
    if (!svgEl) return;
    if (reducedMotion) {
      svgEl.setCurrentTime?.(0);
      svgEl.pauseAnimations?.();
    } else {
      svgEl.unpauseAnimations?.();
    }
  }, [reducedMotion, pastCeiling]);

  return (
    <div
      className="global-loader"
      role="status"
      aria-live="polite"
    >
      {pastCeiling ? (
        <div className="global-loader__content global-loader__ceiling">
          <p>This is taking unusually long.</p>
          <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      ) : (
        <div className="global-loader__content">
          {/* The mascot's box is sized to its resting pose; the jump apex
              overflows upward into space that is empty regardless. */}
          <div className="global-loader__mascot">
            <LoaderMascot ref={mascotRef} />
          </div>
          {/* global_loader_2: the word travels while the capsule stretches
              across and back. Purely decorative — the accessible name comes
              from the visually-hidden text below. */}
          <div className="global-loader__bar" aria-hidden="true">
            <span className="global-loader__bar-text">loading</span>
            <span className="global-loader__bar-fill" />
          </div>
          <span className="global-loader__sr">Loading…</span>
        </div>
      )}

      <style>{`
        .global-loader {
          position: fixed;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px;
          background: var(--background);
          /* Above the app shell and every in-page overlay (max is z-50), so a
             route transition genuinely covers what it is replacing. */
          z-index: 60;
        }
        .global-loader__content {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 16px;
          animation: global-loader-enter 140ms cubic-bezier(0.22, 1, 0.36, 1)
            ${CONTENT_DELAY_MS}ms both;
        }
        @keyframes global-loader-enter {
          from { opacity: 0; }
          to   { opacity: 1; }
        }

        /* ── global_loader_1 ─────────────────────────────────────────────── */
        .global-loader__mascot {
          position: relative;
          /* Sized to the RESTING pose only — see LoaderMascot's geometry note.
             240:370 viewBox at 95px wide puts the resting slice (156 of 370
             units) at almost exactly 62px tall. */
          width: 95px;
          height: 62px;
        }
        .global-loader__mascot svg {
          position: absolute;
          bottom: 0;
          left: 0;
          display: block;
          width: 100%;
          /* 95 / (240/370) — the full viewBox, bottom-anchored, so the jump
             leaves the box upward instead of being clipped or squashed. */
          height: 146px;
          overflow: visible;
        }

        /* ── global_loader_2 ─────────────────────────────────────────────── */
        .global-loader__bar {
          position: relative;
          width: 80px;
          height: 34px;
        }
        .global-loader__bar-text {
          position: absolute;
          top: 0;
          margin: 0;
          font-size: 0.8rem;
          letter-spacing: 1px;
          color: var(--muted-foreground);
          animation: global-loader-text 3.5s ease both infinite;
        }
        .global-loader__bar-fill {
          position: absolute;
          bottom: 0;
          display: block;
          width: 16px;
          height: 10px;
          border-radius: 50px;
          background-color: var(--loader-accent);
          transform: translateX(64px);
          animation: global-loader-fill 3.5s ease both infinite;
        }
        .global-loader__bar-fill::before {
          content: "";
          position: absolute;
          width: 100%;
          height: 100%;
          border-radius: inherit;
          background-color: color-mix(in oklch, var(--loader-accent), white 45%);
          animation: global-loader-fill-inner 3.5s ease both infinite;
        }
        @keyframes global-loader-text {
          0%   { letter-spacing: 1px; transform: translateX(0); }
          40%  { letter-spacing: 2px; transform: translateX(26px); }
          80%  { letter-spacing: 1px; transform: translateX(32px); }
          90%  { letter-spacing: 2px; transform: translateX(0); }
          100% { letter-spacing: 1px; transform: translateX(0); }
        }
        @keyframes global-loader-fill {
          0%   { width: 16px;  transform: translateX(0); }
          40%  { width: 100%;  transform: translateX(0); }
          80%  { width: 16px;  transform: translateX(64px); }
          90%  { width: 100%;  transform: translateX(0); }
          100% { width: 16px;  transform: translateX(0); }
        }
        @keyframes global-loader-fill-inner {
          0%   { width: 16px; transform: translateX(0); }
          40%  { width: 80%;  transform: translateX(0); }
          80%  { width: 100%; transform: translateX(0); }
          90%  { width: 80%;  transform: translateX(15px); }
          100% { width: 16px; transform: translateX(0); }
        }

        .global-loader__sr {
          position: absolute;
          width: 1px;
          height: 1px;
          padding: 0;
          margin: -1px;
          overflow: hidden;
          clip: rect(0, 0, 0, 0);
          white-space: nowrap;
          border: 0;
        }

        .global-loader__ceiling {
          gap: 12px;
          text-align: center;
        }
        .global-loader__ceiling p {
          margin: 0;
          font-size: 13px;
          color: var(--muted-foreground);
        }

        /* The bar's whole point is horizontal travel, so there's no honest
           reduced-motion variant of it — swap to a static capsule that just
           breathes opacity. The mascot's SMIL is paused in JS above. */
        @media (prefers-reduced-motion: reduce) {
          .global-loader__content {
            animation: none;
            opacity: 1;
          }
          .global-loader__bar-text,
          .global-loader__bar-fill,
          .global-loader__bar-fill::before {
            animation: none;
          }
          .global-loader__bar-text {
            transform: none;
          }
          .global-loader__bar-fill {
            width: 100%;
            transform: none;
            animation: global-loader-breathe 2s ease-in-out infinite;
          }
          .global-loader__bar-fill::before {
            width: 40%;
          }
        }
        @keyframes global-loader-breathe {
          0%, 100% { opacity: 0.45; }
          50%      { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
