import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

// Two button designs, platform-wide.
//
//   ink   — the primary. Black face, white label; on hover the fill crossfades
//           away to leave the label in ink on the page. 320ms in, 420ms out:
//           leaving is deliberately slower than arriving, which is what reads
//           as a fade-out rather than a rewind.
//   white — the secondary. Rotating gradient rim, ink bloom from the
//           bottom-right, 1.2deg tilt. Choreography lives in global.css
//           (.btn-white) because it needs pseudo-elements and a mask.
//
// Everything else is a *tone* of one of those two: same geometry, same
// choreography, different hue. `destructive` and `success` change the label,
// the border, and the two rim stops and nothing else — an irreversible action
// keeps its colour warning without becoming a third button design.
//
// Removed here: `secondary`, `link`, and `hero` (zero call sites between
// them), and the blue `default`, which is folded into `ink`. That last one is
// the visible change — --primary is #2563EB, so every <Button> written without
// an explicit variant was rendering blue and is now black.
const INK =
  "btn-edge border-foreground bg-foreground text-background duration-[420ms] hover:bg-transparent hover:text-foreground hover:duration-[320ms]"

const WHITE = "btn-white btn-edge bg-background"

const buttonVariants = cva(
  // `transition-all` was animating every animatable property, including layout
  // ones we never intended to move; the named list is what actually changes.
  // 180ms + ease-out-quint is the shared curve (--ease-out in global.css).
  // background-size/position are on the list for the white button's bloom,
  // which is painted as a background-image rather than an extra element.
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-[color,background-color,border-color,box-shadow,transform,background-size,background-position] duration-[180ms] ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: INK,
        // Kept as a name because thirteen call sites already say `invert` and
        // the word still describes what it does. Same button as `default`.
        invert: INK,
        outline: `${WHITE} border-border text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:border-input dark:bg-input/30`,
        // The white button with its border held back until hover. For icon
        // buttons inside dense tables, where a resting border on every row
        // control is chrome the table doesn't need. Same object, same hover.
        ghost: `${WHITE} border-transparent bg-transparent text-foreground hover:border-border aria-expanded:bg-muted aria-expanded:text-foreground`,
        destructive: `${WHITE} text-[var(--destructive-ink)] border-[color-mix(in_oklch,var(--destructive)_30%,transparent)] [--btn-rim-a:var(--destructive)] [--btn-rim-b:oklch(0.72_0.16_35)] [--btn-bloom:var(--destructive)] focus-visible:border-destructive/40 focus-visible:ring-destructive/20`,
        success: `${WHITE} text-[var(--success-ink)] border-[color-mix(in_oklch,var(--success)_30%,transparent)] [--btn-rim-a:var(--success)] [--btn-rim-b:oklch(0.7_0.13_170)] [--btn-bloom:var(--success)] focus-visible:border-success/40 focus-visible:ring-success/20`,
      },
      // Size changes height, padding, and type scale — never the radius. The
      // small sizes used to override it to `min(var(--radius-md),10px)`, but
      // --radius-md is not defined anywhere in this project: the min() was
      // invalid at computed-value time, so border-radius fell back to 0 and
      // every xs/sm/icon-xs/icon-sm button rendered with square corners. An
      // inline borderRadius on each call site hid that until those inline
      // styles were removed. One radius, from the base, for all of them.
      size: {
        default:
          "h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        xs: "h-6 gap-1 px-2 text-xs has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 px-2.5 text-[0.8rem] has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        icon: "size-8",
        "icon-xs": "size-6 [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-7",
        "icon-lg": "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

// The variants built on the white button. Each one gets the orbiting-beam rim,
// which needs a static masked container wrapping two independently rotating
// children — the button's own ::before/::after cannot supply that, and ::after
// is already the orange edge. So the white family renders one real element.
const WHITE_VARIANTS = new Set(["outline", "ghost", "destructive", "success"])

function Button({
  className,
  variant = "default",
  size = "default",
  nudge = false,
  children,
  ...props
}: ButtonPrimitive.Props &
  VariantProps<typeof buttonVariants> & {
    /**
     * Slides the trailing icon 3px on hover. Opt-in rather than automatic:
     * it reads as "this goes somewhere", so it belongs on "Open report" or
     * "View run" and never on Cancel. Meaningless without a trailing icon.
     */
    nudge?: boolean
  }) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), nudge && "btn-nudge", className)}
      {...props}
    >
      {/* Absolutely positioned and aria-hidden: out of flow, so it never
          contributes to the flex gap or the accessible name. */}
      {variant && WHITE_VARIANTS.has(variant) && (
        <span aria-hidden="true" data-slot="button-rim" className="btn-rim" />
      )}
      {children}
    </ButtonPrimitive>
  )
}

export { Button, buttonVariants }
