"use client";

import { useEffect, useRef, useState } from "react";

export default function NotFound() {
  const [reducedMotion, setReducedMotion] = useState(false);
  const sceneRef = useRef(null);
  const sceneSvgElRef = useRef(null);
  const reducedMotionRef = useRef(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(media.matches);
    const onChange = (e) => setReducedMotion(e.matches);
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
    fetch("/error-404-scene.svg")
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
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-6 py-12">
      <div className="w-full max-w-[420px] flex flex-col items-center text-center">
        <div ref={sceneRef} className="not-found__scene" />

        <h1 className="mt-2 text-2xl font-semibold text-gray-900 tracking-[-0.02em] m-0">
          Page not found
        </h1>
        <p className="mt-2 text-sm text-gray-500">
          The page you&rsquo;re looking for doesn&rsquo;t exist or may have moved.
        </p>
        <a
          href="/"
          className="mt-6 inline-flex items-center justify-center rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-gray-800 focus-visible:ring-2 focus-visible:ring-blue-400/60"
        >
          Back to AEP
        </a>
      </div>

      <style>{`
        .not-found__scene {
          width: min(80vw, 320px);
          aspect-ratio: 1 / 1;
          opacity: 0;
          transition: opacity 0.3s ease-out;
        }
        .not-found__scene.is-loaded {
          opacity: 1;
        }
        .not-found__scene svg {
          display: block;
          width: 100%;
          height: 100%;
        }
      `}</style>
    </div>
  );
}
