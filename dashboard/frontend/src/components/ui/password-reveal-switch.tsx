"use client"

import * as React from "react"

import { cn } from "@/lib/utils"
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip"

/**
 * Is the surface this element sits on a dark one?
 *
 * The tooltip is portalled to <body>, so it cannot inherit anything from the
 * card it visually belongs to — a CSS variable set on the login card never
 * reaches it. The only way for it to know what it is sitting on is to measure.
 *
 * Walks up from the element compositing background colours until they add up
 * to opaque. Compositing matters here rather than "first background wins":
 * the login field wells are `bg-white/[0.04]` over `bg-gray-950`, so the first
 * colour found is a 4%-alpha white — read naively that says "light surface"
 * when the surface is nearly black.
 */
function surfaceIsDark(el: HTMLElement | null): boolean {
  if (!el || typeof window === "undefined") return false

  type Layer = { r: number; g: number; b: number; a: number }
  const layers: Layer[] = []

  for (let node: HTMLElement | null = el; node; node = node.parentElement) {
    const parsed = getComputedStyle(node).backgroundColor.match(
      /rgba?\(([^)]+)\)/
    )
    if (!parsed) continue
    const [r, g, b, a = 1] = parsed[1].split(",").map((n) => parseFloat(n))
    if (!a) continue
    layers.push({ r, g, b, a })
    if (a >= 1) break
  }

  if (!layers.length) return false

  // Farthest ancestor is the base; each nearer layer paints over it.
  let { r, g, b } = layers[layers.length - 1]
  for (let i = layers.length - 2; i >= 0; i--) {
    const l = layers[i]
    r = l.r * l.a + r * (1 - l.a)
    g = l.g * l.a + g * (1 - l.a)
    b = l.b * l.a + b * (1 - l.a)
  }

  // Rec. 601 luma — close enough for a light/dark decision, and cheaper than
  // a full relative-luminance conversion.
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5
}

interface PasswordRevealSwitchProps
  extends Omit<
    React.ComponentProps<"input">,
    "type" | "checked" | "onChange" | "defaultChecked"
  > {
  /** Whether the password is currently visible as plain text. */
  revealed: boolean
  onRevealedChange: (revealed: boolean) => void
  /** Hide the adjacent Show/Hide text (the switch keeps its aria-label). */
  hideLabel?: boolean
  labelClassName?: string
  wrapperClassName?: string
}

/**
 * The one show/hide control for password inputs, so every password field in
 * the app reveals the same way.
 *
 * The state lives with the caller rather than in here: the input's `type` has
 * to swap between "password" and "text" at the call site anyway, so owning it
 * here would just mean handing the same boolean straight back out.
 *
 * The visual is a physical rocker switch — red while the password is masked,
 * green while it is visible, with the knob travelling the length of the body.
 * Two notes on the implementation:
 *
 *   - It is a real `<input type="checkbox">` inside a `<label>`, not a div with
 *     a click handler. Space toggles it, it participates in tab order, and
 *     screen readers announce it as a checkbox — none of which comes for free
 *     with a styled div.
 *   - The design was supplied as a styled-components snippet. This project has
 *     no CSS-in-JS runtime (Tailwind plus scoped <style>, as in GlobalLoader
 *     and AppShell), so the same rules ship as a scoped stylesheet rather than
 *     pulling in a dependency for one control. The geometry is unchanged; only
 *     the base font-size is fixed at 13.333px, which puts the switch at exactly
 *     44px tall — the minimum touch target, and a clean match for the field
 *     heights it sits beside.
 */

const SWITCH_CSS = `
  .aep-prs {
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    /* 8px + the switch's own 8px margin, less the 8px knob overhang, leaves
       8px of visual air between the knob and the Show/Hide text. */
    gap: 8px;
  }

  /* The switch — the box around the slider. 3.3em at this base = 44px. */
  .aep-prs__switch {
    font-size: 13.333px;
    position: relative;
    display: inline-block;
    width: 1.2em;
    height: 3.3em;
    flex-shrink: 0;
    /* The knob is 2.4em wide against a 1.2em body, so it overhangs by 0.6em
       (8px) on each side. Layout only reserves the body, so without this the
       knob visually collides with whatever sits either side of the switch. */
    margin-inline: 8px;
  }
  /* The body is only 16px wide, which is a small pointer target on its own.
     This transparent pseudo-element widens the hit area without changing the
     drawn switch. Kept narrow enough not to overlap the field beside it. */
  .aep-prs__switch::before {
    content: "";
    position: absolute;
    inset: -4px -8px;
  }

  /* Hide default HTML checkbox */
  .aep-prs__chk {
    position: absolute;
    opacity: 0;
    width: 0;
    height: 0;
    margin: 0;
  }

  /* The slider */
  .aep-prs__slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    /* Masked is the resting state, and red at rest reads as an error on a
       login screen when nothing is wrong. Neutral grey while hidden, colour
       only for the state that is actually happening. This grey clears 3:1
       against both surfaces the switch appears on (the dark terminal well and
       the dialog's white), so the control's edge stays perceivable. */
    background-color: rgb(122, 122, 122);
    transition: 0.4s;
    border-radius: 5px;
  }

  .aep-prs__slider::before {
    position: absolute;
    content: "";
    height: 0.5em;
    width: 2.4em;
    border-radius: 5px;
    left: -0.6em;
    top: 0.2em;
    background-color: rgb(68, 66, 66);
    box-shadow: 0 6px 7px rgba(0, 0, 0, 0.3);
    transition: 0.4s;
  }

  .aep-prs__slider::before,
  .aep-prs__slider::after {
    content: "";
    display: block;
  }

  .aep-prs__slider::after {
    background:
      linear-gradient(transparent 50%, rgba(255, 255, 255, 0.15) 0) 0 50% / 50% 100%,
      repeating-linear-gradient(
          90deg,
          rgb(78, 78, 78) 0,
          rgb(141, 135, 135),
          rgb(97, 96, 96) 20%,
          rgb(97, 95, 95) 20%,
          rgb(99, 99, 99) 40%
        )
        0 50% / 50% 100%,
      radial-gradient(circle at 50% 50%, rgb(95, 94, 94) 25%, transparent 26%);
    background-repeat: no-repeat;
    border: 0.25em solid transparent;
    border-left: 0.4em solid #464646;
    border-right: 0 solid transparent;
    transition: border-left-color 0.1s 0.3s ease-out, transform 0.3s ease-out;
    transform: translateX(-22.5%) rotate(90deg);
    transform-origin: 25% 50%;
    position: relative;
    top: 0.5em;
    left: 0.55em;
    width: 2em;
    height: 1em;
    box-sizing: border-box;
  }

  .aep-prs__chk:checked + .aep-prs__slider {
    background-color: limegreen;
  }

  /* The snippet's 1px limegreen glow is not a visible focus indicator — it
     disappears against the switch's own green in the revealed state. A real
     ring on :focus-visible only, so a pointer click doesn't draw one. */
  .aep-prs__chk:focus-visible + .aep-prs__slider {
    outline: 2px solid var(--ring);
    outline-offset: 3px;
  }

  .aep-prs__chk:checked + .aep-prs__slider::before {
    transform: translateY(2.3em);
  }

  .aep-prs__chk:checked + .aep-prs__slider::after {
    transform: rotateZ(90deg) rotateY(180deg) translateY(0.45em) translateX(-1.4em);
  }

  .aep-prs__chk:disabled + .aep-prs__slider {
    cursor: not-allowed;
    opacity: 0.5;
  }

  /* Inverted tooltip, for when the switch sits on a dark surface (the login
     card). The shared tooltip is bg-foreground/text-background — near-black on
     near-black there. Same pill, same arrow, colours swapped, so it stays the
     one tooltip design rather than becoming a second one.
     The child selector targets the arrow: the popup's only element child,
     everything else in it being text. */
  .aep-tip-invert {
    background-color: var(--background);
    color: var(--foreground);
  }
  .aep-tip-invert > * {
    background-color: var(--background);
    fill: var(--background);
  }

  /* The travel is decorative; the colour change is what carries the state, so
     reduced motion keeps the state and drops the animation. */
  @media (prefers-reduced-motion: reduce) {
    .aep-prs__slider,
    .aep-prs__slider::before,
    .aep-prs__slider::after {
      transition: none;
    }
  }
`

function PasswordRevealSwitch({
  revealed,
  onRevealedChange,
  hideLabel = false,
  labelClassName,
  wrapperClassName,
  className,
  disabled,
  ...props
}: PasswordRevealSwitchProps) {
  const title = revealed ? "Hide password" : "Show password"
  // The mascot gets a moment here. Loading states aside, this is one of the
  // few places the spider can show up without undercutting anything — nothing
  // about revealing your own secret is safety-critical. No emoji: the spider
  // and web glyphs turn to mush at the tooltip's 12px.
  const tip = revealed ? "Back in the web" : "Let the spider peek"

  // Measured, not hard-coded per call site: the same switch appears on the
  // near-black login card and on the white credential dialog, and the tooltip
  // has to be legible on both. Re-measured each time it opens rather than once
  // on mount, so a theme change or a restyled surface is picked up.
  const wrapperRef = React.useRef<HTMLSpanElement>(null)
  const [onDarkSurface, setOnDarkSurface] = React.useState(false)
  const measure = React.useCallback(
    () => setOnDarkSurface(surfaceIsDark(wrapperRef.current)),
    []
  )
  React.useEffect(measure, [measure])

  return (
    <span ref={wrapperRef} className={cn("aep-prs", wrapperClassName)}>
      <style>{SWITCH_CSS}</style>
      {/* No `title` attribute alongside this — the native tooltip would show
          up a second later on top of the real one. The accessible name lives
          on the input's aria-label, which is what a screen reader announces;
          this tooltip is the sighted-hover equivalent. */}
      <Tooltip onOpenChange={(open) => open && measure()}>
        <TooltipTrigger
          render={
            <label className={cn("aep-prs__switch", className)}>
              <input
                className="aep-prs__chk"
                type="checkbox"
                checked={revealed}
                disabled={disabled}
                onChange={(e) => onRevealedChange(e.target.checked)}
                // The switch is the only control here, so it carries the
                // accessible name. The adjacent text is decorative and
                // mirrors the same state.
                aria-label={title}
                {...props}
              />
              <span className="aep-prs__slider" />
            </label>
          }
        />
        {/* 10px clears the knob's overhang above the switch body. */}
        <TooltipContent
          sideOffset={10}
          className={onDarkSurface ? "aep-tip-invert" : undefined}
        >
          {tip}
        </TooltipContent>
      </Tooltip>
      {!hideLabel && (
        <span
          aria-hidden="true"
          className={cn(
            "select-none text-xs text-muted-foreground",
            labelClassName
          )}
        >
          {revealed ? "Hide" : "Show"}
        </span>
      )}
    </span>
  )
}

export { PasswordRevealSwitch }
