"use client"

import * as React from "react"

import { cn } from "@/lib/utils"
import { Switch } from "./switch"

interface PasswordRevealSwitchProps
  extends Omit<React.ComponentProps<typeof Switch>, "checked" | "onCheckedChange"> {
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
 */
function PasswordRevealSwitch({
  revealed,
  onRevealedChange,
  hideLabel = false,
  labelClassName,
  wrapperClassName,
  className,
  ...props
}: PasswordRevealSwitchProps) {
  return (
    <span
      className={cn("inline-flex shrink-0 items-center gap-2", wrapperClassName)}
    >
      <Switch
        checked={revealed}
        onCheckedChange={(checked) => onRevealedChange(checked === true)}
        // The switch is the only control here, so it carries the accessible
        // name. The adjacent text is decorative and mirrors the same state.
        aria-label={revealed ? "Hide password" : "Show password"}
        className={className}
        {...props}
      />
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
