"use client";

import { useEffect, useRef, useState } from "react";

// Delay before showing anything, so fast transitions don't flash a loader.
const SHOW_DELAY_MS = 180;
// Past this, stop implying progress and offer a way out. PRODUCT.md: never
// claim progress that isn't actually happening.
const CEILING_MS = 15000;

// Crop of the mascot's 0 0 500 500 canvas around its resting + jump-apex
// bounding box, so it fills the rig instead of floating in empty space.
const MASCOT_VIEWBOX = "150 70 230 410";

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
 * `fullscreen` (default) covers the viewport for route transitions.
 * Pass `fullscreen={false}` to fill a content region instead.
 */
export default function GlobalLoader({ fullscreen = true }: { fullscreen?: boolean }) {
  const [visible, setVisible] = useState(false);
  const [pastCeiling, setPastCeiling] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  const mascotRef = useRef<HTMLDivElement>(null);
  const mascotSvgElRef = useRef<SVGSVGElement | null>(null);
  const reducedMotionRef = useRef(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(media.matches);
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const showTimer = window.setTimeout(() => setVisible(true), SHOW_DELAY_MS);
    return () => window.clearTimeout(showTimer);
  }, []);

  // One timer for the only time-based state left. Replaces the old 100ms
  // polling interval, which existed to drive the checklist badges.
  useEffect(() => {
    if (!visible) return;
    const ceilingTimer = window.setTimeout(
      () => setPastCeiling(true),
      Math.max(0, CEILING_MS - SHOW_DELAY_MS),
    );
    return () => window.clearTimeout(ceilingTimer);
  }, [visible]);

  // Mirror the preference into a ref, and apply it to the SVG if it has
  // already landed. The ref exists because the fetch below is mount-scoped
  // and would otherwise close over a stale `reducedMotion`.
  useEffect(() => {
    reducedMotionRef.current = reducedMotion;
    const svgEl = mascotSvgElRef.current;
    if (!svgEl) return;
    if (reducedMotion) svgEl.pauseAnimations?.();
    else svgEl.unpauseAnimations?.();
  }, [reducedMotion]);

  // The mascot is a self-contained SMIL-animated SVG (no JS animation
  // library needed). It's fetched once and injected so we can (a) crop its
  // viewBox and (b) pause/resume its native animation for reduced-motion,
  // since prefers-reduced-motion doesn't pause SMIL automatically.
  //
  // Mount-scoped ([] deps) on purpose. The previous version keyed this on
  // `reducedMotion` and used a "have I fetched yet" ref to dedupe, which
  // broke under React StrictMode: the first pass was cancelled by its own
  // cleanup while the ref already read `true`, so the second pass skipped
  // the fetch entirely and the mascot never rendered in dev.
  useEffect(() => {
    let cancelled = false;
    fetch("/loader-orb.svg")
      .then((res) => res.text())
      .then((svgText) => {
        if (cancelled || !mascotRef.current) return;
        mascotRef.current.innerHTML = svgText;
        const svgEl = mascotRef.current.querySelector("svg");
        if (!svgEl) return;
        svgEl.setAttribute("viewBox", MASCOT_VIEWBOX);
        svgEl.setAttribute("aria-hidden", "true");
        mascotSvgElRef.current = svgEl;
        mascotRef.current.classList.add("is-loaded");
        // Read the ref, not the closed-over state: the preference may have
        // flipped while this request was in flight.
        if (reducedMotionRef.current) svgEl.pauseAnimations?.();
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      mascotSvgElRef.current = null;
    };
  }, []);

  if (!visible) return null;

  return (
    <div
      className={`global-loader${fullscreen ? " global-loader--fixed" : ""}`}
      role="status"
      aria-live="polite"
    >
      {pastCeiling ? (
        <div className="global-loader__ceiling">
          <p>This is taking unusually long.</p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      ) : (
        <>
          <div className="global-loader__mascot" ref={mascotRef} aria-hidden="true" />
          {/* global_loader_2: the word travels while the capsule stretches
              across and back. Purely decorative — the accessible name comes
              from the visually-hidden text below. */}
          <div className="global-loader__bar" aria-hidden="true">
            <span className="global-loader__bar-text">loading</span>
            <span className="global-loader__bar-fill" />
          </div>
          <span className="global-loader__sr">Loading…</span>
        </>
      )}

      <style>{`
        .global-loader {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 20px;
          width: 100%;
          min-height: min(60vh, 520px);
          padding: 24px;
          background: var(--background);
        }
        .global-loader--fixed {
          position: fixed;
          inset: 0;
          z-index: 50;
          min-height: 0;
        }
        .global-loader__mascot {
          width: 96px;
          height: 96px;
          opacity: 0;
          transition: opacity 0.3s ease-out;
        }
        .global-loader__mascot.is-loaded {
          opacity: 1;
        }
        .global-loader__mascot svg {
          display: block;
          width: 100%;
          height: 100%;
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
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 12px;
          text-align: center;
        }
        .global-loader__ceiling p {
          margin: 0;
          font-size: 13px;
          color: var(--muted-foreground);
        }
        .global-loader__ceiling button {
          border: 1px solid var(--border);
          background: var(--secondary);
          color: var(--secondary-foreground);
          border-radius: var(--radius);
          padding: 6px 16px;
          font-size: 13px;
          cursor: pointer;
        }
        .global-loader__ceiling button:hover {
          background: var(--accent);
        }

        /* The bar's whole point is horizontal travel, so there's no honest
           reduced-motion variant of it — swap to a static capsule that just
           breathes opacity. The mascot's SMIL is paused in JS above. */
        @media (prefers-reduced-motion: reduce) {
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
