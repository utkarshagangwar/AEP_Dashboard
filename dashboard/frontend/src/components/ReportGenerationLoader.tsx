"use client";

import { useEffect, useRef, useState } from "react";

export default function ReportGenerationLoader() {
  const [reducedMotion, setReducedMotion] = useState(false);
  const sceneRef = useRef<HTMLDivElement>(null);
  const sceneSvgElRef = useRef<SVGSVGElement | null>(null);
  const reducedMotionRef = useRef(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(media.matches);
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  // Mirror the preference into a ref, and apply it to the SVG if it has
  // already landed. The ref exists because the fetch below is mount-scoped
  // and would otherwise close over a stale `reducedMotion`.
  useEffect(() => {
    reducedMotionRef.current = reducedMotion;
    const svgEl = sceneSvgElRef.current;
    if (!svgEl) return;
    if (reducedMotion) svgEl.pauseAnimations?.();
    else svgEl.unpauseAnimations?.();
  }, [reducedMotion]);

  // Self-contained SMIL-animated illustration (no JS animation library
  // needed). Fetched once and injected so its native animation can be
  // paused for reduced-motion, which prefers-reduced-motion doesn't do
  // automatically for SMIL.
  //
  // Mount-scoped ([] deps) on purpose. The previous version keyed this on
  // `reducedMotion` and used a "have I fetched yet" ref to dedupe, which
  // broke under React StrictMode: the first pass was cancelled by its own
  // cleanup while the ref already read `true`, so the second pass skipped
  // the fetch entirely and the scene never rendered in dev.
  useEffect(() => {
    let cancelled = false;
    fetch("/report-loading-scene.svg")
      .then((res) => res.text())
      .then((svgText) => {
        if (cancelled || !sceneRef.current) return;
        sceneRef.current.innerHTML = svgText;
        const svgEl = sceneRef.current.querySelector("svg");
        if (!svgEl) return;
        svgEl.setAttribute("aria-hidden", "true");
        sceneSvgElRef.current = svgEl;
        sceneRef.current.classList.add("is-loaded");
        // Read the ref, not the closed-over state: the preference may have
        // flipped while this request was in flight.
        if (reducedMotionRef.current) svgEl.pauseAnimations?.();
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      sceneSvgElRef.current = null;
    };
  }, []);

  return (
    <div className="report-loader" role="status" aria-live="polite">
      <div ref={sceneRef} className="report-loader__scene" aria-hidden="true" />
      <p className="report-loader__status">Putting your report together…</p>

      <style>{`
        /* In-flow, not a fixed overlay: this loader renders inside AppShell
           so the sidebar/nav stay visible and usable while a report loads —
           matching the skeleton this replaced. */
        .report-loader {
          width: 100%;
          min-height: min(60vh, 520px);
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 18px;
          background: var(--background);
          padding: 24px;
          animation: report-loader-fade-in 0.25s ease-out;
        }
        @keyframes report-loader-fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        .report-loader__scene {
          width: min(90vw, 480px);
          aspect-ratio: 16 / 9;
          opacity: 0;
          transition: opacity 0.3s ease-out;
        }
        .report-loader__scene.is-loaded {
          opacity: 1;
        }
        .report-loader__scene svg {
          display: block;
          width: 100%;
          height: 100%;
        }
        .report-loader__status {
          margin: 0;
          font-size: 13px;
          color: var(--muted-foreground);
        }
        @media (prefers-reduced-motion: reduce) {
          .report-loader {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
