"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

interface DeleteIconButtonProps extends React.ComponentProps<"button"> {
  /**
   * Text revealed on hover/focus. Rendered through CSS `content`, so it is
   * decorative — always pass `aria-label` for the accessible name.
   */
  label?: string
}

/**
 * The delete action button.
 *
 * A native <button> rather than the shared ui/Button: this design owns its own
 * size, radius, colours and hover geometry, and layering it on Button meant
 * fighting that component's variant classes for every one of them.
 *
 * The deliberate exception to the two-button system. Its hover IS the
 * affordance — the revealed label is how you learn what the icon does — so
 * folding it into the shared white button traded a working disclosure for a
 * tooltip. Kept as-is on purpose; see the `.delete-button` comment in
 * app/global.css.
 *
 * The outer span is not decoration — it reserves the button's fully-expanded
 * width so the hover animation costs zero layout. Without it the button grows
 * in normal flow and shoves every sibling along with it, which reflowed whole
 * table rows sideways. See the `.delete-button-slot` comment in
 * app/global.css.
 *
 * `className` and `style` land on that wrapper because they are what callers
 * use for layout (`flex-shrink-0`, spacing). The label custom property is set
 * there too and inherits down to the button's ::before.
 */
function DeleteIconButton({
  className,
  label = "Delete",
  style,
  type = "button",
  ...props
}: DeleteIconButtonProps) {
  return (
    <span
      className={cn("delete-button-slot", className)}
      style={
        {
          // JSON.stringify, not template quotes: CSS `content` needs a quoted
          // string, and a label containing a quote would otherwise break the
          // declaration and silently blank the label out.
          "--delete-button-label": JSON.stringify(label),
          ...style,
        } as React.CSSProperties
      }
    >
      <button
        type={type}
        data-slot="delete-icon-button"
        className="delete-button"
        {...props}
      >
        <svg
          viewBox="0 0 448 512"
          className="delete-button__icon"
          aria-hidden="true"
        >
          <path d="M135.2 17.7L128 32H32C14.3 32 0 46.3 0 64S14.3 96 32 96H416c17.7 0 32-14.3 32-32s-14.3-32-32-32H320l-7.2-14.3C307.4 6.8 296.3 0 284.2 0H163.8c-12.1 0-23.2 6.8-28.6 17.7zM416 128H32L53.2 467c1.6 25.3 22.6 45 47.9 45H346.9c25.3 0 46.3-19.7 47.9-45L416 128z" />
        </svg>
      </button>
    </span>
  )
}

export { DeleteIconButton }
