"use client"

import * as React from "react"

import { Button } from "./button"
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip"

interface IconTooltipButtonProps extends React.ComponentProps<typeof Button> {
  tooltip: React.ReactNode
  side?: React.ComponentProps<typeof TooltipContent>["side"]
}

function IconTooltipButton({
  tooltip,
  side = "bottom",
  variant = "ghost",
  size = "icon",
  ...props
}: IconTooltipButtonProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            data-slot="icon-tooltip-button"
            variant={variant}
            size={size}
            {...props}
          />
        }
      />
      <TooltipContent side={side}>{tooltip}</TooltipContent>
    </Tooltip>
  )
}

export { IconTooltipButton }
