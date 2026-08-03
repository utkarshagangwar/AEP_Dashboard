"use client"

import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"

interface SuccessCheckProps extends React.ComponentProps<"div"> {
  size?: number
  label?: string
}

// Self-contained SMIL-animated checkmark badge (no JS animation library
// needed): a ring swoops in and settles behind a drawn checkmark. Fetched
// once and injected so its native animation can be paused for
// reduced-motion, which prefers-reduced-motion doesn't do automatically
// for SMIL.
function SuccessCheck({
  size = 64,
  label = "Success",
  className,
  ...props
}: SuccessCheckProps) {
  const [reducedMotion, setReducedMotion] = useState(false)
  const sceneRef = useRef<HTMLDivElement>(null)
  const sceneSvgElRef = useRef<SVGSVGElement | null>(null)
  const reducedMotionRef = useRef(false)

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)")
    setReducedMotion(media.matches)
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches)
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [])

  // Mirror the preference into a ref, and apply it to the SVG if it has
  // already landed. The ref exists because the fetch below is mount-scoped
  // and would otherwise close over a stale `reducedMotion`.
  useEffect(() => {
    reducedMotionRef.current = reducedMotion
    const svgEl = sceneSvgElRef.current
    if (!svgEl) return
    if (reducedMotion) svgEl.pauseAnimations?.()
    else svgEl.unpauseAnimations?.()
  }, [reducedMotion])

  // Mount-scoped ([] deps) on purpose. The previous version keyed this on
  // `reducedMotion` and used a "have I fetched yet" ref to dedupe, which
  // broke under React StrictMode: the first pass was cancelled by its own
  // cleanup while the ref already read `true`, so the second pass skipped
  // the fetch entirely and the badge never rendered in dev.
  useEffect(() => {
    let cancelled = false
    fetch("/success-scene.svg")
      .then((res) => res.text())
      .then((svgText) => {
        if (cancelled || !sceneRef.current) return
        sceneRef.current.innerHTML = svgText
        const svgEl = sceneRef.current.querySelector("svg")
        if (!svgEl) return
        svgEl.setAttribute("aria-hidden", "true")
        sceneSvgElRef.current = svgEl
        sceneRef.current.classList.add("is-loaded")
        // Read the ref, not the closed-over state: the preference may have
        // flipped while this request was in flight.
        if (reducedMotionRef.current) svgEl.pauseAnimations?.()
      })
      .catch(() => {})
    return () => {
      cancelled = true
      sceneSvgElRef.current = null
    }
  }, [])

  return (
    <div
      data-slot="success-check"
      role="img"
      aria-label={label}
      className={cn("success-check", className)}
      style={{ width: size, height: size }}
      {...props}
    >
      <div ref={sceneRef} className="success-check__scene" />
      <style>{`
        .success-check__scene {
          width: 100%;
          height: 100%;
          opacity: 0;
          transition: opacity 0.3s ease-out;
        }
        .success-check__scene.is-loaded {
          opacity: 1;
        }
        .success-check__scene svg {
          display: block;
          width: 100%;
          height: 100%;
        }
      `}</style>
    </div>
  )
}

export { SuccessCheck }
